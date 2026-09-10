# Dataset format, schema version 1

Each `record` creates a **new** directory; existing directories are rejected to prevent overwriting recordings. Default: `data/raw/<UTC timestamp>/`. Everything under `data/` is ignored by Git. Keep real datasets local.

```text
session/
  metadata.json
  packets.jsonl
  acc.csv
  gyro.csv
  hr.csv
  labels.csv
  label_events.jsonl
  connection.log         # automatic device/Windows backend diagnostics in new sessions
  plots/                 # created by plot command
```

Separate streams preserve independently timestamped ACC and gyro without inventing one-to-one synchronization. Exploratory analysis uses a derived uniform grid for spectra/counting only, with the method documented in [initial_signal_analysis.md](initial_signal_analysis.md). There is no production synchronized training-table export yet. A later resampler must retain original files, document grid/interpolation policy and avoid interpolating across gaps. `packets.jsonl` is the lossless byte record; CSVs are decoded views that can be regenerated after decoder improvements.

## IMU CSVs

| Field | Meaning |
|---|---|
| `time_s` | Approximate host-session-relative coordinate, derived from the common device clock anchor |
| `device_timestamp_ns` | Reconstructed sample timestamp, integer ns in original Polar epoch |
| `packet_timestamp_ns` | Unmodified final-sample timestamp from this notification's header |
| `packet_id`, `sample_index` | Received notification ID and zero-based sample index within it |
| `host_monotonic_ns` | Host **packet arrival**, repeated for all samples in that packet |
| `host_time_utc` | ISO-8601 host **packet arrival**, not acquisition UTC |
| `timestamp_method` | `nominal_first`, `interpolated`, `discontinuity_nominal`, or `non_monotonic_frame` |
| `acc_x_mg`, `acc_y_mg`, `acc_z_mg` | Scaled acceleration, including gravity; fractional mg retained |
| `gyro_x_dps`, `gyro_y_dps`, `gyro_z_dps` | Scaled angular velocity, degrees/s |

Only ACC axis columns occur in acc.csv and gyro columns in gyro.csv. Large timestamps must be loaded as integer/string, never float64. Python's `csv` module reads them as strings; use `int()`. Do not open/re-save raw files through Excel if timestamp precision matters.

## HR and packet bytes

hr.csv contains `time_s` (host elapsed), `host_monotonic_ns`, `host_time_utc`, `packet_id`, `hr_bpm`, `contact_supported`, `contact_detected`, `energy_expended`, and a JSON-array `rr_intervals_ms` field. Absent values are empty. There is no fabricated HR device timestamp. HR contact status on Verity Sense is not a trustworthy wear detector.

Each packets.jsonl line contains `packet_id`, `stream`, `payload_hex`, `host_monotonic_ns` and `host_time_utc`. Preserve metadata's `control_exchanges` and `scale_factors` with these bytes. Notification order is arrival order and can interleave streams; it is not a globally sample-sorted table.

## Ground truth

labels.csv fields: `set_id`, `activity`, `start_time_s`, `end_time_s`, `expected_rep_count`, `note`, `completion`. Times are keyboard-event host elapsed seconds. IDs are zero-padded decimal strings. Empty repetition count means **unknown/not applicable**, not zero. A deliberately entered zero is retained as zero. `completion` is `open`, `complete` or `interrupted`.

labels.csv is atomically rewritten after each change. The independent, flushed label_events.jsonl stores activity selections, set starts/ends, entered counts, notes and interrupted sets, all with host-relative `time_s`. Notes before the first set live only in the journal; otherwise N attaches to the current or latest set. Unmarked periods are **unlabelled**, not automatically negative ground truth. Explicitly record background sets. Custom activity names support reaching, carrying, dressing and other negatives.

Changing activity during an open set is rejected. Ending a set immediately fixes the end timestamp before prompting for reps. Acquisition continues while text/counts are entered. An open set on exit is closed as interrupted with unknown count. Correcting a missed count later can be done by editing only labels.csv (keep a copy and note the correction); raw IMU data stays unchanged. Re-run diagnose/plot afterward. The event journal preserves what was originally entered.

## metadata.json

Contains schema/software version, UUID session ID, host start UTC/monotonic reference, OS/Python/dependency versions, subject, explicit sensor position and arm, notes, queried device identity/firmware/battery/capabilities, requested/acknowledged configurations, scale factors and raw control exchanges, HR enabled status, requested/actual recording durations, streaming-start offset, warnings/errors, shutdown status, application buffer losses, clock mapping and quality report.

Actual counts/rates/gaps are in `quality.acc`, `quality.gyro` and `quality.hr`. The report also includes ACC/gyro overlap and duration mismatch, label counts, missing counts and interrupted sets. `diagnose` recomputes from current CSVs without altering metadata. `synthetic: true` is reserved for explicitly artificial fixtures. See [protocol.md](protocol.md) for clock reconstruction, error behavior and limits of loss inference.

New recordings also contain optional `termination` and `connection_events` fields. Termination distinguishes duration reached, keyboard finish, cancellation, link loss, notification timeout, buffer overflow and setup/acquisition errors. It includes whether disconnect preceded cleanup, host-relative last-notification times/ages, maximum host delivery gaps, buffered packet count and the maximum main recording-loop interval. Connection events retain host monotonic and UTC timestamps and identify whether a disconnect occurred during cleanup. Backend detail is saved in UTC-stamped `connection.log`; a specific physical disconnect reason may still be unavailable. These additions are backward compatible; older sessions may lack them.

Only `status: complete` plus a satisfactory quality report and physical sanity checks passes the whole-session completion gate. A failed partial session can still contain usable complete sets, provided sample coverage, timestamps and labels are verified and the failure/provenance remain explicit. Do not infer valid acquisition from file existence alone or relabel a partial session as complete merely because one set survived.

## Sensor-memory sessions

`offline start` creates a new directory with `offline-session.json`; `offline sync`
adds the original `sensor-files/*.REC` binaries and the CSV/packet/metadata files
above. The manifest retains the device identity, lifecycle, source paths, reported
sizes, hashes and protocol exchanges. `capture_mode: sensor_memory` in metadata
distinguishes these sessions. A `connection.log` appends backend detail across retries.

Verified file contents may be reconstructed from repeated transport payloads using
the safeguards in [offline recording](offline_recording.md). `transfer-attempts/`
retains unmodified received bytes and RFC76 packet arrays. Manifest
`download_attempts` and `downloads[].verification` record the method, hashes,
reported/received byte counts and any removed transport-block indices. Recovery
requires two matching reconstructed reads and does not deduplicate IMU samples.

Offline `time_s` starts at the earliest downloaded sample across both streams, with
integer device timestamps and relative ACC/gyro alignment preserved. Host packet
arrival fields are empty in CSV and null in JSON: downloading is not acquisition.
Packet records additionally identify `source: sensor_memory`, `source_path` and
`source_offset` inside the original binary. Packet IDs are assigned during decoding,
stream by stream, not by Bluetooth arrival order. Header dates are recorded as reported
without assuming clock accuracy. Metadata's start UTC refers to the PC start command;
download UTC and sensor header dates are separate fields.

Offline HR is currently disabled, with an empty standard `hr.csv`. `labels.csv`
starts empty, with later manual annotations preserved. There is no keyboard-event
journal for an unattended recording. The same `diagnose`, `plot` and `count` commands
work on exported data. `status: complete` refers to downloaded file integrity and
parsed sample quality; it does not independently establish how long the sensor
remained powered during the intended workout. See [offline recording](offline_recording.md).

## Derived automatic counts

The offline `count` command writes `counts.json`, `sets.csv`, `cycles.csv`, `pauses.csv` and an optional `automatic_count.png` under `SESSION/automatic-count/` or an explicit `--output` directory. These are derived predictions, separate from manually entered ground truth. The counter does not read `labels.csv`, label events or HR. It uses validated device-relative timing, splits at gaps, and keeps original CSVs and metadata unchanged.

`counts.json` stores input ACC/gyro SHA-256 hashes, source session status, algorithm/settings, all evaluated intact overlaps, gaps, detected set boundaries and each accepted complete cycle. With schema version **2**, local ACC/gyro directions, waveform fit and periodic reference windows are stored in each set's `motion_blocks` (previously directly on a v1 set). Every cycle has a `block_id`. Set-level `pauses` retain the interval and evidence for each short rest used to join blocks; `pause_duration_s` is their total, not a measurement of every brief hesitation within individual blocks. Sets retain a flag if near a recording/gap edge. Similarities are not accuracy probabilities. The activity value is `unclassified_repetitive_motion` until a separate activity classifier is validated. See [automatic_counting.md](automatic_counting.md) for details. Raw recording schemas and source labels are unchanged.
