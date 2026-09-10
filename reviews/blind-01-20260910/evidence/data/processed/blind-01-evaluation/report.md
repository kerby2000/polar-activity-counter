# blind-01: frozen blind prediction

Predictions saved 2026-09-10T20:30:48.155922+00:00 before disclosure of the user's activity order and repetition counts. The model and analyser match the pre-test validation hashes. This session is not in the model references; no target labels or notes were inspected. No retraining or threshold changes were made.

## Result

**No exercise sets accepted; no reliable exercise repetition counts produced.** This does not establish that no exercises were performed. The model predicts walking at 55.5-59.5 s, 86.5-97.5 s, and 105.5-115.5 s. Walking steps are not counted by this version.

Time is measured from the beginning of the stored recording. Four-second classifier windows make transitions approximate; the first and last roughly two seconds have no full-window classification.

| Session seconds | Frozen final classification |
|---|---|
| 1.5-20.5 | other movement |
| 20.5-28.5 | unknown |
| 28.5-44.5 | other movement |
| 44.5-54.5 | stationary |
| 54.5-55.5 | other movement |
| 55.5-59.5 | walking |
| 59.5-77.5 | unknown |
| 77.5-86.5 | other movement |
| 86.5-97.5 | walking |
| 97.5-105.5 | other movement |
| 105.5-115.5 | walking |
| 115.5-151.5 | other movement |
| 151.5-157.5 | stationary |

## Recording quality

- ACC: 8,460 samples; 159.828 seconds.
- Gyro: 8,460 samples; 159.825 seconds.
- Common coverage: 159.825 seconds (about 2 minutes 40 seconds).
- Packet endpoint rates: about 52.938 Hz, configured 52 Hz.
- No detected gaps, duplicate or backward timestamps, or packet-frame discontinuities. No inferred missing samples; without sequence counters this is not proof of zero loss.
- Source status: complete; analyser warnings: none.

## Why no repetitions were reported

The unchanged classifier initially matched pull-up references around 20.5-28.5 seconds and 61.5-73.5 seconds. The arm excursion detector found no qualifying cycles in either interval, so those labels became unknown. These are rejected, tentative matches, not confirmed pull-ups.

The generic periodic detector found candidate groups of 3, 7, and 4 cycles around 102.2-105.1, 115.8-122.4, and 123.3-127.1 seconds. Their features matched the combined standing/sitting reference category, which this analyser outputs as other movement. They were therefore excluded from exercise counts. They are not validated repetitions or steps, and the match does not prove standing or sitting.

The raw signals contain substantial movement. The bottleneck is activity recognition and acceptance of motion cycles, not an empty recording. Sensor orientation, movement style, and narrow reference coverage are hypotheses to investigate after the actual activities are disclosed; this test alone does not establish the cause.

## Evaluation status

Ground truth remains undisclosed. Activity accuracy and count error cannot yet be scored. Preserve this result even if the analyser is subsequently improved using the recording; a tuned rerun would be a development result, not this blind result.

Please provide the actual activity order and approximate counts, with any remembered pauses. Approximate timings are useful if known, but not required. No repeat recording is needed to review this test.

## Evidence

- [Frozen machine output](frozen-predictions/analysis.json)
- [Frozen signal and activity plot](frozen-predictions/analysis.png)
- [Source, model and code hashes; signal quality](manifest.json)
- [Unchanged-function diagnostic trace](diagnostic-trace.json)

Frozen model SHA256: `9d3327191446cc38c5de56f2027a5edd9c238cafd5ccf131f9009272e4550076`.
