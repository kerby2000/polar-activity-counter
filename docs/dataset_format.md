# Dataset format, schema version 1

Each `record` creates a **new** directory; existing directories are rejected to prevent overwriting recordings. Default: `data/raw/<UTC timestamp>/`. Everything under `data/` is ignored by Git. Keep real datasets local.

```text
session/
  metadata.json
  packets.jsonl
  acc.csv
  gyro.csv
  hr.csv
  labels.csv
  label_events.jsonl
  plots/                 # created by plot command
```

Separate streams preserve independently timestamped ACC and gyro without inventing one-to-one synchronization. There is no processed/resampled dataset yet. A later resampler must retain original files, document grid/interpolation policy and avoid interpolating across gaps. `packets.jsonl` is the lossless byte record; CSVs are decoded views that can be regenerated after decoder improvements.

## IMU CSVs

| Field | Meaning |
|---|---|
| `time_s` | Approximate host-session-relative coordinate, derived from the common device clock anchor |
| `device_timestamp_ns` | Reconstructed sample timestamp, integer ns in original Polar epoch |
| `packet_timestamp_ns` | Unmodified final-sample timestamp from this notification's header |
| `packet_id`, `sample_index` | Received notification ID and zero-based sample index within it |
| `host_monotonic_ns` | Host **packet arrival**, repeated for all samples in that packet |
| `host_time_utc` | ISO-8601 host **packet arrival**, not acquisition UTC |
| `timestamp_method` | `nominal_first`, `interpolated`, `discontinuity_nominal`, or `non_monotonic_frame` |
| `acc_x_mg`, `acc_y_mg`, `acc_z_mg` | Scaled acceleration, including gravity; fractional mg retained |
| `gyro_x_dps`, `gyro_y_dps`, `gyro_z_dps` | Scaled angular velocity, degrees/s |

Only ACC axis columns occur in acc.csv and gyro columns in gyro.csv. Large timestamps must be loaded as integer/string, never float64. Python's `csv` module reads them as strings; use `int()`. Do not open/re-save raw files through Excel if timestamp precision matters.

## HR and packet bytes

hr.csv contains `time_s` (host elapsed), `host_monotonic_ns`, `host_time_utc`, `packet_id`, `hr_bpm`, `contact_supported`, `contact_detected`, `energy_expended`, and a JSON-array `rr_intervals_ms` field. Absent values are empty. There is no fabricated HR device timestamp. HR contact status on Verity Sense is not a trustworthy wear detector.

Each packets.jsonl line contains `packet_id`, `stream`, `payload_hex`, `host_monotonic_ns` and `host_time_utc`. Preserve metadata's `control_exchanges` and `scale_factors` with these bytes. Notification order is arrival order and can interleave streams; it is not a globally sample-sorted table.

## Ground truth

labels.csv fields: `set_id`, `activity`, `start_time_s`, `end_time_s`, `expected_rep_count`, `note`, `completion`. Times are keyboard-event host elapsed seconds. IDs are zero-padded decimal strings. Empty repetition count means **unknown/not applicable**, not zero. A deliberately entered zero is retained as zero. `completion` is `open`, `complete` or `interrupted`.

labels.csv is atomically rewritten after each change. The independent, flushed label_events.jsonl stores activity selections, set starts/ends, entered counts, notes and interrupted sets, all with host-relative `time_s`. Notes before the first set live only in the journal; otherwise N attaches to the current or latest set. Unmarked periods are **unlabelled**, not automatically negative ground truth. Explicitly record background sets. Custom activity names support reaching, carrying, dressing and other negatives.

Changing activity during an open set is rejected. Ending a set immediately fixes the end timestamp before prompting for reps. Acquisition continues while text/counts are entered. An open set on exit is closed as interrupted with unknown count. Correcting a missed count later can be done by editing only labels.csv (keep a copy and note the correction); raw IMU data stays unchanged. Re-run diagnose/plot afterward. The event journal preserves what was originally entered.

## metadata.json

Contains schema/software version, UUID session ID, host start UTC/monotonic reference, OS/Python/dependency versions, subject, explicit sensor position and arm, notes, queried device identity/firmware/battery/capabilities, requested/acknowledged configurations, scale factors and raw control exchanges, HR enabled status, requested/actual recording durations, streaming-start offset, warnings/errors, shutdown status, application buffer losses, clock mapping and quality report.

Actual counts/rates/gaps are in `quality.acc`, `quality.gyro` and `quality.hr`. The report also includes ACC/gyro overlap and duration mismatch, label counts, missing counts and interrupted sets. `diagnose` recomputes from current CSVs without altering metadata. `synthetic: true` is reserved for explicitly artificial fixtures. See [protocol.md](protocol.md) for clock reconstruction, error behavior and limits of loss inference.

Only `status: complete` plus a satisfactory quality report and physical sanity checks should pass a collection gate. Do not infer valid IMU acquisition from the existence of files alone.
