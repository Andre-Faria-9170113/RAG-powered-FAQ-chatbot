from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest


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


if __name__ == "__main__":
    unittest.main()
