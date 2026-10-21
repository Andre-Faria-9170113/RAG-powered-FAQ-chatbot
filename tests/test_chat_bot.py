import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from chat_bot import FAQResponse, generate_faq_answer, lexical_search, load_faq_records


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
                {"role": "assistant", "content": "Welcome"},
                {"role": "user", "content": "How do I publish an ad?"},
                {"role": "assistant", "content": "Use the publish form."},
            ],
        )
        self.assertTrue(all(set(message) == {"role", "content"} for message in api_input))
        self.assertIn("And then?", api_input[-1]["content"])
        self.assertEqual(history, original_history)


if __name__ == "__main__":
    unittest.main()
