"""Streamlit interface for the classifieds FAQ RAG chatbot."""

from __future__ import annotations

import logging

import httpx
import streamlit as st
from openai import OpenAI
from pydantic import ValidationError

from api_metrics import validation_issues
from contact_forms import CONTACT_FORMS, CONTACT_LABELS, reload_contact_forms
from refresh_website_data import refresh as refresh_website_data
from chat_bot import (
    FAQResponse,
    RequestCategory,
    RequestUrgency,
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


@st.cache_resource(show_spinner=False, max_entries=1)
def prepare_website_data() -> int:
    """Complete one successful refresh check per server process before any chat work."""
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        refresh_website_data(client)
    reload_contact_forms()
    return len(load_faq_records())


def render_message(message: dict) -> None:
    role = message["role"]
    content = message["content"]
    with st.chat_message(role):
        if message.get("error"):
            st.error(content)
        else:
            st.markdown(content)
        details = message.get("details")
        render_answer_details(
            FAQResponse.model_validate(details) if details else None,
            category=message.get("category"),
            urgency=message.get("urgency"), urgency_reason=message.get("urgency_reason"),
        )


def render_answer_details(
    answer: FAQResponse | None, *, category: RequestCategory | None = None,
    urgency: RequestUrgency | None = None, urgency_reason: str | None = None,
) -> None:
    if answer is None and category is None and urgency is None:
        return

    with st.expander("Detalhes da resposta", expanded=False):
        if category is not None:
            st.write(f"Categoria: `{category}`")
        if urgency is not None:
            st.write(f"Urgência: `{urgency}`")
            if urgency_reason:
                st.write(f"Motivo: {urgency_reason}")
        if answer is None:
            return
        labels = {"answer": "Resposta", "clarify": "Clarificação", "escalate": "Encaminhamento"}
        st.write(f"Decisão: {labels[answer.action]}")
        st.write(f"Confiança: `{answer.confidence_score}`")
        if answer.topics:
            st.write("Tópicos: " + ", ".join(answer.topics))
        if answer.missing_info:
            st.write("Detalhes necessários: " + ", ".join(answer.missing_info))
        if answer.missing_knowledge:
            st.write("Limitações de conhecimento ou acesso: " + ", ".join(answer.missing_knowledge))
        if answer.sources:
            st.write("FAQs usadas:")
            for source in answer.sources:
                st.write(f"- `{source.chunk_id}`: {source.question}")
        destination = answer.relevant_contact_form
        if destination == "none":
            st.markdown("Encaminhamento: Nenhum")
        else:
            st.markdown(
                f"Encaminhamento: [{CONTACT_LABELS[destination]}]({CONTACT_FORMS[destination]})"
            )


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
    category = None
    urgency = None
    urgency_reason = None

    with st.chat_message("assistant"):
        with st.spinner("A validar o pedido e a consultar as FAQs..."):
            try:
                answer, structured_answer, validation = answer_user_question(
                    prompt,
                    st.session_state.context_history,
                    client=cached_client(),
                )
                category = validation.category
                urgency = validation.urgency
                urgency_reason = validation.urgency_reason
            except Exception as error:
                if isinstance(error, ValidationError):
                    LOGGER.error("Could not answer the current turn: %s", validation_issues(error))
                else:
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
            render_answer_details(
                structured_answer, category=category, urgency=urgency, urgency_reason=urgency_reason,
            )

    assistant_message = {"role": "assistant", "content": answer}
    if failed:
        assistant_message["error"] = True
    if structured_answer is not None:
        assistant_message["details"] = structured_answer.model_dump()
    if not failed:
        assistant_message["category"] = category
        assistant_message["urgency"] = urgency
        assistant_message["urgency_reason"] = urgency_reason
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
    st.title(PROJECT_TITLE)
    st.caption("Chatbot RAG para apoio ao cliente com base nas FAQs do website.")

    faq_count = None
    with st.spinner("A verificar as FAQs e os contactos..."):
        try:
            faq_count = prepare_website_data()
        except Exception:
            LOGGER.exception("Website startup check failed; chat remains blocked")

    with st.sidebar:
        st.header("Estado")
        if faq_count is not None:
            st.caption(f"Corpus carregado · {faq_count} FAQs disponíveis")
            st.caption("A base vetorial é preparada quando envia uma pergunta válida.")
        else:
            st.caption("Corpus de FAQs indisponível")
            st.button("Tentar novamente", icon=":material/refresh:", key="retry_startup")
        st.button(
            "Reiniciar conversa",
            on_click=reset_conversation,
            icon=":material/restart_alt:",
            width="stretch",
        )

    for message in st.session_state.messages:
        render_message(message)

    if faq_count is None:
        st.error("Não foi possível verificar as FAQs e os contactos. Tente novamente.")
        st.chat_input("Escreva a sua pergunta", disabled=True, submit_mode="disable")
        return

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
