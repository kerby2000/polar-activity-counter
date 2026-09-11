# EXP-R1 sources and method mapping

Inspected 2026-09-10/11. Python 3.11.15 on Windows. Installed research extra:
`tslearn==0.9.0`, `scipy==1.17.1`; existing `numpy==2.4.6` retained. The dependency
resolver selected compatible Windows wheels. Exact environment is in each run's
manifest and `data/processed/exp-r1/sources/dependency-resolution.json`.

| Source | Version inspected | Role / licence |
|---|---|---|
| [RecoFit data and official loader](https://github.com/microsoft/Exercise-Recognition-from-Wearable-Sensors/tree/fd4c44508c0f76118d8e8234a2832acafc64e767) | `fd4c44508c0f76118d8e8234a2832acafc64e767` | Separate detection, naming and counting; external continuous data. CDLA-Permissive-2.0. |
| [MM-Fit paper, section 4.4](https://vradu.uk/publications/UbiComp2020.pdf) | Strömbäck, Huang, Radu, 2020; DOI 10.1145/3432701 | Independent signal-processing counter implemented from the described algorithm. Paper retains author copyright. |
| [MM-Fit starter repository](https://github.com/KDMStromback/mm-fit/tree/e4b6b7e1fff68e3d93130ed087d23593c9016c94) | `e4b6b7e1fff68e3d93130ed087d23593c9016c94` | Context only; no licence file found in the inspected tree, no source copied. |
| [tslearn dtw_path](https://tslearn.readthedocs.io/en/stable/gen_modules/metrics/tslearn.metrics.dtw_path.html) | Package 0.9.0; repository head inspected `3f2daeb78e43f5ace07fbc4b79a660af91a555aa` | Shared multichannel path with Sakoe–Chiba band. BSD-2-Clause. Repository head is not asserted to be the release commit. |

The installed `dtw_path` signature supports `global_constraint` and
`sakoe_chiba_radius`. The installed `dtw_subsequence_path` accepts only subsequence,
long sequence and backend; the experiment does not pass unsupported constraints.
The recorded score is distance divided by square root of path length, checked
against the path's Euclidean cost. It is not a probability.

## Explicit adaptations

The MM-Fit-inspired branch standardises gyro axes, applies third-order
Savitzky–Golay smoothing, projects onto PCA, and suppresses peaks using spacing,
local autocorrelation and amplitude. Fixed choices absent from the paper are a
0.44-second smoothing window, a deterministic PCA sign, and minimum ACF overlap.
Period bounds come from training cycles; unsupported classes use the declared
0.5–6-second diagnostic bounds. Boundary peaks are not extrapolated. This is an
attributed adaptation, not an exact reproduction of the paper's benchmark.

DTW uses at most three actual training exemplars per supported class, common
modality scales, 64 points, and a radius-eight band. Physical amplitude and duration
remain acceptance inputs. Long cycles receive additional antialias filtering before
the 64-point representation. Four proper sign alternatives act jointly on ACC and
gyro in the PCA branch; nearly degenerate frames use a documented gravity-relative
fallback. Neither representation is anatomical calibration.

The stable-baseline diagnostic uses a normalised vector mean within a quiet
0.8-second region, rejects poor/ambiguous support, and retains dismount truncation.
It differs from Pro's median-vector prototype and is compared separately from the
unchanged existing-counter branch.

## Actual RecoFit format

The `multionly` release is a little-endian, compressed MAT-v5 cell matrix with
94 subject rows. The inspected data has seven annotation columns; the official
loader documents the first five. Additional columns are retained in the original
subset bytes but do not enter the counter. ACC is converted from g to mg; gyro
remains degrees/s. Subject/visit boundaries and original activity names are kept.
Jumping Jacks is not silently relabelled as the user's jump variant.

The importer reads the initial complete subject cells through a bounded HTTP range
and reconstructs a small MAT container for SciPy. The retained subset has its own
SHA256. The upstream ETag matches the pinned LFS object identity, but the entire
1,571,881,721-byte object was not downloaded or independently hashed. Provenance
records this distinction. Only the first visit of each of the first two subjects is
evaluated. Tap/setup intervals and unknown counts remain explicit; only known
exercise totals contribute count-error summaries. Data and source licence remain
under ignored `data/`, outside the source repository.

Public results are an assisted counting and continuous motion-proposal check,
not Polar transfer accuracy. No public observations are added to personal training.
