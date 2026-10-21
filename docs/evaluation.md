# Triage evaluation

This implements item 1 of the project improvement plan: a labeled dataset, a
repeatable evaluation runner and an inspectable baseline. The evaluator calls
the production chatbot without injecting gold labels or changing its policy.
The later [contact catalog update](contact-routing.md) expands runtime routing;
the initial baseline predates that update and remains historical.

## Dataset and label ownership

`data/evaluations/triage_cases.json` (v1.1) contains 34 synthetic Portuguese support
requests, authored against the local FAQ corpus. They started as assistant-authored
drafts; owner approval is recorded in the working file's `label_status` and
`reviewed_by`. They are synthetic, not production conversations or an independently
validated benchmark. Approval of expected actions, contact destinations and
rubrics is separate from judging the generated responses.

There are 22 development cases and 12 holdout cases. The cases include 14 expected
answers, 5 clarifications, 11 escalations and 4 rejections. Four cases provide
conversation context, including the new-chat regression, a topic change and a
summary. All ten contact destinations appear in escalation labels. IDs and
payment/support references are synthetic; no real customer details are needed.

The holdout split is not run by default. Tune using development cases, then use
holdout for a later check. Once holdout failures are used to tune the system,
those cases are no longer an independent final check; add a fresh holdout.

Each case contains:

- `category`: the expected request category, scored against the validation-stage prediction.
- `expected_action`: answer, clarify, escalate or reject.
- `expected_contact_form`: the exact destination for required escalations.
- `expected_source_groups`: for each group, citing any one listed FAQ satisfies
  that evidence expectation. Multiple groups mean multiple required facts.
- `rubric`: semantic requirements and prohibited assumptions for review.
- Optional `history` and `critical` fields.

The dataset stores the normalized FAQ content hash and `contact_forms_version`,
a hash of the pinned official contact catalog (excluding its fetch timestamp).
If FAQs or contact subjects/URLs change, the runner stops until labels and source
IDs are reviewed and the corresponding hash is updated. This avoids silently
evaluating old labels against changed sources or routing destinations.
The separate `data/website_snapshot.json` marker controls
[website scraping](website-data.md), not label approval or vector-index freshness.
A new website deployment can leave FAQ/contact content unchanged; in that case
their evaluation hashes stay valid. The refresher never changes expected labels,
their hashes or review status automatically. FAQ IDs are positional, so changed
ordering also requires checking every expected source group.
Changing label status to `human_reviewed` requires a named `reviewed_by` value.
That is an attestation by the person editing the dataset, not independent proof
of an external review. Dataset changes create a new evaluation version/hash and
require a new run; old results cannot silently adopt revised labels.

The catalog revision retains all original 30 requests and their expected actions.
Their seven escalation destinations remain appropriate after reviewing the live
form subjects; rubrics clarify services, complaints and image-policy questions.
Cases 31-34 add suggestions, institutional press, mobile interface issues and
phone-number access. These new destinations should not be collapsed into `outros`.
Routing precedence is an assistant interpretation of the published subjects,
not a routing guarantee from CustoJusto, and still needs owner validation.

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

This creates `reports/development-run-01.json` and `.md`, automatically including
the current token, EUR-cost and latency metrics; no metrics flag is needed.
Choose a new output name for each fresh run to preserve previous results.

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
latency, not a latency SLA. New runs also capture provider-reported token usage,
per-call latency and estimated token costs reported in EUR; see below.

Published baseline artifacts live under `docs/evaluations/`; exploratory output
under `reports/` is ignored by git. Raw results include model names, retrieval
settings, UTC run time, Git revision, corpus/contact catalog hashes, and dataset/
implementation/contact-router/evaluator/telemetry hashes and the full pricing
snapshot used for the run. Code hashes identify uncommitted changes too;
the Git revision alone does not imply a clean worktree.

## Latency, tokens and cost

Every new case result contains `api_calls` with the requested/reported model,
stage, elapsed SDK-call latency, safe error type, provider-reported input, cached
input, cache-write (when reported), output, reasoning (when reported), and total
tokens. Prompts, keys and raw API error messages are not stored in these records.
The normal app has no active metrics collector; its API behavior is unchanged.

Stages distinguish `validation`, `corpus_embedding`, `query_embedding`, and
`answer_generation`. History summarization is instrumented too, but this runner's
fixed history fixtures do not invoke it, so it is not part of these case costs.
A cold/rebuilt Chroma index charges its corpus embedding call to the first case
that triggers it. A warm index makes no corpus embedding call. Compare this stage
separately when comparing runs rather than attributing all of that cost to a
normal support answer.

JSON and Markdown reports include per-case pipeline latency, mean/p95 pipeline
latency, per-call latency, stage latency sums, token totals, estimated total EUR
and known-cost subtotals. Pipeline latency includes local retrieval/index work
and API work, but not app-startup website scraping or writing evaluation reports.
API-call latency includes SDK processing; this is not server-only latency or
time-to-first-token. SDK retries remain disabled for evaluation.

`data/evaluations/model_pricing.json` pins public **standard** USD prices per
million tokens, the verification date, supported exact model names/aliases and
source URLs. The initial snapshot covers the configured defaults:
[GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) and
[text-embedding-3-small](https://developers.openai.com/api/docs/models/text-embedding-3-small).
No live pricing request is made during evaluation. Use `--pricing path/to/rates.json`
for a reviewed alternative snapshot when changing models or rates. The full
validated snapshot is copied into run metadata, and report regeneration retains
the saved per-call estimates rather than repricing historical calls.

The same file pins `reporting_currency="EUR"` and an `eur_exchange_rate` object
with `usd_per_eur`, the reference date and source URL. The initial conversion
uses the [ECB reference rate](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/eurofxref-graph-usd.en.html)
for 2026-10-20: 1 EUR = 1.1215 USD. This is a dated reporting assumption, not a
live exchange rate or a bank's settlement rate. Update this block deliberately
when preparing a later pricing snapshot; evaluation makes no live FX request.
Per-call, case, stage and run costs are displayed in EUR, with
`estimated_cost_eur` and `known_cost_eur_subtotal` in JSON metrics. The underlying
USD estimates remain in JSON for auditing OpenAI's published USD token prices.

For supported text calls without separate cache-write charges:

```text
estimated USD = ((input - cached input) * input rate
                 + cached input * cached-input rate
                 + output * output rate) / 1,000,000
estimated EUR = estimated USD / usd_per_eur
```

Cached tokens are a subset of input, not additional input. Reasoning tokens are
already part of output and are not charged twice. Embeddings use their reported
prompt-token count and input rate. Calls with nonzero cache writes are recorded
but left unpriced because this pricing schema does not supply write rates.
This distinction follows [OpenAI's caching documentation](https://developers.openai.com/api/docs/guides/prompt-caching).

These are estimates, **not invoices**: taxes, credits, account-specific discounts,
regional/provider surcharges and nonstandard service tiers are not modeled.
Bank conversion spreads and fees are not included. If the saved EUR rate is
missing, euro estimates remain N/A even when a USD estimate is available. Reports
without recorded EUR costs are not silently converted using today's rate.
Unknown model prices, nonstandard tiers, missing/inconsistent usage, and calls
that raise before exposing usage produce `null`/N/A, never invented free calls.
Completed calls remain recorded if a later step fails. Aggregate full totals
remain N/A when coverage is incomplete; explicitly labelled known subtotals and
coverage counts show what was actually recorded. No tokenizer-based guesses are
used to fill missing provider usage.

Historical results without `api_calls` retain their latency measurements but
show token/cost tracking as unavailable. The initial published baseline is not
rewritten, and its usage cannot be reconstructed from its answer text.

## What the automatic metrics mean

| Metric | Definition |
|---|---|
| Action accuracy | Observed triage action matches the expected action. |
| Escalation recall | Required human escalations that actually take the escalation branch. |
| Correct escalation destination | Required escalations that both escalate and select the expected contact key. |
| Answers when non-answer expected | Cases labelled clarify/escalate/reject that take the answer branch; lower is better. |
| Expected source group coverage | Fraction of expected FAQ groups represented among citations, including zero credit when the case errors or has no citations. |
| Answer citation ID validity | Answer-branch outputs with at least one citation and all cited IDs present in the corpus. |

New runs use `FAQResponse.action`: `answer`, `clarify` or `escalate`. The validation
stage still handles invalid requests (`reject`) and missing necessary user details
(`clarify`) before retrieval. A valid request with no answer is `unresolved`;
exceptions, including inconsistent generated triage fields, are `error`.
Validation distinguishes scope from specificity: missing intent, symptoms or an unresolved reference
must have `is_valid_question=true`, `needs_clarification=true`, and a nonblank
clarification reply. Rejection must have `is_valid_question=false`,
`needs_clarification=false`, and a nonblank refusal. The prompt requests an empty
standalone question for early returns, but a nonempty unused value no longer
blocks a valid clarification/refusal: neither can trigger retrieval. A request proceeding to retrieval needs a
nonblank standalone question. Contradictory flags and blank early-return replies
fail validation rather than silently becoming rejection or an empty chat bubble.
Vague problems are not rewritten as a guessed editing, deletion or refund task.
A known topic or contact category alone is not evidence that human support is
needed. Broad informational questions can still retrieve FAQs; explicit requests
for human intervention do not ask for details needed only by the support team.
Explicit human-intervention intent in the original message takes priority over
generic FAQ troubleshooting or omissions in the rewritten search query. Decision
fields lead both response schemas so dependent fields are generated after them.

There is no automatic relabelling. Each structured stage permits one additional
model attempt after a Pydantic parsing/contract error. It reuses the original
question, history and evidence with corrective instructions containing only safe
rule codes and field names, never the failed output or exception text. It does
not retry API/network errors, missing parsed responses or citation-check failures.
After a second invalid output, the case remains an error. Syntactically valid but
semantically wrong decisions are not retried or silently changed.

New run metadata records `validation_contract_version=4` and
`max_structured_repair_attempts=1`. API calls use separate `validation_repair` and
`answer_generation_repair` stages, so both attempts contribute to elapsed latency
and usage/cost tracking. When SDK parsing raises before exposing usage, that
attempt's usage and full run cost remain N/A; the known subtotal includes any
successful repair, not an invented estimate for the failed call.

Failed calls and final failed cases include `validation_issues` with allowlisted
rule codes (for example `clarification_rejected` or `non_escalation_destination`)
and sanitized field paths. Input values, messages, contexts, arbitrary property
names and raw model outputs are excluded. Streamlit logs these safe diagnostics
instead of the raw Pydantic traceback on validation failures. Recovered cases retain the first
attempt's diagnostics in `api_calls`. Case details display these diagnostics and
both validation flags; historical reports cannot gain diagnostics that were
never captured.
The evaluator stores validation snapshots separately from the stricter live
model, so older contradictory flags and their original scores remain readable.
Confidence no longer selects the action. A high-confidence, FAQ-supported referral
is an escalation and receives the official form link.

`missing_info` means a detail the user must provide to answer or choose a channel;
it is required for answer-stage clarification and empty for other actions.
`missing_knowledge` records unresolved FAQ/capability limitations, not a reason to
ask the user for unavailable policy or backend information. Only escalations may
select a contact key, and they must select one. Answer generation receives the
original request as well as the rewritten search question to retain attempted
troubleshooting and requests for human intervention.

New metadata includes `triage_decision_version=2`. Saved reports without that
marker retain their recorded inferred actions and legacy `missing_info` meaning;
regeneration does not invent explicit decisions or improve historical scores.
The historical schema is private to the evaluator; the chatbot and Streamlit
use only the current `FAQResponse`. Restart Streamlit when upgrading from a
pre-action version, since old in-memory chat details are not migrated.
Use a fresh output name for a new live run, for example:

```bash
.venv/bin/python evaluate.py --split development --output reports/development-triage-05
```

Review the response text as well as the decision. The decision/schema tests and
mocked SDK tests establish application behavior, not live model accuracy; a fresh
paid development run is still needed to measure the prompt changes. Labels and
the holdout split are unchanged.

### Category classification

The existing validation call now also returns a required `category`. This is the
single category prediction for the turn, including early clarification and
rejection; it is not inferred from FAQ `topics`, the selected action or a contact
destination. Classification sees the current request and conversation context,
not the evaluation labels. It does not filter retrieval or force escalation.
Urgency and extraction of supplied details are outside this increment.

Allowed categories are the ten official contact-topic keys plus:

- `indeterminado`: the topic cannot yet be identified, even with history.
- `fora_ambito`: rejected requests, including prohibited requests.

`outros` means a known in-scope topic with no more specific category, not a missing
prediction. A vague payment problem can be `pagamentos_servicos` and still need
clarification. Follow-ups inherit the resolved topic, while an explicit topic
change takes precedence. Category and support destination are separate decisions.

Fresh runs include `category_classification_version=1` and
`validation_contract_version=5`. `category_accuracy` uses exact label matches over
all completed cases; errors or missing predictions get no credit. Each case shows
its expected and predicted categories. Historical results without predictions
remain **N/A**, even when a contact destination was recorded; regeneration does
not reclassify old requests. The published run-07 baseline remains unchanged.
No dataset labels or approval metadata were changed for this increment.

To measure classification and check for action/routing regressions:

```bash
.venv/bin/python evaluate.py --split development --output reports/development-category-01
```

Errors count against action accuracy and any required escalation. Metrics with
no applicable examples are **N/A**, not 0% or 100%. Citation metrics only check
identifiers/expected source selection; they do **not** establish factual support
or detect every unsupported claim. A high model confidence is not a calibrated
probability and does not change the expected labels.

## Human review

The [current published baseline](evaluations/README.md) preserves run 07 and its
matching execution-time dataset. A separate [qualitative AI review](evaluations/current-development-review.md)
assesses all 22 responses against their rubrics and evidence. It is attributed
to Codex, not a human reviewer: it does not populate the original `review` fields
or turn the `_human` metrics into AI-graded scores. The working dataset's later
owner approval only changed approval metadata, not the cases or expected labels.
The raw run retains its original draft status and dataset hash for provenance.

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
The preserved v1.0 baseline cannot be regenerated against the v1.1 dataset. Its
original labels are archived in `docs/evaluations/triage-cases-v1.json` for audit;
the current runner requires the new contact-catalog version field. Run a fresh
development evaluation after reviewing the revised draft labels instead of
overwriting or relabelling that historical baseline.

The course PDF's 30-request recommendation motivates this initial dataset, not
a claim of statistical significance or representative production accuracy.
The distinction between automatic checks and human judgment also follows the
[official OpenAI evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
