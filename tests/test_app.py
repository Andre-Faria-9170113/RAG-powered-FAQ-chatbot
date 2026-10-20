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


if __name__ == "__main__":
    unittest.main()
