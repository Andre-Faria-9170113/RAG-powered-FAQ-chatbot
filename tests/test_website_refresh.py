import json
import tempfile
import unittest
from copy import deepcopy
from io import StringIO
from itertools import combinations
from pathlib import Path
from unittest.mock import patch

import httpx

from contact_forms import CONTACT_CATALOG
from refresh_website_data import STATE_NAME, refresh
from scrape_contact_forms import SOURCE_URL as CONTACT_URL
from scrape_faqs import SOURCE_URL as FAQ_URL, parse_faqs, scrape
from website_scraping import fetch_html, website_version

FAQ = {
    "@context": "https://schema.org", "@type": "FAQPage",
    "mainEntity": [{
        "@type": "Question", "name": "Question & detail?",
        "acceptedAnswer": {"@type": "Answer", "text": '<p>Answer &amp; <a href="/ai">link</a>.</p>'},
    }],
}


def page(version="v1", body="") -> str:
    return f'<html><head><meta content="{version}" name="version"/></head><body>{body}</body></html>'


def faq_page(version="v1", payload=None) -> str:
    return page(version, '<script type="application/ld+json" id="faqs">' + json.dumps(FAQ if payload is None else payload) + '</script>')


class MetadataTest(unittest.TestCase):
    def test_meta_attribute_order_self_closing_tags_and_unrelated_scripts(self) -> None:
        self.assertEqual(website_version(page("1288d905")), "1288d905")
        self.assertEqual(website_version('<META NAME="VERSION" content=" v2 ">'), "v2")
        self.assertEqual(parse_faqs('<script id="other">not JSON</script>' + faq_page()), FAQ)

    def test_missing_empty_or_duplicate_versions_fail_closed(self) -> None:
        for html in ("", '<meta name="version">', page(""), page() + page()):
            with self.subTest(html=html), self.assertRaisesRegex(ValueError, "meta\\[name=version\\]"):
                website_version(html)

    def test_script_json_is_preserved_without_html_entity_decoding(self) -> None:
        self.assertEqual(parse_faqs(faq_page()), FAQ)
        with self.assertRaisesRegex(ValueError, "script#faqs"):
            parse_faqs('<h4>Question</h4><p>Must not scrape accordions.</p>')
        with self.assertRaisesRegex(ValueError, "script#faqs"):
            parse_faqs(faq_page() + faq_page())
        with self.assertRaises(json.JSONDecodeError):
            parse_faqs('<script id="faqs">not JSON</script>')

    def test_invalid_or_empty_faqs_are_rejected(self) -> None:
        for payload in ([], {}, FAQ | {"mainEntity": []}, FAQ | {"mainEntity": [None]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_faqs(faq_page(payload=payload))
        for field in ("name", "acceptedAnswer"):
            payload = deepcopy(FAQ)
            payload["mainEntity"][0][field] = ""
            with self.assertRaises(ValueError):
                parse_faqs(faq_page(payload=payload))

    def test_external_sources_are_rejected_before_any_request(self) -> None:
        with httpx.Client(transport=httpx.MockTransport(lambda r: self.fail("Unexpected request"))) as client:
            with self.assertRaises(ValueError):
                fetch_html(client, "https://example.com/faq")

    def test_browser_is_only_used_when_script_is_absent(self) -> None:
        for html, expected_calls in ((faq_page(), 0), (page(), 1)):
            with self.subTest(expected_calls=expected_calls):
                with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html))) as client:
                    with patch("scrape_faqs.render_faq_page", return_value=faq_page()) as render:
                        self.assertEqual(scrape(client, "v1"), FAQ)
                        self.assertEqual(render.call_count, expected_calls)

    def test_browser_version_must_match_the_probe(self) -> None:
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page()))) as client:
            with patch("scrape_faqs.render_faq_page", return_value=faq_page("v2")):
                with self.assertRaisesRegex(ValueError, "version changed"):
                    scrape(client, "v1")


class RefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        output = patch("sys.stdout", new_callable=StringIO)
        self.output = output.start()
        self.addCleanup(output.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.requests = []
        self.version = "v1"
        self.page_version = None
        self.broken_faq = False
        self.broken_contact = False
        self.client = httpx.Client(transport=httpx.MockTransport(self.respond))
        self.addCleanup(self.client.close)

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        version = self.page_version or self.version
        if str(request.url) == FAQ_URL:
            if self.broken_faq:
                return httpx.Response(200, text=page(version, '<script id="faqs">invalid</script>'))
            return httpx.Response(200, text=faq_page(version))
        section = request.url.params.get("section")
        if section is None:
            body = "".join(f'<a href="?section={f.section}">{f.label}</a>' for f in CONTACT_CATALOG)
            return httpx.Response(200, text=page(self.version, body))
        form = next(f for f in CONTACT_CATALOG if f.section == section)
        heading = "Wrong page" if self.broken_contact else form.label
        body = f"<h1>{heading}</h1><select>" + "".join(
            f'<option value="{s.value}">{s.label}</option>' for s in form.subjects
        ) + "</select>"
        return httpx.Response(200, text=page(version, body))

    def files(self) -> dict:
        return {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.path.iterdir()}

    def test_bootstrap_then_unchanged_version_makes_one_request_and_no_writes(self) -> None:
        self.assertTrue(refresh(self.client, self.path))
        self.assertEqual(len(self.requests), 12)
        self.assertTrue(all(r.method == "GET" for r in self.requests))
        self.assertEqual(json.loads((self.path / "faqs.json").read_text()), FAQ)
        before = self.files()
        self.requests.clear()
        with patch("refresh_website_data.scrape_faqs") as faqs, patch("refresh_website_data.scrape_contacts") as contacts:
            self.assertFalse(refresh(self.client, self.path))
            faqs.assert_not_called()
            contacts.assert_not_called()
        self.assertEqual([str(r.url) for r in self.requests], [CONTACT_URL])
        self.assertEqual(self.files(), before)

    def test_version_change_refreshes_both_and_records_new_state(self) -> None:
        refresh(self.client, self.path)
        self.version = "v2"
        self.requests.clear()
        self.assertTrue(refresh(self.client, self.path))
        self.assertEqual(len(self.requests), 12)
        state = json.loads((self.path / STATE_NAME).read_text())
        self.assertEqual(state["website_version"], "v2")
        self.assertEqual(set(state["files"]), {"faqs.json", "contact_forms.json"})

    def test_every_combination_of_missing_files_is_recreated_on_unchanged_version(self) -> None:
        names = ("faqs.json", "contact_forms.json", STATE_NAME)
        refresh(self.client, self.path)
        for count in range(1, len(names) + 1):
            for missing in combinations(names, count):
                with self.subTest(missing=missing):
                    for name in missing:
                        (self.path / name).unlink()
                    self.requests.clear()
                    self.assertTrue(refresh(self.client, self.path))
                    self.assertEqual(len(self.requests), 12)
                    self.assertTrue(all((self.path / name).is_file() for name in names))
                    self.assertEqual(json.loads((self.path / "faqs.json").read_text()), FAQ)
                    state = json.loads((self.path / STATE_NAME).read_text())
                    self.assertEqual(state["website_version"], self.version)
                    before = self.files()
                    self.requests.clear()
                    self.assertFalse(refresh(self.client, self.path))
                    self.assertEqual(len(self.requests), 1)
                    self.assertEqual(self.files(), before)

    def test_failed_missing_file_recovery_preserves_surviving_files_and_can_retry(self) -> None:
        refresh(self.client, self.path)
        (self.path / "contact_forms.json").unlink()
        before = self.files()
        self.broken_contact = True
        with self.assertRaises(ValueError):
            refresh(self.client, self.path)
        self.assertEqual(self.files(), before)
        self.assertFalse((self.path / "contact_forms.json").exists())
        self.broken_contact = False
        self.assertTrue(refresh(self.client, self.path))
        self.assertTrue((self.path / "contact_forms.json").is_file())

    def test_missing_snapshot_does_not_allow_overwriting_edits_to_its_neighbor(self) -> None:
        refresh(self.client, self.path)
        (self.path / "contact_forms.json").unlink()
        (self.path / "faqs.json").write_text("Manually edited", encoding="utf-8")
        before = self.files()
        with self.assertRaisesRegex(ValueError, "modified"):
            refresh(self.client, self.path)
        self.assertEqual(self.files(), before)

    def test_invalid_faq_or_contact_leaves_both_snapshots_and_version_untouched(self) -> None:
        refresh(self.client, self.path)
        before = self.files()
        self.version = "v2"
        for field in ("broken_faq", "broken_contact"):
            setattr(self, field, True)
            with self.subTest(field=field), self.assertRaises(ValueError):
                refresh(self.client, self.path)
            self.assertEqual(self.files(), before)
            setattr(self, field, False)
        self.assertTrue(refresh(self.client, self.path))

    def test_mixed_deployment_versions_do_not_update_snapshots(self) -> None:
        refresh(self.client, self.path)
        before = self.files()
        self.version = "v2"
        self.page_version = "v3"
        with self.assertRaisesRegex(ValueError, "version changed"):
            refresh(self.client, self.path)
        self.assertEqual(self.files(), before)

    def test_mixed_contact_page_version_does_not_publish_valid_faqs(self) -> None:
        refresh(self.client, self.path)
        before = self.files()
        self.version = "v2"
        self.page_version = "v3"
        with patch("refresh_website_data.scrape_faqs", return_value=FAQ):
            with self.assertRaisesRegex(ValueError, "version changed"):
                refresh(self.client, self.path)
        self.assertEqual(self.files(), before)

    def test_changed_source_is_not_silently_reused_when_version_is_unchanged(self) -> None:
        refresh(self.client, self.path)
        before = self.files()
        with self.assertRaisesRegex(ValueError, "FAQ source differs"):
            refresh(self.client, self.path, "https://www.custojusto.pt/another-faq")
        self.assertEqual(self.files(), before)

    def test_missing_meta_or_network_error_cannot_trigger_scraping(self) -> None:
        for response in (httpx.Response(200, text="No version"), httpx.Response(503)):
            with httpx.Client(transport=httpx.MockTransport(lambda r: response)) as client:
                with patch("refresh_website_data.scrape_faqs") as faqs, patch("refresh_website_data.scrape_contacts") as contacts:
                    with self.assertRaises((ValueError, httpx.HTTPStatusError)):
                        refresh(client, self.path)
                    faqs.assert_not_called()
                    contacts.assert_not_called()
        self.assertEqual(self.files(), {})

    def test_local_edits_are_not_overwritten_on_unchanged_version(self) -> None:
        refresh(self.client, self.path)
        (self.path / "faqs.json").write_text("Manually edited", encoding="utf-8")
        before = self.files()
        with self.assertRaisesRegex(ValueError, "modified"):
            refresh(self.client, self.path)
        self.assertEqual(self.files(), before)

    def test_corrupt_state_does_not_silently_mark_data_fresh(self) -> None:
        (self.path / STATE_NAME).write_text('{"website_version": "v1", "files": []}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid website snapshot state"):
            refresh(self.client, self.path)


if __name__ == "__main__":
    unittest.main()
