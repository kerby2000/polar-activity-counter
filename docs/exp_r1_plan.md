# EXP-R1 execution plan

Status: **EXPERIMENT_EXECUTED**. All five stages below completed. See the
[decision report](exp_r1_report.md) for measured results and the next three tasks.
Branch: `feature/exp-r1-dtw-mmfit`. Starting commit:
`d68065257aec33cc7e2852855afad25a661c6e01`, with the existing local acquisition,
offline/USB and analyser changes preserved. The starting source/test/model snapshot
and working-tree inventory are saved under `data/processed/exp-r1/baseline/`.

1. **Freeze and reproduce.** Inventory the nine usable personal recordings, source
   hashes and annotation provenance. Reproduce all frozen baseline decisions; retain
   the original blind failure. The starting 198 tests passed (16.81 seconds).
2. **Instrument and diagnose.** Persist class evidence, motion candidates, rejection
   reasons and accepted counts separately. Compare frozen/no-mean/gravity-relative
   features and a stable-context pull-up baseline without modifying the old default.
3. **Implement experimental methods.** Training-only complete-cycle exemplars,
   bounded sliding proposals, constrained multichannel DTW with raw/PCA variants,
   deterministic overlap resolution, and an independent MM-Fit-inspired counter.
4. **Execute controlled comparisons.** F0 frozen, D1 interval/class-assisted counting,
   D2 interval-assisted naming, E1 automatic DTW plus existing counter, E2 identical
   proposed sets plus MM-Fit counter. Whole-recording development folds, paired
   coordinate rotations, boundary stress, and a separate small RecoFit reference run.
5. **Verify and report.** Test isolation, numerical invariants, gaps, ambiguous/partial
   events, provenance and deterministic replay. Save predictions before scoring;
   report missed/wrong sets and background false positives as well as counts.

The downloaded Pro task is retained at
`data/processed/exp-r1/CODEX_EXP_R1_Polar_Recognition_Experiment.md` and the independent
review at `data/processed/blind-01-evaluation/pro-review-results/`. Their proposals
are combined here with measured local evidence. No new physical recording is needed.

## Initial failure map

- `recognition.features/classify`: absolute mean-ACC XYZ and axis spread dominate
  matching without registration; nearest-reference scores are distances, not probabilities.
- `recognition.predict_windows`: four-second evidence, one-second emissions and an
  isolated-label smoother. `analyser.analyse_session` subsequently erases exercise
  identity when count evidence is absent.
- `counter._windows/_chains`: eight-second seeds, period cap `size // 3`, and at least
  three matching cycles. Short and slow sets can fail different gates.
- `analyser._exercise_sets`: classifies variable-length bouts against four-second
  features and suppresses background/locomotion proposals.
- `motion_quality.arm_excursions`: baseline from two seconds before a classifier
  edge; vector already normalised. Threshold occupancy must be 0.8–8 seconds.
  The existing dismount cutoff protects unfinished attempts but is not the active
  cause of either frozen blind miss, according to the independent review.

## Evaluation contract

Use a fixed initial experiment configuration, never the exposed blind counts to
choose thresholds. Unknown counts stay unknown; unlabelled time is not background.
Approximate waveform annotations remain approximate, and historical blind-01 is
regression/development only. All-session training replay and whole-recording holdout
results are separate. Unsupported single-source classes have no generalisation claim.
Do not promote a new default analyser from these results. The deliverable is an
executed experiment and at most three evidence-backed next recommendations.
