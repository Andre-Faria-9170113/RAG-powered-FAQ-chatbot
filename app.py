"""Streamlit interface for the classifieds FAQ RAG chatbot."""

from __future__ import annotations

import logging

import streamlit as st
from openai import OpenAI

from chat_bot import (
    FAQResponse,
    answer_user_question,
    compact_history_if_needed,
    conversation_history,
    load_faq_records,
)

LOGGER = logging.getLogger(__name__)

PROJECT_TITLE = "Assistente CustoJusto"
WELCOME_MESSAGE = (
    "Olá! Posso ajudar com dúvidas sobre anúncios, pesquisas, conta, contactos, "
    "serviços pagos e outras FAQs do website."
)
SUGGESTIONS = {
    ":material/campaign: Publicar anúncio": "Como posso publicar um anúncio?",
    ":material/search: Pesquisar anúncio": "Como pesquisar um anúncio?",
    ":material/delete: Eliminar anúncio": "Como faço para eliminar o meu anúncio?",
}


def initialise_session_state() -> None:
    st.session_state.setdefault(
        "messages",
        [{"role": "assistant", "content": WELCOME_MESSAGE}],
    )
    st.session_state.setdefault("pending_prompt", None)
    if "context_history" not in st.session_state:
        st.session_state.context_history = conversation_history(
            st.session_state.messages
        )


def reset_conversation() -> None:
    st.session_state.messages = [{"role": "assistant", "content": WELCOME_MESSAGE}]
    st.session_state.pending_prompt = None
    st.session_state.context_history = []
    st.session_state.suggestion = None


@st.cache_resource(show_spinner=False)
def cached_client() -> OpenAI:
    return OpenAI()


@st.cache_data(show_spinner=False)
def cached_faq_count() -> int:
    return len(load_faq_records())


def render_message(message: dict) -> None:
    role = message["role"]
    content = message["content"]
    with st.chat_message(role):
        if message.get("error"):
            st.error(content)
        else:
            st.markdown(content)
        if details := message.get("details"):
            render_answer_details(FAQResponse.model_validate(details))


def render_answer_details(answer: FAQResponse | None) -> None:
    if answer is None:
        return

    with st.expander("Detalhes da resposta", expanded=False):
        st.write(f"Confiança: `{answer.confidence_score}`")
        if answer.topics:
            st.write("Tópicos: " + ", ".join(answer.topics))
        if answer.missing_info:
            st.write("Informação em falta: " + ", ".join(answer.missing_info))
        if answer.sources:
            st.write("FAQs usadas:")
            for source in answer.sources:
                st.write(f"- `{source.chunk_id}`: {source.question}")
        st.write(f"Encaminhamento: `{answer.relevant_contact_form}`")


def has_user_turn() -> bool:
    return any(message["role"] == "user" for message in st.session_state.messages)


def suggestion_prompt() -> str | None:
    if has_user_turn() or st.session_state.get("pending_prompt"):
        return None
    selected = st.pills(
        "Experimente perguntar",
        list(SUGGESTIONS.keys()),
        key="suggestion",
        label_visibility="collapsed",
        width="stretch",
        wrap=True,
    )
    if selected:
        st.session_state.pending_prompt = SUGGESTIONS[selected]
        st.rerun()
    return None


def answer_prompt(prompt: str) -> None:
    st.session_state.messages.append({"role": "user", "content": prompt})
    render_message({"role": "user", "content": prompt})
    failed = False

    with st.chat_message("assistant"):
        with st.spinner("A validar o pedido e a consultar as FAQs..."):
            try:
                answer, structured_answer, _validation = answer_user_question(
                    prompt,
                    st.session_state.context_history,
                    client=cached_client(),
                )
            except Exception:
                LOGGER.exception("Could not answer the current turn")
                answer = (
                    "Não consegui processar o pedido neste momento. "
                    "Por favor, tente novamente."
                )
                structured_answer = None
                failed = True

        if failed:
            st.error(answer)
        else:
            st.markdown(answer)
            render_answer_details(structured_answer)

    assistant_message = {"role": "assistant", "content": answer}
    if failed:
        assistant_message["error"] = True
    if structured_answer is not None:
        assistant_message["details"] = structured_answer.model_dump()
    st.session_state.messages.append(assistant_message)

    if failed:
        return

    st.session_state.context_history.extend(
        [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ]
    )
    try:
        st.session_state.context_history = compact_history_if_needed(
            cached_client(),
            st.session_state.context_history,
        )
    except Exception:
        LOGGER.exception(
            "History compaction failed; retaining the complete model context"
        )


def main() -> None:
    st.set_page_config(
        page_title=PROJECT_TITLE,
        page_icon=":material/support_agent:",
        layout="centered",
    )
    initialise_session_state()

    with st.sidebar:
        st.header("Estado")
        try:
            faq_count = cached_faq_count()
            st.caption(f"Corpus carregado · {faq_count} FAQs disponíveis")
            st.caption("A base vetorial é preparada quando envia uma pergunta válida.")
        except Exception as exc:
            st.caption("Corpus de FAQs indisponível")
            st.error(str(exc))
        st.button(
            "Reiniciar conversa",
            on_click=reset_conversation,
            icon=":material/restart_alt:",
            width="stretch",
        )

    st.title(PROJECT_TITLE)
    st.caption("Chatbot RAG para apoio ao cliente com base nas FAQs do website.")

    for message in st.session_state.messages:
        render_message(message)

    pending_prompt = st.session_state.get("pending_prompt")
    st.session_state.pending_prompt = None
    chat_prompt = st.chat_input("Escreva a sua pergunta", submit_mode="disable")
    prompt = pending_prompt or chat_prompt

    if not prompt:
        suggestion_prompt()
        return

    answer_prompt(prompt)


if __name__ == "__main__":
    main()
