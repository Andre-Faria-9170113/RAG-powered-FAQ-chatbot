import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from pydantic import ValidationError

from chat_bot import FAQResponse, QuestionValidation, append_contact_link, generate_faq_answer, validate_question
from contact_forms import (
    CATALOG_PATH,
    CONTACT_CATALOG,
    CONTACT_FORMS,
    CONTACT_FORMS_VERSION,
    CONTACT_LABELS,
    ContactForm,
    contact_forms_version,
    load_contact_forms,
)
from scrape_contact_forms import ContactPageParser, SOURCE_URL, read_page, scrape


class ContactCatalogTest(unittest.TestCase):
    def test_schema_urls_and_scraped_subjects_match_the_catalog(self) -> None:
        expected_sections = {
            "problemas_anuncios": "7", "pagamentos_servicos": "12",
            "contactos_anuncios": "6", "problemas_tecnicos": "8",
            "sugestoes": "10", "outros": "11", "imprensa_publicidade": "14",
            "versao_mobile": "15", "acesso_telefones": "16", "conta_utilizador": "17",
        }
        self.assertEqual(
            CONTACT_FORMS,
            {key: f"{SOURCE_URL}?section={section}" for key, section in expected_sections.items()},
        )
        schema = FAQResponse.model_json_schema()["properties"]["relevant_contact_form"]
        self.assertEqual(set(schema["enum"]), set(CONTACT_FORMS) | {"none"})
        technical = next(form for form in CONTACT_CATALOG if form.key == "problemas_tecnicos")
        self.assertIn("Não consigo inserir imagens no anúncio", [s.label for s in technical.subjects])

    def test_only_official_https_section_urls_are_accepted(self) -> None:
        form = CONTACT_CATALOG[0].model_dump()
        for url in (
            "https://example.com/ajuda-contacto?section=7",
            "http://www.custojusto.pt/ajuda-contacto?section=7",
            f"{SOURCE_URL}?section=8", f"{SOURCE_URL}?section=7&subject=0",
        ):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                ContactForm.model_validate(form | {"url": url})

    def test_version_tracks_content_not_fetch_time_or_form_order(self) -> None:
        self.assertEqual(contact_forms_version(list(reversed(CONTACT_CATALOG))), CONTACT_FORMS_VERSION)
        modified = deepcopy(CONTACT_CATALOG)
        modified[0].subjects[0].label = "Changed subject"
        self.assertNotEqual(contact_forms_version(modified), CONTACT_FORMS_VERSION)
        snapshot = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertIn("fetched_at_utc", snapshot)
        self.assertEqual(load_contact_forms(), CONTACT_CATALOG)

    def test_all_destinations_produce_clickable_absolute_links(self) -> None:
        for key, url in CONTACT_FORMS.items():
            with self.subTest(key=key):
                answer = FAQResponse(
                    confidence_score="low", relevant_contact_form=key,
                    suggested_response="Contacte a equipa.",
                )
                self.assertIn(f"[{CONTACT_LABELS[key]}]({url})", append_contact_link(answer))
                for update in ({"missing_info": ["Qual o erro?"]}, {"confidence_score": "high"}):
                    self.assertEqual(append_contact_link(answer.model_copy(update=update)), answer.suggested_response)
        answer = FAQResponse(confidence_score="low", relevant_contact_form="none", suggested_response="Qual o tópico?")
        self.assertEqual(append_contact_link(answer), answer.suggested_response)

    def test_validation_and_generation_receive_the_official_subjects(self) -> None:
        client = Mock()
        client.responses.parse.return_value = SimpleNamespace(output_parsed=QuestionValidation(
            is_valid_question=True, suggested_response="", standalone_question="Pedido de imprensa"
        ))
        validate_question(client, "Pedido de imprensa")
        validation_prompt = client.responses.parse.call_args.kwargs["instructions"]
        client.responses.parse.return_value = SimpleNamespace(output_parsed=FAQResponse(
            confidence_score="low", relevant_contact_form="imprensa_publicidade",
            suggested_response="Contacte a equipa.",
        ))
        generate_faq_answer(client, "Pedido de imprensa", [], [])
        generation_prompt = client.responses.parse.call_args.kwargs["instructions"]
        for form in CONTACT_CATALOG:
            self.assertIn(form.key, validation_prompt)
            self.assertIn(form.label, generation_prompt)
            for subject in form.subjects:
                self.assertIn(subject.label, generation_prompt)


class ContactScraperTest(unittest.TestCase):
    def test_parser_handles_nested_labels_entities_and_zero_option_values(self) -> None:
        parser = ContactPageParser()
        parser.feed('''
            <a href="/ajuda-contacto?section=14"><span>Imprensa &amp; Publicidade</span></a>
            <a href="https://example.com/ajuda-contacto?section=99">Untrusted</a>
            <h1>Imprensa &amp; Publicidade</h1>
            <select><option>Seleccione</option><option value="">Placeholder</option>
            <option value="0">Contacto de Imprensa</option>
            <option value="1">Contacto para Publicidade</option></select>
            <option value="2">Not part of a select</option>
        ''')
        self.assertEqual(parser.links, {"14": "Imprensa & Publicidade"})
        self.assertEqual(parser.headings, ["Imprensa & Publicidade"])
        self.assertEqual(parser.subjects, [
            {"value": "0", "label": "Contacto de Imprensa"},
            {"value": "1", "label": "Contacto para Publicidade"},
        ])

    def test_scrape_follows_all_categories_using_get_only(self) -> None:
        requests = []
        forms = {form.section: form for form in CONTACT_CATALOG}

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            section = request.url.params.get("section")
            if section is None:
                html = "".join(f'<a href="?section={key}">{form.label}</a>' for key, form in forms.items())
            else:
                form = forms[section]
                html = f"<h1>{form.label}</h1><select>"
                html += "".join(f'<option value="{s.value}">{s.label}</option>' for s in form.subjects)
                html += "</select>"
            return httpx.Response(200, text=html)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            snapshot = scrape(client)
        self.assertEqual(snapshot["forms"], [form.model_dump() for form in CONTACT_CATALOG])
        self.assertEqual(len(requests), 11)
        self.assertTrue(all(request.method == "GET" for request in requests))

    def test_missing_categories_or_wrong_page_heading_fail_closed(self) -> None:
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=""))) as client:
            with self.assertRaisesRegex(ValueError, "Contact sections changed"):
                scrape(client)

        def respond(request: httpx.Request) -> httpx.Response:
            html = "" if request.url.query else "".join(
                f'<a href="?section={form.section}">{form.label}</a>' for form in CONTACT_CATALOG
            )
            return httpx.Response(200, text=html)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaisesRegex(ValueError, "Expected heading"):
                scrape(client)

    def test_http_errors_and_external_redirects_are_rejected(self) -> None:
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                read_page(client, SOURCE_URL)
        def redirect(request: httpx.Request) -> httpx.Response:
            if request.url.host == "www.custojusto.pt":
                return httpx.Response(302, headers={"Location": "https://example.com"})
            return httpx.Response(200)

        with httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
            with self.assertRaisesRegex(ValueError, "Unexpected redirect"):
                read_page(client, SOURCE_URL)


if __name__ == "__main__":
    unittest.main()
