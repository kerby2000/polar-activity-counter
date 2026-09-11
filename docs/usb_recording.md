# USB download of sensor-memory recordings

Start with the [user manual](user_manual.md#optional-usb-download) for installation
and a new session. The measured results below describe the original physical check;
its analysis used the legacy engine. Current recognition uses `--engine adaptive`
with your own trained model.

The `usb` commands copy ACC/GYRO recordings through the supplied USB adapter and
export the same CSV/packet format used by `offline sync` and `analyze`. They use
Windows' native HID support through the optional `hidapi` dependency. They do not
use Bluetooth, FlowSync, a cloud account or a replacement USB driver for downloads.

**Status, 10 September 2026: physical USB download passed** on CF204722, firmware
3.0.16. Both pull-up files (21,890 bytes) downloaded in 1.494 seconds of file-transfer
time, or **14.3 KiB/s**, and match the saved BLE reference byte for byte. The complete
connection/list/download/export operation took 1.823 seconds. Both IMUs contain
3,220 samples with no quality warnings; the analyser again estimates two pull-ups.
This small-file check does not establish sustained full-day download performance.

## First test using the saved pull-up recording

Tested firmware is **3.0.16**, whose [release notes](https://support.polar.com/en/updates/polar-verity-sense-3016-firmware-update)
describe the restored USB setting. Enable **USB** in the Polar Flow app first; it is
disabled by default. Place the sensor into its adapter with the contacts aligned,
then connect it to the PC. Close FlowSync before using our commands so it does not
compete for the device. See [Polar's USB instructions](https://support.polar.com/en/using-polar-verity-sense-with-usb-and-flowsync).

Windows may show the sensor under **Human Interface Devices → USB Input Device**,
alongside a **HID-compliant vendor-defined device**, rather than by its Polar name.
The tested instance is `USB\VID_0DA4&PID_0008\CF204722`; its standard Microsoft
`HidUsb` driver (`input.inf`) reports problem code 0. No special Polar driver was
installed. `usb scan` reads the descriptive product name, **Polar INW4J**, and serial.

Run from the repository in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[usb]"
.\.venv\Scripts\python.exe -m polar_activity usb scan
.\.venv\Scripts\python.exe -m polar_activity usb list --output data/usb-probe-01.json
.\.venv\Scripts\python.exe -m polar_activity usb sync data/raw/pullups-01 --output data/raw/pullups-01-usb
.\.venv\Scripts\python.exe -m polar_activity analyze data/raw/pullups-01-usb
```

No new exercise is needed. The sync command downloads both original `.REC` files
again and requires their SHA-256 hashes to equal the verified BLE copies. The
expected result is **3,220 samples per IMU**, without quality warnings. Original
PC recordings and sensor files are retained. The separate output also avoids
mistaking a cached BLE download for a successful USB transfer.

If multiple Polar HID interfaces appear, use the exact USB serial or `path` from
`usb scan` as `--device` on `list`/`sync`. This argument is a USB identifier, not a
Bluetooth address. An unknown USB serial is acceptable for this reference test
only because every downloaded file must match the existing reference hash.

## Future recordings

Start internal recording with the existing `offline start` command, exercise out
of range, then stop it over Bluetooth **before docking**:

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline stop data/raw/SESSION
.\.venv\Scripts\python.exe -m polar_activity usb sync data/raw/SESSION --output data/raw/SESSION-usb
.\.venv\Scripts\python.exe -m polar_activity analyze data/raw/SESSION-usb --engine adaptive --model data/models/personal-adaptive.json
```

USB currently performs downloads only. The source must be a stopped or already
downloaded session created by this app. It does not start/stop measurements, infer
that docking stopped a recording, or import ordinary Flow heart-rate sessions.
For a first download without reference hashes, the USB serial must match the
source manifest's Polar ID. If firmware uses a different serial representation,
save the scan output for inspection; the existing BLE sync remains available.

Both IMUs and all sequential split files must be present. Sequential ACC/GYRO
starts may use adjacent one-second recording directories, as seen in pullups-02.
More widely separated starts or multiple new directories for one stream are
refused rather than combined.

## Integrity, retries and timing

Every received recording is saved under `transfer-attempts/`, including malformed,
short or interrupted transfers. Corresponding JSON retains exact HID reports,
byte counts, hashes, errors and file-transfer timing. `sensor-files/` contains
files that pass the directory-size and strict recording-format checks. Existing
reference hashes, when available, are an additional mandatory check.

The device directory is checked again after download. CSV exports are published
only after all selected files are verified and the listing is stable. Completion
describes those files; it does not independently prove requested wall-time
coverage. Original timestamps, scale factors and decoder quality checks remain in
effect. Offline recordings have no host arrival times and no HR samples in these
ACC/GYRO files.

After an interruption, reconnect the adapter and repeat **the same sync command**.
Verified local files are hash-checked and reused; an incomplete file is downloaded
again from its beginning. Packet-offset resume is not implemented. A completed
retry validates local hashes without opening USB. To benchmark another actual
transfer, choose a new output folder.

`usb-session.json` records the source manifest hash, reference session ID, selected
sensor files, attempts, connection diagnostics and export hashes. Unrelated
existing output directories and modified verified files are refused.

Progress appears every five seconds during a long file transfer. The final report
shows useful bytes, time spent transferring files and useful KiB/s. Directory
queries, connection setup, verification and CSV decoding are outside that rate;
connection elapsed time is also saved. Do not extrapolate a simulated HID speed
or a tiny transfer into a full-day hardware estimate. The existing decoder builds
exports in memory; memory use and runtime on a full sensor remain unbenchmarked.

`usb list --output NEW.json` saves diagnostics even when a USB reply is malformed
or times out. `--timeout 30` extends the idle reply timeout. `--disk` additionally
tries the read-only disk-space query; that USB query is optional pending hardware
verification. Do not run FlowSync concurrently or replace drivers for this test.

## Protocol implementation and evidence

USB and BLE use distinct envelopes. This implementation independently encodes
64-byte HID reports: host report ID `01`, a length/continuation byte, an 8-bit sequence
number, and up to 61 data bytes. This sensor's input report ID is **`11`**, confirmed
by its HID descriptor and actual replies; legacy input ID `01` remains accepted.
The initial implementation rejected `11` and was corrected during the physical
test, with a captured-reply regression. Responses are acknowledged by sequence number;
padding is excluded by the encoded size. RFC60 GET requests use the existing
protobuf command/path encoder, with USB message termination. Response status and
termination are checked before a payload is accepted. Timeout, cancellation,
sequence mismatch and unexpected framing make that connection unusable until
reopened. Only GET and disk-space query 5 are accepted by the transport; it has no
sensor-file write/delete/reset operations.

Framing references are primary implementations for **older Polar devices**, not
Verity-specific wire captures:

- [polarusbdump, PolarPacket/PolarRequest/PolarResponse/PolarService](https://github.com/dredzik/polarusbdump/tree/b9676cbd704c99d0d3b32a18718d8b9379a85c34/src/main/java/io/typedef/polar/io)
- [v800_downloader USB request and response implementation](https://github.com/profanum429/v800_downloader/blob/600a59ccf92cab2ed57795a3f22ea4bff378e855/src/usb/v800usb.cpp)
- [Polar SDK device information protobuf](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/sources/Android/android-communications/library/src/sdk/proto/device.proto)

Tests cover fixed wire bytes, report boundaries, sequence wrap, trailing zero
bytes, fragmented requests, invalid headers/status, short writes, cancellation,
timeouts, retry, partial evidence, reference mismatches, source preservation,
changed listings and analyser-compatible exports. An optional local test replays
the existing private pull-up files through the HID transport and compares both
CSV streams and packet JSON byte for byte. CI skips that private-data test when
the recording is unavailable. A separate regression uses the actual first
`/DEVICE.BPB` reply. Physical evidence is retained in `data/usb-probe-20260910-03.json`,
`data/usb-pending-device-probe-20260910.json` (descriptor and investigation), and
`data/raw/pullups-01-usb/usb-session.json` plus its transfer-attempt files.
