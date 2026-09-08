# Acquisition and clock protocol

Sources and inspected commit hashes are in [research.md](research.md). This is a live-streaming Windows adapter; it never requests SDK mode, changes the device clock, starts offline recording or deletes sensor files.

## Connection and settings

Bleak discovers advertised `Polar Sense` / `Verity Sense` names; `--device` matches an exact ID suffix, full name or OS BLE identifier. Multiple matches require selection. A discovered BLEDevice is passed to one BleakClient on one asyncio loop. Advertised identity and optional Device Information firmware/model and battery reads are recorded, with missing values left null.

PMD control UUID: `FB005C81-02E7-F387-1CAD-8ACD2D8DF0C8`; data UUID: `FB005C82-02E7-F387-1CAD-8ACD2D8DF0C8`. UUIDs and PMD settings serialization come from polar-python 1.1.1. Standard HR uses `2A37`, battery `2A19`, firmware `2A26`, model `2A24` under the Bluetooth base UUID.

Read PMD features (`0F` prefix + feature bits). Query supported settings with `01 <type>`, start with `02 <type> <settings>`, stop with `03 <type>`. ACC type is 2, gyro 5. Normal settings must actually advertise ACC 52/16/8/3 and gyro 52/16/2000/3 (Hz/bits/range/channels). Missing required choices are a visible failure, not silently replaced with constants. Report other capabilities without starting them.

Only one command is outstanding at once. Responses must start `F0 <opcode> <type> <status>`, optionally followed by the continuation byte and parameter data. Four-byte acknowledgements are valid in the current official SDK parser. All errors are rejected, continuation fragments assembled, and stop acknowledgements consumed. A timeout or mismatched response invalidates the channel until reconnect. The session records request/response hex and exact IEEE-754 scale-factor bits; a missing/invalid start scale factor stops acquisition. Successful ACC and gyro starts leave both subscriptions active. HR subscription is attempted afterward and may fail independently.

## Notifications and samples

Every notification is saved in `packets.jsonl` before decoding, together with local packet ID, host monotonic arrival and host UTC arrival. The packet ID counts application-received notifications; it is **not** a sensor sequence counter. Callback work is limited to copying/enqueuing bytes. A bounded 4096-packet buffer fails visibly on overflow and records the exact number rejected by this application. CSV writing and keyboard polling share the event loop; buffered files flush at least once per second. This is adequate for the intended ~104 IMU samples/s and detects overload rather than silently discarding it.

The first 10 bytes contain measurement type (masked with `0x3F`), an unsigned little-endian 64-bit final-sample timestamp at bytes 1-8, and frame type at byte 9. Bit 7 of frame type indicates delta compression. Accepted IMU forms:

* ACC compressed type 0 and 1, 16-bit three-channel reference samples; ACC raw types 0/1/2 (8/16/24-bit signed axes).
* Gyro type 0, compressed or raw, signed three-axis data with 16-bit references/raw values.
* Other forms, including gyro float type 1, are explicitly unsupported in VS-0 and retained as raw bytes with an error. They must not silently decode as a supported type.

Compressed blocks carry bit width, delta-sample count and LSB-first packed signed differences. Each reconstructed vector is relative to the preceding one. Sign extension includes the one-bit case. Truncation, impossible block widths, empty counts and signed 32-bit accumulation overflow are rejected. There are no silent partial decodes. Each vector is multiplied by the negotiated factor, and ACC type 0 additionally by 1000 to convert g to mg. We retain fractional mg, unlike the high-level library's integer conversion. For raw scaled formats this follows the technical PDF's scaling requirement; normal Verity Sense compressed type 0 is the primary hardware acceptance format. All formats/units still require a stationary gravity and gyro check on hardware.

## Reconstructing sample timestamps

Let `T` be this packet's original final-sample timestamp, `P` the previous packet's original final-sample timestamp, `N` the number of samples, and `r` the configured rate.

* First packet: `t[i] = T - round((N - 1 - i) * 1e9 / r)`, `i = 0..N-1`.
* Plausibly contiguous packet: `t[i] = P + round((i + 1) * (T - P) / N)`.
* Contiguity requires `abs((T-P) - N*1e9/r) <= 0.5*1e9/r`. This half-period tolerance is an explicit heuristic, not a vendor guarantee. It allows ordinary clock/rate variation without smoothing away a whole missing sample.
* Otherwise retain the gap: reconstruct backward from `T` at the nominal rate and flag `discontinuity_nominal`. Do not distribute a missing batch over the remaining samples. Lost sample locations inside a packet interval are not identifiable.
* Duplicate/backward endpoints or overlapping reconstructed batches are retained and flag `non_monotonic_frame`; stop the session. Do not invent a monotonic device clock over a reset. Begin a new session after investigating.

All epoch arithmetic is integer nanoseconds. The final reconstructed timestamp equals the original packet timestamp exactly. Each CSV row retains both `packet_timestamp_ns` and reconstructed `device_timestamp_ns`, and names the reconstruction method. Original Polar epoch is 2000-01-01; polar-python's Unix epoch adjustment is not used.

## Shared time axis and labels

ACC and gyro share the device clock but have independent packets and sample times. They are never zipped or resampled in raw storage. `time_s` for both uses a single mapping established by the first valid IMU packet:

`time_s = ((sample_device_ns - anchor_device_ns) + (anchor_host_ns - host_session_start_ns)) / 1e9`

Here `anchor_device_ns` is the first packet's final-sample timestamp and `anchor_host_ns` its host arrival. This preserves relative ACC/gyro timing and rate while expressing the device axis near the keyboard-label axis. It **does not make arrival equal acquisition**: the constant offset includes unknown buffering/BLE latency. `alignment_uncertainty_ms` is null, not zero. Plot labels are approximate in this respect. Drift over longer sessions remains to be measured; no hidden clock correction is applied. Keep a little stillness before/after sets and consider recorded synchronization gestures in later experiments.

Labels and HR use host monotonic time relative to session start. Standard HR has no acquisition timestamp, so no device timestamp is fabricated. HR flags, optional RR intervals and energy fields are retained along with the raw notification. Host UTC is separately recorded; Windows wall-clock changes cannot alter elapsed time.

## Shutdown and diagnostic limits

Duration begins after stream setup; metadata separately records session wall duration and streaming start. Q/duration stop active streams in reverse order, drain queued packets and finalize metadata. Ctrl+C does the same and records interrupted status. Unexpected disconnect/parse error/10s stream silence records failed status and a partial dataset. A second forced interrupt, process kill, disk failure or power loss can leave partial files and up to the recent buffered writes missing; `status: recording` is not evidence of completion. Original packets and journal events permit later recovery.

Quality reports distinguish sample-axis rate from packet-endpoint rate. The latter is `(samples after first packet)/(last packet endpoint - first packet endpoint)` and is unavailable for a single batch. The first batch's nominally reconstructed spacing alone cannot demonstrate actual sample rate. Detect >1.5 nominal-period sample gaps, duplicate/backward times, frame discontinuities, estimated missing samples, overlap and duration mismatch. Host delivery jitter is excluded. Without a stream sequence counter these are **estimates**, not proof of exact radio packet loss.
