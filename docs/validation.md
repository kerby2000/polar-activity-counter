# Validation evidence and development history

Current summary, **11 September 2026**: Bluetooth capture, sensor-memory capture,
BLE/USB download and the personal adaptive analyser are implemented. All **289
hardware-free tests pass**, with Ruff and dependency checks passing. Blind-02
predicted 3 jumps, 10 squats and 11 push-ups before disclosure; the participant
confirmed all three. This is one fresh session for one participant/placement.

See [illustrated blind results](examples.md), [adaptive evaluation](adaptive_analysis.md)
and the [installation manual](user_manual.md). Long-session reliability and broader
recognition accuracy remain open. The entries below are **dated development
history**; earlier test totals, pending gates and model results describe their stage.

## HR, MAG and final-effort continuation, 2026-09-11

Real `magnetic-01` capture downloaded 8,360 ACC, 8,131 GYRO, 158 HR and 3,168 MAG
samples. MAG runs at about 20.10 Hz with no internal timestamp gaps; its type-0
format does not report calibration. HR has 157 valid readings after excluding an
initial zero. The IMUs have continuous timestamps but GYRO ends 4.326 seconds before
ACC. Both exercise sets are inside their common coverage; no missing tail samples
were fabricated. The logs do not establish why that tail is shorter.

After the user disclosed 10 push-ups and 3 pull-ups, bounded continuation recovered
the third pull-up effort after a 4.28-second rest. The updated estimate is 10
push-ups and 3 pull-up attempts, with only 2 pull-up returns fully observed. The
original 10/2 prediction remains frozen, and the model was not retrained. All 11
earlier recording replays preserve their sets, counts, boundaries and unassigned
attempts. This is development evidence after feedback; see the
[published comparison](examples.md#magnetic-01-motion-magnetism-and-heart-rate).

The first sync disconnected during the initial PMD status read before downloading
or stopping anything. The app now detects link loss promptly and reconnects once
for that read only, retaining ownership checks and command timing/outcomes. Tests
cover repeated failure, protocol rejections, malformed status and failures after
stopping, without replaying mutating commands. Cancellation before data arrives
now retains the interrupted-session status. No new physical retry test was needed
for these changes; the new recovery path is verified with simulated failures.

**289 automated tests pass**, with Ruff lint/format and dependency checks passing.
An additional 42 integrity checks verified raw/frozen artifacts and downloads;
the personal model hash remains unchanged. HR/MAG stay outside recognition until
their value is established on held-out recordings with timing/calibration checks.

## Automatic boundary and complete-cycle counter, 2026-09-10

The new offline `count` command scans full ACC/gyro recordings without reading labels or expected counts. It automatically finds one interval at 47.483872–67.123872s and 13 complete motion cycles in `exercise-02`. All three earlier diagnostic recordings yield no qualifying set. Three coordinate rotations of the actual signals and a 123.4s clock shift preserve the count; source hashes remain unchanged. This is development evidence, not independent counting accuracy against verified ground truth.

The full suite now passes **80 tests**, including 19 counter tests for known synthetic cycles/cadences, separate sets, sensor rotation/bias/noise, partial repetitions, clipping, data gaps, high-frequency vibration, irregular/one-way motion, invalid timestamps and label-independent CLI export. Ruff lint and formatting pass. No further physical recording was needed. See [automatic counting](automatic_counting.md) for the method, actual output plot, command and limits; earlier test counts below are historical.

## First labelled set and connection follow-up, 2026-09-10

Real `data/raw/exercise-02` contains one complete labelled push-up interval, 30.594–76.797s, with 2446 samples per IMU stream. It remains usable despite the later disconnect. All 197 raw packets match the saved CSVs. Exploratory analysis finds 13 candidate events in each selected axis, approximately 1.49s apart. The original entered count was 12; Sergey later said it may have been 13. No automatic-count accuracy claim follows from this uncertain label. Full findings and plots: [initial signal analysis](initial_signal_analysis.md).

The original link loss did not reproduce during a fresh 180-second capture with ACC, gyro and HR: 9900 samples per IMU stream, 187 HR readings, approximately 52.942 Hz packet-endpoint rates, zero detected gaps/duplicates/backward timestamps, zero application drops and no warnings. Device spans were ACC 187.011s / gyro 187.041s, including setup/shutdown samples. A subsequent 20-second check verified automatic connection logging and normal termination metadata: 1440 samples per IMU stream and 28 HR readings, again no quality warnings. No exercise was requested and posture/placement during these checks was unobserved.

New recordings save `connection.log`, termination reasons, last-notification ages, loop timing and timestamped connection/cleanup events. Cleanup no longer emits an HR stop error when the link has already gone. Full reasoning, limitations and evidence: [disconnect investigation](disconnect_investigation.md). Latest automated suite: **61 tests pass**; Ruff lint and formatting pass. Historical test counts and earlier limitations below describe their respective investigation stages.

## Real recording and fixes, 2026-09-10

The failed user session returned successful ACC start acknowledgement `f002020000`, which contains no FACTOR. The collector incorrectly required this optional field. The official SDK defaults to 1.0; the device sends compressed ACC type 1 already in mg. Gyro returned `f0020500000501295c8f3d`, factor approximately 0.07, and compressed type 0. Raw ACC scaling and gyro float-frame handling were audited against the same SDK source revision.

A live capture also exposed an overly strict timestamp assumption: nominal 52 Hz versus an actual packet-endpoint rate about 52.94 Hz. Reconstructing every unmatched batch at exactly 52 Hz caused artificial backward timestamps. Contiguous intervals now allow 2% nominal variation, while larger gaps remain explicit and original timestamps are retained. This cannot establish exact radio packet loss or detect every small loss within the tolerance.

Replayed saved packet evidence, then ran the installed recorder with `--duration 20 --no-interactive`. Local evidence: `data/raw/startup-check-20260910-fixed/`. No exercise or placement was requested, so these are startup diagnostics, not labelled training data.

| Stream | Samples | Packet-endpoint rate | Device span | Detected gaps / duplicates / backwards |
|---|---:|---:|---:|---|
| ACC | 1220 | 52.943 Hz | 23.057 s | 0 / 0 / 0 |
| Gyro | 1200 | 52.943 Hz | 22.691 s | 0 / 0 / 0 |

HR delivered 23 readings. Status `complete`, empty quality warnings and parse errors, zero application drops, and successful stop acknowledgements for both IMU streams. Mean acceleration magnitude was 1000.389 mg. Startup and shutdown samples account for device spans longer than the requested ready-to-stop interval; total session duration including setup was 33.203 seconds. This verifies a brief simultaneous recording, not sensor calibration, long-session reliability or exercise classification.

The recorder now waits for two valid packets per stream before printing READY and accepting labels. It reports saved counts during recording. Regression checks cover the empty ACC start acknowledgement through recording, physical packet timing intervals, format-specific units, gyro float deltas, malformed samples and readiness. **55 automated tests pass.**

Source: [official SDK factor handling](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/sources/Android/android-communications/library/src/main/java/com/polar/androidcommunications/api/ble/model/gatt/client/pmd/BlePMDClient.kt#L75-L82), with adjacent `model/AccData.kt`, `model/GyrData.kt` and `model/PmdTimeStampUtils.kt`.

## Windows connection investigation, 2026-09-10

The TP-Link UB600 now uses the signed TP-Link driver 20.11.3036.3000. Discovery receives the Verity Sense advertisement. The original `verify` failure was reproduced: connection completed, then the PMD control indication subscription failed with Windows error -2147023673 (operation canceled). A services-changed warning was also logged. There is no explicit pairing call in that path.

A separate probe with uncached Windows service discovery successfully read firmware `0.1.5`, model `INW4J`, battery 34%, and PMD feature bytes `0f6e62000000000000000000000000000000`. Both PMD control indications and data notifications were enabled, and the device returned ACC settings: 52 Hz, 16 bits, range 8, three channels. These are actual device responses, not assumed firmware/configuration values. Enabling notifications alone does not establish sample delivery.

A follow-up connection exceeded the old ten-second setup budget. Later scans returned no matching sensor. This evidence motivates fresh service discovery and a separate, configurable connection timeout, but does **not** yet prove that the connection fix is repeatable. The patched CLI needs another live run once the sensor is advertising again. No physical ACC/gyro recording has been obtained during this investigation.

The updated automated suite passes **43 tests**, including stale service handling, stage-specific setup timeouts, cleanup after Windows cancellation, and non-empty CLI timeout errors. Ruff lint passes. These checks do not replace the pending hardware acceptance gate.

## Environment

* Microsoft Windows 11 Pro, build 10.0.26200, x86-64.
* Python 3.11.15, project-local `.venv`.
* polar-python 1.1.1, Bleak 3.0.2, NumPy 2.4.6, matplotlib 3.11.1.
* Development: pytest 9.1.1, Ruff 0.16.6.

## Initial hardware attempt, 2026-09-09

Executed the installed application:

```powershell
.\.venv\Scripts\python.exe -m polar_activity verify --scan-timeout 8 --json
```

Exit code 1, with actionable error:

```text
Error: BLE scan failed: ('No Bluetooth adapter found',
<BleakBluetoothNotAvailableReason.NO_BLUETOOTH: 1>).
Enable Bluetooth in Windows Settings, check the adapter/driver
and run from a plain PowerShell terminal.
```

No sensor identity, firmware, negotiated configuration, physical samples or simultaneous-stream result was obtained. **Actual ACC/gyro configured rate, measured rate, counts, duration, gaps and loss evidence are all unavailable.** This is an environment limitation, not proof that the sensor or protocol failed. There is no hardware-evidence commit and no exercise recognition claim.

## Software validation

Automated suite exercises the production adapter via fake GATT services: device capability reads, serialized commands, error codes, four-byte acknowledgements, response fragmentation, stale-channel timeouts, start/stop lifecycle, simultaneous ACC/gyro delivery, optional HR failure, partial startup, cancellation, disconnect and malformed packet preservation. It also verifies signed compressed deltas, large integer timestamps, scaling precision, missing batches, label controls, background activities, CSV storage, diagnostics and whole-session/set/activity/time-range PNGs.

Commands: `python -m pytest -q` (**35 passed**), `python -m ruff check .` (passed), `python -m ruff format --check .` (passed), `python -m pip check` (no broken requirements), using the venv Python. The test suite is hardware-free; CI uses Windows/Linux and Python 3.11/3.12.

Ran `scripts/make_synthetic_session.py data/raw/synthetic-qa`, then `python -m polar_activity plot data/raw/synthetic-qa`. Artificial QA fixture evidence, **not device measurements**:

| Stream | Configured / measured endpoint rate | Samples | Device span | Largest gap | Inferred missing / duplicate / backward |
|---|---|---|---|---|---|
| Synthetic ACC | 52 / 52.000 Hz | 624 | 11.981 s | 19.231 ms | 0 / 0 / 0 |
| Synthetic gyro | 52 / 52.000 Hz | 624 | 11.981 s | 19.231 ms | 0 / 0 / 0 |

The fixture has 12 synthetic HR values and four illustrative labelled intervals. PNGs are generated locally under ignored `data/`; they are not real training examples. ACC, gyro and HR PNGs were visually checked; the right-edge HR label placement was corrected after that review.

## Physical acceptance gate

Follow the exact ten-second sanity check and short labelled exercise sequence in [README](../README.md#first-physical-experiment-for-sergey). A successful real test must establish discovery, connection, queried capabilities, units/gravity sanity, simultaneous ACC+gyro acquisition, actual rates, preserved timestamps, label ground truth and useful plots. HR is optional. Repeat exercise sets/background activities after basic acquisition quality passes.

Remaining questions: real firmware compatibility and negotiated formats/factors; 52-Hz reliability with HR; timestamp/label offset and long-session drift; upper-arm exercise signatures, visible reps, orientation changes and daily-motion false positives. Offline rates, power-up trigger behavior, simultaneous offline ACC+gyro, battery/storage and iOS download caveats need a later native hardware test.

## Sensor-memory implementation, 2026-09-10

The new Windows `offline start/status/stop/list/sync` workflow is implemented; a
native iOS app is not required for explicit PC start and later PC download. **120
tests passed**, `ruff check .`, `ruff format --check .` and `git diff --check` passed.
The suite includes a persistent fake sensor recording across BLE disconnection,
lost-start-ACK recovery, interrupted download/retry, old-file preservation,
restarted-recording protection, strict file decoding and existing counter input.

Additional checks against official Polar SDK `.REC` test vectors decoded 460 ACC
and 72 gyro samples, matching the expected first two samples and last timestamp.
These are upstream fixtures, not new physical exercise data.

The actual CF204722 returned offline settings for both ACC and gyro at 13/26/52 Hz.
Its Software Revision (`2A28`) is **3.0.16**, matching Flow; its separate Firmware
Revision (`2A26`) is **0.1.5**. The report now retains both. Historical metadata
containing only 0.1.5 should not be interpreted as the application firmware version.

At that stage the sensor stopped advertising, so physical verification was pending.
The subsequent completed stationary check is recorded below. No offline exercise
recording was requested or claimed as completed. See [commands, evidence and
limitations](offline_recording.md).

### Completed physical check later the same day

After the user turned on CF204722, three initial attempts exposed a Windows link
teardown when closing a full Flow-style sync session. They failed before any PMD
recording-start request. The implementation was corrected to use the SDK's direct
offline-file GET approach, avoiding those initialization/termination notifications.

`data/raw/offline-check-20260910-04` then passed actual start, BLE disconnect,
reconnect, stop, download, decode, diagnostics and counting. There are **2,660
samples per IMU**, spanning 50.273 s ACC and 50.308 s gyro, measured endpoint rates
52.937 Hz, with zero detected gaps/duplicates/backwards and no quality warnings.
The PC waited more than 44 seconds before attempting reconnection. Median ACC
magnitude is 1,006.09 mg. All CSV values and packet bytes match replay of retained
sensor files. Counter result: zero qualifying sets, consistent with the requested
stationary check. Repeated sync verified local hashes without using Bluetooth.
Both streams were stopped, and original sensor files were retained.

The software suite now has **121 passing tests**, including a direct GATT
start/download regression; Ruff lint and formatting checks pass. Full evidence and
remaining longer-duration, power-off and movement validation limits are in
[offline recording](offline_recording.md). No exercise test is needed to repeat
this completed acceptance check.

### Pull-up file-transfer recovery

The subsequent real `pullups-01` exercise exposed adjacent duplicate RFC76 payloads
with advancing sequence numbers, including duplicate terminal packets. Two original
ACC downloads exceeded the listed 10,498-byte file size. The recording was recovered
by requiring exact reconstructed sizes, strict decoding, monotonic frame timestamps
and byte-for-byte agreement between two independent reads of each file. Unmodified
received bytes and transport packets are preserved under `transfer-attempts/`.

Recovered: 3,220 samples per IMU, approximately 60.85 seconds, no quality warnings,
and exact replay agreement between verified files, CSV values and packet records.
The user confirmed two repetitions total, recorded separately as reference data;
the current three-cycle-minimum detector returned zero qualifying sets. Source
sensor files were not deleted and no additional exercise was requested.

The expanded suite passes **128 tests**, including terminal-packet isolation,
two-read recovery agreement, rejection of ambiguous/missing data, preservation of
failed transfers and unchanged correctly sized files with legitimate repetitions.
Ruff lint and formatting checks pass. See [recovery details](offline_recording.md).

### USB download implementation, 10 September 2026

Added an independent HID transport, optional `hidapi` dependency, USB inventory,
verified downloads into a separate folder and interrupted-download recovery.
The suite now has **187 passing tests**, including 36 USB tests. Ruff lint,
formatting and dependency checks pass. The optional private-data test was run
locally: real recovered pull-up files replayed through an injected HID endpoint
produce byte-identical ACC CSV, GYRO CSV and packet JSON, with 3,220 samples per
IMU and no quality warnings. The original recording was unchanged.

These are protocol/workflow tests, **not a physical USB acceptance result**.
Native `hidapi` is installed in the Windows venv, but no Polar USB device was
connected during initial enumeration. Verity Sense HID framing, USB serial
identity and useful transfer speed remain pending the user's adapter connection.
The next test will copy the existing sensor files and compare their SHA-256
hashes with the BLE reference; no new exercise or sensor-file deletion is needed.
See [USB commands, evidence and remaining limits](usb_recording.md).

### Completed physical USB check later the same day

Windows subsequently enumerated `USB\VID_0DA4&PID_0008\CF204722` as **USB Input
Device**, HIDClass. Its existing Microsoft `HidUsb` driver (`input.inf`) had problem
code 0; no special driver or driver replacement was needed. HID API identified
**Polar INW4J**, serial CF204722. The first device-information response exposed
input report ID `0x11`, confirmed by the HID descriptor. The parser now accepts
this ID while retaining legacy `0x01` input support; requests and ACKs still use
output ID `0x01`. A real captured reply is included in the regression suite.

The corrected `usb list` found both existing recording directories. `usb sync`
downloaded the 10,498-byte ACC and 11,392-byte GYRO pull-up files into
`data/raw/pullups-01-usb`. Both SHA-256 hashes match the verified BLE reference.
File transfer took **1.4935 s**, **14.3 KiB/s** useful throughput; the full connection,
two directory scans, downloads and export took **1.8228 s**. Both IMUs contain
**3,220 samples**, without quality warnings. Personal analysis again reports
**2 pull-ups**, 22.33–28.89 s, correctly identified as a training replay.

No original PC or sensor recording was deleted or modified. This small transfer
does not validate full-memory throughput or long-session decoder memory use. The
full suite now has **188 passing tests**, including 37 USB tests; Ruff lint and
format checks pass. Evidence paths are listed in [USB recording](usb_recording.md).

### Pullups-02 and stairs-01 reference update

Both new recordings pass integrity checks: 4,120 and 5,900 samples per IMU,
respectively, without detected gaps or quality warnings. The prior model missed
the new pull-up set and had no stairs class. Those predictions were frozen before
adding references. The updated model contains 13 intervals/170 correlated windows.

Pullups-02 now yields four attempts with three returned motion cycles. The fourth
has 58% of their median sensor-direction excursion and no return before a dismount
cutoff. This is a motion comparison, not an anatomical form score. Stairs-01 yields
four stairs intervals matching the user's described sequence; direction and
tread counts remain user context rather than automatic outputs. Handrail use is
confirmed only as absent during the second descent.

All eight recordings were replayed and original file hashes rechecked unchanged.
Previous push-up, jump and squat counts remain unchanged. The original two-pull-up
count remains two, with its final return conservatively marked unobserved by the
new impact cutoff. USB accepts verified ACC/GYRO starts across adjacent seconds,
covering the directory layout exposed by pullups-02. There are **198 passing tests**;
Ruff lint/format pass. The updated model is frozen for the next blind test. See
[full analysis and evaluation limits](pullups_stairs_validation.md).
