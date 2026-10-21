import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from chat_bot import FAQRecord, FAQResponse, QuestionValidation
from contact_forms import CONTACT_FORMS, CONTACT_LABELS

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class StreamlitAppTest(unittest.TestCase):
    def setUp(self) -> None:
        st.cache_resource.clear()
        self.addCleanup(st.cache_resource.clear)
        refresh = patch("refresh_website_data.refresh", return_value=False)
        self.refresh = refresh.start()
        self.addCleanup(refresh.stop)

    def test_fresh_app_recovers_when_all_three_data_files_are_absent(self) -> None:
        root = APP_PATH.parent
        contacts = json.loads((root / "data" / "contact_forms.json").read_text(encoding="utf-8"))
        faqs = json.loads((root / "data" / "faqs.json").read_text(encoding="utf-8"))
        faqs["mainEntity"] = faqs["mainEntity"][:1]
        script = '''
import json
from pathlib import Path
import sys
from unittest.mock import patch
import httpx
from streamlit.testing.v1 import AppTest
import app
import contact_forms

fixtures = json.loads(sys.argv[1])
forms = fixtures["contacts"]["forms"]
requests = []
assert not contact_forms.CONTACT_CATALOG
assert not Path("data").exists()

def respond(client, url):
    request = httpx.Request("GET", url)
    requests.append(request)
    if "section=faq" in url:
        body = '<script id="faqs" type="application/ld+json">' + json.dumps(fixtures["faqs"]) + '</script>'
    elif "section" not in request.url.params:
        body = ''.join('<a href="?section=' + f["section"] + '">' + f["label"] + '</a>' for f in forms)
    else:
        form = next(f for f in forms if f["section"] == request.url.params["section"])
        body = '<h1>' + form["label"] + '</h1><select>' + ''.join(
            '<option value="' + s["value"] + '">' + s["label"] + '</option>' for s in form["subjects"]
        ) + '</select>'
    html = '<meta name="version" content="fixture-v1">' + body
    return httpx.Response(200, text=html, request=request)

with patch("httpx.Client.get", autospec=True, side_effect=respond):
    with patch("chat_bot.create_embeddings") as embed:
        tested = AppTest.from_file("app.py", default_timeout=30).run()
        assert not tested.exception, str(tested.exception)
        assert not tested.error, str(tested.error)
        assert not tested.chat_input[0].disabled
        assert len(contact_forms.CONTACT_FORMS) == len(forms)
        assert len(requests) == 12
        for name in ("faqs.json", "contact_forms.json", "website_snapshot.json"):
            assert (Path("data") / name).is_file(), name
        tested.run()
        assert len(requests) == 12
        embed.assert_not_called()
print("Fresh bootstrap verified")
'''
        with tempfile.TemporaryDirectory() as directory:
            for name in (
                "app.py", "chat_bot.py", "contact_forms.py", "refresh_website_data.py",
                "scrape_contact_forms.py", "scrape_faqs.py", "website_scraping.py",
            ):
                shutil.copy2(root / name, Path(directory) / name)
            result = subprocess.run(
                [sys.executable, "-c", script, json.dumps({"contacts": contacts, "faqs": faqs})],
                cwd=directory, capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Fresh bootstrap verified", result.stdout)

    def test_initial_render_does_not_require_openai_call(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()

        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Assistente CustoJusto")
        self.assertIn("Olá! Posso ajudar", app.markdown[0].value)
        self.assertEqual(len(app.chat_input), 1)
        self.assertEqual(len(app.pills), 1)
        self.refresh.assert_called_once()

    def test_answer_details_link_each_official_contact_form(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        for destination, url in CONTACT_FORMS.items():
            with self.subTest(destination=destination):
                answer = FAQResponse(
                    confidence_score="low", relevant_contact_form=destination,
                    suggested_response="Contacte a equipa.",
                )
                app.session_state["messages"] = [
                    {"role": "user", "content": "Preciso de apoio."},
                    {"role": "assistant", "content": answer.suggested_response,
                     "details": answer.model_dump()},
                ]
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(
                    app.expander[0].markdown[-1].value,
                    f"Encaminhamento: [{CONTACT_LABELS[destination]}]({url})",
                )

    def test_answer_details_show_nenhum_without_a_link_when_not_referred(self) -> None:
        answer = FAQResponse(
            confidence_score="high", relevant_contact_form="none",
            suggested_response="Resposta da FAQ.",
        )
        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        app.session_state["messages"] = [
            {"role": "user", "content": "Tenho uma pergunta."},
            {"role": "assistant", "content": answer.suggested_response,
             "details": answer.model_dump()},
        ]
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.expander[0].markdown[-1].value, "Encaminhamento: Nenhum")

    def test_startup_check_is_shared_across_reruns_resets_and_sessions(self) -> None:
        app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        app.run()
        app.button[0].click().run()
        another_session = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        self.assertFalse(app.exception)
        self.assertFalse(another_session.exception)
        self.refresh.assert_called_once()

    def test_startup_failure_blocks_chat_and_is_retried_until_success(self) -> None:
        self.refresh.side_effect = [RuntimeError("Website unavailable"), False]
        with (
            patch("chat_bot.answer_user_question") as answer,
            patch("chat_bot.create_embeddings") as embed,
            patch("contact_forms.reload_contact_forms") as reload_contacts,
        ):
            app = AppTest.from_file(str(APP_PATH), default_timeout=10)
            app.session_state["pending_prompt"] = "Como publicar um anúncio?"
            with self.assertLogs("__main__", level="ERROR"):
                app.run()
            self.assertFalse(app.exception)
            self.assertTrue(app.chat_input[0].disabled)
            self.assertEqual(len(app.pills), 0)
            self.assertTrue(app.error)
            answer.assert_not_called()
            embed.assert_not_called()
            reload_contacts.assert_not_called()

            app.session_state["pending_prompt"] = None
            app.button(key="retry_startup").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.chat_input[0].disabled)
            self.assertEqual(len(app.pills), 1)
            self.assertEqual(self.refresh.call_count, 2)
            reload_contacts.assert_called_once()
            app.run()
            self.assertEqual(self.refresh.call_count, 2)

    def test_refresh_and_reload_precede_first_faq_read_and_embedding(self) -> None:
        events = []
        self.refresh.side_effect = lambda _client: events.append("refresh")
        validation = QuestionValidation(
            is_valid_question=True, standalone_question="Pergunta", suggested_response=""
        )
        response = FAQResponse(
            confidence_score="low", relevant_contact_form="outros", suggested_response="Contacte a equipa."
        )
        record = FAQRecord(
            chunk_id="faq-001", question="Pergunta atualizada", answer="Resposta atualizada",
            text="Pergunta: Pergunta atualizada\nResposta: Resposta atualizada",
        )
        collection = Mock()
        collection.get.return_value = {"ids": []}
        collection.query.return_value = {"ids": [[]], "metadatas": [[]], "distances": [[]]}
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("contact_forms.reload_contact_forms", side_effect=lambda: events.append("reload")),
            patch("chat_bot.load_faq_records", side_effect=lambda: events.append("read_faqs") or [record]),
            patch("openai.OpenAI", return_value=Mock()),
            patch("chat_bot.validate_question", return_value=validation),
            patch("chat_bot.get_collection", return_value=collection),
            patch("chat_bot.VERSION_PATH", Path(directory) / "version.txt"),
            patch("chat_bot.create_embeddings", side_effect=lambda *_: events.append("embed") or [[1.0]]) as embed,
            patch("chat_bot.generate_faq_answer", return_value=response),
            patch("chat_bot.compact_history_if_needed", side_effect=lambda _client, history: history),
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
        ):
            app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
            self.assertEqual(events, ["refresh", "reload", "read_faqs"])
            app.chat_input[0].set_value("Pergunta").run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertLess(events.index("refresh"), events.index("embed"))
            self.assertLess(events.index("reload"), events.index("embed"))
            self.assertEqual(embed.call_args_list[0].args[1], [record.text])
            self.assertEqual(embed.call_count, 2)
            self.refresh.assert_called_once()

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
