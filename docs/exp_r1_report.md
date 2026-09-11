# EXP-R1: executed recognition experiment

This is the preserved first comparison, preceding the [adaptive combination](adaptive_analysis.md)
and [fresh blind-02 validation](examples.md). DTW/MM-Fit experiments remain opt-in;
they are not the recommended adaptive inference path.

Status: **EXPERIMENT_EXECUTED**, 2026-09-11. Local branch
`feature/exp-r1-dtw-mmfit`; run `75cc9cfd21036b86`.

**Decision: keep the new methods experimental.** The orientation-aware DTW matcher
recovers useful cycles but also confuses exercise types and ordinary motion. The
MM-Fit-inspired counter succeeds with supplied blind-test intervals, but undercounts
the automatically selected intervals and performs poorly on the small public check.
The strongest targeted repair is a stable-context baseline for pull-ups, followed by
removing absolute sensor-axis averages from activity features.

This implements the experiment proposed in the two ChatGPT Pro project discussions
and the downloaded EXP-R1 task. The original acquisition commands, analyser,
model, recordings and frozen blind-test failure remain intact. No new physical
recording was needed and no experimental output was published to GitHub.

## What ran

- Nine complete recording groups, with 951.92 seconds of overlapping ACC/gyro data.
  Eight development recordings use whole-recording exclusion; `blind-01` is an
  exposed retrospective regression, excluded from every training/calibration fold.
- Raw-axis and joint-PCA constrained multichannel DTW: 18 completed folds and
  52,288 candidate intervals, with complete coverage of the configured duration/stride
  grid. Coverage does not mean every possible start time or duration was searched.
- At most three complete-cycle training exemplars per available exercise, with
  shared ACC/gyro scaling, a shared constrained alignment path, proper joint signs,
  explicit fallback for unstable PCA frames and deterministic overlap resolution.
  The complete-cycle proposal pool contains 41 cycles: 27 push-up, 10 squat, 4 pull-up.
- F0: original analyser/model. D1: supplied interval and class, comparing counters.
  D2: supplied interval only, comparing naming. E1/E2: identical automatically
  proposed DTW sets, counted separately by the existing and new counter.
  DTW cycle totals are an additional diagnostic, not a fusion of E1/E2 counts.
  F0 contains the original labelled reference recordings being replayed; its
  development scores are not an equally held-out accuracy comparison to E1/E2.
- Fixed initial configuration, training-only template/threshold fitting, saved
  predictions hashed before target annotations enter diagnostics or scoring.
- Paired coordinate rotations, feature comparisons, interval-start stress tests,
  and a baseline-only comparison on the original automatic classifier intervals.
- Two complete public RecoFit sessions, with assisted counts and separate continuous
  unclassified-motion proposals. Public recordings were not mixed into Polar training.

## Automatic results on the former blind test

All times below are seconds on the original recording clock. User reference totals
are five push-ups and three pull-ups; their approximate timing was reconstructed
after disclosure. These counts and timing were not supplied to automatic inference.

| Method | Push-up bout | Pull-up bout | Other proposed exercise bouts |
|---|---|---|---|
| F0 original analyser | Missed | Missed | None |
| Raw-axis DTW | Missed | One cycle, 73.50–75.30s | None |
| Joint-PCA DTW cycle diagnostic | 5 cycles, 21.25–28.10s | 2 cycles, 65.25–71.70s | 3 |
| E1: same PCA bouts, existing counter | 0 | 2 | Counts: unknown, 0, 0 |
| E2: same PCA bouts, MM-Fit-inspired counter | 3 | 2 | Counts: unknown, 2, unknown |

The three extra PCA bouts occur at 12.00–13.20s (push-up), 82.75–85.35s (pull-up)
and 147.50–149.10s (pull-up). They are outside the supplied exercise intervals but
not inside precisely labelled background intervals, so they remain unverified
predictions rather than confirmed background false positives in the numerical score.

**Assisted results are different:** given the supplied intervals and correct class,
the new counter returns **5 push-ups and 3 pull-ups**. The existing counter adapters
return 0 and 0 on those intervals. These are component diagnostics, not an automatic
5/3 result. The automatic pull-up bout ends before the third repetition; even a good
counter cannot recover motion outside the interval it receives.

## Transfer, false positives and support limits

| Held-out recording | Reference | PCA DTW cycles / E1 / E2 in the main bout | Other finding |
|---|---|---|---|
| exercise-02 | 12–13 push-ups | 13 / 13 / 11 | Extra pull-up proposal after the main bout |
| exercise-04 | 14 push-ups, provisional user agreement | 14 / 14 / 13 | Three other pull-up proposals |
| pullups-01 | 2 attempts | Misidentified as push-ups | Supplied-interval counter still returns 2 |
| pullups-02 | 3 complete plus reduced fourth attempt | 3 / 3 / 3 | Fourth attempt split into a separate one-cycle bout; extra push-up proposal |
| exercise-03 household | No exercise set | 3 false bouts; E2 counts 4 reps | 65.36 seconds explicitly labelled household |

Across all 185.267 seconds of explicitly marked background, PCA proposes four false
exercise bouts. E2 assigns five repetitions to them; E1 assigns zero to three and
abstains on one. F0 proposes none. These small, selected background intervals are
not a reliable estimate of false counts per day. Unlabelled time is never treated
as known background.

Jump cycle templates are unsupported in this experiment: paired acceleration
impulses do not establish a verified full cycle. The squat recording has no other
squat recording to train on when held out. Their misses remain visible in saved
scores, but are not evidence of closed-set generalisation. Walking/stairs and
standing/sitting remain contextual or negative classes here; this experiment does
not deliver new stair direction, step counting or posture recognition.

## Targeted diagnostic results

Changing the baseline while keeping the **same original pre-gating classifier
intervals**, impact cutoff and two-second end extension gives:

| Recording / automatic interval | Original excursions | Stable-context excursions |
|---|---:|---:|
| pullups-01, 20.51–27.51s | 2 (1 observed return) | 2 (1 observed return) |
| pullups-02, 16.50–35.50s | 4 (3 observed returns) | 4 (3 observed returns) |
| blind-01, 61.50–73.50s | 0 | 3 (3 observed returns) |

All six eligible original classifier intervals were checked, including spurious
ones. The stable method adds no excursions in the misnamed blind push-up interval
and removes a single preparation excursion in `pullups-02`. For the blind pull-ups,
the old pre-context has mean gyro speed 115.9 degrees/s; the selected quiet baseline
at 64.20–64.96s has 10.7 degrees/s. This supports the baseline explanation directly.
Quiet pose is a motion reference, not a claim about correct anatomical posture.

The separate whole-recording feature comparison uses four-second windows wholly
inside supplied intervals:

| Target | Frozen feature recipe, refitted per fold | No absolute mean XYZ | Gravity-relative features |
|---|---:|---:|---:|
| pullups-01: correctly named windows | 0/7 | 6/7 | 6/7 |
| pullups-02: correctly named windows | 0/14 | 11/14 | 11/14 |
| blind push-ups: correctly named windows | 0/5 | 5/5 | 5/5 |
| blind pull-ups: correctly named windows | 9/10 | 9/10 | 9/10 |

These are correlated windows, not independent accuracy trials. The frozen feature
recipe in this comparison is refitted per fold and differs from the original F0
model. The simpler six-invariant representation loses important discrimination
(0/10 blind pull-up windows), so removing all directional structure is not a solution.

Under the tested common 73-degree coordinate rotation of ACC and gyro, PCA retains
every candidate class/rejection decision and cycle selection on `pullups-01`,
`pullups-02` and `blind-01`. Raw-axis matching loses its one blind-test set. The raw
method's unchanged empty results on the two pull-up recordings do not demonstrate
useful robustness. Rotation stability alone does not fix the wrong PCA class on
`pullups-01` or the household false positives.

## Public reference result

The first visit from each of the first two RecoFit subject cells was decoded and
evaluated, preserving original activity names. A bounded prefix of the official
compressed MAT file supplied complete cells; the full 1.57 GB object was not needed.
The source commit, provider object digest, downloaded prefix length, subset digest,
unit conversion and licence are recorded. See [source notes](exp_r1_sources.md).

The assisted MM-Fit-inspired adaptation produced a numeric estimate on all 16 sets
with known positive repetition totals: **1/16 exact, 1/16 within one, mean absolute
error 8.5 reps**. This uses broad 0.5–6s period limits without exercise-specific
calibration. It is a result for this adaptation, not a reproduction or refutation
of the published MM-Fit result.

Saved-period inspection suggests harmonic selection contributes in some cases:
selected median periods are approximately 2x the annotated envelope-per-repetition
duration in both walking-lunge sets, 1.94x in one squat set and 2.95x in one squat-jump
set. That comparison is approximate because annotation envelopes include pauses.
Several other undercounts have near-1x periods, so harmonics do not explain every
error; peak suppression, projection, amplitude screening and boundaries also need
inspection. No period limits were changed to improve the reported totals.

## Verification and artifacts

- 222 tests passed; Ruff lint/format checks passed; `pip check` passed.
- 196 integrity checks passed: source versions, 18 raw stream hashes, models,
  annotations, all completed-fold artifact hashes, configured scan coverage and
  training isolation. The saved blind windows, sets, activity intervals and five
  CSV exports reproduce the baseline exactly.
- Resuming `--group r09` verified both completed representations, reused saved
  decisions and kept the same run ID. Plots were inspected for waveform, matching
  and baseline interpretation.

Local evidence is in `data/processed/exp-r1/runs/75cc9cfd21036b86/`:

- `manifest.json`, `integrity-review.json`: source/data/environment identity.
- `report.md`, `scores.json`, `review-summary.json`: per-recording results, exact
  and within-one denominators, conditional/end-to-end errors, abstentions and
  background false positives. Missed or wrongly named reference sets receive zero
  credited repetitions; uncertainty ranges remain ranges.
- `r*/{raw_axes_v1,joint_pca_v1}/`: templates/provenance, all candidate scores and
  rejection reasons, selected cycles, independent counts and assisted traces.
- `baseline-ablation.json`, `feature-summary.json`, `rotation_summary.json`,
  `fold_support.json`, `scan_coverage.json`: diagnostic evidence and limitations.
- `r09/overview.png`, `r09/baseline-ablation-5.png`, and alignment/counter plots.
- Public evidence: `data/processed/exp-r1/recofit/{source,report,summary}.json`.

The data and local manifests are intentionally ignored by Git. A fresh clone needs
the recordings plus its own dataset/annotation manifests; the checked-in config
points to the prepared manifests on this workstation. Exact native paths and package
versions are saved in the run manifest. Reference-count uncertainty and approximate
timing are retained in the annotation manifest.

To reproduce locally, from the repository:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[experiment]"
.\.venv\Scripts\python.exe -m polar_activity experiment inventory --config experiments/exp_r1.json
.\.venv\Scripts\python.exe -m polar_activity experiment run --config experiments/exp_r1.json
.\.venv\Scripts\python.exe -m polar_activity experiment report --run data/processed/exp-r1/runs/75cc9cfd21036b86
.\.venv\Scripts\python.exe scripts/review_exp_r1_results.py data/processed/exp-r1/runs/75cc9cfd21036b86 --recofit data/processed/exp-r1/recofit
.\.venv\Scripts\python.exe scripts/plot_exp_r1_alignment.py data/processed/exp-r1/runs/75cc9cfd21036b86 --group r09
```

The optional public run downloads a bounded source prefix on first use:

```powershell
.\.venv\Scripts\python.exe -m polar_activity experiment recofit --output data/processed/exp-r1/recofit --subjects 2
```

## Next three tasks

1. **Test the targeted repairs together:** gravity-relative feature evidence and
   stable-context pull-up excursions, retaining uncertainty and the existing impact
   cutoff. Evaluate every recording and known background interval before promotion.
2. **Repair boundary and phase handling:** preserve short sets and brief rests,
   search for observed outward-and-return cycles around candidate edges, and report
   incomplete attempts separately. Keep DTW as evidence; do not force all methods
   to agree with an expected count. Use saved peak/period traces to diagnose the
   independent counter before broadening its role.
3. **Improve rejection using existing ordinary-motion recordings**, then freeze a
   candidate analyser and perform one fresh mixed blind recording. Tune on existing
   data first; there is no reason to repeat strenuous reference sets now.

No untouched personal test set remains. Whole-recording exclusion does not establish
independent wearing sessions or new-person accuracy, and the small number of source
recordings prevents an independent inner calibration split. These limits rule out
declaring any tested approach a general recognition solution at this stage.
