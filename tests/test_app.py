from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from chat_bot import FAQResponse, QuestionValidation

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class StreamlitAppTest(unittest.TestCase):
    def test_initial_render_does_not_require_openai_call(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Assistente CustoJusto")
        self.assertIn("Olá! Posso ajudar", app.markdown[0].value)
        self.assertEqual(len(app.chat_input), 1)
        self.assertEqual(len(app.pills), 1)

    def test_suggestions_disappear_after_user_turn(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        app.session_state["messages"] = [
            {"role": "assistant", "content": "Olá"},
            {"role": "user", "content": "Como pesquisar um anúncio?"},
            {"role": "assistant", "content": "Resposta de teste"},
        ]
        app.run()

        self.assertFalse(app.exception)
        self.assertEqual(len(app.pills), 0)
        self.assertEqual(len(app.chat_input), 1)

    def test_compaction_keeps_full_transcript_and_reset_clears_both_histories(self) -> None:
        response = FAQResponse(
            confidence_score="high",
            relevant_contact_form="none",
            suggested_response="Resposta com fontes",
        )
        validation = QuestionValidation(
            is_valid_question=True,
            standalone_question="Pergunta",
            suggested_response="",
        )

        def compact(_client, history):
            if len(history) <= 10:
                return history
            return [
                *history[:2],
                {"role": "assistant", "content": "Resumo de teste"},
                *history[-2:],
            ]

        with (
            patch("openai.OpenAI", return_value=Mock()),
            patch(
                "chat_bot.answer_user_question",
                return_value=(response.suggested_response, response, validation),
            ) as answer,
            patch("chat_bot.compact_history_if_needed", side_effect=compact),
        ):
            app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
            for turn in range(6):
                app.chat_input[0].set_value(f"Pergunta {turn}").run()
                self.assertFalse(app.exception)

            self.assertEqual(len(app.session_state["messages"]), 13)
            self.assertEqual(len(app.session_state["context_history"]), 5)
            self.assertEqual(len(app.chat_message), 13)
            self.assertEqual(len(app.expander), 6)
            self.assertNotIn("Resumo de teste", [m.value for m in app.markdown])

            app.chat_input[0].set_value("Mais uma pergunta").run()
            self.assertFalse(app.exception)
            sent_history = answer.call_args.args[1]
            self.assertTrue(
                any(m["content"] == "Resumo de teste" for m in sent_history)
            )
            self.assertTrue(all(set(m) == {"role", "content"} for m in sent_history))

            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.session_state["messages"]), 1)
            self.assertEqual(app.session_state["context_history"], [])
            self.assertEqual(len(app.pills), 1)

    def test_failed_summary_retains_context_and_transcript(self) -> None:
        validation = QuestionValidation(
            is_valid_question=False, suggested_response="Resposta"
        )
        with (
            patch("openai.OpenAI", return_value=Mock()),
            patch(
                "chat_bot.answer_user_question", return_value=("Resposta", None, validation)
            ),
            patch(
                "chat_bot.compact_history_if_needed",
                side_effect=RuntimeError("Summary unavailable"),
            ),
            self.assertLogs("__main__", level="ERROR"),
        ):
            app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
            app.chat_input[0].set_value("Pergunta").run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.session_state["messages"]), 3)
            self.assertEqual(len(app.session_state["context_history"]), 2)


if __name__ == "__main__":
    unittest.main()
