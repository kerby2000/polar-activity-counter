# Future work

Status: 11 September 2026. These are priorities, not delivered features or promised
dates. Online capture, sensor-memory recording, BLE/USB download, the adaptive
analyser and the first successful fresh mixed blind test are implemented.

## Next: establish useful everyday reliability

| Work | Why | Evidence of completion |
|---|---|---|
| More held-out mixed sessions | One successful blind test does not cover different orders, tempos and short rests | Freeze predictions first; report missed/extra sets and per-set count error across sessions |
| Longer ordinary-movement recordings | Short household checks under-sample false counts | Independently identify background intervals and report false bouts/reps per observed background hour |
| Additional reference recordings per class | Holding out the only squat/jump source removes that class from training | At least two separate sources where practical, with whole-recording exclusion and class coverage reported |
| Better unknown/rejection decisions | Everyday movement can resemble a small exercise set | Reduce known false positives without losing already-supported short sets |
| More placements and people | Current evidence is one person's left upper arm | Separate subject/placement evaluation; document recalibration needs instead of assuming transfer |

Normal-use recordings can provide much of this coverage. Repeated strenuous sets
should be reserved for a concrete unresolved question. Approximate user counts are
useful; ambiguous counts and questionable attempts should remain marked uncertain.

## Make it easier to use

- A guided setup and reference-recording workflow, with visual interval selection.
- A compact review screen for sets, counts, pauses and incomplete attempts.
- Clearer separation of timeline class estimates from accepted exercise sets.
- A versioned model export/import flow that explains person and placement coverage.
- A downloadable demonstration dataset/model with explicit consent and licensing;
  the current public examples are charts, not a bundled universal model.
- A live count display and eventual mobile workflow. Neither is implemented by the
  current CLI's “online” recording mode.

## Make long sessions measurable and manageable

- Benchmark raw ACC/GYRO memory use and battery life over longer recordings.
- Measure end-to-end BLE and USB transfer speed for large files, including retries
  and decoding time. The existing 14.3 KiB/s USB result is only a small-file test.
- Support bounded-memory decoding/analysis and review the current 32 MiB transfer
  limit before claiming full-day support.
- Add deliberate sensor-memory cleanup only after verified local copies exist,
  with an explicit user action and a clear view of which files will be removed.
- Improve reconnect/transfer recovery while preserving original data and source
  ownership; do not silently combine unrelated sessions.

## Research directions

Optional 20 Hz MAG capture, export and plots are implemented. Investigate orientation
fusion and correlations across activities and locations, with calibration and
magnetic-disturbance handling before using heading. Keep location/background
recordings held out so a metal exercise bar does not become an activity shortcut.
See [magnetometer experiments](magnetometer.md).

HR capture and display are implemented for online and offline sessions. Next
compare motion-only recognition with HR-assisted stair-direction estimates on
held-out ascent/descent recordings, including recovery after other exercises.
Account for HR lag and approximate offline timing; do not infer elevation from
orientation or magnetic heading. See [sensor capabilities](heart_rate_and_elevation.md).

Stair direction/step counts, sitting versus standing, more jump styles and more
exercise types need new evidence. An upper-arm sensor may not provide enough
information for some of these distinctions. Technique assessment would require
independent anatomical ground truth, such as reviewed video or additional sensors;
relative arm excursion alone is insufficient.

Larger learned models or improved template matching can be compared once the data
support them. Any candidate should beat the current method on independent sessions
and ordinary movement, with its data, preprocessing and evaluation rules recorded.
Randomly splitting overlapping windows would overstate the available evidence.

## Repository and reproducibility

Keep the installation smoke test and hardware-free CI current. Preserve frozen
predictions when improving the method, publish clear comparisons, and distinguish
public artifacts from local private recordings. Select an explicit project/data
license before presenting the repository as a generally reusable open-source
dataset or distributing third-party model/data bundles.
