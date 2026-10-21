# Preserved Historical Notes

These AI-authored observations were carried over from the previous archive index.
They describe the initial run and implementation at that time, not the current
feature inventory. They are not a newly completed human review.

## Observations

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

See [the evaluation guide](../../evaluation.md) for commands, metric definitions,
limitations, and how to enter human judgments without making more API calls.
