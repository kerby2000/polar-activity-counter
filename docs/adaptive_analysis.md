# Adaptive analyser: combined features, baseline and short-set boundaries

Implemented and evaluated on 2026-09-11. Final run:
`data/processed/exp-r2/runs/5dd128f7f53c3480/`.

The new `--engine adaptive` combines gravity-relative activity features with a
stable pull-up baseline and a counter that accepts short sets with two observed
cycles. It automatically finds **five push-ups and three pull-ups in blind-01**,
and counts **zero exercise sets in the household recording** even when that
recording is excluded from training. These are retrospective development results;
the activity order and totals were already disclosed before this work.

For new installations, start with the [user manual](user_manual.md) and train a
personal model. The [illustrated blind results](examples.md) include the subsequent
successful fresh blind-02 test. The paths below describe the original local data.

The local model is ready at `data/models/personal-adaptive.json`. The existing
default model, legacy analyser, raw recordings and EXP-R1 results are preserved.
No additional dependencies are required to use the new engine.

## Use it

From the repository directory in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m polar_activity analyze data/raw/blind-01 --engine adaptive
```

Replace the session path to analyse a different recording. Outputs go into that
session's separate `analysis-adaptive/` directory:

- `analysis.json`: accepted sets, class evidence, candidate intervals, rejected
  motion/boundary evidence, model/source hashes and unassigned incomplete attempts.
- `sets.csv`, `repetitions.csv`, `activities.csv`, `unassigned_attempts.csv`.
- `analysis.png` and, when available, `motion_quality.png`.

The lower band of the plot is activity-class evidence. A colour there does not
establish a counted exercise set; accepted sets are marked above the waveform.
Walking/stairs names remain estimates, and their steps are not counted.

`--output` chooses another derived folder. `--no-plot` skips figures. The ordinary
command without `--engine adaptive` continues to use the legacy model/engine.
Both engines can be compared without overwriting each other's default outputs.

To rebuild the prepared model:

```powershell
.\.venv\Scripts\python.exe -m polar_activity train data/models/references-adaptive.json --engine adaptive
```

The adaptive model uses 191 four-second reference windows from the eight existing
development recordings. Neither `blind-01` nor expected repetition totals enter
training. These overlapping windows are not 191 independent training subjects.
The model and reference manifest are local ignored data; a new clone needs the
recordings and a reference manifest before training.

## What changed

1. **Activity features:** four-second windows now use gravity-relative and magnitude
   features instead of absolute mean ACC XYZ. Distance still uses the mean of the
   nearest three same-class reference windows, with the existing 0.85 threshold.
   Class evidence is saved separately from counting decisions. Two seconds of
   class support are counted as emissions, avoiding floating-point boundary loss.
2. **Pull-up baseline and boundaries:** the quiet baseline from EXP-R1 is reused.
   A counted excursion needs an observed near-baseline origin before departure.
   This rejects an already-raised preparation posture at the beginning of a
   classifier interval. Boundaries are extended to observed near-baseline returns,
   with bounded context and the existing dismount cutoff retained. Equally quiet,
   incompatible baseline poses produce an explicit abstention.
3. **Short push-up/squat sets:** periodic seeds are tested at 4, 6, 8 and 12 seconds.
   Two periods can seed a short set; the old requirement for three periods inside
   a seed no longer blocks it. All passing seeds are considered before selecting
   coverage. Both IMUs must support repetition, with outward and return motion,
   bounded duration/amplitude, shape agreement and limited net rotation. A supported
   fundamental is preferred over a near-equally-correlated harmonic. Opposite phase
   counts are never added together.
4. **Rest and uncertainty:** brief class-evidence gaps and qualifying quiet rests
   can be bridged, within three seconds. Pull-up grouping uses full movement
   boundaries rather than just time above an angle threshold. Incomplete attempts
   separated by a longer rest remain visible separately; they are not silently
   converted into completed repetitions. Gaps in sensor data always split analysis.

Jump impact pairing retains its existing convention and detector. This work does
not add a new jumping biomechanical model, stair direction, posture inference or
an anatomical technique score.

## Results with the prepared personal model

| Recording | Result | Interpretation |
|---|---|---|
| exercise-02 | 13 push-ups, 47.52–67.16s | Within the user's 12–13 uncertainty |
| exercise-03 household | No counted sets | Also zero when household is excluded from training |
| exercise-04 | 14 push-ups, 30.63–53.03s | Preserves the earlier reviewed count |
| exercise-05 | Two jump sets of 10; additional 3-squat estimate at 110.76–114.08s | Jump totals preserved; extra squat estimate is unverified |
| exercise-06 | 10 squats, 21.84–45.00s | Preserves the labelled squat set |
| pullups-01 | 2 attempts, 21.81–28.73s | One return fully observed; the second remains incomplete |
| pullups-02 | 3 completed returns, 18.68–29.32s; separate incomplete attempt, 33.24–35.44s | Preserves the questionable fourth attempt explicitly |
| stairs-01 | Additional 2-squat estimate at 6.44–8.40s | Unverified setup movement; stair steps are not counted |
| blind-01 | 5 push-ups, 21.12–28.00s; 3 pull-ups, 66.00–75.24s | Automatic inference; no extra counted sets |

Except for `blind-01`, this table replays recordings used to train the prepared
personal model. It checks regressions and practical behavior, not independent
accuracy. The additional squat estimates are visible in the results; their times
are outside precise background labels, so they are not silently treated as known
background in the numerical score.

## Whole-recording exclusion and false counts

Every recording was also analysed with all of its own references excluded.
The two push-up counts remain 13 and 14; the short pull-up recording returns two
attempts, and the longer pull-up recording returns three completed cycles.
Its doubtful fourth attempt is missed in that fold because activity evidence ends
too early. `blind-01` remains five push-ups and three pull-ups.

The household check contains **65.36 seconds** of explicitly identified household
activity. It produces **zero counted sets and zero repetitions**, including the
fold with no household training examples. Four proposed exercise intervals in
that fold are rejected for lacking a supported return sequence.

Across all **185.267 seconds** of explicitly marked background:

| Method | Confirmed false exercise bouts | Assigned false repetitions |
|---|---:|---:|
| EXP-R1 joint-PCA DTW + MM-Fit-inspired counter | 4 | 5 |
| Adaptive, target recording excluded | 1 | 2 |
| Adaptive, reference replay | 0 | 0 |

The remaining excluded-recording false positive is two pull-up estimates during
sitting in `exercise-06`, at 107.24–118.64s. That same fold also misnames parts of
the squat set as push-ups. There is no second squat or standing/sitting source to
train on when this recording is excluded. Likewise, holding out `exercise-05`
removes all jump training examples. These unsupported cases stay in the report;
they cannot establish recognition accuracy for those classes.

The background sample is short and selected. Zero counts here does not establish
zero false counts during a full day. Unlabelled time is not known background.

## Verification and reproducibility

- **244 tests passed**, including 22 new checks for two-cycle sets, slow cycles,
  pauses, incomplete/one-direction/noisy movement, candidate-edge posture, ambiguous
  baselines, rotation, gaps, corrupt models, CLI routing and label/count independence.
- Ruff lint and format checks passed; `pip check` found no broken requirements.
- **270 integrity checks passed:** the 153 preserved EXP-R1 JSON artifacts, 18 raw
  stream hashes, final source versions, completed-run files, fold isolation and
  the frozen original model.
- A common 73-degree coordinate rotation of ACC and gyro changed no window classes
  or set/count results on the household, two pull-up recordings and `blind-01`.
- The CLI trained the prepared model and reproduced the blind, household and longer
  pull-up outputs. Waveform/boundary plots were visually checked.

All decisions are saved and hashed before reference totals enter scoring. Both
whole-recording exclusion and reference replay are included in the final run:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_adaptive.py --config experiments/exp_r2.json
```

The evaluation uses the existing optional experiment dependencies for scoring;
install `.[experiment]` if they are missing. The normal adaptive engine needs only
the base application dependencies. Completed cases are verified and reused on a
repeat run. Source/config/data changes produce another run ID.

The first combined development run (`2ccd2b9a9254b725`) counted an already-raised
preparation posture as a fourth blind pull-up. Its output is retained. Requiring
an observed origin fixed that boundary error; no thresholds were adjusted to
match the disclosed count. An intermediate final-method run (`56e65da525b0510c`)
differs from the final run only by CLI line-ending formatting.

The source/test snapshot before these changes is in
`data/processed/exp-r2/baseline/`. The previous experiment remains under `exp-r1/`.
These evaluation artifacts remain local under ignored `data/`. The implementation,
documentation and selected figures are published with this release; the frozen
raw recordings and detailed runs are not included in a fresh clone.

## Fresh blind validation: blind-02

The adaptive model and source were frozen before the next recording was analysed.
Predictions were saved and hashed before the user disclosed the activities or
counts. The user then confirmed the following results exactly:

| Activity, in order | Frozen prediction | User-confirmed count |
|---|---:|---:|
| Jumps | 3 | 3 |
| Squats | 10 | 10 |
| Push-ups | 11 | 11 |

All three activity/count pairs matched, with **24 total repetitions** and no extra
or missing sets against the reported exercise inventory. This is a successful
fresh blind validation for this participant and placement. Precise timing and
individual-cycle boundaries were not independently annotated.

The frozen predictions, confirmation and evaluation are preserved under
`data/processed/blind-02-evaluation/`. The recording is retained as validation and
has not been added to the training references. The model was not changed after
the user's reveal. This single session does not establish full-day performance
or accuracy for other participants.

## What remains

### Bounded final pull-up continuation (2026-09-11)

An established pull-up set with at least two observed returns can now inspect up
to eight extra seconds beyond its classifier interval, using the same baseline
pose. A continuation needs a rest within twice the typical full cycle duration
(bounded to 3–5 seconds), at least 80% of the set's typical arm excursion and a peak
direction within 25 degrees of the earlier peaks. The first impact cutoff remains
in force; unmatched motion or a longer pause stops continuation. These are initial
development heuristics, not biomechanical definitions of a correct pull-up.

An effort truncated by dismount is retained as an attempt with an incomplete
return, never silently promoted to a completed cycle. The saved continuation trace
records the search limit, checks and accepted/rejected evidence. This fixes the
third effort in `magnetic-01` after user feedback while leaving the earlier 11
recording replays unchanged. The raw data, original predictions and classifier
model were preserved. See [MAG findings](magnetometer.md) for the sensor evidence.

### Preserve compatible cycle-sequence edges (2026-09-11)

Reviewing magnetic-01 exposed an already-accepted first push-up cycle at
17.28–18.84s that selection discarded: the longest candidate covered the later
cycles, while a shorter overlapping candidate included the first. Selection now
merges compatible sequences only when at least two shared cycles match and every
cycle in the overlap has matching boundaries within two analysis samples. It
retains qualified edge cycles and counts the overlap once, with merge evidence
saved in the trace. Opposite phases, harmonics and inconsistent boundaries cannot
be combined. Existing amplitude, shape, return and duration gates are unchanged.

The estimate becomes 11 push-up cycles and 3 pull-up attempts. The participant
withdrew the earlier recollection of 10 push-ups, so the true total is uncertain.
The earlier 11 recording replays still preserve counts and boundaries. This is a
development correction, not a new blind result.

The requested combination, boundary repair and household comparison are complete.
The first fresh mixed blind test has passed. Further coverage can come from normal
use rather than repeating strenuous reference sessions. Ordinary movement, stair
use and classes with only one source recording still need broader validation
before claiming reliable all-day recognition.
