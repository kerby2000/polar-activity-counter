# Investigation of the exercise-02 disconnect

The historical plots and detailed data artifacts referenced below remain local
under ignored `data/`. See [Examples](examples.md) for the published blind figures
and [the manual](user_manual.md) for current installation and analysis.

2026-09-10. **The earlier disconnect is confirmed, but its physical cause is unresolved. A new three-minute capture completed successfully.** No repeat exercise was requested.

## User observations and saved evidence

Sergey reports exercising approximately 3–4 metres from the PC. The sensor remained on. He pressed Space to end the set and was looking up the next instructions when the recording failed. This makes an intentional power-off unlikely; it does not establish why Windows lost the BLE link.

The original session started at 09:13:13.942886 UTC. All times below are relative to its host session clock; device sample times use the preserved approximate arrival-based mapping.

| Event | Session time |
|---|---:|
| READY | 13.234 s |
| Set start | 30.594 s |
| Space ends the set | 76.797 s |
| Count 12 submitted, in label journal | 84.437 s |
| Final HR notification | 86.453 s |
| Final gyro sample, mapped device time | 86.820 s |
| Final ACC sample, mapped device time | 86.876 s |
| Final ACC and gyro notifications arrive at Windows | 88.797 s |
| Recorder begins finalization after observing disconnect | 94.844 s |

All saved raw packets match the CSVs, with no parse failures, application buffer drops, detected gaps or reversed timestamps. Samples continue after both Space and submission of the repetition count. The final IMU notification arrives about 1.92–1.98 seconds after its mapped packet endpoint, followed by approximately 6.05 seconds without further notifications before finalization. The original recorder did not persist the exact native disconnect-event time or Windows GATT status; finalization time is not an exact measurement of the radio disconnection.

Original session packet delivery timing (local artifact: `data/processed/exercise-02-analysis/connection_timing.png`)

Maximum host arrival gaps before the last packet were 2.703 seconds for ACC, 3.500 seconds for gyro and 1.172 seconds for HR. These are BLE delivery/batching intervals, not missing-sample intervals. Sensor timestamps remain continuous. No matching System events were found in the checked 09:12:30–09:16:00 UTC interval. Bluetooth Bthmini and Policy operational logs were disabled, so absence of an event is not proof of normal radio operation.

## Code and backend investigation

The error path was the BLE disconnect callback, rather than the recorder's ten-second no-data watchdog or the 180-second duration limit. The keyboard implementation polls input without calling blocking `input()`; Space changes the label state and a count prompt does not end the BLE session. The warning `Stopping hr: Not connected` was emitted during cleanup after the failure. It is a consequence, not the initiating cause.

Bleak 3.0.2's Windows backend already creates a GATT session and sets `maintain_connection = True`. The backend reports a closed GATT session through its disconnect callback; native errors/status are logged separately. The current application callback does not receive a physical disconnect reason. There is no evidence here that periodic application keep-alive writes are the missing fix. See the [official Bleak Windows backend source](https://bleak.readthedocs.io/en/latest/_modules/bleak/backends/winrt/client.html).

The selected TP-Link adapter is currently reported healthy by Windows. That snapshot does not exclude a transient earlier radio/USB problem. Distance, body obstruction, interference, another app/device connection, sensor firmware and transient Windows/adapter behavior remain possible explanations, not proven diagnoses. We did not change drivers, power policies, pairing or sensor firmware during this investigation.

## Fresh live evidence

| Check | Three-minute capture | Instrumented follow-up |
|---|---:|---:|
| Local session | `link-check-20260910-01` | `link-check-20260910-instrumented` |
| Requested interval after READY | 180 s | 20 s |
| ACC samples | 9900 | 1440 |
| Gyro samples | 9900 | 1440 |
| HR readings | 187 | 28 |
| ACC / gyro endpoint rate | 52.942 / 52.942 Hz | 52.942 / 52.942 Hz |
| Detected gaps / duplicates / backwards | 0 / 0 / 0 | 0 / 0 / 0 |
| Application drops / parse errors | 0 / 0 | 0 / 0 |
| Status | complete | complete |

Both ran with ACC, gyro and HR enabled and successful IMU stop acknowledgements. Setup/shutdown data accounts for signal spans exceeding the requested interval. No physical exercise was requested and actual placement/posture during these checks was not observed, so these are connection checks rather than labelled exercise tests. The successful three-minute run demonstrates operation beyond the earlier failure time; it does not prove long-term reliability at the earlier exercise location.

The instrumented follow-up recorded `termination.reason: duration_reached`, `disconnected_before_cleanup: false`, and a disconnect event only during cleanup. Maximum measured recording-loop interval was 32 ms. It demonstrates that the new diagnostics can distinguish normal finish from link loss without requiring verbose console output.

## Changes made

Every new recording now saves a UTC-timestamped **`connection.log`** containing device events and detailed Windows backend messages. `metadata.json` additionally records:

- normal duration completion, keyboard finish, cancellation, notification timeout, buffer overflow or disconnection as separate termination reasons;
- whether disconnection was observed before cleanup;
- the last notification time and its age for each stream, maximum notification gaps, queued packets and largest recording-loop interval;
- timestamped connection, cleanup and disconnection events, with a flag distinguishing cleanup from unexpected disconnection.

Cleanup skips HR/IMU stop attempts if the link is already gone, avoiding the misleading secondary HR warning. Raw data and the original failure remain preserved. Native Windows messages may still lack a definitive radio failure reason; the log records what the backend actually provides.

Hardware-free regression tests now cover disconnect after readiness, preservation of received data, normal versus unexpected disconnection, logging and logger-state restoration. Numerical tests separately check known synthetic cadence/cycle counts and spectral energy accounting. **61 tests pass**, alongside Ruff checks. The hardware tests above supply separate evidence; synthetic tests do not establish radio reliability.

## Next action if the problem recurs

Use the normal recorder command; logging is now automatic. Keep the entire session directory, including `connection.log`. There is no need to repeat exercise solely to diagnose Bluetooth. If further reproduction is needed, first compare a seated capture close to the dongle with one at the exercise location, changing only distance/location. Consider HR-off comparison only if the detailed evidence warrants it; simultaneous HR succeeded in the tests above.

Do not automatically reconnect and merge samples into the same set: an outage may hide repetitions and the device clock may reset. The current behavior saves the partial session and reports the failure explicitly.

Reproduce the original packet-timing summary without connecting to the sensor:

```powershell
.\.venv\Scripts\python.exe scripts/analyze_connection.py data/raw/exercise-02 --output data/processed/exercise-02-analysis
```

Full private evidence is under `data/raw/exercise-02`, `data/raw/link-check-20260910-01`, `data/link-check-20260910-01.log`, and `data/raw/link-check-20260910-instrumented`. Derived timing values are in `data/processed/exercise-02-analysis/connection_timing.json`.
