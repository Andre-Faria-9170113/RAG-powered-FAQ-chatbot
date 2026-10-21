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
