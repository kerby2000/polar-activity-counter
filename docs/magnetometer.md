# Recording magnetic-field data

Add **`--mag`** to `record` or `offline start` to save magnetometer data alongside
ACC/GYRO and the default HR stream. The app requests **20 Hz, 16-bit, three axes,
±50 Gauss** in normal sensor mode, after checking advertised settings. It does not
enable SDK mode or request a calibration operation. Magnetometer recording is
optional so existing recording commands, memory use and datasets remain compatible.

## Offline: walk away and download later

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline start --device YOUR_ID --mag --subject me --sensor-position upper_arm_left --output data/raw/magnetic-01
```

Wait for all recording confirmations, including **Internal MAG recording confirmed
at 20 Hz**, and command exit. Exercise or walk normally, then return:

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline sync data/raw/magnetic-01
.\.venv\Scripts\python.exe -m polar_activity diagnose data/raw/magnetic-01
.\.venv\Scripts\python.exe -m polar_activity plot data/raw/magnetic-01
```

The same manifest tells sync to download `MAG.REC` or numbered split parts. No
extra flag is needed for BLE or USB sync. If using USB, stop via BLE before docking,
as described in [USB recording](usb_recording.md). Adding `--no-hr` is independent
of `--mag`. Preserve the manifest and source files if a transfer fails.

## Online: stay within Bluetooth range

```powershell
.\.venv\Scripts\python.exe -m polar_activity record --device YOUR_ID --mag --duration 180 --subject me --sensor-position upper_arm_left --output data/raw/magnetic-online-01
```

Wait for the ACC/GYRO and MAG readiness messages before exercising. Press `Q` to
finish. The existing quality checks also inspect MAG timestamps, rate and gaps.

## Files and plots

`mag.csv` contains `mag_x_ut`, `mag_y_ut`, `mag_z_ut` in **microtesla (µT)**, original
integer device/packet timestamps, sample indices and a timestamp-method field.
Online host-arrival fields are retained; offline arrival fields are empty because
download time is not acquisition time. MAG uses the existing ACC/GYRO clock origin
and its own sampling rate; no one-to-one sample matching is fabricated.

Calibration fields preserve `calibration_status_raw` and its SDK meaning:
unknown, poor, OK or good when supplied. Type 0 does not report calibration status;
the CSV explicitly says `not_reported`. Unexpected status codes are retained.
These are firmware reports, not independent proof of an undisturbed field or
accurate heading. The app does not silently calibrate, rotate, normalize or
discard magnetic measurements.

Polar's [MAG parser](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/sources/Android/android-communications/library/src/main/java/com/polar/androidcommunications/api/ble/model/gatt/client/pmd/model/MagData.kt)
defines two supported compressed formats: type 0 has three signed axes scaled to
Gauss; type 1 has three milliGauss axes plus an unscaled calibration-status channel.
The app applies the file/start-response factor and converts Gauss × 100 or
milliGauss × 0.1 to µT. Original packets and sensor files remain available.

`plot` produces `plots/mag_session.png` with X/Y/Z and magnitude. `analyze` adds
these traces as a magnetic-field panel in the combined motion/HR/activity figure:

```powershell
.\.venv\Scripts\python.exe -m polar_activity analyze data/raw/magnetic-01 --engine adaptive --model data/models/personal-adaptive.json
```

`analysis.json` records MAG counts, field-magnitude range and percentiles, calibration-status counts
and source hash separately from classifier inputs. **The current predictions use
ACC/GYRO only.** Old recordings have no MAG to recover; their frozen outputs remain
unchanged. The combined plot breaks magnetic traces at gaps exceeding 150 ms.

## How other systems use a magnetometer

**Orientation fusion:** a gyro tracks rapid rotation but accumulates heading error.
Acceleration provides a gravity reference when movement permits; magnetism adds
a heading reference. Complementary filters and Kalman filters combine them, often
representing orientation as a quaternion. Practical systems calibrate magnetic
offsets/distortions and reduce the influence of disturbed readings. See
[NXP's sensor-fusion architecture](https://www.nxp.com/docs/en/data-sheet/NSFK_DS.pdf)
and [magnetometer/inertial calibration research](https://arxiv.org/abs/1601.05257).

**Indoor positioning:** magnetic patterns caused by building materials can be
mapped and matched to later measurements. [IndoorAtlas](https://www.indooratlas.com/platform/)
combines magnetic maps with inertial and other sensor data. Such correlations
can identify a location after mapping; they are not a direct elevation reading.
Magnetic patterns near one staircase or pull-up bar might be useful context,
but may change at another location.

## What to investigate with our recordings

- Compare X/Y/Z rotation patterns and their timing with the gyro during repeated
  movements. In a uniform field, rotation changes the axes while magnitude stays
  approximately constant; calibration errors can also change magnitude.
- Inspect field magnitude near the PC, metal pull-up bar and stairs. Label location
  separately from exercise to avoid teaching the model that a room is an activity.
- Compare the same movement facing different directions or at another location,
  using ordinary recordings where possible. Keep whole recordings held out.
- Before adding quaternions to recognition, establish axis alignment, calibration,
  gap-aware synchronization and disturbance handling. Compare ACC/GYRO with and
  without MAG using the same held-out sessions and count/false-positive measures.

Automated checks cover Polar's reference packets, online and offline lifecycle,
USB export, timestamps and unchanged motion predictions when MAG is added.
Real-device capture was verified on 2026-09-11 in `magnetic-01`: 3,168 MAG samples,
about 20.10 Hz, continuous timestamps and all four downloaded files verified.
Firmware 3.0.16 used compressed type 0, so calibration status is **not reported**.
The raw field magnitude varied about 90–190 µT. The axis waveforms show repeated
exercise motion, but this recording cannot separate sensor offset, rotation and
location-dependent field changes well enough to establish calibrated heading.

The original, saved motion-only prediction was 10 push-ups and two pull-ups.
After the user disclosed three pull-ups, bounded continuation analysis found the
last effort beyond the classifier boundary: about 4.28 seconds of rest, then an
excursion about 88% of the first two, ending at the dismount cutoff without a clear
return. That first revision was **10 push-ups and three pull-up attempts: two observed
returns plus one incomplete return**. MAG also shows this effort, but was not needed
to recover it from ACC/GYRO. This correction is a development replay after feedback,
not another successful blind test. The personal classifier/reference model is unchanged.

The participant later questioned a missing first push-up and no longer remembered
the total. It was already a qualifying ACC/GYRO cycle at 17.28–18.84 seconds in a
shorter candidate sequence, discarded by the old overlap selection. Matching
compatible sequences now preserves that first cycle and counts the shared cycles
only once. The latest result is **11 estimated push-up cycles and three pull-up
attempts**. Eleven is not an independently confirmed repetition total.

Within the three motion-derived pull-up intervals, the largest change of the MAG
vector from its first sample was approximately **36.0, 40.0 and 39.9 µT**. This is
useful evidence that the third effort has a comparable magnetic change; it is not
an independent repetition detector or a calibrated measure of exercise quality.

All 11 earlier recording replays retain their prior sets and counts, including the
three blind sessions and zero counted sets in the household recording. This is a
regression check, not proof of all-day false-positive performance. The next useful
MAG comparison needs whole recordings held out, different headings/locations and
a calibrated, disturbance-aware orientation estimate before enabling it as a
classifier feature. Existing recordings remain usable without MAG.

The first sync failed during the initial PMD status read, before a file download.
The new sync recovery reconnects once for that read only. In the successful export,
GYRO ends 4.326 seconds before ACC; the exercise sets are inside their common
coverage and no internal timestamp gaps were found. The files contain no missing
tail samples to reconstruct, and the logs do not establish why the sensor's gyro
tail is shorter. Battery/storage overhead still needs longer-session measurement.
