# RAG-Powered FAQ Chatbot

Streamlit chatbot for a classifieds website support flow. It validates whether a
message is an in-scope support question before spending embedding/RAG calls, then
answers from the local FAQ corpus in `data/faqs.json`.

## Run locally

1. Copy `.env.example` to `.env` and set `OPENAI_API_KEY`.
2. Install dependencies with the course environment, for example `uv sync`.
3. Start the app:

```bash
streamlit run app.py
```

## Architecture

- `app.py` contains the Streamlit chat UI.
- `chat_bot.py` contains the pre-validation, FAQ loading, Chroma indexing,
  retrieval, structured answer generation, and history compaction.
- `data/faqs.json` is treated as the website FAQ source. The Chroma index is
  rebuilt when the normalized FAQ content hash changes.
- `data/contact_forms.json` pins the ten official contact categories and their
  published subject options. `contact_forms.py` supplies routing context, URLs
  and the structured response's allowed destinations from that catalog.
- `refresh_website_data.py` checks the website's version meta tag before
  refreshing both datasets. `data/website_snapshot.json` records the last
  successful version and snapshot checksums.

See [contact routing](docs/contact-routing.md) for the source, refresh command
and routing distinctions. The app reads this local snapshot, not the live site.
On the first page load after server startup, it checks the website version and
refreshes both snapshots if needed, before accepting questions or embedding FAQs.

## Refresh website data

```bash
.venv/bin/python refresh_website_data.py
```

The first run bootstraps both snapshots. Missing `faqs.json`, `contact_forms.json`
or `website_snapshot.json` also triggers a full bootstrap on the next startup,
even if the site version is unchanged. With a complete, valid local set, later
runs make one version-check request and skip both scrapers if
`<meta name="version" content="...">` has not changed.
FAQs come exclusively from `script#faqs`, not from HTML accordions.
The current site inserts that script through JavaScript, so refreshes use
Playwright with installed Google Chrome or Chromium. To install Chromium:

```bash
.venv/bin/playwright install chromium
```

The same refresh runs automatically once per server process on the first page
load. Successful preparation is shared across sessions and reruns; failures
disable chat and can be retried. It reloads contact routing and the FAQ count
before any model calls. No browser is needed for unchanged versions with
complete snapshots.
Questions do not repeat the version check. The CLI remains available for manual
maintenance; restart a running app after a separate manual refresh. See
[website data](docs/website-data.md) for details.

Generated local vector data is stored in `.chroma/` and ignored by git.

## Conversation flow

The first structured LLM call validates the request in context, classifies its
category and urgency, and resolves follow-ups into a standalone question. Both embedding
search and lexical search use that resolved question. A rejected or genuinely
ambiguous request returns before indexing or embedding. The answer uses retrieved FAQs as evidence;
conversation history only helps interpret the request.

`QuestionValidation.category` identifies the request topic independently of its
action or contact destination, with no additional model call. It uses the ten
official contact-topic keys plus `indeterminado` for an unknown topic and
`fora_ambito` for rejected requests. A known payment/ad topic can still require
clarification. Category appears in response details, including early replies,
and fresh evaluations score `category_accuracy` against the existing labels.
Historical runs without category predictions remain N/A.

The same call returns `urgency` (`low`, `normal`, `high`, `unknown`, or
`not_applicable`) and a short `urgency_reason`. These appear in response details
and fresh reports. Urgency is independent of action: high can need essential
clarification, and normal can need human support. It does not assign a support
queue, promise faster service or add a model call. See [urgency](docs/evaluation.md#urgency-assessment)
for the policy and urgency labels now merged into the main evaluation set.
The original approved dataset and focused urgency set are preserved as snapshots;
the expanded dataset awaits label approval. The current published baseline
includes urgency assessments; older baselines remain unchanged in the archive.

Answer generation also receives the original message so a search rewrite cannot
discard troubleshooting already attempted or a request for human intervention.
Its explicit `action` selects `answer`, `clarify` or `escalate`, independently of
evidence confidence. `missing_info` contains only necessary user details;
`missing_knowledge` records unresolved evidence or capability limits. Escalations
always append the selected official contact-form link, even with high confidence
or FAQ citations. The app never submits a form or contacts the team itself.

Streamlit keeps two per-session histories:

- `messages`: the full visible transcript, including FAQ details for each answer.
- `context_history`: plain role/content messages used by the model, without the
  welcome message or UI metadata.

After `HISTORY_MAX_TURNS` complete exchanges (default: 5), model context is
compacted to the first user/assistant exchange, a summary of the middle, and the
latest exchange. Subsequent summaries incorporate the previous summary. Both
validation and generation receive that context without an additional last-N
slice. A summary failure is logged and retains the unsummarized context for a
later retry. Reset clears both histories. Neither history survives a new browser
session or server restart.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests mock API calls and cover follow-up resolution plumbing, early exits,
summary retention, repeated compaction, transcript preservation and reset.
See [the implementation review](docs/implementation-review.md) for remaining
differences from the brief and course references.

## Triage evaluation

`data/evaluations/triage_cases.json` v1.6 contains 45 synthetic Portuguese support
cases: 31 development and 14 holdout cases, all named `case-NN` and explicitly
labelled for urgency. The original 12 holdout questions and other expectations are unchanged;
the two additions are new scenarios, not promoted development cases.
The expanded set is a draft pending approval of its
new labels; the original 34-case owner-reviewed v1.1 is archived under
`docs/evaluations/datasets/triage-cases-v1.1-reviewed.json`. Label approval is distinct from
reviewing the chatbot's generated responses.

The duplicate of `case-01` was removed, and v1.6 closes the numbering gap by
shifting former `case-36` through `case-46` down one. IDs now run `case-01` through
`case-45`. The misleading-urgency-wording test is now `case-39`.

```bash
.venv/bin/python evaluate.py --dry-run
.venv/bin/python evaluate.py --split development --output reports/development-run-01
```

Live evaluation makes paid API calls and generates JSON/Markdown reports using
the current chatbot. Holdout is not run by default. Category accuracy, triage
decisions, contact routing and source selection are scored. Urgency accuracy and
high-urgency recall use only cases with explicit urgency labels.
Factual support and clarification quality have separate human-review fields.
New reports also include per-case/stage token usage and latency, mean/p95 case
latency, and estimated EUR cost using the dated model prices and ECB conversion in
`data/evaluations/model_pricing.json`. Missing usage or unsupported pricing stays
N/A; the historical baseline has no retroactively invented token/cost numbers.

See [the evaluation guide](docs/evaluation.md) for metric definitions, label
review, report regeneration and reproducibility details.
The [published baselines](docs/evaluations/README.md) are organized into UTC-dated
run folders. The current publication is `development-run-01`: 31 development
cases, its exact dataset/evidence snapshots and an explicitly AI-authored
qualitative review. The earlier triage and initial baselines remain archived
with unchanged raw reports and dataset bytes. Execution-time approval metadata
is preserved rather than backdated; the holdout split remains unused.
