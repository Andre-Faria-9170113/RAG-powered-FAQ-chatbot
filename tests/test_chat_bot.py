import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chat_bot import (
    AnswerSource,
    FAQResponse,
    QuestionValidation,
    answer_user_question,
    compact_history_if_needed,
    generate_faq_answer,
    lexical_search,
    load_faq_records,
    validate_question,
)


class RetrievalTest(unittest.TestCase):
    def test_lexical_search_finds_new_chat_faqs(self) -> None:
        results = lexical_search(load_faq_records(), "Tenho uma questão sobre o novo chat")

        questions = {result.question for result in results}
        self.assertIn("O que mudou na nova versão da plataforma de chat?", questions)
        self.assertIn("Que alterações têm este novo chat?", questions)


class ConversationTest(unittest.TestCase):
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
                    suggested_response="",
                    standalone_question=first_question,
                )
            ),
            SimpleNamespace(output_parsed=first_answer),
            SimpleNamespace(
                output_parsed=QuestionValidation(
                    is_valid_question=True,
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
            _, answer, _ = answer_user_question(follow_up, history, client)

        self.assertEqual(embed.call_args.args[1], [resolved])
        self.assertEqual(lexical.call_args.args[1], resolved)
        validation_input = client.responses.parse.call_args_list[2].kwargs["input"]
        self.assertEqual(validation_input[0]["content"], first_question)
        self.assertEqual(validation_input[-1]["content"], follow_up)
        self.assertNotIn("details", validation_input[1])
        self.assertEqual(answer, second_answer)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_rejected_or_ambiguous_turn_does_not_retrieve(self) -> None:
        for valid, clarify in [(False, False), (True, True)]:
            with self.subTest(valid=valid, clarify=clarify):
                client = Mock()
                client.responses.parse.return_value = SimpleNamespace(
                    output_parsed=QuestionValidation(
                        is_valid_question=valid,
                        needs_clarification=clarify,
                        suggested_response="Qual é o tópico?",
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


if __name__ == "__main__":
    unittest.main()
