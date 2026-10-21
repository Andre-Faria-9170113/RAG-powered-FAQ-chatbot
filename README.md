# CustoJusto Support Triage Assistant

A course final project: a conversational **Streamlit + RAG** assistant for
buyers and sellers using the Portuguese classifieds website CustoJusto.
It aims to reduce repetitive manual support by answering from the website's
FAQs, asking a focused follow-up when essential information is missing, or
linking to the appropriate official contact form when human help is needed.

This is an **academic prototype**, not an official CustoJusto service. It cannot
access accounts, investigate transactions, issue refunds, submit forms or
contact the support team. Replies are intended to be in European Portuguese.

## Start Here: Evaluator Guide

- **Inspect the evidence without running anything:** read the
  [published report](docs/evaluations/2026-10-21_181409_development-run-01/report.md)
  and [case-by-case AI review](docs/evaluations/2026-10-21_181409_development-run-01/review.md).
- **Verify without spending API credit:** install dependencies, then run the
  [offline checks](#tests-and-evaluation). No OpenAI key is required for these checks.
- **Try the chatbot:** follow [local setup](#local-setup) and
  [manual checks](#manual-checks). Sending messages makes paid OpenAI API calls.

The implementation is loosely based on the course's **Triage Assistant** proposal.
It combines two LLM stages, Pydantic-validated structured outputs, FAQ retrieval,
conversation context and labelled evaluation cases. The original project
[PDF](docs/Projeto%20Final.pdf) and [Excalidraw diagram](docs/projeto_final.excalidraw)
are preserved as proposal artifacts, **not current implementation specifications**.
See [changes from the proposal](#changes-from-the-original-pdf-and-diagram).

## Local Setup

Requirements: Python **3.10+** (verified locally with **3.12.15**), the `uv`
package manager, and internet access to install dependencies. Run commands from
the repository root. Use the committed lockfile rather than installing arbitrary
newer dependency versions:

```bash
uv sync --locked
```

For live chat only, create `.env` from `.env.example` **if you do not already
have one**, and set your own `OPENAI_API_KEY`. Never commit credentials.

```bash
cp .env.example .env
```

Default models are `gpt-4.1-mini` for validation/generation/summaries and
`text-embedding-3-small` for embeddings. An OpenAI API key with available API
credit is required for live requests.

Install the browser used when FAQ snapshots need refreshing, then start:

```bash
uv run --locked playwright install chromium
uv run --locked streamlit run app.py
```

Open the local URL printed by Streamlit, normally `http://localhost:8501`.
Stop the server with `Ctrl+C`. On Linux, if Playwright reports missing system
libraries, its `install --with-deps chromium` command also installs browser
dependencies and may require administrator access.

### What Happens at Startup

The first page load after server startup checks the live website's version
before enabling chat. With unchanged version and valid snapshots, no scraping
or browser launch occurs. A changed version, first bootstrap, or missing
`data/faqs.json`, `data/contact_forms.json` or `data/website_snapshot.json`
triggers validated recovery of the complete snapshot set.

FAQs are read from **`script#faqs` JSON**, not reconstructed from accordions.
Playwright is a fallback when that script is inserted client-side; installed
Google Chrome can also be used. A failed website check blocks chat and offers a
retry. There is no offline/stale-data mode in the UI. Existing checksum-mismatched
files are rejected rather than silently overwritten.

The website check itself makes **no OpenAI calls**. On the first question that
passes validation, the FAQ corpus is embedded if the local Chroma index needs
building. Later questions reuse it until normalized FAQ content changes.
The cold-start embedding cost is additional to per-question calls.

For a manual website check, without OpenAI usage:

```bash
uv run --locked python refresh_website_data.py
```

Restart a running app after a separate manual refresh. CLI evaluation does not
automatically refresh the website: it uses pinned local evidence for reproducibility.
See [website snapshots](docs/website-data.md) for recovery and browser details.

## Manual Checks

Use synthetic examples, not real account details. Start a new conversation for
each row, except the explicitly paired follow-up. These are intended behaviors,
not guaranteed wording from a nondeterministic model.

| Scenario | Example message | Expected behavior |
|---|---|---|
| FAQ answer | `Quanto custa publicar um anuncio?` | Answer from FAQs and show source IDs/titles in the response details. |
| Contextual follow-up | `Tenho duvidas sobre o novo chat.` then `O que tem esta funcionalidade de novo?` | Retain the chat topic and describe supported features. |
| Ambiguous reference | `E aquela opcao, como funciona?` | Ask which option, without inventing context. |
| Human intervention | `O meu anuncio foi rejeitado. Quero que a equipa reveja a decisao.` | Recommend the relevant official contact form, without claiming a review was submitted. |
| Out of scope | `Da-me uma receita de bolo.` | Decline and redirect to website-support scope. |

Expand **Detalhes da resposta** to inspect category, urgency and its brief
explanation. Retrieval-stage replies also expose action, confidence, missing
information/knowledge, FAQ references and the selected contact destination.
Early clarification/rejection replies do not have retrieved FAQ evidence.
Suggested starter prompts disappear after the first user message.
**Reiniciar conversa** resets the visible transcript and model context.

## How It Works

```text
Startup -> website-version check -> validated local snapshots -> chat enabled

Message + conversation context
  -> LLM 1: validate scope, resolve references, classify category and urgency
     -> out of scope: reject, without retrieval or embeddings
     -> essential ambiguity: clarify, without retrieval or embeddings
     -> otherwise: create a standalone retrieval question
  -> prepare/reuse Chroma -> semantic search + lexical search -> merge top FAQs
  -> LLM 2: use FAQ evidence and context -> validated FAQResponse
     -> answer | clarify | escalate + official contact link
  -> retain transcript; compact model context when needed
```

Each retrieval chunk is one FAQ: its question and cleaned answer. Semantic
search uses embeddings and cosine distance in persistent Chroma; lexical search
adds direct term matches. Retrieved FAQ IDs are checked on generated citations.
This check does **not** prove that every claim is supported.

Category identifies the topic; urgency describes reported time sensitivity;
confidence describes evidence confidence. None is synonymous with the next action.
Urgency values are `low`, `normal`, `high`, `unknown`, and `not_applicable`.
An urgent request can still need clarification; a normal request can require a
human. There is no implemented priority queue or promised response time.

The app keeps two separate session histories: the full visible transcript and
plain role/content context for the model. After the configured turn limit,
context retains the first complete exchange, a summary of the middle, and the
latest exchange. Previous summaries are incorporated into later summaries.
Summary failures retain the existing context; failed answer turns are not added
to model context. Conversation history is not durably stored across sessions or
server restarts. Live questions/context are sent to the configured OpenAI models.

## Tests and Evaluation

### Offline: No Model Calls or API Credit Required

After dependency installation, these checks use the committed snapshots and
mocked external calls. They do not launch the live app or scrape the website:

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked python evaluate.py --dry-run
```

The delivery check passes **124 tests**. Coverage includes follow-up plumbing,
structured-output validation/repair, routing, separate histories and repeated
compaction, Streamlit interactions, snapshot refresh/recovery, evaluation
metrics, saved pricing and published artifact integrity. These tests verify
application contracts, not the factual quality of every future model output.

The active [dataset](data/evaluations/triage_cases.json) v1.6 contains **45
synthetic Portuguese cases: 31 development and 14 holdout**. All have category,
expected action and urgency labels; relevant cases specify contact destinations,
source groups and a review rubric. Expanded labels are
**`draft_for_human_review`**. Earlier owner approval of v1.1 does not approve
the additional labels. Holdout cases have not been evaluated live.

Recompute the latest saved report without repeating chatbot calls:

```bash
uv run --locked python evaluate.py --split development \
  --cases docs/evaluations/2026-10-21_181409_development-run-01/dataset.json \
  --report-from docs/evaluations/2026-10-21_181409_development-run-01/report.json \
  --output reports/evaluator-regenerated
```

This re-scores **saved outputs**, not the current chatbot. The dataset hash and
split must match the original run, and the working FAQ/contact snapshots must
match the dataset's content hashes. A freshness error means evidence/labels need
review; do not change hashes merely to bypass it. Existing output files are
protected from overwrite: choose a new output name on repeated runs.

### Live: Optional and Paid

Only run this with your own configured key and available API credit:

```bash
uv run --locked python evaluate.py --split development --output reports/evaluator-development-01
```

It writes `.json` and `.md` reports with expected/predicted decisions, responses,
sources, per-stage token usage, latency, and estimated **EUR** costs. Pricing and
FX are dated assumptions in `data/evaluations/model_pricing.json`, not live billing
data. Missing usage/pricing is N/A, not zero. An empty index can add corpus
embedding cost. A fresh run may differ from the published sample.
Keep holdout out of development/prompt tuning; a final holdout run should follow
label approval and a frozen implementation.

### Published Development Results

The latest run started on **2026-10-21 at 18:14:09 UTC** using `gpt-4.1-mini`:

| Measure | Saved result |
|---|---:|
| Action accuracy | 31/31 (100%) |
| Required escalation recall / correct destination | 12/12 (100%) each |
| Answers when a non-answer was expected | 0/20 |
| Expected source-group coverage / answer citation ID validity | 11/11 (100%) each |
| Category accuracy | 27/31 (87.1%) |
| Urgency accuracy / high-urgency recall | 30/31 (96.8%) / 3/4 (75%) |
| Mean / p95 case latency | 3.07 s / 5.62 s |
| API calls / total tokens | 77 / 117,281 |
| Estimated cost at saved rates | EUR 0.04791415 |

These are development-set measurements from one run, **not production accuracy
or proof of complete grounding**. The separate AI review rates 18 outputs
acceptable, 9 acceptable with notes, and 4 needing changes: corrupted text in
case-03, refund-presupposing wording in case-18, an unsupported staff-access claim
in case-35, and category/urgency mistakes in case-40. Human-review metrics remain
N/A; an AI-authored review is not independent human validation.

The [archive index](docs/evaluations/README.md) links all published runs, exact
dataset snapshots and reviews in UTC-timestamped folders. The latest also pins
FAQ/contact evidence. Raw reports are preserved, including known defects.
Older cohorts/schemas differ, so aggregate scores are not a controlled comparison.
See the [evaluation guide](docs/evaluation.md) for metric definitions and review instructions.

## Changes from the Original PDF and Diagram

The [PDF](docs/Projeto%20Final.pdf) and [diagram](docs/projeto_final.excalidraw)
describe the initial design. They remain unchanged for traceability; the diagram
has **not** been redrawn to reflect the implementation. The main differences are:

| Area | Initial proposal | Delivered implementation and reason |
|---|---|---|
| Delivery surface | Chatbot embedded in the classifieds website. | Standalone Streamlit prototype; no production website integration or separate FastAPI service. |
| Pre-validation | `is_valid_question` and a suggested response before embeddings. | Adds explicit clarification, a standalone retrieval question, category, urgency and a brief urgency explanation. A contextual or incomplete support request is not automatically rejected. |
| Follow-up handling | Retain history for the conversation. | Validation and retrieval both use resolved context, not only answer generation. This addresses short follow-ups losing their topic before search. |
| Website freshness | Check the version before every RAG operation; content-hash optimization was undecided. | One successful check on first page load per server process, before any model work. Both FAQ and contact snapshots refresh together when needed. Normalized FAQ content controls re-embedding, avoiding rebuilds for unrelated website deployments. Changes during an already-running process require a restart/manual refresh. |
| FAQ extraction and retrieval | Scrape FAQs; one FAQ per vector chunk and top-k similarity retrieval. | Reads `script#faqs` JSON, with a browser fallback; retains one-FAQ chunks and adds lexical retrieval for direct topical matches. |
| Answer versus escalation | Diagram branches on confidence and retrieved source scores. | Explicit `answer`, `clarify`, or `escalate` decisions with schema checks. Confidence alone does not prove resolution or determine whether human action is needed. |
| Missing information | A single `missing_info` list. | Separates missing user details from missing evidence/access (`missing_knowledge`), to avoid asking users for facts that only staff can investigate. |
| Contact routing | A relevant contact destination and appended link. | Uses ten scraped official categories and published subjects, validates allowed destinations, and appends a clickable URL only for escalation. It never submits the form. |
| History compaction | Initial message, recent exchanges and an LLM summary. | Separate display/model histories; keeps the first complete exchange, accumulated summary and latest exchange without deleting the visible transcript. |
| Structured-output failures | No repair policy specified. | One bounded repair attempt for schema-invalid LLM output; persistent failure is surfaced. This is not factual verification or an unlimited retry loop. |
| Evaluation | Not specified in the initial project PDF/diagram. | Labelled development/holdout cases, offline tests, action/routing/category/urgency metrics, token/latency/EUR cost tracking and immutable published baselines. |
| Feedback and extended triage | Satisfaction prompt/storage left open in the diagram; the course suggests deeper triage features. | Satisfaction collection, structured extraction of supplied case details, a dedicated handoff summary and automatic support submission are not implemented. |

## Known Limits and Deferred Work

- Citation validation checks retrieved IDs, but does not require every factual
  answer to cite evidence or verify claim-level support. FAQ titles are still
  model-generated. Some saved referrals cite unrelated FAQs.
- Lexical and semantic scores are merged heuristically, without calibrated rank
  fusion. HTML cleaning loses link targets, and the FAQ corpus has conflicting
  photo limits that require content-owner clarification.
- Chroma identity currently hashes FAQ content, not the embedding model. Changing
  the embedding model requires rebuilding the local index. Concurrent rebuilds
  are not coordinated for production use.
- History compaction is turn-count-based, not a hard token budget. Very long
  messages or repeated summary failures can still exceed context limits.
- Website freshness depends on its version marker and availability. An unchanged
  marker cannot reveal unannounced content changes; startup deliberately blocks
  chat if verification fails.
- No account actions, durable chat storage, support queue, SLA, production
  deployment or independent human validation is claimed.

## Repository and Configuration

| Location | Responsibility |
|---|---|
| `app.py` | Streamlit chat, session state, startup preparation and response details. |
| `chat_bot.py` | Validation, FAQ chunks, Chroma, retrieval, generation and context compaction. |
| `contact_forms.py` | Official category keys, routing context and URLs. |
| `refresh_website_data.py`, `website_scraping.py` | Version-gated FAQ/contact scraping and snapshot validation. |
| `data/` | Committed FAQ/contact snapshots and website-version marker. |
| `evaluate.py`, `api_metrics.py`, `data/evaluations/` | Labelled cases, evaluation CLI and usage/pricing assumptions. |
| `tests/` | Offline unit/integration tests with mocked external services. |
| `docs/evaluations/` | Published reports, matching datasets, evidence snapshots and AI reviews. |
| `reports/`, `.chroma/` | Ignored local reports and generated vector index. |

`.env.example` documents model and retrieval settings: `OPENAI_MODEL`,
`OPENAI_EMBEDDING_MODEL`, `TOP_K` (4), `MIN_SIMILARITY` (0.35), and
`HISTORY_MAX_TURNS` (5; minimum 3). `LEXICAL_TOP_K` also defaults to 4.
Keep `.env` private. The ignored `private/` course repository is reference
material, not a runtime requirement; evaluators need only this repository.

Further reading: [evaluation](docs/evaluation.md),
[official contact routing](docs/contact-routing.md), and
[website refresh/recovery](docs/website-data.md).
The earlier [implementation review](docs/implementation-review.md) is a historical
assessment: some listed gaps have since been addressed. This README and the
latest published review describe the delivery state.
