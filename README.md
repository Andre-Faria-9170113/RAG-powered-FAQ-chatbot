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

See [contact routing](docs/contact-routing.md) for the source, refresh command
and routing distinctions. The app reads this local snapshot, not the live site.

Generated local vector data is stored in `.chroma/` and ignored by git.

## Conversation flow

The first structured LLM call validates the request in context and resolves
follow-ups into a standalone question. Both embedding search and lexical search
use that resolved question. A rejected or genuinely ambiguous request returns
before indexing or embedding. The answer uses retrieved FAQs as evidence;
conversation history only helps interpret the request.

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

`data/evaluations/triage_cases.json` contains 34 draft-labelled Portuguese support
cases: 22 development cases and 12 holdout cases. Labels need review by the
project owner before being treated as human-approved ground truth.

```bash
.venv/bin/python evaluate.py --dry-run
.venv/bin/python evaluate.py --split development --output reports/development-run-01
```

Live evaluation makes paid API calls and generates JSON/Markdown reports using
the current chatbot. Holdout is not run by default. Category accuracy is not yet
measurable; inferred actions, contact routing and source selection are scored.
Factual support and clarification quality have separate human-review fields.

See [the evaluation guide](docs/evaluation.md) for metric definitions, label
review, report regeneration and reproducibility details.
The [historical development baseline](docs/evaluations/README.md) includes results
and failure examples for dataset v1.0, before the contact catalog update. It is
not a score for the current dataset or prompts; the holdout split remains unused.
