# Heart rate, stair direction and other sensors

## What is recorded

| Signal | Current app | What it adds |
|---|---|---|
| Accelerometer (ACC) | Online/offline, 52 Hz | Movement, gravity-relative posture, impacts |
| Gyroscope (GYRO) | Online/offline, 52 Hz | Arm rotation and repeated motion cycles |
| Heart rate (HR) | Online/offline by default; `--no-hr` opts out | Exertion/recovery context, displayed beside motion |
| Raw optical signal (PPG) | Available on Verity Sense, not captured | Optical waveform and possible signal-quality research; substantially more data than BPM |
| Pulse-to-pulse intervals (PPI) | Available, not captured | Beat-interval research, mainly at rest |
| Magnetometer | Online/offline with `--mag`, 20 Hz | Magnetic heading/orientation and location-context research |
| Battery/device information | Connection-time snapshots | Acquisition diagnostics, not a continuous sensor stream |

The [Polar Verity Sense SDK guide](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/documentation/products/PolarVeritySense.md)
lists HR, ACC, GYRO, PPG, PPI and magnetometer access. The tested sensor's feature
bytes (`0f6e620000000000000000000000000000`, firmware 3.0.16) advertise offline HR.
They do not advertise pressure, location, temperature or ECG. Do not infer that
every measurement type in the broader Polar SDK exists on this sensor.

PPI is deliberately left off: Polar documents a separate algorithm that updates
HR only every five seconds when PPI is enabled, delays the first batch, and is
incompatible with internal training. It is not needed to save ordinary BPM.
Raw PPG is also unnecessary for the device's HR estimate. HR/PPI are unavailable
in SDK mode; this app uses normal sensor mode.

## Recording and viewing HR

Online `record` already captures the standard Bluetooth HR notifications.
New `offline start` sessions now request ACC + GYRO + the separate offline HR
measurement (PMD type 14). Wait for **Internal HR recording confirmed** as well as
the IMU confirmation. `offline sync` downloads `HR.REC` or its numbered split
files and exports `hr.csv`. USB sync carries the same requested streams.
Use `--no-hr` on either start command for an IMU-only session. An unsupported
offline HR request fails before starting the IMUs and explains this option.

Run the usual `analyze ... --engine adaptive --model ...` command to get a combined
ACC/GYRO/HR/activity plot. `plot SESSION` also creates a separate HR plot.
Analysis saves HR summary statistics and the HR file hash separately from the
motion inputs. **HR does not yet influence the classifier, set boundaries or
repetition counts.** Frozen blind-test predictions and models stay unchanged.

Older offline sessions, including blind-01/02/03, contain no HR file from the
sensor. Their empty `hr.csv` cannot be filled from acceleration or gyro data.
New plots label these sessions **HR not recorded**. Original frozen plots remain
as published. Zero/invalid readings and reported loss of contact are excluded
from summary statistics, and plots break at invalid readings or gaps over three
seconds. Raw readings remain available for inspection; contact flags on this
optical sensor are not a reliable wear detector.

### Timing limitations

Offline HR has a recording-start date but no individual sample timestamps; frame
timestamps may be zero. The SDK's
[offline HR parser](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/sources/Android/android-communications/library/src/main/java/com/polar/androidcommunications/api/ble/model/gatt/client/pmd/model/OfflineHrData.kt)
returns ordered BPM values, and Polar confirms the nominal one-per-second rate in
[its timestamp discussion](https://github.com/polarofficial/polar-ble-sdk/issues/465).

The app estimates HR time as the file-header start plus `(sample index + 1)` seconds,
expressed relative to the existing ACC/GYRO clock origin. Repeated split headers
continue the index. A different split header must start after preceding samples.
This is labelled **approximate 1 Hz timing**: the header has whole-second
resolution; first-sample phase, drift and missing internal HR samples are not
independently measured. Frame timestamps are preserved without claiming they
timestamp individual beats. Adding HR never shifts the original IMU timeline.
Online HR uses host notification arrival time and also has transport/algorithm
latency. Neither mode supports exact step-to-heartbeat alignment.

### Verification

On 11 September 2026, firmware 3.0.16 successfully started all three streams,
continued after BLE disconnection, then stopped and downloaded 4,580 samples per
IMU and 87 HR samples during a short stationary check. The 138-byte `HR.REC` used
raw frame type 0, empty settings and a zero frame timestamp. No acquisition quality
warnings were reported. The private recording is excluded from training and from
the public repository. This checks capture/decoding, not HR accuracy or stair
direction. Automated tests cover raw types 0/1, rejected/lost HR start responses,
split files, old sessions, USB export/retry and unchanged motion predictions.

## Can we measure elevation?

A **barometer** measures atmospheric pressure; local pressure change can support
relative height/floor estimates, with allowance for weather and indoor airflow.
Verity Sense does not expose a pressure sensor. Its **magnetometer** measures the
magnetic field, primarily helping heading. Indoor metal and wiring can disturb it;
it is not a dependable altimeter.

It can nevertheless contribute to orientation fusion and mapped indoor location.
The app now supports [logging and inspecting magnetic-field data](magnetometer.md)
to evaluate those possibilities without altering the current classifier.

A **quaternion** represents orientation, not height. ACC/GYRO/magnetometer fusion
can estimate orientation and help subtract gravity. To estimate height from the
remaining vertical acceleration requires integrating twice, plus initial velocity
and boundary constraints. Small bias and orientation errors accumulate quickly,
and upper-arm movement is superimposed on body movement. The current analyser
uses gravity-relative features without claiming an absolute height trajectory.
See [NXP's sensor-fusion overview](https://www.nxp.com/docs/en/data-sheet/NSFK_DS.pdf)
and [inertial position-estimation background](https://arxiv.org/abs/1311.4572).

## Could HR distinguish upstairs from downstairs?

Possibly as supporting evidence over a longer interval. Ascent usually requires
more effort, but HR rises and recovers with delay. Previous push-ups/pull-ups can
leave HR high during descent, while a short ascent may finish before HR rises
clearly. Fitness, pace, carried loads and optical motion artifacts also matter.
[Stair-ascent/descent research](https://pubmed.ncbi.nlm.nih.gov/11932581/) supports
different average cardiovascular demands; it does not establish a reliable
per-person direction rule for short mixed sessions.

Next evaluate motion-only versus motion-plus-HR using separately identified
ascent/descent intervals and whole held-out sessions, including reversed order and
rest after other exercises. Keep timing uncertainty explicit. A phone/barometer
or other elevation reference would help establish ground truth. Until that
evaluation, output remains **stairs**, without an asserted direction or floor count.
