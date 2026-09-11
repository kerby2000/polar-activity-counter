# New-recording validation: household activity and push-ups

The historical plots and detailed data artifacts referenced below remain local
under ignored `data/`. See [Examples](examples.md) for the published blind figures
and [the manual](user_manual.md) for current installation and analysis.

**Follow-up:** Sergey subsequently said 14 seems to be the right amount and asked that short breaks be accounted for. This is a provisional retrospective reference, not an independent blinded count. Version 2 now groups the original 6 + 8 blocks as one 14-cycle set, retaining the 1.240 s rest separately and preserving every original cycle boundary. See [the pause update](automatic_counting.md#short-rests-within-a-set). The analysis below preserves the original v1 baseline.

Analyzed 2026-09-10 with the existing **periodic-return-v1** detector. Its code, thresholds and automatic outputs were not adjusted to fit these recordings. The counter scanned the entire saved ACC/gyro overlap without using labels or expected counts.

| Recording | User description | Automatic result | Assessment |
|---|---|---|---|
| exercise-03 | Household | 0 sets, 0 cycles | No false exercise-like counts in this recording |
| exercise-04 | Push-ups | 2 groups: 6 + 8 = **14 cycles** | Clear repeated motion; groups separated by a short low-motion pause |

The descriptions in this table come from the user. The detector still reports `unclassified_repetitive_motion`; it does not identify the exercise class. No independent manual count was saved for exercise-04, so counting accuracy cannot yet be scored against a reference total. These sessions were first evaluated after the algorithm was implemented; neither was used to tune this baseline result.

Whole recordings and numbered push-up cycles (local artifact: `data/processed/exercise-03-04-validation/validation_overview.png`)

## Acquisition and storage quality

| Check | exercise-03 | exercise-04 |
|---|---:|---:|
| Time from READY to finish | 61.328 s | 60.563 s |
| Overlapping saved IMU span | 65.370 s | 63.857 s |
| ACC samples | 3,480 | 3,400 |
| Gyro samples | 3,460 | 3,380 |
| HR readings | 65 | 64 |
| Measured IMU packet-endpoint rate | 52.940 Hz | 52.937 Hz |
| Detected gaps / duplicate / backward timestamps | 0 / 0 / 0 | 0 / 0 / 0 |
| Parsing errors / application packet drops | 0 / 0 | 0 / 0 |
| Finish reason | Keyboard finish | Keyboard finish |
| Unexpected disconnect before cleanup | No | No |

The saved IMU spans include startup and shutdown samples, so they exceed the time between READY and finish. Both sessions have `status: complete`. The disconnect callbacks in their connection logs occurred during normal cleanup after the finish command. The earlier unexpected-disconnect failure did not recur here; these short recordings do not establish long-session reliability.

All **162 raw packets** from exercise-03 and **173 raw packets** from exercise-04 were decoded again and matched against every saved ACC, gyro and HR row, including values, timestamps, packet IDs and sample indices. This checks storage consistency using the same decoder, rather than independently validating the sensor protocol. Source hashes were checked against the counter outputs and again after analysis. Original data and labels remain unchanged.

The sensor has no usable sequence counter in this capture format, so absence of inferred gaps is not proof of zero radio packet loss. No gap is present at the push-up group split.

## Push-up boundaries and the short pause

| Group | First complete cycle starts | Last complete cycle ends | Count |
|---|---:|---:|---:|
| 1 | 30.593 s | 38.673 s | 6 |
| 2 | 39.913 s | 52.993 s | 8 |

There is **1.240 s** between the accepted groups. The signal is nearly stationary in the middle of that interval: bias-corrected, filtered gyro magnitude has a median of 2.26 degrees/s and a 95th percentile of 5.24 degrees/s over 38.873–39.713 s. This supports a short physical pause rather than an unrecorded repetition or a Bluetooth interruption. A further shorter pause of about 0.48 s between cycles 8 and 9 remains inside the second group.

The existing chain rule permits gaps of at most 0.6 times the reference movement period: about 0.768 s for the first group and 0.912 s for the second. The longer pause exceeds both limits. The automatically selected reference periods also differ, at 1.28 s and 1.52 s. This explains the split without altering the baseline detector.

If the user intended one push-up set with brief rests, the counter has fragmented that intended set even though the accepted total remains 14. Grouping compatible cycles across a short rest is a separate decision from counting the cycles themselves. Any future grouping change should preserve this unchanged baseline, check that household activity still produces zero, and avoid adding a repetition for the rest interval.

“Complete cycle” means the detected signal visits one end of its movement range, reaches the opposite end and returns. It does not certify push-up depth or technique. The signal review supports the 14 detected cycles, but a confirmed manual count or video would be needed to measure counting error independently.

## Household movement rejection

The household recording contains substantial motion, including a rhythmic section around 38–48 s. Three overlapping eight-second windows pass the initial periodicity checks. However, its full-cycle candidates do not form a qualifying chain of at least three consistent cycles: the surviving chains have only one cycle each. The final result is therefore zero counts despite visible movement.

This is useful negative evidence for this particular household example. It does not establish rejection of every household task, walking or other periodic activity, and does not support a general false-positive percentage from a single minute of recording.

## The saved keyboard markers

| Recording | Activity selected | Space/start marker | Finish |
|---|---:|---:|---:|
| exercise-03 | 21.250 s | 62.063 s | 75.266 s |
| exercise-04 | 17.266 s | 62.891 s | 73.110 s |

Both label rows have `completion: interrupted` and an empty expected repetition count. The event journal records an activity selection, then a later set start, then session finish; it contains no Space/end or count submission. In exercise-04, the set-start marker is about 9.9 s after the last detected push-up cycle. These markers cannot serve as exercise-boundary ground truth.

This affects annotations, not acquisition: selecting an activity does not start a labelled set, and ending the recording while a label is open marks that label interrupted. The collector continuously saved the actual earlier movement, and the automatic counter does not depend on the markers. For a future labelled recording, the sequence is activity key, Space before the set, Space after the set, enter count, then Q. No repeat is needed to analyze these files.

## Reproduce and inspect

```powershell
.\.venv\Scripts\python.exe -m polar_activity count data/raw/exercise-03 --output data/processed/exercise-03-validation
.\.venv\Scripts\python.exe -m polar_activity count data/raw/exercise-04 --output data/processed/exercise-04-validation
```

- Household baseline output (local artifact: `data/processed/exercise-03-validation/counts.json`)
- Push-up baseline output (local artifact: `data/processed/exercise-04-validation/counts.json`)
- All 14 cycle boundaries (local artifact: `data/processed/exercise-04-validation/cycles.csv`)
- Replay, quality, grouping diagnostics and source/code hashes (local artifact: `data/processed/exercise-03-04-validation/validation.json`)

No hardware session was started for the baseline analysis. That analysis changed no production detector code, raw files or source labels. The later provisional reference is stored separately in reference.json (local artifact: `data/processed/exercise-04-pauses/reference.json`); the original source labels remain unchanged.
