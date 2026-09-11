# Record in sensor memory, then download on Windows

For installation and the current end-to-end workflow, see the [user manual](user_manual.md).
This document also retains the dated physical checks and recovery investigation.
Replace the historical device ID, subject and folder examples with your own values.

The `offline` commands start accelerometer and gyroscope recording **inside the
Verity Sense**, disconnect from Bluetooth, and download the saved motion data when
you return to the PC. No phone recorder or continuous Bluetooth connection is needed.
This is separate from the sensor's ordinary button-selected training recording:
use the commands below to explicitly request raw ACC and gyro.

There is also a [USB adapter download implementation](usb_recording.md) with
separate output folders and reference-hash verification. Its physical check on
CF204722/3.0.16 passed: both saved pull-up files match the BLE reference byte for
byte. The physical results below describe the **Bluetooth** recording/download path.

Implementation status (2026-09-10): **physical start/disconnect/download check passed**
on CF204722, firmware 3.0.16. Both IMUs saved 2,660 samples across approximately
50 seconds, including more than 44 seconds before the PC attempted to reconnect.
No timestamp gaps, duplicates or backward timestamps were detected. The check below
is available for a new setup; Sergey does not need to repeat it before reference sets.

## First check: no exercise

Turn on the sensor in **sensor/heart mode (blue side LED)**, unplug it from the
charger and leave it near the PC. Close Polar Flow's connection. From the repository
directory in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline start `
  --device CF204722 `
  --subject sergey `
  --sensor-position upper_arm_left `
  --notes "Stationary offline recording check; no exercise" `
  --output data/raw/offline-check-01
```

Wait for **`Internal ACC + GYRO recording confirmed at 52 Hz`** and for the command
to exit. The Bluetooth connection has then closed while both streams keep recording
internally. Leave the sensor on for about 30 seconds, then run:

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline sync data/raw/offline-check-01
.\.venv\Scripts\python.exe -m polar_activity diagnose data/raw/offline-check-01
```

`sync` reconnects to the same sensor, stops the streams, downloads the new files,
decodes them and reports sample counts. The acceptance check should show both ACC
and gyro with nonzero samples spanning the disconnected interval, sensible units,
monotonic timestamps and useful overlap. A small difference in their start/stop
times is expected because commands are sequential. A stationary check is not a
test of exercise recognition or usable Bluetooth range.

Use a **new output directory** for every `start`. A failed start retains a manifest
and error details in its directory. If it reached a recording-start request, use
`offline stop` or `offline sync` with that same directory to resolve any uncertain
start before trying a new recording.

## Exercise workflow after the check passes

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline start `
  --device CF204722 --subject sergey --sensor-position upper_arm_left `
  --notes "Reference pull-up recording 1; reps supplied afterwards" `
  --output data/raw/pullups-01
```

After the confirmation and command exit, walk to the bar or stairs, perform one
reference set, and return. Keep the sensor **on throughout**. The recording includes
walking there, rests, exercise and walking back; boundaries are detected later.
Do not start another recording with another app before this session is downloaded.
There is no time limit configured by this command: explicitly stop or sync when done.

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline sync data/raw/pullups-01
.\.venv\Scripts\python.exe -m polar_activity count data/raw/pullups-01
```

The generic counter accepts these downloads in the same way as live recordings.
For exercise recognition, train a personal model and use `analyze SESSION --engine
adaptive --model MODEL.json`, as described in the [manual](user_manual.md#train-your-personal-model).
Supply reference activities and approximate counts afterwards for training/review.
Offline recording has no keyboard labels while away;
`labels.csv` starts empty. Notes and folder names are not detector inputs.

## Other commands and retry behavior

```powershell
# Read active measurement flags and free sensor memory.
.\.venv\Scripts\python.exe -m polar_activity offline status --device CF204722
# Stop this session now; download it later.
.\.venv\Scripts\python.exe -m polar_activity offline stop data/raw/pullups-01
# Inspect stored ACC/gyro files when no IMU recording is active.
.\.venv\Scripts\python.exe -m polar_activity offline list --device CF204722
```

All subcommands accept `--scan-timeout` and `--connect-timeout`. Put `--verbose`
before `offline` for console diagnostics. A session's `connection.log` and
`offline-session.json` preserve connection events, control exchanges, file-transfer
requests, progress, byte counts and errors. Keep that directory even after failure.

If a download fails, leave the sensor files intact and repeat **the same `sync`
command**. Successfully downloaded raw files remain locally. Retry downloads the
selected files again and checks their sizes before decoding. A fully completed sync
verifies saved hashes and exits without Bluetooth access. If a recording is not yet
visible, wait briefly and retry; sensor writes are buffered. Polar documents that
files can take several minutes to appear during an ongoing recording.

Some observed transfers repeat adjacent payload blocks with advancing sequence
numbers, even repeating the final packet. The downloader now retains all received
bytes and transport packets under `transfer-attempts/`. If the received size is wrong,
it tries removing only adjacent byte-identical transport payloads. Recovery is accepted
only when the result has the exact directory size, strictly decodes with increasing
frame timestamps, and a **second independent file read produces identical bytes**.
It never removes repeated motion samples or changes a correctly sized file. Failed
or ambiguous recovery keeps the attempts and reports an error. Duplicate terminal
packets are consumed before the next request to prevent cross-file contamination.

The implementation refuses to take over active ACC/gyro recording at start, checks
the sensor identity on stop/sync, and refuses ambiguous new files or missing split
parts. It remembers streams already stopped and refuses to stop a later recording
that has restarted those streams. Keep one outstanding session per sensor: the PMD
status supplies active types, not a unique remotely queryable session ID.

## Storage, provenance and current scope

* `offline-session.json`: persistent lifecycle, baseline directory listing, device
  identity/settings, owned start requests, source paths, errors and SHA-256 hashes.
* `sensor-files/*.REC`: verified sensor file contents; any transport reconstruction
  is explicitly recorded in `downloads[].verification` and its attempt records.
* `transfer-attempts/`: original received bytes, RFC76 packets, hashes and recovery
  indices, including failed/mismatched reads. These are never silently overwritten.
* `acc.csv`, `gyro.csv`, `packets.jsonl`, `metadata.json`: decoded data compatible
  with `diagnose`, `plot` and `count`.
* `hr.csv`: empty standard header; this implementation records ACC and gyro only.
* `labels.csv`: empty initially; later manual labels are preserved on export retry.

Both IMUs retain integer device timestamps and share one time origin: the earliest
saved sample. Host arrival fields are empty/null because Bluetooth was disconnected
during acquisition. File-header dates are retained as reported, without claiming
that the sensor clock was calibrated to UTC. Download UTC is recorded separately.

Transfers validate RFC76 sequence numbers and reported file byte counts. Local
hashes detect later changes; they are **not a sensor-provided end-to-end checksum**.
Malformed files fail visibly and remain available for decoder repair. Split parts
are ordered numerically with timestamp continuity checked across files. A successful
file export proves the files were downloaded and parsed, not that an unexpected
power-off could not have shortened the intended workout. Quality warnings produce
`metadata.status: partial`.

No files are deleted from the sensor. There is a 2 MiB free-space gate at start and
a 32 MiB maximum per transferred file. Firmware updates, device-clock changes,
SDK mode, encryption and automatic power-up triggers are not used. Power-off,
full memory or battery exhaustion can stop recording; restart/download behavior
after those events still needs hardware validation. The current workflow therefore
keeps the sensor on until sync finishes. Do not use the live `record` command at
the same time as offline IMU recording.

## Firmware identification and evidence

CF204722 returned two different Device Information values during the live probe:
`2A26` Firmware Revision **0.1.5**, and `2A28` Software Revision **3.0.16**. The latter
matches Polar Flow's displayed application firmware. New reports retain both and
use `2A28` for `device.firmware`, falling back to `2A26` if absent. Historical raw
recording metadata is unchanged; its old `firmware: 0.1.5` field did not establish
that the application firmware was out of date.

Observed PMD offline settings on this sensor: ACC and gyro both offer 13/26/52 Hz,
16-bit, three channels, ranges 8 g and 2000 degrees/s respectively. Both types were
inactive during the initial capability probe, saved under ignored
`data/offline-initial-probe.json`.

The successful physical check is in `data/raw/offline-check-20260910-04/`:

| Result | ACC | Gyro |
|---|---|---|
| Samples / PMD frames | 2,660 / 20 | 2,660 / 12 |
| Device sample span | 50.273 s | 50.308 s |
| Configured / measured endpoint rate | 52 / 52.937 Hz | 52 / 52.937 Hz |
| Detected gaps / duplicates / backwards | 0 / 0 / 0 | 0 / 0 / 0 |
| Median vector magnitude | 1,006.09 mg | 2.862 degrees/s |
| Original `.REC` size | 5,551 bytes | 3,279 bytes |

Both offline-active status flags were confirmed before disconnect and after
reconnection, then both stops were acknowledged. The PC closed its recorder
connection at 14:16:08 UTC, began reconnecting at 14:16:52 UTC, and completed
GATT setup at 14:16:58 UTC. The two streams overlap for 50.273 seconds. All decoded
values and raw packets replay exactly from the retained files. `diagnose` reports
no warnings, `count` finds zero qualifying sets, and repeating `sync` verifies local
hashes without BLE. Evidence is in `offline-validation.json`, `metadata.json`,
`offline-session.json` and `connection.log` in that session directory. There was no
requested exercise, range test or deliberate power-off during recording. Battery
was 19% at start; charge before collecting workout references.

Initial checks `offline-check-20260910-01` through `03` failed **before any recording
start request**. Full Flow-style sync close notifications caused BLE teardown and
a subsequent Windows service-discovery failure. The fix uses direct PFTP GET
requests, matching the official SDK's offline-record list/download operations.
The final implementation does not send full-sync initialization/termination
notifications. A GATT-level regression test covers that path, including chunked
requests, sequence-counter wrap and both file downloads. No pairing changes were made.

Hardware-free tests cover framing/protobuf errors, dropped transfer frames,
timeouts, partial starts, lost start acknowledgements, reconnect/stop recovery,
download retry, preservation of existing recordings, restarted-stream ownership,
file selection, split-file continuity, original timestamps and counter input.
The decoder was additionally checked against upstream SDK `accOfflineFrame` and
`gyroOfflineFrame` vectors: 460 ACC samples in six frames and 72 gyro samples in
two frames, matching each vector's expected first two samples and final timestamp.
This is upstream fixture evidence, not a new physical recording. Results are in
ignored `data/offline-sdk-vector-check.json`.

Protocol references are pinned to official Polar BLE SDK commit
`3d15da61dd0c63e6be2582d03fd80f6c7ba02e11`:

* [Offline recording guide](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/documentation/SdkOfflineRecordingExplained.md)
* [Synchronization sequence](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/documentation/SyncImplementationGuideline.md)
* [RFC60/RFC76 transport](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/sources/Android/android-communications/library/src/main/java/com/polar/androidcommunications/api/ble/model/gatt/client/psftp/BlePsFtpUtils.kt)
* [Offline file decoder](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/sources/Android/android-communications/library/src/main/java/com/polar/androidcommunications/api/ble/model/offlinerecording/OfflineRecordingData.kt)
* [Upstream decoder test vectors](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/sources/Android/android-communications/library/src/test/java/com/polar/androidcommunications/api/ble/model/offlinerecording/OfflineRecordingDataTest.kt)

`pftp.py` implements only the needed framing, read queries and direct file GETs;
it does not depend on an Android/iOS runtime or copy the official SDK wholesale.

## First pull-up reference recovered, 2026-09-10

`data/raw/pullups-01` initially failed the size check twice. The sensor listed
10,498 bytes for ACC, while the two failed reads returned 18,250 and 18,706 bytes.
Captured transport packets showed repeated adjacent payloads with new sequence
numbers; the bytes were not encrypted. The underlying reason for those repeats
within the sensor/Windows transfer path has not been isolated.

With the recovery checks above, two independent reconstructed reads matched for
each stream. ACC has 10,498 bytes / 38 frames / 3,220 samples; gyro has 11,392 bytes /
41 frames / 3,220 samples. Their sample spans are 60.848 and 60.854 seconds, with
no detected gaps, duplicate timestamps or backwards timestamps. All exported values
and packet bytes match replay of the verified binaries. Original sensor files and
received transfer attempts are retained.

The user confirmed **two repetitions total**. This is stored separately from
predictions in `data/processed/pullups-01/reference.json`; exercise boundaries remain
unlabelled because the session also includes approach/return and rest. The current
generic detector at that stage required at least three consistent cycles and returned zero qualifying
sets. That output is not a zero-repetition ground-truth label. Recovery validation
and the motion plot are in the same processed directory. The exercise need not be
repeated to recover these data.
