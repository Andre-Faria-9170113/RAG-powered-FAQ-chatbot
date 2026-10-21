import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from typing import get_args
from unittest.mock import Mock, patch

from pydantic import ValidationError

from chat_bot import (
    AnswerSource,
    FAQResponse,
    QuestionValidation,
    RequestCategory,
    answer_user_question,
    append_contact_link,
    compact_history_if_needed,
    generate_faq_answer,
    lexical_search,
    load_faq_records,
    validate_question,
)


class TriageContractTest(unittest.TestCase):
    def test_categories_match_contact_topics_plus_unknown_and_rejected(self) -> None:
        from contact_forms import ContactFormKey

        self.assertEqual(
            set(get_args(RequestCategory)),
            (set(get_args(ContactFormKey)) - {"none"}) | {"indeterminado", "fora_ambito"},
        )

    def test_category_is_required_and_rejections_have_a_distinct_category(self) -> None:
        base = {"is_valid_question": True, "urgency": "low", "urgency_reason": "Pedido informativo.", "suggested_response": "", "standalone_question": "Pergunta"}
        for update in ({}, {"category": "none"}, {"category": "invented"}, {"category": "fora_ambito"}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                QuestionValidation.model_validate(base | update)
        with self.assertRaises(ValidationError):
            QuestionValidation(is_valid_question=False, category="outros", urgency="not_applicable", urgency_reason="Recusa.", suggested_response="Recusa")
        rejection = QuestionValidation(is_valid_question=False, category="fora_ambito", urgency="not_applicable", urgency_reason="Recusa.", suggested_response="Recusa")
        self.assertEqual(rejection.category, "fora_ambito")

    def test_known_category_does_not_prevent_clarification(self) -> None:
        validation = QuestionValidation(
            is_valid_question=True, needs_clarification=True, category="pagamentos_servicos",
            urgency="unknown", urgency_reason="Faltam sintomas e impacto.",
            suggested_response="O que aconteceu com o pagamento?",
        )
        self.assertTrue(validation.needs_clarification)
        self.assertEqual(validation.category, "pagamentos_servicos")

    def test_validation_rejects_contradictory_flags_and_empty_early_replies(self) -> None:
        invalid = [
            {"is_valid_question": False, "needs_clarification": True, "suggested_response": "A que opção se refere?"},
            {"is_valid_question": False, "needs_clarification": True, "suggested_response": ""},
            {"is_valid_question": False, "suggested_response": ""},
            {"is_valid_question": False, "suggested_response": " \n "},
            {"is_valid_question": True, "needs_clarification": True, "suggested_response": ""},
            {"is_valid_question": True, "needs_clarification": True, "suggested_response": " \n "},
            {"is_valid_question": True, "suggested_response": "", "standalone_question": " "},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                QuestionValidation.model_validate(payload | {
                    "category": "indeterminado" if payload["is_valid_question"] else "fora_ambito",
                    "urgency": "unknown" if payload["is_valid_question"] else "not_applicable",
                    "urgency_reason": "Sem impacto definido ou pedido rejeitado.",
                })

    def test_action_is_required_and_inconsistent_triage_is_rejected(self) -> None:
        base = {
            "confidence_score": "high", "relevant_contact_form": "none",
            "suggested_response": "Resposta.",
        }
        invalid = [
            {},
            {"action": "escalate"},
            {"action": "escalate", "relevant_contact_form": "outros", "missing_info": ["Qual o erro?"]},
            {"action": "answer", "relevant_contact_form": "outros"},
            {"action": "answer", "missing_info": ["Qual o erro?"]},
            {"action": "answer", "missing_knowledge": ["Acesso à conta"]},
            {"action": "clarify"},
            {"action": "clarify", "missing_info": ["Qual o erro?"], "relevant_contact_form": "outros"},
        ]
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(ValidationError):
                FAQResponse.model_validate(base | update)

    def test_knowledge_gaps_and_faq_supported_referrals_both_produce_links(self) -> None:
        for confidence, gaps, sources in (
            ("low", ["Procedimento de revisão manual não documentado"], []),
            ("high", [], [AnswerSource(chunk_id="faq-059", question="Como obter apoio?")]),
        ):
            with self.subTest(confidence=confidence):
                answer = FAQResponse(
                    action="escalate", confidence_score=confidence,
                    relevant_contact_form="problemas_tecnicos", missing_knowledge=gaps,
                    sources=sources, suggested_response="Contacte a equipa.",
                )
                self.assertIn("section=8", append_contact_link(answer))
        for action, details in (("answer", []), ("clarify", ["Qual o erro?"])):
            answer = FAQResponse(
                action=action, confidence_score="low", missing_info=details,
                relevant_contact_form="none", suggested_response="Resposta ou pergunta.",
            )
            self.assertEqual(append_contact_link(answer), answer.suggested_response)


class RetrievalTest(unittest.TestCase):
    def test_lexical_search_finds_new_chat_faqs(self) -> None:
        results = lexical_search(load_faq_records(), "Tenho uma questão sobre o novo chat")

        questions = {result.question for result in results}
        self.assertIn("O que mudou na nova versão da plataforma de chat?", questions)
        self.assertIn("Que alterações têm este novo chat?", questions)


class ConversationTest(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_generation_keeps_original_troubleshooting_even_if_rewrite_omits_it(self) -> None:
        question = (
            "As mensagens para o anunciante não chegam. Confirmámos as contas e "
            "o spam e testámos app e web. Preciso que a equipa verifique a entrega."
        )
        rewritten = "Como resolver mensagens que não chegam ao anunciante?"
        answer = FAQResponse(
            action="escalate", confidence_score="high",
            relevant_contact_form="contactos_anuncios",
            suggested_response="Contacte a equipa para investigar a entrega.",
            missing_knowledge=["Sem acesso aos registos de entrega."],
        )
        client = Mock()
        client.responses.parse.side_effect = [
            SimpleNamespace(output_parsed=QuestionValidation(
                is_valid_question=True, category="contactos_anuncios", suggested_response="", standalone_question=rewritten,
                urgency="normal", urgency_reason="Falha de entrega persistente.",
            )),
            SimpleNamespace(output_parsed=answer),
        ]
        with (
            patch("chat_bot.ensure_vector_store"),
            patch("chat_bot.retrieve_faqs", return_value=[]) as retrieve,
        ):
            text, structured, validation = answer_user_question(question, [], client)
        self.assertEqual(retrieve.call_args.args[2], rewritten)
        generation = client.responses.parse.call_args.kwargs
        self.assertIn(question, generation["input"][-1]["content"])
        self.assertIn(rewritten, generation["input"][-1]["content"])
        self.assertIn("section=6", text)
        self.assertEqual(structured.action, "escalate")
        self.assertFalse(validation.needs_clarification)

    def test_second_message_sends_history_without_ui_metadata(self) -> None:
        history = [
            {"role": "assistant", "content": "Welcome"},
            {"role": "user", "content": "How do I publish an ad?"},
            {
                "role": "assistant",
                "content": "Use the publish form.",
                "details": {"sources": [{"chunk_id": "faq-001"}]},
            },
        ]
        original_history = deepcopy(history)
        client = Mock()
        client.responses.parse.return_value = SimpleNamespace(
            output_parsed=FAQResponse(
                action="clarify", missing_info=["Which step needs help?"],
                confidence_score="low",
                relevant_contact_form="none",
                suggested_response="Which step do you need help with?",
            )
        )

        generate_faq_answer(client, "And then?", [], history)

        api_input = client.responses.parse.call_args.kwargs["input"]
        self.assertEqual(
            api_input[:-1],
            [
                {"role": "user", "content": "How do I publish an ad?"},
                {"role": "assistant", "content": "Use the publish form."},
            ],
        )
        self.assertTrue(all(set(message) == {"role", "content"} for message in api_input))
        self.assertIn("And then?", api_input[-1]["content"])
        self.assertEqual(history, original_history)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_follow_up_uses_resolved_question_in_both_retrievers(self) -> None:
        first_question = "Tenho dúvidas sobre o novo chat."
        follow_up = "o que tem este feature de novo?"
        resolved = "Que novidades tem o novo chat do CustoJusto?"
        first_answer = FAQResponse(
            action="answer",
            sources=[
                AnswerSource(
                    chunk_id="faq-056", question="Que alterações têm este novo chat?"
                )
            ],
            confidence_score="high",
            relevant_contact_form="none",
            suggested_response="Tem dúvidas sobre as novidades do chat?",
        )
        second_answer = first_answer.model_copy(
            update={
                "suggested_response": "Pode pesquisar conversas e editar ou apagar mensagens."
            }
        )
        client = Mock()
        client.responses.parse.side_effect = [
            SimpleNamespace(
                output_parsed=QuestionValidation(
                    is_valid_question=True,
                    category="contactos_anuncios",
                    urgency="low", urgency_reason="Pedido informativo sobre o chat.",
                    suggested_response="",
                    standalone_question=first_question,
                )
            ),
            SimpleNamespace(output_parsed=first_answer),
            SimpleNamespace(
                output_parsed=QuestionValidation(
                    is_valid_question=True,
                    category="contactos_anuncios",
                    urgency="low", urgency_reason="Pedido informativo sobre o chat.",
                    suggested_response="",
                    standalone_question=resolved,
                )
            ),
            SimpleNamespace(output_parsed=second_answer),
        ]
        collection = Mock()
        collection.query.return_value = {
            "ids": [[]], "metadatas": [[]], "distances": [[]]
        }
        history = []
        with (
            patch("chat_bot.ensure_vector_store", return_value=collection),
            patch("chat_bot.create_embeddings", return_value=[[1.0]]) as embed,
            patch("chat_bot.lexical_search", wraps=lexical_search) as lexical,
        ):
            text, _, _ = answer_user_question(first_question, history, client)
            history.extend(
                [
                    {"role": "user", "content": first_question},
                    {
                        "role": "assistant",
                        "content": text,
                        "details": first_answer.model_dump(),
                    },
                ]
            )
            _, answer, validation = answer_user_question(follow_up, history, client)

        self.assertEqual(embed.call_args.args[1], [resolved])
        self.assertEqual(lexical.call_args.args[1], resolved)
        validation_input = client.responses.parse.call_args_list[2].kwargs["input"]
        self.assertEqual(validation_input[0]["content"], first_question)
        self.assertEqual(validation_input[-1]["content"], follow_up)
        self.assertNotIn("details", validation_input[1])
        self.assertEqual(answer, second_answer)
        self.assertEqual(validation.category, "contactos_anuncios")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_rejected_or_ambiguous_turn_does_not_retrieve(self) -> None:
        for valid, clarify in [(False, False), (True, True)]:
            with self.subTest(valid=valid, clarify=clarify):
                client = Mock()
                client.responses.parse.return_value = SimpleNamespace(
                    output_parsed=QuestionValidation(
                        is_valid_question=valid,
                        category="indeterminado" if valid else "fora_ambito",
                        urgency="unknown" if valid else "not_applicable", urgency_reason="Tópico desconhecido ou rejeitado.",
                        needs_clarification=clarify,
                        suggested_response="Qual é o tópico?",
                        standalone_question="Unused question that must not trigger retrieval",
                    )
                )
                with (
                    patch("chat_bot.ensure_vector_store") as index,
                    patch("chat_bot.retrieve_faqs") as retrieve,
                ):
                    text, answer, _ = answer_user_question("E isso?", [], client)
                self.assertEqual(text, "Qual é o tópico?")
                self.assertIsNone(answer)
                index.assert_not_called()
                retrieve.assert_not_called()

    def test_validation_prompt_separates_unresolved_references_from_rejection(self) -> None:
        clarification = QuestionValidation(
            is_valid_question=True, needs_clarification=True,
            category="indeterminado",
            urgency="unknown", urgency_reason="Não é possível identificar a opção.",
            suggested_response="A que opção se refere?",
        )
        client = Mock()
        client.responses.parse.return_value = SimpleNamespace(output_parsed=clarification)
        result = validate_question(client, "E aquela opção, como funciona?")
        self.assertEqual(result, clarification)
        instructions = client.responses.parse.call_args.kwargs["instructions"]
        self.assertIn("is_valid_question=true, needs_clarification=true", instructions)
        self.assertIn("Nunca combines is_valid_question=false com needs_clarification=true", instructions)
        self.assertIn("recusa breve e educada", instructions)
        self.assertIn("não deixes o utilizador sem resposta", instructions)
        self.assertIn("Se faltarem a intenção ou os sintomas", instructions)
        self.assertIn("transformes um problema vago numa intenção concreta", instructions)
        self.assertIn("não basta para presumir a operação", instructions)
        self.assertIn("A categoria não altera as regras", instructions)
        self.assertIn("um novo tópico explícito prevalece", instructions)
        self.assertIn("não uses indeterminado só por faltar esse detalhe", instructions)

    def test_api_failures_and_missing_parsed_responses_are_not_repaired(self) -> None:
        for failure in (RuntimeError("API failure"), SimpleNamespace(output_parsed=None)):
            client = Mock()
            if isinstance(failure, Exception):
                client.responses.parse.side_effect = failure
            else:
                client.responses.parse.return_value = failure
            with self.subTest(failure=failure), self.assertRaises(RuntimeError):
                validate_question(client, "Pergunta")
            self.assertEqual(client.responses.parse.call_count, 1)

    def test_summary_reaches_validation_and_generation_without_truncation(self) -> None:
        history = [
            {"role": "user", "content": "First request"},
            {"role": "assistant", "content": "First answer"},
            {
                "role": "assistant",
                "content": "Resumo do contexto anterior: topic is the new chat",
            },
            *[
                {"role": role, "content": f"Recent message {index}"}
                for index, role in enumerate(["user", "assistant"] * 3)
            ],
        ]
        client = Mock()
        client.responses.parse.return_value = SimpleNamespace(
            output_parsed=QuestionValidation(
                is_valid_question=True,
                category="contactos_anuncios",
                urgency="low", urgency_reason="Pedido informativo sobre o chat.",
                standalone_question="What is new in the chat?",
                suggested_response="",
            )
        )
        validate_question(client, "What is new?", history)
        self.assertEqual(
            client.responses.parse.call_args.kwargs["input"][:-1], history
        )
        client.responses.parse.return_value = SimpleNamespace(
            output_parsed=FAQResponse(
                action="clarify", missing_info=["Which aspect needs help?"],
                confidence_score="low",
                relevant_contact_form="none",
                suggested_response="Please clarify",
            )
        )
        generate_faq_answer(client, "What is new in the chat?", [], history)
        self.assertEqual(
            client.responses.parse.call_args.kwargs["input"][:-1], history
        )


class HistoryCompactionTest(unittest.TestCase):
    def make_history(self, turns: int) -> list[dict[str, str]]:
        return [
            {"role": role, "content": f"{role} turn {turn}"}
            for turn in range(turns)
            for role in ("user", "assistant")
        ]

    def test_short_history_does_not_summarize_or_count_greeting(self) -> None:
        client = Mock()
        turns = self.make_history(5)
        history = [{"role": "assistant", "content": "Welcome"}, *turns]
        self.assertEqual(compact_history_if_needed(client, history), turns)
        client.responses.create.assert_not_called()

    def test_repeated_compaction_preserves_first_exchange_and_previous_summary(self) -> None:
        client = Mock()
        client.responses.create.side_effect = [
            SimpleNamespace(output_text="Earlier discussion about the new chat"),
            SimpleNamespace(output_text="Updated discussion, still about the new chat"),
        ]
        turns = self.make_history(6)
        history = [{"role": "assistant", "content": "Welcome"}, *turns]
        original = deepcopy(history)
        compact = compact_history_if_needed(client, history)
        self.assertEqual(compact[:2], turns[:2])
        self.assertEqual(compact[-2:], turns[-2:])
        self.assertEqual(len(compact), 5)
        transcript = client.responses.create.call_args.kwargs["input"]
        self.assertIn("user turn 1", transcript)
        self.assertNotIn("user turn 0", transcript)
        self.assertNotIn("user turn 5", transcript)
        self.assertEqual(history, original)

        expanded = [*compact, *self.make_history(3)]
        compact_again = compact_history_if_needed(client, expanded)
        self.assertEqual(compact_again[:2], turns[:2])
        self.assertEqual(compact_again[-2:], expanded[-2:])
        self.assertIn(
            "Earlier discussion about the new chat",
            client.responses.create.call_args.kwargs["input"],
        )

    def test_empty_summary_fails_without_modifying_history(self) -> None:
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output_text="   ")
        history = self.make_history(6)
        original = deepcopy(history)
        with self.assertRaisesRegex(RuntimeError, "summary"):
            compact_history_if_needed(client, history)
        self.assertEqual(history, original)


class UrgencyContractTest(unittest.TestCase):
    def test_assessment_is_required_and_explained(self):
        base = {
            "is_valid_question": True, "category": "pagamentos_servicos",
            "needs_clarification": True, "suggested_response": "O que aconteceu?",
            "urgency": "unknown", "urgency_reason": "Faltam sintomas e impacto.",
        }
        invalid = [
            {key: value for key, value in base.items() if key != "urgency"},
            {key: value for key, value in base.items() if key != "urgency_reason"},
            base | {"urgency": "urgent"}, base | {"urgency": "not_applicable"},
            base | {"urgency_reason": ""}, base | {"urgency_reason": " \n "},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                QuestionValidation.model_validate(payload)
        for urgency in ("low", "normal", "high", "unknown"):
            with self.subTest(urgency=urgency):
                validation = QuestionValidation.model_validate(base | {"urgency": urgency})
                self.assertTrue(validation.needs_clarification)
        with self.assertRaises(ValidationError):
            QuestionValidation.model_validate(base | {
                "is_valid_question": False, "needs_clarification": False,
                "category": "fora_ambito", "urgency": "high",
            })

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_high_urgency_does_not_skip_essential_clarification(self):
        validation = QuestionValidation(
            is_valid_question=True, category="indeterminado", needs_clarification=True,
            urgency="high", urgency_reason="O utilizador relata prejuízo em curso, mas não identifica o problema.",
            suggested_response="O que está a acontecer no website?",
        )
        client = Mock()
        client.responses.parse.return_value = SimpleNamespace(output_parsed=validation)
        with patch("chat_bot.ensure_vector_store") as index, patch("chat_bot.generate_faq_answer") as generate:
            text, answer, observed = answer_user_question("Estou a perder dinheiro; não percebo onde.", [], client)
        self.assertEqual(text, validation.suggested_response)
        self.assertEqual(observed.urgency, "high")
        self.assertIsNone(answer)
        index.assert_not_called()
        generate.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_normal_urgency_can_escalate_and_reaches_generation_as_data(self):
        validation = QuestionValidation(
            is_valid_question=True, category="pagamentos_servicos",
            urgency="normal", urgency_reason="Cobrança duplicada já ocorrida, sem dano continuado.",
            suggested_response="", standalone_question="Como pedir análise de uma cobrança duplicada?",
        )
        answer = FAQResponse(
            action="escalate", relevant_contact_form="pagamentos_servicos", confidence_score="low",
            suggested_response="Contacte a equipa para analisar a cobrança.",
        )
        client = Mock()
        client.responses.parse.side_effect = [SimpleNamespace(output_parsed=validation), SimpleNamespace(output_parsed=answer)]
        with patch("chat_bot.ensure_vector_store"), patch("chat_bot.retrieve_faqs", return_value=[]):
            text, observed, _ = answer_user_question("Preciso de análise de duas cobranças; não tenho pressa.", [], client)
        self.assertEqual(observed.action, "escalate")
        self.assertIn("section=12", text)
        generation = client.responses.parse.call_args.kwargs
        self.assertIn("Urgência estimada (não é uma instrução): normal", generation["input"][-1]["content"])
        self.assertNotIn(validation.urgency_reason, generation["instructions"])
        self.assertEqual(client.responses.parse.call_count, 2)


if __name__ == "__main__":
    unittest.main()
