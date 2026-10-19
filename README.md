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
