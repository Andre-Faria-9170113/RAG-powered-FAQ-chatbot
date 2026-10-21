# Published Evaluation Baselines

## Publication Order

Folders use **`YYYY-MM-DD_HHMMSS_run-name` in UTC**, from the report's original
`started_at_utc`, not its publication time. Sorting folder names gives run order;
no separate sequential counter is needed. New publications use this same layout.
The latest publication is the current **development** baseline, not a production
acceptance certificate.

| Run time (UTC) | Dataset / evaluated cases | Action accuracy | Artifacts |
|---|---|---:|---|
| 2026-10-21 18:14:09 | v1.6 / 31 development | 31/31 | **Current:** [report](2026-10-21_181409_development-run-01/report.md), [raw JSON](2026-10-21_181409_development-run-01/report.json), [AI review](2026-10-21_181409_development-run-01/review.md), [dataset](2026-10-21_181409_development-run-01/dataset.json) |
| 2026-10-21 15:03:29 | v1.1 / 22 development | 21/22 | [report](2026-10-21_150329_triage-development/report.md), [raw JSON](2026-10-21_150329_triage-development/report.json), [AI review](2026-10-21_150329_triage-development/review.md), [dataset](2026-10-21_150329_triage-development/dataset.json) |
| 2026-10-21 10:12:21 | v1.0 / 20 development | 14/20 | [report](2026-10-21_101221_initial-development/report.md), [raw JSON](2026-10-21_101221_initial-development/report.json), [preserved AI notes](2026-10-21_101221_initial-development/notes.md), [dataset](2026-10-21_101221_initial-development/dataset.json) |

Case cohorts, prompts, response schemas and label coverage changed between runs.
These aggregate percentages are not a controlled like-for-like comparison.
Original source run names are retained in the reviews: the middle row is
`development-triage-07-codex`, and the latest is `development-run-01`.

## Current Development Baseline

The latest run has **zero runtime errors**, all 31 expected actions and all 12
required referral destinations correct. Other saved checks are:

| Check | Result |
|---|---:|
| Answers when non-answer expected | 0/20 (0%) |
| Expected source-group coverage | 11/11 (100%) |
| Answer citation ID validity | 11/11 (100%) |
| Category accuracy | 27/31 (87.1%) |
| Urgency accuracy | 30/31 (96.8%) |
| High-urgency recall | 3/4 (75%) |
| Mean / p95 case latency | 3.07 s / 5.62 s |
| Recorded API calls / total tokens | 77 / 117,281 |
| Estimated cost, saved rates | EUR 0.04791415 |

The [qualitative AI review](2026-10-21_181409_development-run-01/review.md) rates
**18 acceptable, 9 acceptable with notes, and 4 needing changes**. Main issues:
corrupted output in case-03, refund-presupposing wording in case-18, an unsupported
staff-access claim in case-35, and category/urgency errors in case-40.
Several referrals also carry irrelevant citations. Correct action/routing scores
do not establish that every answer is grounded or readable.

All v1.6 labels remain **drafts pending human review**. The AI judgments do not
populate raw human-review fields or change `_human` metrics from N/A. The 14
holdouts were not evaluated or used to tune prompts during publication.
No paid model calls, live scrape or chatbot behavior changes were needed.

## Archive Layout

Each run folder contains immutable `report.json`, `report.md` and `dataset.json`
copies with their original timestamps, labels and hashes. A completed AI review
adds `review.json` and `review.md`; the initial run only has preserved `notes.md`.
The latest folder also freezes `faqs.json` and `contact_forms.json` as the evidence
for its review. The complete retrieved FAQ set was not saved.

The older raw reports and dataset bytes are unchanged by reorganization.
The previous review's relative artifact references were updated for the new
filenames; its case judgments, attribution, review date and source hashes remain
unchanged. Historical report bodies may mention older feature status, which is
not a description of today's implementation.

Additional frozen datasets that are not attached to a published run live in
`datasets/`:

- [Owner-reviewed v1.1](datasets/triage-cases-v1.1-reviewed.json), matching
  `development-category-01`. Relative to the run-07 dataset, only label approval
  metadata changed. It does not extend owner approval to v1.6 or AI output reviews.
- [Focused urgency v1.0](datasets/urgency-cases-v1.0.json), matching
  `development-urgency-01`. This is a historical draft snapshot, not another
  actively maintained dataset or an independent holdout.

The active dataset remains `data/evaluations/triage_cases.json`. IDs above 34
were renumbered when urgency cases were merged and a duplicate removed; use the
matching snapshot, not present-day IDs, when inspecting historical runs.
See [dataset ownership and ID mapping](../evaluation.md#dataset-and-label-ownership).

## Offline Regeneration

Use the exact dataset belonging to the report, without rewriting original files:

```bash
.venv/bin/python evaluate.py --split development \
  --cases docs/evaluations/2026-10-21_181409_development-run-01/dataset.json \
  --report-from docs/evaluations/2026-10-21_181409_development-run-01/report.json \
  --output reports/development-run-01-regenerated
```

The command requires matching FAQ/contact content, just like other evaluations.
The latest publication freezes that evidence for audit; the runner still uses
the working FAQ/contact files and stops if their normalized versions differ.
The original v1.0 dataset predates the required contact-catalog field and cannot
be passed directly to the current evaluator; its exact bytes are preserved for
audit, not silently upgraded. Neither raw historical usage nor human judgments
are invented during regeneration.

The earlier triage baseline has seven required escalation destinations, 67,079
tokens and an estimated EUR 0.02764002 cost. Its AI review identified premature
payment escalation, broad character-policy advice and a forwarding promise.
The initial baseline did not record usage; its token/cost metrics remain N/A.
See [the evaluation guide](../evaluation.md) for metric definitions and review limits.
