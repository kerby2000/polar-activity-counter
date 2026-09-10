# Automatic set boundaries and complete-cycle counting

For exercise names, jump counting and two-repetition pull-up estimates, use the new [`analyze` command](personal_analysis.md). This page documents the preserved generic `count` command and its v2 regression baseline.

Implemented 2026-09-10; updated for brief rests the same day. Current algorithm: **`periodic-return-v2`**. This is an offline counter for saved ACC/gyro recordings. It scans the entire recording and does not read activity labels, keyboard markers, HR or expected repetition counts.

The [unchanged v1 validation on the new recordings](exercise_03_04_validation.md) found zero household counts and 14 push-up motion cycles in two groups separated by a brief pause. Sergey subsequently said 14 seems right and requested accounting for short breaks. That is a provisional retrospective reference, recorded separately from the raw labels.

## Short rests within a set

Version 2 groups already accepted motion blocks across a quiet rest of up to **3 seconds**, provided that movement direction, waveform and cadence remain compatible. It does not change the cycle detector, lower the minimum qualifying chain length, infer repetitions during a rest or bridge missing sensor data. A short block that never qualified on its own is still not rescued by this grouping stage.

On **exercise-04**, the result is now **one set of 14 cycles, 30.593–52.993 s**, including the **1.240 s** rest between its original 6-cycle and 8-cycle blocks. Every accepted cycle boundary and fit score remains exactly equal to v1. Exercise-03 still gives zero counts, exercise-02 still gives 13, and the three diagnostic recordings still give zero. Three coordinate rotations of exercise-04 also preserve one set and 14 cycles.

```powershell
.\.venv\Scripts\python.exe -m polar_activity count data/raw/exercise-04
```

The report now includes `pauses.csv`; the plot marks joined rests in amber. Frozen v1 results remain in the original validation folders. [The v2 result](../data/processed/exercise-04-pauses/counts.json), [plot](../data/processed/exercise-04-pauses/automatic_count.png), [comparison checks](../data/processed/exercise-04-pauses/validation.json) and [provisional reference](../data/processed/exercise-04-pauses/reference.json) are saved separately.

Joining requires ACC and gyro direction agreement of at least 0.9, centered 3D waveform correlations of at least 0.85 across the neighboring cycles, and a reference-period ratio of 0.65–1.55. The middle of the rest must have gyro magnitude at the 95th percentile no greater than 15 degrees/s; integrated rotation over the gap must be no greater than 20 degrees, and changes in resting acceleration must remain within 150 mg. Boundary margins of at most 0.2 s allow residual motion/filter settling. These are conservative development defaults, not validated universal definitions of an exercise set. Two intentionally separate sets with the same movement and a sufficiently brief quiet rest may still be grouped.

The grouping step runs separately within each intact ACC/gyro overlap. It joins only consecutive, already accepted blocks and retains their local reference axes/windows under `motion_blocks`. Each cycle identifies its block. This avoids describing a merged set with a single reference axis when the sensor direction or cadence changes slightly.

On the first push-up recording it automatically finds **one set, 47.48–67.12 seconds, with 13 complete motion cycles**. Preparation and finishing movements are excluded. Exercise type remains `unclassified_repetitive_motion`: the dataset is not yet sufficient to distinguish push-ups, squats, walking or other recurring movements reliably.

## Run it

From the local repository in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m polar_activity count data/raw/exercise-02
```

Expected summary for this recording:

```text
Automatic repeated-motion count (exercise type unclassified)
Set 001: 47.48-67.12s | 13 complete cycles
Total: 13 complete cycles
```

There is also a warning that the original session ended early. That preserves the disconnect history; it does not invalidate the intact detected interval.

By default, derived results go into `SESSION/automatic-count/`. To choose another output folder:

```powershell
.\.venv\Scripts\python.exe -m polar_activity count data/raw/exercise-02 --output data/processed/exercise-02-auto
```

Optional `--json` prints the machine-readable report; `--no-plot` skips plot generation. Re-running replaces the generated count files in that output directory. Original input CSVs, labels and metadata are not modified. No additional dependencies are needed.

This command processes a saved recording. The current recording command continues to collect raw data; keyboard labels remain useful for evaluation but are not needed by the counter. A recording made with `record --no-interactive` can also be counted afterwards.

## Result on the actual recording

![Automatic boundaries and complete motion cycles](../data/processed/exercise-02-auto/automatic_count.png)

The upper panels show the whole saved session. Shading isolates the automatically detected set; the lower panel shows the automatically selected gyro projection with each accepted cycle shaded and numbered. Plot boundaries are the detector's result, not the previous manual analysis interval.

| Item | Result |
|---|---|
| Source session | `exercise-02`, `3e8ceca1-1300-4cf3-99a6-186661e0e35d` |
| Automatic set start | 47.483872 s |
| Automatic set end | 67.123872 s |
| Detected span | 19.640 s |
| Accepted complete cycles | **13** |
| Manual labels used | **No** |
| Original manual label, comparison only | 12; Sergey subsequently said it may have been 13 |
| Exercise class | Unclassified repeated motion |

The earlier exploratory script used a manually chosen 47.5–67.5-second interval and fixed axes. This implementation automatically finds an interior periodic window, derives useful sensor directions from the signals and extends to the first/last accepted complete cycles. No 47-second offset, target count of 13 or push-up-specific time interval is built into it.

Source CSV hashes, settings, chosen axes, internal reference window, every cycle boundary and fit measurements are saved in [counts.json](../data/processed/exercise-02-auto/counts.json). Original source files were hash-checked after the runs and remain unchanged.

## How it works

1. **Validate and split the data.** Both streams must contain finite XYZ values, increasing timestamps and a typical sample rate of at least 20 Hz. The CSV time axis is checked against integer device timestamp differences. Gaps above 1.5 times the median sample interval split each stream; only overlapping intact parts are analyzed. The algorithm never interpolates or counts a cycle across such a gap.
2. **Prepare a derived time grid.** Each intact native stream receives a symmetric Blackman-windowed sinc low-pass filter, approximately 0.8 seconds long with a 6 Hz cutoff, before interpolation to 25 Hz. A five-sample centered moving average then reduces residual noise. Filtering happens before sample-rate reduction so high-frequency vibration does not turn into a false slow rhythm through aliasing. All processing is on derived arrays; the saved measurements are unchanged.
3. **Find sustained periodic motion.** Scan eight-second windows every 0.48 seconds. Principal components find a dominant direction separately for ACC and gyro. Require sufficient movement, a dominant gyro direction, and matching periodicity in both signals. The searched period range is approximately 0.48–2.64 seconds, with at least three periods represented in a window.
4. **Learn the local movement range.** Adjacent qualifying windows form a candidate region. The strongest window supplies sensor directions, typical period and two separated ACC bands describing opposite ends of the movement. A quiet one-second gyro interval supplies a bias estimate where available; otherwise the periodic-window mean is used and that fallback is recorded.
5. **Require an excursion and a return.** A candidate must visit one ACC band, reach the opposite band and return to its starting band. It must also have rotation in both directions, observed near-neutral/bracketed-zero gyro boundaries, appropriate duration and amplitude, and limited net rotation. Unobserved beginnings/endings are not extrapolated.
6. **Reject isolated or inconsistent movements.** Candidate waveforms are compared with local full-cycle templates from the periodic window. Require at least three consecutive accepted cycles with consistent timing. Opposite phase origins can describe the same set; overlapping alternatives are resolved once using complete coverage and waveform fit, rather than adding both counts together.
7. **Group compatible blocks across a short rest.** Apply the observed-rest and movement checks above after selecting complete cycles. Preserve the rest interval separately and keep all original cycle boundaries.

Defaults are recorded under `settings` in `counts.json`. Seed requirements include gyro standard deviation ≥15 degrees/s, ACC standard deviation ≥40 mg, dominant gyro variance fraction ≥0.65, gyro lag correlation ≥0.8 and ACC lag correlation ≥0.7. Accepted cycles need ACC/gyro waveform correlations ≥0.8/0.85. The signal bands are inset by 15% of the seed's 10th–90th percentile range; accepted excursions span 65–170% of that reference range. Duration, rotation and cadence checks reject large irregular entry/exit movements. These are development heuristics, not learned exercise-class probabilities.

**“Complete” means a complete observed out-and-back signal cycle.** It does not certify push-up depth, technique or anatomical range of motion. This counter cannot determine exercise form from the available evidence. Partial one-way movements and incomplete recording edges are excluded; smaller out-and-back movements may still qualify if they meet the learned signal criteria.

## Output files

| File | Contents |
|---|---|
| `counts.json` | Total, detected sets and cycles, source hashes/status, algorithm settings, signal directions, timing/shape evidence, gaps and limitations |
| `sets.csv` | One row per detected set, automatic boundaries and complete-cycle count |
| `cycles.csv` | One row per accepted cycle, start/end/duration and waveform similarities |
| `pauses.csv` | One row per rest used to join previously separate motion blocks; no repetitions added |
| `automatic_count.png` | Whole-session detection overview and numbered cycle intervals |

The reported set interval is from the beginning of its first accepted complete cycle to the end of its last accepted complete cycle. It excludes waiting and preparation time. `near_recording_or_gap_edge` flags a set that may be part of a longer interrupted bout; its count covers only observed complete cycles. A 40 ms grid is a processing resolution, not a claim of 40 ms physical boundary accuracy: filtering and the original unknown BLE/host alignment still affect timing.

Similarity values describe waveform agreement with a local template. For example, a value near 0.98 is **not 98% counting accuracy**.

## Validation evidence

The original v1 suite had 80 tests, including 19 counter tests. The pause update adds nine regression cases, bringing the suite to **89 tests**, including 28 counter tests. Those tests use independent synthetic rotation/gravity signals with specified numbers of cycles, not a copy of the detector's output.

New checks cover several short-rest durations, repeated rests within one set, byte-equivalent cycle values before/after grouping, long rests, changed motion direction, active movement during a break, missing samples during a rest, and JSON/CSV/PNG output with pause and block references.

- Correct counts and approximate boundaries for 5, 8, 13 and 6 cycles at several cadences.
- Two separately detected sets with 7 and 6 cycles.
- Unchanged count after sensor-axis rotation, bias and small noise.
- An unfinished extra repetition does not add a count; a recording cut in motion is not extrapolated.
- Missing gyro samples split the analysis; no accepted cycle spans the gap.
- Quiet signals, irregular noise, a single movement, one-direction-only rotation and drift produce no qualifying set.
- High-frequency vibration is rejected instead of aliasing into an exercise rhythm.
- Non-finite values and duplicate/backward timestamps are rejected.
- CLI export works with no labels present and gives the same result after deliberately malformed label files are added. Inputs remain byte-identical.

Separate replay of private real recordings produced:

| Recording | Automatic sets | Complete cycles |
|---|---:|---:|
| `exercise-02` | 1 | 13 |
| `startup-check-20260910-fixed` | 0 | 0 |
| `link-check-20260910-01` | 0 | 0 |
| `link-check-20260910-instrumented` | 0 | 0 |

Three in-memory coordinate rotations of the actual push-up signals preserve the count of 13. Shifting the recording's entire clock by 123.4 seconds shifts the detected boundaries correspondingly and preserves the count, checking that detection does not depend on a fixed session offset. Full private replay evidence is in [validation.json](../data/processed/exercise-02-auto/validation.json).

These recordings were development evidence. The diagnostic captures were not annotated as specific negative activities, and posture during them was unobserved. They do not establish a daily-activity false-positive rate. The single push-up set has an uncertain manual count, so it is not an independent accuracy benchmark. No additional physical exercise or BLE test was required for this implementation.

## Remaining limits

The detector favors regular motion with visible arm rotation and needs an intact overlap of at least eight seconds plus three accepted cycles. Very short, slow, low-rotation or highly variable sets may be missed or split. Periodic walking or household motion may qualify as repeated motion; exercise naming and reliable rejection of these activities require further examples and validation across separate sessions.

The next validation should use independently counted sets and labelled everyday movements, preserving whole-set/session separation in evaluation. Live counting, automatic exercise naming, iPhone/offline sensor download and all-day totals remain separate work.
