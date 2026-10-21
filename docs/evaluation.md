# Triage evaluation

This implements item 1 of the project improvement plan: a labeled dataset, a
repeatable evaluation runner and an inspectable baseline. It does not change
chatbot prompts, output schemas, retrieval or routing policy.

## Dataset and label ownership

`data/evaluations/triage_cases.json` contains 30 synthetic Portuguese support
requests, authored against the local FAQ corpus. These are **assistant-authored
draft labels**, not production conversations or independently human-reviewed
ground truth. The project owner should review the expected actions, contact
destinations and rubrics before treating the scores as authoritative.

There are 20 development cases and 10 holdout cases. The cases include 14 expected
answers, 5 clarifications, 7 escalations and 4 rejections. Four cases provide
conversation context, including the new-chat regression, a topic change and a
summary. All six contact destinations appear in escalation labels. IDs and
payment/support references are synthetic; no real customer details are needed.

The holdout split is not run by default. Tune using development cases, then use
holdout for a later check. Once holdout failures are used to tune the system,
those cases are no longer an independent final check; add a fresh holdout.

Each case contains:

- `category`: an expected category for future classification evaluation.
- `expected_action`: answer, clarify, escalate or reject.
- `expected_contact_form`: the exact destination for required escalations.
- `expected_source_groups`: for each group, citing any one listed FAQ satisfies
  that evidence expectation. Multiple groups mean multiple required facts.
- `rubric`: semantic requirements and prohibited assumptions for review.
- Optional `history` and `critical` fields.

The dataset stores the normalized FAQ content hash. If FAQs change, the runner
stops until the labels and source IDs are reviewed against the new corpus. This
avoids silently evaluating old labels against changed or reordered FAQs.
Changing label status to `human_reviewed` requires a named `reviewed_by` value.
That is an attestation by the person editing the dataset, not independent proof
of an external review. Dataset changes create a new evaluation version/hash and
require a new run; old results cannot silently adopt revised labels.

## Commands

Validate labels and source references without making API calls:

```bash
.venv/bin/python evaluate.py --dry-run
.venv/bin/python evaluate.py --split holdout --dry-run
```

Run development evaluation using the configured `.env` models and API key:

```bash
.venv/bin/python evaluate.py --split development --output reports/development-run-01
```

This makes paid API calls through the same `answer_user_question` entry point as
the app. Only each case's question and supplied history are passed to the
chatbot; expected labels and rubrics are not included in model inputs. Supplied
history is a fixed fixture, not generated earlier turns. This evaluates follow-up
interpretation; history compaction is covered separately by the unit/AppTest
suite. The runner does not perform additional LLM grading calls.

Each completed case checkpoints JSON results and a Markdown report. Existing
reports are protected unless `--overwrite` is supplied. Connection,
authentication, permission and HTTP rate-limit failures stop the run with
partial results. Other case errors are recorded and evaluation continues. A
run with errors exits with a nonzero status. A model/label mismatch is a measured
result, not an execution error; there is no invented pass threshold.

The runner uses the app's existing Chroma index and freshness checks. First-use
indexing may make the first case slower. Requests run sequentially with a
30-second SDK timeout and SDK retries disabled. Timings are elapsed pipeline
latency, not a latency SLA. Token counts and monetary cost are not measured.

Published baseline artifacts live under `docs/evaluations/`; exploratory output
under `reports/` is ignored by git. Raw results include model names, retrieval
settings, UTC run time, Git revision, corpus hash, and dataset/implementation/
evaluator hashes. The implementation hash identifies uncommitted code too;
the Git revision alone does not imply a clean worktree.

## What the automatic metrics mean

| Metric | Definition |
|---|---|
| Action accuracy | Inferred action matches the expected action. |
| Escalation recall | Required human escalations that actually take the escalation branch. |
| Correct escalation destination | Required escalations that both escalate and select the expected contact key. |
| Answers when non-answer expected | Cases labelled clarify/escalate/reject that take the answer branch; lower is better. |
| Expected source group coverage | Fraction of expected FAQ groups represented among citations, including zero credit when the case errors or has no citations. |
| Answer citation ID validity | Answer-branch outputs with at least one citation and all cited IDs present in the corpus. |

There is currently no explicit action/category output in the chatbot. The
adapter mirrors its structured flags: invalid means reject; validation
clarification or `missing_info` means clarify; low confidence plus a contact key
means escalate; medium/high confidence means answer. Low confidence without a
contact or missing-information flag is `unresolved`. Exceptions are `error`.

This inference can disagree with the wording of a generated response. Review
the text as well as the flags. Category accuracy is **N/A**, because free-text
`topics` and referral destinations are not a dedicated category prediction.

Errors count against action accuracy and any required escalation. Metrics with
no applicable examples are **N/A**, not 0% or 100%. Citation metrics only check
identifiers/expected source selection; they do **not** establish factual support
or detect every unsupported claim. A high model confidence is not a calibrated
probability and does not change the expected labels.

## Human review

Each result includes an initially empty `review` object. A reviewer can supply
their name, `unsupported_claims`, `follow_up_relevant`, and notes after comparing
the response with the rubric and cited evidence. Unreviewed judgments remain
`null`, and the report shows their rates as N/A. Named reviewer fields provide
attribution, not automated verification of who performed a review.

- `unsupported_claims=true`: at least one factual claim or claimed account
  action is not supported by the supplied evidence/capabilities.
- `unsupported_claims=false`: the reviewed output has no such unsupported claim.
- `follow_up_relevant=true/false`: whether an actual clarification asks for a
  useful missing detail without repeating known information. This metric only
  includes outputs inferred as clarify.
- `notes`: explain omissions, label disagreements, contradictory evidence,
  inappropriate questions or discrepancies between flags and response text.

Regenerate reports from annotated results without API calls:

```bash
.venv/bin/python evaluate.py --split development \
  --report-from reports/development-run-01.json \
  --output reports/development-run-01-reviewed
```

Dataset and split must match the saved run. Review changes to result JSON are
picked up when reports regenerate; chatbot calls are not repeated.

The course PDF's 30-request recommendation motivates this initial dataset, not
a claim of statistical significance or representative production accuracy.
The distinction between automatic checks and human judgment also follows the
[official OpenAI evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
