# Personal activity analysis

Latest update: [pullups-02, stairs-01, motion consistency and blind-test readiness](pullups_stairs_validation.md).

Updated 2026-09-10. `analyze` combines the existing complete-motion-cycle counter with a personal activity model, jump pulse counting, and short pull-up arm-excursion detection. All processing is local. It never connects to the sensor and does not read the target recording's labels or expected counts during inference.

## Results on all usable exercise recordings

| Recording | Earlier generic counter | Personal analyser | Reference / qualification |
|---|---|---|---|
| exercise-02 | 13 unclassified cycles | 13 push-ups, 47.48–67.12 s | Original label 12; user later said possibly 13. Reference remains uncertain. |
| exercise-03 | 0 | Household movement; no exercise reps | Session identified as household by the user; late keyboard marker is not the motion boundary. |
| exercise-04 | 14 unclassified cycles | 14 push-ups, 30.59–52.99 s, including the existing 1.24 s rest | User retrospectively said 14 seems right. Not included in the new model's reference windows. |
| exercise-05 | Missed jumps; found 3 recurring cycles during walking | Two jump bouts: **10 + 10 estimated reps**, 32.18–50.15 s and 64.28–79.43 s | User subsequently confirmed both sets were 10 jumps with arms raised; the first was before pressing Space. Both counts match. |
| exercise-06 | 10 unclassified cycles | **10 squats**, 21.84–45.00 s | Matches the completed 10-rep squat label. |
| pullups-01 | 0; failed the minimum-three-cycle requirement | **2 pull-up arm excursions**, 22.33–28.89 s | User confirmed two total repetitions; precise boundaries were not independently labelled. |

Exercise-05 also has estimated walking intervals at **108.3–128.3 s** and **134.3–159.3 s**. The second overlaps the completed walking label. Walking strides no longer contribute to the exercise-repetition total. Steps are not counted yet.

Each jump bout contains **20 resolved acceleration impact pulses**. The personal model treats a pulse pair as one repetition, giving 10. This counting convention was calibrated against the second bout's 10-rep label and alternating motion pattern. The user subsequently confirmed another 10 jumps before pressing Space and described jumping with arms raised. Thus both predicted counts agree with the reference: **20 repetitions total**. The movement remains labelled `jump`; leg movement was not specified, so the clarification does not establish a jumping-jack subtype. Pulse pairs describe the calibrated counting unit, not a direct measurement of body flight or proven biomechanics.

The first bout's reference count was confirmed after the prediction. Its windows remain outside the feature bank, while the second bout supplies the jump reference. Since both come from the same recording, this is still a development check rather than an independent blind test. The confirmation is saved separately in `data/processed/analyser-v3/exercise-05/reference.json`; raw keyboard labels and motion data remain unchanged. Model provenance and reports have been refreshed, with identical feature vectors and identical results across all six exercise sessions. Raw pulses and pair boundaries remain available for review; no expected target count is imposed on detector output. Models with an unconfirmed convention can report impacts/pairs without a rep estimate instead.

The late, interrupted squat marker at the end of exercise-05 lasts less than a second and extends past the final sample. It supplies no usable squat training interval. Exercise-06 contains the actual completed squat reference.

The standing label (63.781–93.078 s) and sitting label (106.625–141.469 s) in exercise-06 contain substantial arm gestures. They are pooled as background references. At inference, quiet windows are `stationary`, and moving windows matching that pool are `other_movement`; the analyser does **not** assign a standing/sitting posture. Similar upper-arm signals can occur with different body postures. Stairs now has four reviewed cores from stairs-01; the new report documents that reference and its limits.

## Recording quality

Exercise-05 contains 9,720 ACC and 9,720 gyro samples, about 183.64 seconds of overlapping motion data. Exercise-06 contains 8,026 ACC and 7,929 gyro samples, about 149.80 seconds of overlap. Neither has detected internal gaps, duplicate/backward timestamps or application-dropped packets. Packet-endpoint rates are about 52.934 Hz despite the configured 52 Hz setting.

The exercise-06 gyro stop warning occurred during shutdown. The stream end mismatch is 1.832 seconds, and the ACC and gyro saved ranges both cover all three completed labels. The warning remains in report provenance; it does not invalidate the squat set. No new exercise or sensor recovery is needed.

Exercise-01 has zero IMU samples and is excluded. The old failed startup-check recording has duplicate/backward device timestamps and is also excluded. Four valid stationary diagnostic recordings remain entirely `stationary` under the feature model and retain zero generic exercise counts. All analysed motion CSV hashes match the frozen pre-change baseline.

## Run analysis

From the repository in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m polar_activity analyze data/raw/exercise-05
.\.venv\Scripts\python.exe -m polar_activity analyze data/raw/exercise-06
.\.venv\Scripts\python.exe -m polar_activity analyze data/raw/pullups-01
```

Default output is `SESSION/analysis/`. Options: `--output PATH`, `--model PATH`, `--no-plot`, and `--json`. Output contains:

| File | Contents |
|---|---|
| analysis.json | Sets, per-window feature distances, activity intervals, source/model hashes, calibration convention, warnings and limitations |
| analysis.png | Whole-session acceleration, gyro, counted bouts and activity timeline |
| sets.csv | Exercise names, estimated counts, automatic boundaries and counting method |
| repetitions.csv | Estimated rep intervals; no rows for jump reps when their convention is unconfirmed |
| jump_impacts.csv | Individual resolved acceleration pulses, separate from repetition estimates |
| activities.csv | Activity estimates, including unknown/background windows |

New reports for this development pass are saved under `data/processed/analyser-v3/SESSION/`. `validation.json` in that parent directory records the full replay and source hashes. `baseline/` retains the unchanged generic-counter results. Prior analysis outputs and raw labels were preserved.

## Update the personal model

The existing local `data/models/references.json` selects 13 reference intervals from exercise-02, exercise-03, exercise-05, exercise-06, pullups-01, pullups-02 and stairs-01. It records where each boundary came from, including analyst-selected boundaries. It produces 170 overlapping four-second feature windows. Those are correlated windows from a few recordings, **not 170 independent exercise examples**.

```powershell
.\.venv\Scripts\python.exe -m polar_activity train data/models/references.json
```

The default output is `data/models/personal.json`. Training checks that the subject and sensor placement match and that each selected interval lies entirely inside intact ACC/gyro overlap. The manifest is the sole source of training labels; `expected_rep_count` fields are ignored by feature fitting. Jump counting convention is separately declared in the manifest and its calibration source is saved with the model. Paths are relative to the manifest's directory. A minimal manifest has this structure:

```json
{
  "subject": "your-subject",
  "sensor_position": "upper_arm_left",
  "jump_count_convention": "unconfirmed",
  "references": [
    {
      "session": "../raw/reference-session",
      "activity": "push-up",
      "start_time_s": 20.0,
      "end_time_s": 35.0,
      "boundary_source": "Reviewed active interval inside the completed keyboard label"
    }
  ]
}
```

Use actual reviewed intervals of at least four seconds; exclude unrelated preparation/rest. Supported reference classes are push-up, pull-up, squat, jump, walking, stairs, household and the deliberately pooled standing_or_sitting background. Jump convention may be `unconfirmed`, `paired_impacts`, or `single_impact`. Changing convention changes the unit being counted and should be based on the performed movement, not an arbitrary desired count.

## Implementation and limits

The feature model low-passes native IMU data before resampling to 25 Hz, independently in each gap-free overlap. It compares four-second windows every second using 15 scaled features: acceleration mean and axis variability, gyro axis variability, acceleration-magnitude quantiles, motion strength and dominant gyro-axis fraction. Each class gets the mean distance to its three nearest reference windows; sufficiently distant motion is unknown. Distances are not probabilities. Isolated one-second class flips are smoothed; original predictions remain in JSON.

Push-ups and squats still require the original observed excursion/return cycles, including its short-rest grouping. Model predictions supply activity names and prevent walking/household cycles becoming exercise reps. Exercise-like preparation gestures require corresponding counted motion before they retain an exercise name in the timeline.

Jump detection resolves acceleration pulses of at least 2,400 mg, separated by at least 0.4 s, with an observed preceding low-acceleration phase and subsequent settling. Pulses must overlap a model-recognized jumping interval. Groups split after a three-second interruption or source gap. In paired mode, adjacent pulses at most 1.1 s apart form one estimated repetition; unmatched pulses remain recorded separately. These defaults are calibrated development heuristics, not universal jump mechanics.

The short pull-up path uses a recognized bout and preceding arm orientation, then detects large departures and returns of the smoothed acceleration direction. It can accept two excursions without an eight-second periodic seed. An unobserved return is flagged as an incomplete attempt, not silently described as a complete cycle. Observed return means crossing a signal threshold, not fully straightened elbows, pull-up depth or technique. The current detector excludes dismount impacts and filter look-ahead. The original two-rep result retains two attempts but now marks the second return unobserved; this conservative signal flag is not a form judgement.

Keep the same arm, position and sensor orientation. Subject/position mismatches are rejected; changes in strap rotation or arm use may still cause wrong activity names. In particular, the model recognizes the new walking intervals better than the approach/return motion in the earlier offline pull-up recording; much of that latter motion remains `other_movement`. Household and background are broad, weakly validated classes.

Most current sessions contributed references or detector development. Exercise-04 is absent from the new classifier's references, but was used in earlier counter development, so its preserved 14-count result is a regression check, not a fresh blind test. No random window split or percentage-accuracy claim is made. The next meaningful evaluation is a new session withheld from training.

The automated suite contains 198 tests, including the later USB and motion-quality additions. The new tests cover separate training/inference inputs, changed target labels and expected counts, raw-input preservation, model provenance, invalid models, placement mismatches, waveform-based jump events at three sample rates and axis rotations, counting conventions, missing-data boundaries, two slow excursions and incomplete returns. They do not replace a blind human recording.
