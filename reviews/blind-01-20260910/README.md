# Polar recognition: first mixed blind test and independent review

This is a review snapshot, published with the recording owner's permission. It does not replace the application on the default branch. The frozen analyser missed five push-ups, three pull-ups and two stair traversals in `blind-01`. The recording itself has intact ACC/gyro data. Some walking was predicted, with timing accuracy not yet scored.

## Review entry points

- [Full independent-review request](evidence/REVIEW_REQUEST.md)
- [Download the complete evidence archive](pro-review-package.zip?raw=true) (3.8 MB)
- [Ground truth and failed-result comparison](evidence/data/processed/blind-01-evaluation/ground-truth-comparison.md)
- [Frozen prediction](evidence/data/processed/blind-01-evaluation/frozen-predictions/analysis.json)
- [Signal and predicted-activity plot](evidence/data/processed/blind-01-evaluation/frozen-predictions/analysis.png)
- [Gate diagnostic trace](evidence/data/processed/blind-01-evaluation/diagnostic-trace.json)
- [Pre-disclosure evidence hashes and quality](evidence/data/processed/blind-01-evaluation/manifest.json)

## Code and data

- [Recognition features and classifier](evidence/src/polar_activity/recognition.py)
- [Activity/count integration and rejection gates](evidence/src/polar_activity/analyser.py)
- [Periodic cycle detector](evidence/src/polar_activity/counter.py)
- [Pull-up excursion/baseline/cutoff logic](evidence/src/polar_activity/motion_quality.py)
- [Frozen personal model](evidence/data/models/personal-evaluation-20260910.json)
- [Explicit reference intervals](evidence/data/models/references.json)
- [All nine recordings](evidence/data/raw): eight development sessions and the mixed blind test; original ACC and gyro CSVs, plus selected analysis-only metadata.
- [Blind acceleration CSV](evidence/data/raw/blind-01/acc.csv)
- [Blind gyro CSV](evidence/data/raw/blind-01/gyro.csv)
- [Prior reference analysis and limitations](evidence/docs/pullups_stairs_validation.md)

Heart-rate measurement files, BLE connection logs, and offline session/encryption credentials are excluded. Metadata is a selected export; model/code/IMU files retain their original bytes for reproducibility. The archive contains a per-file SHA256 index.

The full request includes the reproduction entry point. Work from the `evidence` directory or extracted archive root and write rerun outputs to a new directory. Earlier software test results and successful training/development replays are not independent recognition accuracy. Ground truth was disclosed after the original predictions were saved; any future tuning on this session is development, not a new blind success.
