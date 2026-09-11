# User manual

This guide covers installation, first capture, personal training and review. The
hardware-tested setup is Windows with a Polar Verity Sense. Python 3.11 and 3.12
run in CI on Windows and Linux; other hardware/platform combinations need their
own connection checks. Analysis runs after recording; there is no live rep display.

## Install

You need Python 3.11 or 3.12, Git, a Verity Sense and a working Bluetooth Low Energy
adapter for capture. Use the same upper-arm location and strap orientation across
reference recordings and later sessions. Other Polar products are not supported
by this application's tested acquisition path.

Install [Python for Windows](https://www.python.org/downloads/windows/) and
[Git for Windows](https://git-scm.com/downloads/win), then open PowerShell:

```powershell
git clone https://github.com/kerby2000/polar-activity-counter.git
cd polar-activity-counter
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m polar_activity --help
```

If you installed 3.12, use `py -3.12` instead. All following commands run from the
repository directory. Explicit Python paths work without activating the venv.

```powershell
# Optional: USB adapter downloads
.\.venv\Scripts\python.exe -m pip install -e ".[usb]"
# Optional: full developer tests and research comparisons
.\.venv\Scripts\python.exe -m pip install -e ".[dev,experiment]"
```

On Linux/macOS, create the environment with `python3.11 -m venv .venv` and replace
`.\.venv\Scripts\python.exe` with `.venv/bin/python`. Data analysis can be used
there; the physical workflows described here were tested on Windows.

### If Python shows the wrong version

Multiple Python installations can coexist. The first `python` on PATH may differ
from the version selected by pyenv; an existing virtual environment keeps the
interpreter it was created with. Inspect the commands and use an explicit version:

```powershell
Get-Command python -All
py -0p
.\.venv\Scripts\python.exe --version
```

If the Windows Python launcher is unavailable, run the full path to your installed
Python executable with `-m venv .venv`. To change an existing environment, create
a separate new venv with the intended interpreter and reinstall the package there.
Changing `pyenv shell` alone does not replace an existing venv's interpreter.

### Hardware-free installation check

```powershell
.\.venv\Scripts\python.exe scripts/make_synthetic_session.py data/raw/synthetic-demo
.\.venv\Scripts\python.exe -m polar_activity diagnose data/raw/synthetic-demo
.\.venv\Scripts\python.exe -m polar_activity plot data/raw/synthetic-demo
.\.venv\Scripts\python.exe -m polar_activity count data/raw/synthetic-demo
```

This makes a 12-second artificial QA fixture, quality report, plots and generic
count output. Its labels are artificial too; it is not a recognition benchmark or
a personal training dataset. Use a new output folder if you repeat it.

## Prepare the sensor

1. Set up/update the sensor using Polar Flow. Firmware **3.0.16** was used for the
   project's physical tests; this is a tested version, not a claim about the newest
   available firmware.
2. Charge it, remove it from the charging adapter, and switch on sensor/heart-rate
   mode (blue side indicator). Keep it close to the PC for setup.
3. Close Polar app connections on the phone and any competing PC recorder.
4. Confirm the Bluetooth adapter works. A new TP-Link UB600 in this project needed
   the manufacturer's driver update before it could discover either a keyboard
   or the sensor.
5. Scan and inspect the capabilities:

```powershell
.\.venv\Scripts\python.exe -m polar_activity scan --scan-timeout 30
.\.venv\Scripts\python.exe -m polar_activity verify --device YOUR_ID --output data/verity-capabilities.json
```

Replace `YOUR_ID` with the exact sensor ID, advertised name or BLE identifier from
the scan. `verify` should report ACC and GYRO support and settings. It checks
capabilities; an actual recording checks simultaneous streaming.

The app does not explicitly call pair/unpair. Windows may request authentication
when accessing a service. An idle device shown as **Not connected** in Windows
Settings does not by itself mean discovery failed. See troubleshooting below.

## Online recording: stay connected to the PC

“Online” means the sensor streams to the PC over Bluetooth throughout the session.
Internet access is not needed for recording or analysis after setup.

```powershell
.\.venv\Scripts\python.exe -m polar_activity record --device YOUR_ID --duration 180 --subject me --sensor-position upper_arm_left --output data/raw/online-01
```

Wait for **READY: receiving ACC and GYRO** before starting. Two valid packets from
each stream must arrive first, and those samples are already saved. The requested
duration begins after readiness. Stay within a reliable Bluetooth range. Use `Q`
to finish early. A new recording always needs a new output folder.

Online recording tries optional heart-rate notifications as well. Add `--no-hr`
to record only the IMUs, or `--no-interactive` when keyboard labels are unnecessary.

### Optional reference labels

| Key | Action |
|---|---|
| `1`, `2`, `3`, `4` | Select pull-up, push-up, squat, jump |
| `5`, `6`, `7` | Select walking, stairs, household |
| `8`, `9`, `0` | Select standing, sitting, other |
| `A` | Enter a custom activity |
| `Space` | Start/end a set label |
| `Enter` after ending a set | Submit the rep count, or leave it unknown |
| `N` | Add a note to the current/latest set |
| `Esc` | Cancel a text prompt |
| `Q` | Finish the session; leave any text prompt first |

Select the activity, press Space before the set, press Space afterward, and enter
your approximate count. Acquisition continues while you type. Labels help select
training intervals and check results. Unlabelled mixed sessions are valid analysis
inputs; pressing Space is not required to detect their boundaries.

## Offline recording: leave Bluetooth range

The app requests ACC and GYRO recording into sensor memory. A normal button-started
Polar training recording does not automatically provide these raw motion streams.
See Polar's [SDK offline-recording interface](https://github.com/polarofficial/polar-ble-sdk/blob/3d15da61dd0c63e6be2582d03fd80f6c7ba02e11/documentation/SdkOfflineRecordingExplained.md).

Start near the PC:

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline start --device YOUR_ID --subject me --sensor-position upper_arm_left --output data/raw/offline-01
```

Wait for **Internal ACC + GYRO recording confirmed at 52 Hz**, then for the command
to exit. Walk away and exercise. Keep the sensor on. There is no duration limit
configured by this command; explicitly stop or sync when finished.

When back in range, use the **same session folder**:

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline sync data/raw/offline-01
.\.venv\Scripts\python.exe -m polar_activity diagnose data/raw/offline-01
```

Sync reconnects, stops this session's streams, downloads the files, checks their
sizes/format and exports ACC/GYRO CSVs. Keep `offline-session.json`: it identifies
the sensor and recording. Do not start another session with another app before
resolving this one. Retry a failed sync using the same command and folder.

```powershell
# Stop now but postpone the download:
.\.venv\Scripts\python.exe -m polar_activity offline stop data/raw/offline-01
# Inspect active measurement flags and free memory:
.\.venv\Scripts\python.exe -m polar_activity offline status --device YOUR_ID
# List stored files when IMU recording is inactive:
.\.venv\Scripts\python.exe -m polar_activity offline list --device YOUR_ID
```

Files are retained on the sensor after download; there is no automatic cleanup.
Offline start requires free space, and the downloader currently limits individual
files to 32 MiB. Full-day battery life, memory use and transfer duration for this
ACC/GYRO configuration have not been established. Start with a brief ordinary
movement check on a new setup. See [recovery details](offline_recording.md).

## Optional USB download

Enable the sensor's **USB** setting in Polar Flow, then use the supplied charging
adapter. USB access is disabled by default; see
[Polar's instructions](https://support.polar.com/en/using-polar-verity-sense-with-usb-and-flowsync).
Close FlowSync before running this application's USB commands.

USB currently downloads an app-created session; starting/stopping still uses BLE.
Stop **before docking**, then create a separate USB copy:

```powershell
.\.venv\Scripts\python.exe -m polar_activity offline stop data/raw/offline-01
# Now dock the sensor in its USB adapter.
.\.venv\Scripts\python.exe -m polar_activity usb scan
.\.venv\Scripts\python.exe -m polar_activity usb sync data/raw/offline-01 --output data/raw/offline-01-usb
```

Install `.[usb]` first. Windows uses its native HID driver; no replacement USB
driver is needed. Device Manager may list **USB Input Device** or a
**HID-compliant vendor-defined device**, rather than “Polar”. A verified BLE
download can also be copied by USB for a byte-for-byte comparison.

The measured small-file transfer was 21,890 bytes in 1.494 seconds (14.3 KiB/s), with
both files equal to their BLE copies. This is not a full-day throughput benchmark.
See [USB details and retries](usb_recording.md).

## Train your personal model

No general-purpose model or raw human dataset is bundled. The screenshots use the
original participant's local model. Start with the classes you want to recognize;
the classifier cannot learn an unrepresented class from its name alone.

1. Record known activities with a consistent subject and sensor-position string.
   Two separate recordings per exercise are a useful starting point when practical;
   include walking and household movement as background examples. Small comfortable
   sets are useful—there is no requirement to perform a large workout.
2. Use `plot` and, for online sessions, `labels.csv` to locate the actual active
   part. Exclude approach, setup and long rests. Training intervals must be **at
   least four seconds**, inside intact overlapping ACC/GYRO data. Times are on the
   original recording clock, not time since the label started.
3. Copy the manifest template into your local model directory:

```powershell
New-Item -ItemType Directory -Force data/models
Copy-Item examples/references.example.json data/models/references.json
.\.venv\Scripts\python.exe -m polar_activity plot data/raw/pushups-reference-01
```

4. Edit `data/models/references.json`. Replace every example folder and time with
   your own observed interval. Add entries for other recordings/classes. Paths are
   relative to the **manifest's directory**, so `../raw/...` works after the copy
   above. Subject and sensor position must match each recording's metadata.

Supported reference names are `pull-up`, `push-up`, `squat`, `jump`, `walking`,
`stairs`, `household`, and `standing_or_sitting`. Pool standing and sitting
as `standing_or_sitting`; they are not reliably distinguished here.

`jump_count_convention` starts as `unconfirmed`. Leave it that way until reviewing
known jumps: the report can show impacts/pairs without declaring a rep total.
Use `paired_impacts` when each observed jump has two detected impacts, or
`single_impact` for a confirmed one-impact-per-jump pattern. Calibrate this from
reference motion; do not choose it to force a blind test's expected count.

5. Train and analyse a **different** session:

```powershell
.\.venv\Scripts\python.exe -m polar_activity train data/models/references.json --engine adaptive --output data/models/personal-adaptive.json
.\.venv\Scripts\python.exe -m polar_activity analyze data/raw/offline-01 --engine adaptive --model data/models/personal-adaptive.json
```

Rep totals do not enter fitting. For a blind test, save the model and predictions
before revealing the activities/counts; keep that recording out of the references.
Training replay checks regressions but is not independent accuracy evidence.

The CLI's default engine remains `legacy` for old-model compatibility. Use
`--engine adaptive` with an adaptive model. The generic `count` command needs no
model and detects repeated motion without naming the exercise. It uses an older
generic counter, so counts can differ from `analyze`.

## Read the output

| File | Meaning |
|---|---|
| `metadata.json` and quality report | Capture configuration, timing, gaps, status and errors |
| `acc.csv`, `gyro.csv` | Original timestamped motion, in mg and degrees/s |
| `labels.csv` | Optional human labels; not predictions |
| `analysis-adaptive/analysis.png` | Signal traces, accepted sets and activity evidence |
| `analysis-adaptive/sets.csv` | Activity, interval and estimated count per accepted set |
| `analysis-adaptive/repetitions.csv` | Individual accepted movement cycles |
| `analysis-adaptive/activities.csv` | Timeline estimates, including outside counted sets |
| `analysis-adaptive/unassigned_attempts.csv` | Incomplete attempts outside accepted sets |
| `analysis-adaptive/analysis.json` | Full evidence, warnings and source/model hashes |
| `analysis-adaptive/motion_quality.png` | Relative excursion/consistency when available |

The activity colour band is weaker evidence than a counted set. A moment labelled
“squat” there may be rejected by the cycle detector. Incomplete attempts and motion
quality flags describe the arm sensor's observations; they do not establish whether
a repetition met an anatomical exercise standard.

Use `--output data/processed/review-01` for another derived version, `--no-plot` for
JSON/CSV only, or `--json` for machine-readable console output. Original motion is
retained. See [Data format](dataset_format.md).

## Troubleshooting

| Symptom | What to check |
|---|---|
| `No Verity Sense found` | Sensor on, out of charger, close to PC, competing app disconnected; try a 30-second scan. Check the dongle can discover another Bluetooth device and update its manufacturer driver if needed. |
| Pairing popup, cancelled operation, notification timeout | Close competing apps, power-cycle the sensor and retry. Collect `--verbose` output and `connection.log`; the app reports the failed setup stage. |
| No samples or missing scale factor | Wait for `READY` before exercise. Inspect capabilities and quality reports; reliable IMU units are required. |
| Disconnect after some samples | The partial session is saved. Diagnose/analyse intact data. Online capture cannot recover samples that never reached the PC; use memory mode when leaving range. |
| Offline size mismatch/interrupted transfer | Keep the folder and sensor on; repeat `offline sync`. Do not trim bytes or delete the manifest. |
| USB sound but no “Polar” device | Enable USB in Flow, align contacts, close FlowSync, install `.[usb]`, and run `usb scan`; Windows may show a generic HID name. |
| Missing model or subject/placement mismatch | Train your own model with matching identity/placement, and select `--engine adaptive`. |
| No sets, or wrong class | Inspect capture quality and reference coverage. Short/unfamiliar motion can fail the class or complete-cycle checks. Add a separate known reference rather than tune against a blind answer. |

See [Bleak troubleshooting](https://bleak.readthedocs.io/en/latest/troubleshooting.html)
for upstream BLE issues. Report the command, Python/package versions and error text.
Review diagnostics before sharing: they can contain device identifiers, personal
paths, notes and optional heart-rate data. `data/` is ignored by Git.

## Update and run checks

With local source changes committed or otherwise preserved:

```powershell
git switch main
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -e .
```

Reinstall your optional extras if their dependencies changed. Full checks:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,experiment]"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

Research configurations refer to local, ignored recordings/evaluation manifests.
They cannot reproduce the original human results from a fresh clone alone. The
published figures and provenance are in [Examples](examples.md).
