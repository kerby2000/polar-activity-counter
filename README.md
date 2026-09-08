# Polar activity counter: VS-0

**VS-0 READY FOR HARDWARE EXERCISE TEST**

Python CLI for collecting labelled upper-arm Polar Verity Sense accelerometer and gyroscope datasets on Windows. It discovers the sensor, queries capabilities, records simultaneous IMU streams and optional HR, prompts for ground-truth repetition counts, and produces quality reports and plots.

This is a dataset collector, not an exercise classifier. Real-sensor acceptance is pending: the development Windows PC reports **no Bluetooth adapter**. Automated tests and synthetic recordings do not establish physical streaming or exercise recognition. See [validation evidence](docs/validation.md).

## Windows setup

Requirements: Windows 10/11 with a working Bluetooth LE adapter, Git, Python 3.11 recommended, and a charged Verity Sense. Install Python from [python.org](https://www.python.org/downloads/windows/) if needed.

In PowerShell:

```powershell
git clone --branch feature/vs0-verity-sense-acquisition https://github.com/kerby2000/polar-activity-counter.git
cd polar-activity-counter
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m polar_activity --help
```

If `py` is unavailable but `python --version` reports Python 3.11+, use `python -m venv .venv`. The commands use the venv executable directly; activation and PowerShell execution-policy changes are unnecessary. Runtime installation without test/lint dependencies: `pip install -e .` using the venv Python (`python -m pip`). No Android device, account, server or cloud service is required to use the collector. macOS/Linux use `.venv/bin/python` instead; Windows is the hardware acceptance platform.

## Discover and verify

Turn on the sensor in **sensor/heart mode**, indicated by the blue side LED. Unplug it from the charger. Keep it near the PC and close other apps/watch connections that might occupy the BLE link. Enable Bluetooth in Windows Settings. Pairing through Windows Settings is not normally necessary; the CLI discovers and connects directly.

```powershell
.\.venv\Scripts\python.exe -m polar_activity scan
.\.venv\Scripts\python.exe -m polar_activity verify --output data/verity-capabilities.json
# If more than one sensor is found, substitute the ID reported by scan:
.\.venv\Scripts\python.exe -m polar_activity verify --device ABCD1234 --json
```

The report contains actual advertised name/identifier, optional battery/firmware/model, PMD stream flags, HR characteristic availability and device-returned settings. `null`/warnings mean unavailable, not a guessed value. Verification queries capabilities; it does not prove simultaneous streaming.

## Record and label

```powershell
.\.venv\Scripts\python.exe -m polar_activity record `
  --duration 300 `
  --output data/raw/first-session `
  --subject sergey `
  --sensor-position upper_arm_left `
  --arm left `
  --notes "Normal armband; record button orientation here"
```

`--duration` is seconds after setup (default 300). `--output` is a **new session directory**; omit it for a unique timestamped folder. Subject and position are required. Arm is inferred from `_left`/`_right` suffix if `--arm` is omitted, otherwise stored as unknown. Conflicting arm/position arguments are rejected. For the right arm, set both accordingly. HR is attempted by default; use `--no-hr` if it interferes. `--no-interactive` is useful for timed diagnostics. Interactive recording requires a real terminal with focus on its window.

| Key | Action |
|---|---|
| 1 / 2 / 3 / 4 | Pull-up / push-up / squat / jump |
| 5 / 6 / 7 | Walking / stairs / household movement |
| 8 / 9 / 0 | Standing / sitting / other |
| A | Type a custom activity, then Enter (e.g. reaching-overhead, carrying, dressing) |
| Space | Start or end a set for the selected activity |
| N | Type a note, then Enter; attaches to current/latest set |
| Q | Finish recording and save |
| Ctrl+C | Interrupt, stop streams and save a partial session |

For a pull-up set: press **1, Space**, perform the set, then **Space**. Type the manually observed count and press **Enter**. Acquisition continues during prompts. Enter alone leaves the count unknown/not applicable for background intervals. Zero is a real entered zero. Esc cancels a text/count prompt. Q is ordinary text inside a prompt; use Esc then Q to exit. An unfinished set is marked interrupted. Activity changes during a set are rejected.

Record negative activities as explicit sets too. Unmarked time is not automatically labelled negative. Set/HR times use host monotonic time; IMU timing uses device timestamps. Their plotting alignment has an explicitly unknown BLE-delay offset; keep a few seconds of stillness around each set. Raw measurements retain original packet timestamps and bytes.

## Quality reports and plots

Every recording prints sample counts, configured/sample-axis/packet-endpoint rates, gaps, estimated missing samples, duplicate/backward timestamps, ACC/gyro overlap and duration mismatch, HR summary and label counts. Metadata stores the report. Unexpected disconnection, missing IMU data, malformed packets and buffer overflow preserve partial files and produce errors.

```powershell
.\.venv\Scripts\python.exe -m polar_activity diagnose data/raw/first-session
.\.venv\Scripts\python.exe -m polar_activity diagnose data/raw/first-session --json
.\.venv\Scripts\python.exe -m polar_activity plot data/raw/first-session
.\.venv\Scripts\python.exe -m polar_activity plot data/raw/first-session --set 002
.\.venv\Scripts\python.exe -m polar_activity plot data/raw/first-session --start 30 --end 90
.\.venv\Scripts\python.exe -m polar_activity plot data/raw/first-session --activity pull-up
```

PNGs are written to the session's `plots/` folder. ACC and gyro each have separate X/Y/Z/magnitude panels, with shaded labelled sets and rep counts. HR has a separate chart. Activity filtering creates plots per matching set. Existing PNGs for the same selection are regenerated; raw files are untouched.

Compatibility wrappers: `python scripts/verify_device.py`, `python scripts/record_session.py`, `python scripts/plot_session.py <session>` using the installed venv.

## First physical experiment for Sergey

1. Wear the normal Verity Sense armband on the **upper arm**. Record left/right, position and button orientation in session notes. Keep placement fixed for the first run.
2. Run `verify` above. Save the capability JSON. Confirm ACC and gyro advertise the required settings. The program never enables SDK mode.
3. Do a ten-second stillness check first:

   ```powershell
   .\.venv\Scripts\python.exe -m polar_activity record --duration 10 --subject sergey --sensor-position upper_arm_left --arm left --output data/raw/sanity-01 --no-interactive
   .\.venv\Scripts\python.exe -m polar_activity plot data/raw/sanity-01
   ```

   Check that both streams contain samples, packet-endpoint rates are near 52 Hz, there are no unexplained gaps/backward times, stationary acceleration magnitude is approximately 1000 mg, and stationary gyro is near zero. Axis signs depend on orientation. Investigate warnings before a longer run; if HR fails, retry a **new** output folder with `--no-hr`.
4. Start the labelled run:

   ```powershell
   .\.venv\Scripts\python.exe -m polar_activity record --duration 900 --subject sergey --sensor-position upper_arm_left --arm left --output data/raw/exercise-01 --notes "Upper arm, fixed orientation; describe button direction"
   ```

5. Follow this sequence. For every row select the activity, press Space, perform the interval, press Space, and submit the count (Enter alone for background). Start/end markers should include ~2s stillness around each exercise.

   | Activity | Interval / repetitions | Key |
   |---|---|---|
   | Standing | 30 seconds | 8 |
   | Pull-ups | 5, enter actual count | 1 |
   | Standing rest | 30 seconds | 8 |
   | Push-ups | 5, enter actual count | 2 |
   | Standing rest | 30 seconds | 8 |
   | Squats | 5, enter actual count | 3 |
   | Standing rest | 30 seconds | 8 |
   | Vertical jumps | 5, enter actual count | 4 |
   | Standing rest | 30 seconds | 8 |
   | Walking | 60 seconds | 5 |
   | Bending, reaching overhead, picking up/carrying objects, doors, random arm motion, dressing | Several separately labelled 10-15s intervals | A or 7 |

   One round is sufficient for the first gate; preferably repeat the four exercises and rests for **three sets each** if convenient. Use N for interruptions, unusual technique or placement changes. Q finishes early; 900s is a time cap.
6. Run `diagnose` and `plot` on `data/raw/exercise-01`. Provide that **entire session folder** and the capability JSON for the next analysis step. Keep it out of Git; no large recordings are committed here.

The next step, after actual data exists, is per-set range/mean/std, magnitudes, dominant axes, periodicity/spectra, autocorrelation and candidate-peak analysis. No `initial_signal_analysis.md` with invented findings or classifier has been generated. Orientation sensitivity and daily-movement false positives remain experimental questions.

## Development and hardware-free smoke test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/make_synthetic_session.py data/raw/synthetic-demo
.\.venv\Scripts\python.exe -m polar_activity plot data/raw/synthetic-demo
```

The synthetic script generates **artificial QA signals**, marks metadata `synthetic: true`, and assigns illustrative labels. It does not simulate or validate the physiology of any exercise. Tests exercise the production Bleak adapter through an injected fake GATT client, along with binary decoding, timestamp reconstruction, partial failures, labels, diagnostics and all plot selections. CI covers Windows/Linux and Python 3.11/3.12 without hardware.

## Troubleshooting and design notes

* **No Bluetooth adapter:** enable/install a working BLE adapter and driver, or use another Windows PC. An attached Polar sensor alone does not provide a PC BLE radio.
* **Not found/timeout:** check sensor power/charge, unplug the charger, use sensor mode, reduce distance, close other connections and retry `verify`. Use exact `--device` from `scan` if needed.
* **PMD code 6:** another online/offline recording may already own that stream. Stop it in its owning app; power-cycle if appropriate. VS-0 never stops recordings it did not start.
* **Missing settings/factor or unsupported format:** retain the capability report and partial session for investigation; the collector refuses to guess sensor scaling.
* **Callbacks hang on Windows:** use a clean venv in plain PowerShell; packages importing pywin32 can initialize the wrong COM threading model. See [Bleak troubleshooting](https://bleak.readthedocs.io/en/latest/troubleshooting.html).
* **Large gaps:** distinguish device timestamp gaps from irregular host BLE delivery. Missing-sample values are estimates; there is no exposed stream sequence counter for exact radio packet losses.
* **Clock alignment:** original Polar timestamps are not assumed to be correct UTC. Host-label alignment uses an arrival anchor with unknown delay; no fake exact synchronization or raw resampling is performed.
* **Process kill/power loss:** files flush periodically, but forced termination can leave partial writes and `status: recording`. Ctrl+C/Q provide orderly finalization.

Further documentation: [source research and offline iOS route](docs/research.md), [protocol/clock semantics](docs/protocol.md), [dataset schema](docs/dataset_format.md), [validation evidence](docs/validation.md). Offline capture is researched only; the likely later implementation is a small native iOS app using Polar's official SDK.
