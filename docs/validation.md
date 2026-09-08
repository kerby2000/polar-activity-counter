# VS-0 validation evidence

Date: 2026-09-09 (Europe/Brussels). Status: **VS-0 READY FOR HARDWARE EXERCISE TEST**.

## Environment

* Microsoft Windows 11 Pro, build 10.0.26200, x86-64.
* Python 3.11.15, project-local `.venv`.
* polar-python 1.1.1, Bleak 3.0.2, NumPy 2.4.6, matplotlib 3.11.1.
* Development: pytest 9.1.1, Ruff 0.16.6.

## Actual hardware attempt

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
