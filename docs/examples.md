# Blind recordings: results and figure provenance

These figures use real ACC/GYRO recordings from one participant wearing a Verity
Sense on the left upper arm. They are presentation exports of saved decisions,
not a new inference run or a relabelling of the signals. The lower colour bands
show classifier evidence; only the shaded regions above are accepted exercise sets.

## Blind-02: prediction before disclosure

The adaptive model and source snapshot were frozen before analysis. Predictions
were saved on **11 September 2026 at 08:51:22 UTC**, before the participant revealed
the activities/counts. The participant subsequently confirmed the predicted sequence.

| Order | Activity | Predicted interval (session seconds) | Predicted reps | Confirmed reps |
|---|---|---|---:|---:|
| 1 | Jump | 12.42–16.93 | 3 | 3 |
| 2 | Squat | 36.48–61.00 | 10 | 10 |
| 3 | Push-up | 97.88–114.36 | 11 | 11 |

![Blind-02 overview: 3 jumps, 10 squats, 11 push-ups](images/blind-02-overview.png)

All three reported exercise sets matched in activity, order and count: **24 total
repetitions**, no extra accepted sets and no missing reported sets. This measures
agreement with the user's reported inventory. It does not measure exact boundary
accuracy, individual-cycle validity or a per-hour background false-positive rate.
The roughly 153-second common signal span is one session, not a population benchmark.

![Blind-02 accepted cycle close-ups](images/blind-02-cycles.png)

The jump plot shows native acceleration magnitude with three saved impact pairs.
Squat and push-up plots show native gyro samples projected onto each saved set's
dominant motion axis, with its saved bias removed. Cycle numbers and shading come
directly from the frozen report. The participant confirmed totals, not these
precise per-cycle boundaries.

## Blind-01: retrospective improvement

The initial blind analysis accepted no exercise sets. The participant then disclosed
five push-ups, three pull-ups and intervening everyday movement, walking and stairs.
That exposed short-set and pull-up-boundary failures and informed development.

The later adaptive result is **5 push-ups at 21.12–28.00s** and **3 pull-ups at
66.00–75.24s**, with no extra accepted sets. It uses no target labels/counts at
inference time, but the developer already knew the answer. It is therefore a
**development replay**, not a recovered independent blind success.

![Blind-01 development replay: 5 push-ups and 3 pull-ups](images/blind-01-overview.png)

The activity timeline outside these sets remains estimated. In particular, this
figure does not establish automatic stair direction or stair-step counting.

## What is preserved

Both blind recordings remain outside the personal model's training references.
The original participant's raw recordings, model and detailed frozen evaluation
folders remain local under ignored `data/`. This repository publishes the three
figures and a compact [provenance manifest](images/provenance.json), including input,
model, prediction and image SHA-256 hashes and the accepted set summaries.

Frozen blind-02 analysis SHA-256:

```text
bf80fd1a95935bfd6c85bd42044f015e3b3b68a441752ee91c5130c673a470b5
```

Frozen model SHA-256:

```text
451fc3805b16c642803583428cd2c06580d95b6df71d612a78105a64b409831b
```

The local evidence comprises `pre-registration.json`, `prediction-lock.json`,
the saved predictions, `user-confirmation.json`, `evaluation.json` and a frozen
source snapshot under `data/processed/blind-02-evaluation/`. Public hashes identify
those artifacts; hashes alone are not an independent reproduction of the results.
The public figures contain motion only, with no heart-rate trace, Bluetooth address
or sensor credential.

## Regenerate the pictures

With the original local data layout available, run:

```powershell
.\.venv\Scripts\python.exe scripts/build_documentation_figures.py
```

The renderer verifies the frozen blind-02 analysis hash and the motion-file hashes
before drawing. It reads `data/raw/blind-01/analysis-adaptive/analysis.json` and
`data/processed/blind-02-evaluation/predictions/analysis.json`. It writes new files
under `docs/images/`; it does not modify frozen evaluation outputs or rerun the
classifier. `--data-root` and `--output` allow another local layout.

A fresh clone has the published pictures but not these private input recordings.
Use the [hardware-free installation check](user_manual.md#hardware-free-installation-check)
or record/train on your own data to try the program.

For broader results, including known false counts and excluded-recording failures,
read [Adaptive analysis](adaptive_analysis.md). Future validation priorities are in
the [roadmap](roadmap.md).
