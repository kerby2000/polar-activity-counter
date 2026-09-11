# Initial signal analysis: the first push-up set

The historical plots and detailed data artifacts referenced below remain local
under ignored `data/`. See [Examples](examples.md) for the published blind figures
and [the manual](user_manual.md) for current installation and analysis.

2026-09-10. **Phase 7 exploratory analysis of real data; no trained classifier or validated accuracy claim.**

Follow-up: automatic set boundaries and complete-motion-cycle counting are now implemented separately. See [automatic_counting.md](automatic_counting.md). This document preserves the earlier exploratory analysis with its manually selected interval.

The recording contains a strong, regular movement pattern. Both selected sensor axes yield **13 candidate events** in a manually selected 20-second interval, with approximately **1.49 seconds between events**. This supports proceeding with a simple repetition-counting prototype at the existing sample rate. It does not establish automatic exercise recognition or reliable detection of set boundaries.

## Recording and label provenance

Source: local `data/raw/exercise-02`, session `3e8ceca1-1300-4cf3-99a6-186661e0e35d`. Subject Sergey; position `upper_arm_left`. The original label is push-up, set 001, **30.594–76.797 seconds**, **46.203 seconds**, manually entered count **12**. On 2026-09-10 Sergey subsequently said he may have miscounted and performed 13. We retain the original CSV and record that uncertainty here; the label is not independent confirmation of the algorithm's result.

The label contains 2,446 ACC and 2,446 gyro samples, plus 46 HR readings. The complete saved session contains 4,223 ACC, 4,200 gyro and 80 HR values. All 197 saved raw packets were previously replayed and verified against the CSVs. There are no detected sample gaps, duplicate timestamps or backward timestamps. Device packet endpoints indicate approximately 52.943 Hz in both streams, configured at 52 Hz. Original timestamps and sensor values remain unchanged. Absence of detected gaps does not prove zero radio packet loss.

The software was deliberately tested without requiring any additional exercise from Sergey.

## What the traces show

The following boundaries are approximate visual interpretations on the original session clock, not automatically detected or video-verified movement phases:

| Time | Observation |
|---|---|
| 30.6–37.5 s | Very little movement after the start marker |
| 37.5–47.5 s | Large and irregular movements, consistent with preparation/position changes |
| **47.5–67.5 s** | Sustained, regular oscillation; selected for exploratory counting |
| 67.5–73 s | Further irregular movement, consistent with finishing/repositioning |
| 73–76.8 s | Low movement before the end marker |

Full labelled interval, axes and magnitudes (local artifact: `data/processed/exercise-02-analysis/overview.png`)

The manually selected quiet reference is **31–36 seconds**. Acceleration magnitude is **1007.64 ± 3.00 mg**, consistent with a roughly stationary sensor under gravity. Gyro magnitude is **2.83 ± 0.09 degrees/s**; this suggests a stationary offset worth estimating before any future gyro integration. These observations are not a formal calibration.

## Signal statistics

Values below use original samples in the selected 47.5–67.5 s interval: 1,059 samples per stream. Standard deviations use the population convention. Full-label and quiet-interval statistics are also saved in analysis.json (local artifact: `data/processed/exercise-02-analysis/analysis.json`).

| Signal | Minimum | Maximum | Mean | Std |
|---|---:|---:|---:|---:|
| ACC X, mg | -1464 | 251 | -629.78 | 352.18 |
| ACC Y, mg | 95 | 1307 | 720.41 | 249.39 |
| ACC Z, mg | -1289 | 318 | -250.48 | 156.10 |
| ACC magnitude, mg | 596.57 | 1886.81 | 1081.07 | 142.27 |
| Gyro X, degrees/s | -95.27 | 93.87 | -3.32 | 41.39 |
| Gyro Y, degrees/s | -257.60 | 158.27 | 0.39 | 43.03 |
| Gyro Z, degrees/s | -150.85 | 158.34 | -1.31 | 85.44 |
| Gyro magnitude, degrees/s | 4.06 | 278.74 | 96.62 | 39.28 |

ACC X and gyro Z have the largest axis standard deviations in the selected interval and show clear periodic patterns. These are sensor-coordinate axes for this recording, not universal anatomical directions. Changes in armband orientation can change which axes are useful. Magnitudes are supplied as well, but discarding the sign can introduce multiple peaks per movement cycle.

The full label includes much larger transients: ACC axes reach -5614 to +7588 mg across the three axes, and gyro axes -798 to +701 degrees/s. The irregular preparation/finishing activity strongly changes whole-label statistics. No recorded ACC axis exceeds the configured ±8000 mg range; magnitude can exceed 8000 mg without any individual axis doing so.

## Counting experiment and periodicity

The method does not receive the manually entered repetition count. It uses a derived uniform time grid at the measured 52.943 Hz, linear interpolation between intact samples, an approximately 0.18-second centered moving average, and demeaning. It selects local maxima above a fixed height with a minimum separation of 0.8 seconds. The chosen signals are positive gyro Z and negative ACC X; those signs/axes and the analysis window were selected by inspection of this recording.

| Measure | Gyro Z | Negative ACC X |
|---|---:|---:|
| Candidate events | **13** | **13** |
| Median event interval | 1.492 s | 1.483 s |
| Minimum–maximum interval | 1.398–1.681 s | 1.341–1.606 s |
| Dominant periodogram bin | 0.700 Hz | 0.700 Hz |
| Autocorrelation lag | 1.492 s | 1.492 s |
| Autocorrelation at that lag | 0.852 | 0.742 |
| Naive count over the entire label | 21 | 19 |

Candidate events numbered on both signals (local artifact: `data/processed/exercise-02-analysis/candidate_peaks.png`)

The two axes naturally peak at different parts of the movement. Agreement in event count does not imply their peaks should occur simultaneously. At this cadence the sensor supplies about **79 samples per cycle**. There is no evidence from this set that a higher sample rate is necessary for counting this motion.

Spectral analysis uses the demeaned raw interpolated axes, a Hann window, a one-sided periodogram without zero padding, and frequency bins about 0.050 Hz apart. Autocorrelation is normalized by its zero-lag value, without lag-dependent debiasing. The dominant bin is approximate; the observed intervals provide the more direct cadence estimate.

Power spectrum and autocorrelation (local artifact: `data/processed/exercise-02-analysis/periodicity.png`)

Sensitivity check: for each axis, all **27 combinations** of moving-average widths 0.12/0.18/0.25 s, minimum peak distances 0.65/0.8/1.0 s, and three height thresholds returned 13 events in the same manually selected window. Heights were 25/40/60 degrees/s for gyro and 80/140/200 mg for ACC. This demonstrates local parameter stability on this set, not generalization to other sets or reliable automatic segmentation. Individual candidate times and all settings are saved in the JSON and candidate CSV (local artifact: `data/processed/exercise-02-analysis/candidate_peaks.csv`).

## What is established, and what remains

**Established:** upper-arm data from this session contains a clearly visible repeated movement; a simple exploratory method finds 13 consistent candidate events; concurrent HR did not prevent intact IMU capture within the set.

**Not established:** whether every candidate corresponds to a completed push-up; objective accuracy against independently verified ground truth; distinguishing push-ups from pull-ups, squats, jumps or daily activities; robustness to armband orientation; automatic selection of the exercise interval. The user's clarification makes 13 plausible, but the original uncertain count must not be used to claim a perfect score.

The immediate algorithmic work should detect sustained periodic motion and track a full movement cycle, explicitly handling entry and exit movements. Applying a peak detector to the full label gives 19–21 events here, so threshold tuning alone is insufficient. Keep the large preparation/finishing motions as useful examples of movements that should not be counted, with their interpretation marked provisional.

Before claiming general accuracy, test against separate labelled sets and normal movement. One set is insufficient for model training and evaluation; any later split must keep whole sets/sessions separate. No further push-ups are needed to complete this analysis.

## Reproduce locally

From the repository's PowerShell terminal:

```powershell
.\.venv\Scripts\python.exe scripts/analyze_set.py data/raw/exercise-02 --output data/processed/exercise-02-analysis --set 001 --start 47.5 --end 67.5 --quiet 31 36
```

The script uses the project's existing NumPy/matplotlib dependencies. Derived outputs stay under ignored `data/processed`; original CSVs and labels are never modified. The report's local figure links require that private dataset/output directory and can be regenerated with the command above. `analysis.json` includes SHA-256 hashes of the input files.
