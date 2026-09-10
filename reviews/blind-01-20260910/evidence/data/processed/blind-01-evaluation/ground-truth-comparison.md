# blind-01: comparison after user disclosure

The user reports five push-ups, an everyday-activity break, three pull-ups, walking to and ascending one staircase, walking to and descending another staircase, then returning to the PC.

| User-reported event | Frozen result |
|---|---|
| 5 push-ups | Set missed; no count produced |
| 3 pull-ups | Set missed; no count produced |
| First staircase, up | Stairs event missed |
| Second staircase, down | Stairs event missed |
| Walking between locations | Some walking predicted; exact overlap unscored |

Both exercise sets were missed (0 of 2 detected); both stair traversals were missed. This is a failed first mixed-activity test, not evidence that the raw recording is faulty or that the task is impossible. The system had no ability to infer stair direction even before this test.

Times were not supplied. The previously rejected pull-up candidates cannot now be relabelled as successful recognitions, and no exact push-up or pull-up boundaries have been established. Keep inferred timing separate from the user's confirmed order and totals. The earlier 18-tread reference and handrail information do not describe this test.

The frozen model, outputs, source CSVs and original blind report/manifest remain unchanged. `ground-truth.json` records the later disclosure separately. No analyser changes or retraining were performed for this review.

See `pro-review-brief.md` for the independent review request and `pro-review-package.zip` for its evidence bundle.
