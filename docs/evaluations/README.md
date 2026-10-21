# Published evaluation baselines

## Current development baseline

The current published baseline is **development-triage-07-codex**, run on
2026-10-21 against the 22 development cases in dataset v1.1. It is preserved as
an inspectable measurement, including its known failures, not a production
accuracy claim.

- [Full report](current-development.md) and [original raw results](current-development.json).
- [Exact dataset used for that run](triage-cases-v1.1-run-07.json).
- [Case-by-case qualitative review](current-development-review.md) and
  [structured review judgments](current-development-review.json), authored by Codex (AI).

| Automatic check | Current baseline |
|---|---:|
| Action accuracy | 21/22 (95.5%) |
| Escalation recall | 7/7 (100%) |
| Correct escalation destination | 7/7 (100%) |
| Answers when non-answer expected | 0/13 (0%) |
| Expected source group coverage | 9/9 (100%) |
| Answer citation ID validity | 9/9 (100%) |
| Runtime errors | 0/22 |
| Mean / p95 pipeline latency | 3.16 s / 4.53 s |
| Provider-reported tokens | 67,079 |
| Estimated cost, saved rates | EUR 0.02764002 |

The qualitative AI review found **12 acceptable outputs, 7 acceptable with
notes, and 3 needing changes**: overly broad policy advice in case-07, premature
escalation in case-13, and an unsupported forwarding promise in case-18. Correct
routes can still carry irrelevant citations. The review also distinguishes
ignored internal validation text from what the user actually sees.

The dataset owner has since approved the labels as **André Faria**. Only approval
metadata changed; all case content is identical to the archived run snapshot.
The original report retains its execution-time `draft_for_human_review` status
and dataset hash. Dataset-label approval does not make the AI response review an
independent human review. Human-review metrics therefore remain N/A.

Regenerate this preserved report without API calls using its matching snapshot:

```bash
.venv/bin/python evaluate.py --split development \
  --cases docs/evaluations/triage-cases-v1.1-run-07.json \
  --report-from docs/evaluations/current-development.json \
  --output reports/current-development-regenerated
```

This requires the matching FAQ/contact content, as with other evaluations. A
fresh run using the working dataset would record its new approval hash; do not
change the old metadata to make those hashes appear identical. The 12-case
holdout remains unrun. The smaller historical baseline below uses a different
dataset/cohort and is not a controlled like-for-like comparison.

## Historical development baseline (dataset v1.0)

The [full report](baseline-development.md) and [raw results](baseline-development.json)
record a live run of the 20 development cases on 2026-10-21 with the configured
`gpt-4.1-mini` generation model. The 10 holdout cases have not been run.

This run predates the ten-category contact catalog and dataset v1.1 (34 cases).
The [original dataset](triage-cases-v1.json) is preserved byte-for-byte with the
hash recorded in the results. The raw JSON and Markdown report are unchanged;
they are not scores for today's prompts or expanded dataset. The run-07 baseline
above records the current implementation. The current holdout has 12 cases and
remains unrun.

New evaluation runs record per-call token usage and estimated EUR costs (with
underlying USD estimates retained in JSON). This
historical run did not capture usage: its latency remains valid, but token/cost
figures cannot be backfilled from the saved responses.

Labels remain assistant-authored drafts for review by the project owner. The
following are descriptive measurements for this one run, not a course grade or
an estimate of production accuracy. No chatbot prompts or behavior were changed
while establishing this baseline.

| Automatic check | Baseline |
|---|---:|
| Structured action agreement | 14/20 (70%) |
| Required escalation branch selected | 1/5 (20%) |
| Required escalation branch and destination correct | 1/5 (20%) |
| Answer branch when non-answer expected | 1/11 (9.1%) |
| Expected cited FAQ groups covered | 8/9 (88.9%) |
| Answer outputs with known, nonempty citation IDs | 9/9 (100%) |
| Runtime errors | 0/20 |
| Mean / p95 elapsed pipeline latency | 2.82 s / 4.75 s |

Category classification, unsupported factual claims, and follow-up relevance
are not scored automatically. The latter two need a named review of the text
and evidence. All human-review judgments remain pending in the raw results.

### Observations from the historical run

These observations are an assistant analysis of outputs, not completed human
review annotations:

- **case-05:** asks which classifieds website the user means instead of
  consulting the saved-search FAQ. The expected answer is in `faq-030`.
- **case-11:** the response correctly asks which option the user means, but
  `is_valid_question=false` causes the adapter to infer reject. The automatic
  mismatch here is a flag/text disagreement, not proof that the question shown
  to the user was inappropriate.
- **case-16:** changes a request for manual review of a rejected ad into generic
  editing instructions and a clarification. The model uses `missing_info` to
  describe missing corpus knowledge, not a detail the user could supply.
- **case-17:** the response provides a support email, but confidence is high and
  the application does not append the selected technical-support form. The
  adapter therefore records answer. Do not interpret the 20% escalation result
  as proving that only 20% of responses mention any support channel.
- **case-19:** asks the user to reconfirm details they said they already checked,
  rather than passing the unresolved delivery issue to the requested team.
- **case-21:** rejects a complaint about customer support, although the expected
  policy routes it to `outros`. The intended scope needs to be explicit.

The new-chat follow-up (case-26) and explicit topic change (case-28) match their
expected answer actions and cite their expected FAQ groups in this run.

The current implementation now has explicit answer/clarify/escalate decisions
and separates missing user information from missing knowledge/capabilities.
Escalation links use that decision rather than confidence. Those changes postdate
this baseline: its saved decisions and scores are unchanged. A dedicated request
category prediction is still pending.

See [the evaluation guide](../evaluation.md) for commands, metric definitions,
limitations, and how to enter human judgments without making more API calls.
