# Implementation review

Reviewed on 2026-10-21 against:

- [Project brief](Projeto%20Final.pdf).
- [Flow diagram](projeto_final.excalidraw).
- Course Session 6, `private/gen-ai-course/sessions/06-conversational-ai/`,
  particularly `starter/01_conversational_rag.py`.
- Course Session 4 grounded-answer example and Session 5 retrieval fusion in
  `private/gen-ai-course/sessions/session_05_support.py`.
- The course Knowledge Copilot milestones.

The course repository is local reference material, not a runtime dependency.

## Findings fixed in this pass

1. **High: follow-ups lost their topic before retrieval.** Validation previously
   saw only the latest message; retrieval embedded that raw message. Retaining
   history only in the final answer call could not fix a rejection or unrelated
   retrieved evidence. Validation now receives model context and produces a
   standalone question, following the Session 6 approach. This reuses the existing
   pre-validation call rather than adding another call to every normal turn.
   Truly unresolved references ask for clarification before embedding.
2. **High: history compaction changed the visible transcript.** The same list
   drove the UI and model context. Summarizing it deleted visible messages and
   their source panels. These now live in separate session-state lists.
3. **Medium: the first exchange was misidentified and summaries could disappear.**
   The welcome message occupied the first history position, so `history[:2]`
   preserved the greeting and first question rather than the first complete
   exchange. Later `history[-6:]` slices could omit the preserved beginning and
   summary. The greeting is now excluded from model context, and validation and
   generation receive the same complete, compacted context.
4. **Medium: summary failures were silent.** Failed or empty summaries now retain
   the existing context and are logged. Failed answer calls are shown in the
   transcript but are not added to model context.
5. **Medium: contact routing did not describe its categories or `none`.** The
   answer prompt now defines their scope and separates clarification from human
   escalation. A clarification with `missing_info` no longer appends a contact
   form just because confidence is low. Contact URLs were not changed.

## Remaining findings, in priority order

1. **High: citation validation is incomplete.** `generate_faq_answer` checks that
   cited IDs were retrieved but accepts a medium/high-confidence answer with no
   citations. The returned FAQ titles are model-generated too. Even valid source
   IDs do not prove that every claim follows from the evidence. The Session 4
   example additionally requires citations and checks supporting quotations.
   Add explicit answer/clarify/escalate outcomes and citation requirements, and
   use corpus titles when rendering sources.
2. **Medium: hybrid retrieval mixes incompatible scores.** `lexical_search`
   converts term counts into an arbitrary 0.45-0.99 score; `merge_retrieval_results`
   compares that directly with cosine similarity. A lexical hit can outrank a
   stronger semantic match without a calibrated basis. Substring matching also
   permits accidental word matches. Use token-based matching and rank fusion
   like Session 5, and evaluate on a broader set of labeled queries before
   tuning thresholds. Do not interpret the fused ranking as confidence.
3. **Medium: index identity excludes the embedding model.** `faq_version` hashes
   FAQ content only. Changing `OPENAI_EMBEDDING_MODEL` can reuse vectors generated
   by a different model, giving invalid similarities or dimension errors. Include
   embedding model/configuration in index identity. Rebuilds also delete old
   records before upserting the new set; concurrent sessions need coordinated or
   atomic replacement before a multi-user deployment.
4. **Medium: evidence loses actionable links and can contradict itself.**
   `html_to_text` discards anchor URLs, leaving link-only FAQs without the target
   that a user needs. The corpus also contains inconsistent photo limits: the
   first FAQ says twelve, while the photo FAQ describes an initial image plus
   five more. Preserve URLs and resolve conflicting/legacy entries with the
   content owner; do not silently choose one as current policy.
5. **Low: model context has a message-count policy, not a hard token limit.**
   Very long individual messages or repeated summary failures can still exceed
   a model's context window. Add a token budget and an explicit failure policy
   before production use. The visible transcript remains in browser-session
   server memory; it is not durable storage.

## Planned features still absent

- Website scraping and checking the frontend version meta tag before RAG.
  Currently the source is `data/faqs.json`; a local content hash is not the same
  as detecting a new website build.
- Verified source URLs and clickable FAQ/contact links. The current UI shows
  source IDs/titles and a contact path formatted as code.
- Satisfaction feedback and a storage decision for it. This was an open question
  in the diagram, not an implemented feature.
- A separate history-only route for rephrasing an earlier answer. The course has
  this optimization; current in-scope requests still go through retrieval.
- FastAPI/container delivery from the later course milestones, if that delivery
  scope is required for this final project. The app currently calls Python
  functions directly from Streamlit.

## Verification

The offline suite covers the exact two-turn case with a mocked structured
rewrite and checks that both retrieval paths use it. It also covers rejection
and clarification without retrieval, API-safe history, summary delivery to both
LLM stages, repeat compaction, empty summaries, transcript retention and reset.
These tests verify application behavior, not model interpretation quality.

A live check used the configured model, the local FAQ corpus, real embeddings
and a temporary Chroma index. The two inputs were:

1. "Tenho dúvidas sobre o novo chat."
2. "o que tem este feature de novo?"

The follow-up was resolved to a question about the new chat's changes and
answered with sources including `faq-051` and `faq-056`, without a clarification.
This single live conversation is a smoke test, not a retrieval benchmark or a
guarantee across all wording variants. No application model was upgraded in
this pass.
