# Independent review: Polar upper-arm activity recognition failed its first mixed blind test

Please act as an independent wearable signal-processing and machine-learning reviewer. Audit the attached code and recordings, identify the dominant failure mechanisms with measured evidence, and recommend a practical implementation plan. Challenge the existing approach. Do not merely add thresholds that make this recording match its disclosed counts.

## Product and constraints

This is a personal, approximate exercise/activity counter for one person using a Polar Verity Sense on the left upper arm. Target activities are push-ups, pull-ups, squats, jumps with arms raised, walking, stairs, household movement, standing and sitting. An occasional off-by-one count is acceptable. The user has limited stamina for collecting repeated exercise datasets. Windows desktop and iPhone only; existing sensor-side recording and later BLE/USB download already work. Recognition can run after download; realtime inference is not necessary for the first useful version.

The available signals are three-axis acceleration in mg and three-axis angular velocity in degrees/s. Nominal sample rate is 52 Hz, measured packet-endpoint rate about 52.94 Hz. No video, foot sensor, joint angles or height measurements accompany these recordings. Do not recommend replacing the hardware or demanding many new strenuous recordings without first testing what can be recovered from the existing data.

## First mixed blind test: failure, preserved before disclosure

`blind-01` has 8,460 samples per IMU, about 159.825 seconds of common coverage. No detected timestamp gaps, duplicates, backwards timestamps or packet-frame discontinuities. It is a complete internally recorded session, not a BLE dropout. The original frozen prediction and code/model hashes are included.

The user disclosed this sequence only AFTER predictions were saved:

1. Five push-ups.
2. An everyday-activity break away from the exercise area.
3. Three pull-ups.
4. Walk to a staircase and ascend it.
5. Walk to a second staircase and descend it.
6. Return to the PC and stop recording.

Exact boundaries, walking step counts, stair tread counts and handrail use for THIS test were not supplied. Do not transfer the 18-tread count or handrail description from the earlier stairs reference to this test. Do not pretend that ordinary personal activities are identifiable from this sensor. Activity order is human ground truth; timestamps inferred using it are post-disclosure annotations, not blind predictions.

Frozen result: **no accepted exercise sets, no push-up or pull-up repetition counts, no stairs labels**. Final walking intervals were 55.5-59.5 s, 86.5-97.5 s and 105.5-115.5 s. Other periods were unknown/other movement/stationary. Thus both repetition sets and both stair traversals were missed at event level. Walking performance cannot be scored without time annotation. A zero accepted-set result is not a successful count of zero actual repetitions.

Diagnostic trace of the unchanged functions:

- Pre-gating classifier produced pull-up candidates at 20.5-28.5 s and 61.5-73.5 s, but the arm-excursion detector returned no qualifying cycles in either. Final labels became unknown.
- The generic cycle detector found 3 cycles at 102.2-105.1 s, 7 at 115.8-122.4 s and 4 at 123.3-127.1 s. Their set-level features matched the combined standing/sitting reference and were emitted as `other_movement`; the analyser excluded them from exercise counts. These are candidate motion cycles, not validated repetitions or steps.
- Many moving windows match the pooled standing/sitting reference. Do not interpret the reference match as demonstrated posture.

## Existing method, with code supplied

The model is a small hand-engineered nearest-reference classifier, not an extensively trained activity model. It contains 13 selected intervals from seven recordings, yielding 170 highly correlated four-second windows. An eighth development recording was used for earlier counting development but is not in the classifier references. Windows overlap at one-second steps; they are not independent trials.

`recognition.py`: intact ACC/gyro overlaps are antialias-filtered and resampled to 25 Hz. Each four-second window yields 15 features: mean ACC XYZ/500, std ACC XYZ/400, std GYRO XYZ/80, ACC magnitude percentiles 10/50/90 divided by 500, log1p ACC magnitude std/2, log1p norm of gyro-axis stds/2, and leading gyro PCA variance fraction. Class distances use nearest examples with a max-distance cutoff of 0.85. Quiet thresholds and isolated-window smoothing are applied. Sensor-frame means and axis spreads are orientation dependent. There is no learned sequence model or robust session calibration.

`counter.py`: generic periodic return detection at 25 Hz with eight-second windows, at least three cycles, periods approximately 0.48-2.64 s, shape/pose checks and short-pause merging. Its exact settings and acceptance logic are in the code.

`analyser.py`: classifies detected periodic bouts and rejects those classified as walking, stairs, household, pooled standing/sitting, other movement, stationary or jump. Separate jump and pull-up paths exist. Exercise labels without a corresponding accepted motion set are changed to unknown. This creates coupled classification/counting rejection points.

`motion_quality.py`: short pull-ups are sought via smoothed ACC direction departures from a median baseline in the preceding two seconds. Open/close thresholds are depth 0.25/0.20; strong peak depth >=0.5 (60 degrees), duration 0.8-8 s. A weaker >=45-degree event can follow two strong events. A low-g or rotating-impact cutoff attempts to exclude dismount. At least two accepted events are required for a set. Assess whether baseline placement, short bursts, motion contamination or the early cutoff destroys valid sets; measure before concluding.

Jumps use >2400 mg smoothed impact pulses, preceding low ACC magnitude, and subsequent settling, then pair impacts under a convention calibrated from two user-confirmed sets of 10. That convention is empirical, not proof of two physical impacts per jump. Audit double counting and cadence assumptions.

Stairs are only a pooled class. Up/down and tread/footfall counting are not implemented. Standing/sitting are deliberately pooled. Upper-arm direction departure is a motion-consistency proxy, not anatomical range, chin clearance, technique or safety scoring.

## Existing data and validation caveats

- exercise-02: push-ups; user count uncertain between 12 and 13; development output 13. Recording ends early, but the set is present.
- exercise-03: household/background; no exercise sets in development output.
- exercise-04: push-ups, user says 14 seems right; output 14 with a short pause. Not a classifier reference, but already used in counting development, so not an untouched holdout.
- exercise-05: two sets of 10 jumps with arms raised, first before keyboard labelling, followed by walking. Outputs 10+10 under paired-impact convention.
- exercise-06: squats, then standing and sitting; development output 10 squats. Verify provenance in accompanying reference notes rather than assuming every automatic output is independent human count truth.
- pullups-01: two total repetitions confirmed by user. Output two attempts, with one return flagged unobserved by the latest cutoff.
- pullups-02: user reports almost four, last not clean; output four attempts, three returned and one reduced/unclosed at 58% of median peak excursion. This is not verified anatomical form.
- stairs-01: four passes, every tread up/down, then alternate treads up and a different final descent; about 18 treads per original flight reported. Final descent no handrail; first descent handrail use not explicitly confirmed. All four reference cores now replay as stairs, but approach/return movement is still poorly classified.

The earlier 198 passing software tests verify selected behaviours and regressions; they do not establish real-world recognition accuracy. Much of the positive evidence is training/development replay. Please keep blind-01's original failed result intact even if using it for diagnosis. Any new fit using this recording turns it into development data and needs a new untouched test later.

## Requested work and deliverables

1. Reproduce the frozen blind result. Audit units, timestamp alignment, resampling, window selection, feature scaling/class imbalance, reference coverage, sensor-orientation sensitivity, cadence and acceptance gates. Distinguish measured causes from hypotheses.
2. Inspect reference and blind waveforms. Use the disclosed order to propose approximate post-disclosure boundaries for five push-ups, three pull-ups and two stair traversals, marking uncertainty. Do not force an algorithm to return 5 or 3 by supplying these totals as its inputs.
3. Run small, reproducible offline ablations where possible: orientation-sensitive versus invariant/gravity-relative features; retaining candidate cycles independently of activity labels; baseline and impact-cutoff diagnostics; temporal context. Report failures as well as improvements. Do not perform broad threshold search against blind-01 then call it blind accuracy.
4. Recommend a small-data architecture: compare a hierarchical activity/motion detector plus class-specific counting, template/DTW approaches, and conventional feature models before proposing a deep network. Explain which information norms discard and which pose information can safely be retained. Consider unknown/background handling, very short sets, brief pauses and changed sensor orientation.
5. Propose honest evaluation using whole recordings/sessions as groups, separating segmentation, activity identity and count error. Avoid leakage from overlapping windows. Specify a realistic first acceptance target without inventing measured accuracy.
6. Give a prioritised implementation plan, concrete module changes, and minimal additional reference collection only if unavoidable. Separate feasible practical counting from unsupported exercise-form or stair-height claims.

Please return an evidence-based diagnosis, reproducible analyses/plots, and a concise recommendation. If you cannot execute the archive or inspect a file, state that explicitly. Do not claim to have run experiments you did not run. Do not modify original evidence or contact any third parties.

## Attachment layout and reproduction

The archive includes `src/polar_activity`, `pyproject.toml`, targeted tests, reference documentation, original ACC/GYRO CSVs for the eight development sessions and blind-01, minimal derived metadata sufficient for analysis, the unchanged frozen model/reference specification, and the blind prediction/diagnostic artifacts. Metadata is a selected analysis-only export; BLE logs, heart-rate data and offline encryption/session credentials are excluded.

From the extracted archive root, use Python >=3.11 and compatible installed dependencies (see pyproject.toml; installing the package may require network access). Then:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path('src').resolve()))
from polar_activity.analyser import analyse_session
result = analyse_session(
    Path('data/raw/blind-01'),
    Path('data/models/personal-evaluation-20260910.json'),
    Path('review-reproduction/blind-01'),
)
assert result['sets'] == []
```

Do not overwrite the supplied `frozen-predictions` folder. Original model SHA256: `9d3327191446cc38c5de56f2027a5edd9c238cafd5ccf131f9009272e4550076`.
