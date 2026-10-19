"""Streamlit interface for the classifieds FAQ RAG chatbot."""

from __future__ import annotations

import streamlit as st
from openai import OpenAI

from chat_bot import (
    FAQResponse,
    answer_user_question,
    compact_history_if_needed,
    ensure_vector_store,
    load_faq_records,
)


PROJECT_TITLE = "Assistente CustoJusto"
WELCOME_MESSAGE = (
    "Olá! Posso ajudar com dúvidas sobre anúncios, pesquisas, conta, contactos, "
    "serviços pagos e outras FAQs do website."
)


def initialise_session_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [{"role": "assistant", "content": WELCOME_MESSAGE}]
    if "last_structured_answer" not in st.session_state:
        st.session_state.last_structured_answer = None


def reset_conversation() -> None:
    st.session_state.messages = [{"role": "assistant", "content": WELCOME_MESSAGE}]
    st.session_state.last_structured_answer = None


@st.cache_resource(show_spinner=False)
def cached_client() -> OpenAI:
    return OpenAI()


@st.cache_resource(show_spinner=False)
def warm_vector_store() -> int:
    client = cached_client()
    records = load_faq_records()
    ensure_vector_store(client, records)
    return len(records)


def render_message(role: str, content: str) -> None:
    with st.chat_message(role):
        st.markdown(content)


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
        if answer.relevant_contact_form != "none":
            st.write(f"Encaminhamento: `{answer.relevant_contact_form}`")


def main() -> None:
    st.set_page_config(page_title=PROJECT_TITLE, page_icon="💬", layout="centered")
    initialise_session_state()

    with st.sidebar:
        st.header("Estado")
        try:
            faq_count = warm_vector_store()
            st.caption(f"Base vetorial pronta · {faq_count} FAQs indexadas")
        except Exception as exc:
            st.caption("Base vetorial ainda não inicializada")
            st.error(str(exc))
        st.button("Reiniciar conversa", on_click=reset_conversation, use_container_width=True)

    st.title(PROJECT_TITLE)
    st.caption("Chatbot RAG para apoio ao cliente com base nas FAQs do website.")

    for message in st.session_state.messages:
        render_message(message["role"], message["content"])

    render_answer_details(st.session_state.last_structured_answer)

    prompt = st.chat_input("Escreva a sua pergunta")
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    render_message("user", prompt)

    with st.chat_message("assistant"):
        with st.spinner("A validar o pedido e a consultar as FAQs..."):
            try:
                answer, structured_answer, _validation = answer_user_question(
                    prompt,
                    st.session_state.messages[:-1],
                    client=cached_client(),
                )
                st.session_state.last_structured_answer = structured_answer
                st.markdown(answer)
            except Exception as exc:
                answer = (
                    "Não consegui processar o pedido neste momento. "
                    f"Detalhe técnico: `{exc}`"
                )
                st.session_state.last_structured_answer = None
                st.error(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})

    try:
        st.session_state.messages = compact_history_if_needed(
            cached_client(),
            st.session_state.messages,
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
