# Pull-ups and stairs reference update — 10 September 2026

The default analyser has been updated from both new recordings. The previous
model and untouched pre-update predictions are retained under
`data/processed/analyser-v4/`. The current model uses **13 reference intervals,
170 correlated four-second windows**, including four stairs cores and the new
pull-up core. Counts and the user's quality judgement are not classifier targets.
These results are development/training replays, not blind accuracy results.

## Data integrity

| Recording | ACC / GYRO samples | Overlapping duration | Quality |
|---|---:|---:|---|
| pullups-02 | 4,120 / 4,120 | 77.845 s | No detected gaps, duplicate/backward timestamps or quality warnings |
| stairs-01 | 5,900 / 5,900 | 111.477 s | No detected gaps, duplicate/backward timestamps or quality warnings |

Rates from packet endpoints are approximately 52.936 and 52.935 Hz. Original
recording files, labels, manifests and CSV streams remain unchanged; hashes for
all eight usable exercise sessions were checked against a frozen baseline.

## Pull-ups: three returned cycles and a fourth reduced attempt

The pre-update model found no qualifying set. The new reference supports
recognition of the new sensor-frame orientation and motion pattern. The updated
counter finds **four attempts at 19.04–35.44 s**, with three observed returns and
one unclosed attempt.

| Attempt | Counted core (s) | Peak sensor-direction departure | Relative to median returned excursion | Signal result |
|---|---|---:|---:|---|
| 1 | 19.04–20.32 | 95.7° | 102% | Return observed |
| 2 | 23.08–24.44 | 94.3° | 100% | Return observed |
| 3 | 27.68–29.08 | 88.5° | 94% | Return observed |
| 4 | 34.00–35.44 | 54.8° | 58% | Reduced excursion; return unobserved before dismount |

The fourth attempt is consistent with the user's report that it was not clean.
The first three have similar excursion sizes; the third is modestly smaller.
Peak-to-peak intervals are approximately 4.04, 4.60 and 6.24 seconds. Those intervals
include pauses and do not independently establish fatigue or lifting speed.

The angle is the departure of smoothed acceleration direction from the preceding
pose. It is **not elbow angle, body rise, pull-up depth or a percentage technique
score**. One upper-arm IMU cannot verify chin clearance, elbow extension, whole-body
swing, kipping or safe technique. A video or additional body measurements would be
needed for those judgements. The application reports motion consistency only.

The new `short-arm-excursions-v2` path truncates an open attempt before a low-g or
large rotating impact, also excluding the smoothing filter's look-ahead. Without
this cutoff the dismount can inflate the fourth attempt's measured range. An
established sequence may retain a smaller subsequent departure as an attempt;
the expected count is never supplied to this detector.

The original two-rep `pullups-01` remains **two attempts**. Its stricter dismount
cutoff now marks the second return unobserved at 28.73 s. This is a conservative
signal-coverage flag, not a revision of the user's count or proof of poor form.
The older relaxed detector accepted that return at approximately 28.89 s.

`analyze` now exports `motion_quality.csv`, `motion_quality.png` and per-attempt
metrics/flags in `analysis.json` for pull-up sets found by this path. Other exercise
types do not yet have equivalent motion-quality comparisons.

## Stairs: four distinct passes

The previous model labelled most moving windows household. Adding reviewed
stairs cores now gives these four continuous activity intervals:

| Estimated interval | Pass described by user | Reference interpretation |
|---|---|---|
| 15.5–26.5 s | Up, every tread | About 18 treads, according to the user |
| 28.5–39.5 s | Down, every tread | Same flight |
| 52.5–60.5 s | Up, every second tread | Nine distinct large acceleration pulses in the reviewed 53–60 s core, consistent with roughly 18 treads traversed two at a time |
| 66.5–75.5 s | Second descent, different style | User confirmed no handrail; the rail is on the left/sensor side while descending |

The first descent's handrail use was not explicitly confirmed and is not invented
as a training label. Up/down order comes from the user's description. The program
currently predicts the pooled activity **stairs**, not ascent/descent, and does
not automatically count footfalls or treads. A foot contact and a tread traversed
are different units when skipping steps. The approximate 18-tread report is kept
as ground-truth context, not forced onto the signal.

Boundaries have about two seconds of feature-window context. Approach, landings,
turns and return movements are excluded from the new training cores. Much of that
unlabelled motion still maps to household/background rather than walking, which
remains a known classification limit. One four-pass recording is not four
independent recording sessions.

## Reference storage and replay

- `data/models/references.json`: the 13 explicit classifier intervals and their provenance.
- `data/models/knowledge-base.json`: user reports, uncertain counts, handrail clarification,
  reviewed intervals, source hashes, counting-unit notes and pending capabilities.
- `data/models/personal.json`: updated default model.
- `data/models/personal-evaluation-20260910.json`: frozen model for the next blind test;
  SHA-256 `9d3327191446cc38c5de56f2027a5edd9c238cafd5ccf131f9009272e4550076`.
- `data/processed/analyser-v4/validation.json`: all-session replay, input/model/code hashes.
- `data/raw/pullups-02/analysis/` and `data/raw/stairs-01/analysis/`: current review outputs.

Existing counts remain 13 and 14 push-ups, 10 + 10 jumps, 10 squats and two earlier
pull-up attempts; the household recording still has no exercise repetitions.
The old two-pull-up return flag changed as described above. No previously unseen
session was used to claim a percentage accuracy.

There are **198 passing automated tests**, with Ruff lint and formatting checks
passing. New cases cover a weak fourth attempt, dismount contamination, rotation
and sample-rate invariance of the motion proxy, three reps without an invented
fourth, and stairs recognition without treating gait as strength repetitions.

The log also exposed ACC/GYRO starts on adjacent seconds (`213653` and `213654`).
USB selection now accepts that one-second directory difference while retaining
source identity, complete split-file lists, size/hash verification and refusal of
unrelated start times. No new device transfer was needed for this analysis.

## What remains before evaluation

A **first informal blind test can be next** for activity recognition and strength/
jump counts. There is no need for another pull-up reference now. Use a new neutral
folder such as `blind-01`, preserve the same sensor placement/orientation, and keep
your activity order/counts private until the frozen model's results have been saved.
Pauses between activities help interpret transitions but need not be precisely timed.

```powershell
.\.venv\Scripts\python.exe -m polar_activity analyze data/raw/blind-01 --model data/models/personal-evaluation-20260910.json
```

For a broader final acceptance test, the unresolved items are:

1. Stair footfall/tread counting and automatic ascent/descent estimation.
2. Standing versus sitting: currently reported as stationary/pooled background,
   not reliable posture recognition from this sensor placement.
3. More independent examples for stairs, squats, walking and household movements;
   orientation changes and handrail use are underrepresented. These can follow the
   first blind test and need not be collected in one demanding session.
4. Biomechanical form assessment, if desired, needs additional evidence beyond the
   current arm-motion consistency indicators.

Freeze predictions before revealing the blind reference. Any adjustments made
after seeing that reference require another held-out recording to evaluate them.
