"""Read public CustoJusto contact categories and subject options; never submit forms."""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from website_scraping import fetch_html, require_version

SOURCE_URL = "https://www.custojusto.pt/ajuda-contacto"
KEYS_BY_SECTION = {
    "7": "problemas_anuncios",
    "12": "pagamentos_servicos",
    "6": "contactos_anuncios",
    "8": "problemas_tecnicos",
    "10": "sugestoes",
    "11": "outros",
    "14": "imprensa_publicidade",
    "15": "versao_mobile",
    "16": "acesso_telefones",
    "17": "conta_utilizador",
}


def normalize(text: str) -> str:
    return " ".join(text.split())


class ContactPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: dict[str, str] = {}
        self.headings: list[str] = []
        self.subjects: list[dict[str, str]] = []
        self._link_section: str | None = None
        self._link_text: list[str] = []
        self._heading_text: list[str] | None = None
        self._in_select = False
        self._option_value: str | None = None
        self._option_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a":
            parsed = urlparse(urljoin(SOURCE_URL, attributes.get("href") or ""))
            sections = parse_qs(parsed.query).get("section", [])
            if (
                parsed.scheme == "https"
                and parsed.hostname == "www.custojusto.pt"
                and parsed.path == "/ajuda-contacto"
                and len(sections) == 1
                and sections[0].isdigit()
            ):
                self._link_section = sections[0]
                self._link_text = []
        if tag in {"h1", "h2"}:
            self._heading_text = []
        if tag == "select":
            self._in_select = True
        if tag == "option" and self._in_select:
            self._option_value = attributes.get("value")
            self._option_text = []

    def handle_data(self, data: str) -> None:
        if self._link_section is not None:
            self._link_text.append(data)
        if self._heading_text is not None:
            self._heading_text.append(data)
        if self._option_value is not None:
            self._option_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link_section is not None:
            label = normalize("".join(self._link_text))
            if label:
                self.links[self._link_section] = label
            self._link_section = None
        if tag in {"h1", "h2"} and self._heading_text is not None:
            self.headings.append(normalize("".join(self._heading_text)))
            self._heading_text = None
        if tag == "option" and self._option_value is not None:
            label = normalize("".join(self._option_text))
            if self._option_value and label:
                self.subjects.append({"value": self._option_value, "label": label})
            self._option_value = None
        if tag == "select":
            self._in_select = False


def read_page(
    client: httpx.Client, url: str, expected_version: str | None = None,
    html: str | None = None,
) -> ContactPageParser:
    html = fetch_html(client, url) if html is None else html
    if expected_version is not None:
        require_version(html, expected_version)
    parser = ContactPageParser()
    parser.feed(html)
    return parser


def scrape(
    client: httpx.Client, expected_version: str | None = None,
    index_html: str | None = None,
) -> dict:
    index = read_page(client, SOURCE_URL, expected_version, html=index_html)
    if set(index.links) != set(KEYS_BY_SECTION):
        raise ValueError("Contact sections changed; review the stable keys before refreshing.")
    forms = []
    for section, label in index.links.items():
        url = f"{SOURCE_URL}?section={section}"
        page = read_page(client, url, expected_version)
        if label not in page.headings:
            raise ValueError(f"Expected heading not found for contact section {section}.")
        forms.append({
            "key": KEYS_BY_SECTION[section],
            "label": label,
            "section": section,
            "url": url,
            "subjects": page.subjects,
        })
        print(f"{section}: {label} ({len(page.subjects)} subjects)", flush=True)
    return {
        "source_url": SOURCE_URL,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "forms": forms,
    }


def main() -> None:
    from refresh_website_data import main as refresh_main

    refresh_main()


if __name__ == "__main__":
    main()
