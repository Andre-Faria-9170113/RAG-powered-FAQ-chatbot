# Historical development baseline (dataset v1.0)

The [full report](baseline-development.md) and [raw results](baseline-development.json)
record a live run of the 20 development cases on 2026-10-21 with the configured
`gpt-4.1-mini` generation model. The 10 holdout cases have not been run.

This run predates the ten-category contact catalog and dataset v1.1 (34 cases).
The [original dataset](triage-cases-v1.json) is preserved byte-for-byte with the
hash recorded in the results. The raw JSON and Markdown report are unchanged;
they are not scores for today's prompts or expanded dataset. A fresh development
run is needed after review of the current labels. The current holdout has 12
cases and remains unrun.

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

## Observations for the next improvement step

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

The next planned item, explicit structured triage decisions, should distinguish
the request category, the chosen action, missing user information, and missing
knowledge. It should also make the application's actual escalation behavior
consistent with its flags. These are proposed next changes, not part of this
evaluation implementation.

See [the evaluation guide](../evaluation.md) for commands, metric definitions,
limitations, and how to enter human judgments without making more API calls.
