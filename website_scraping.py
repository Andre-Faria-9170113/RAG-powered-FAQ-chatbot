"""Shared public-page fetching and website metadata extraction."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx


def fetch_html(client: httpx.Client, url: str) -> str:
    def is_official(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme == "https" and parsed.netloc == "www.custojusto.pt"

    if not is_official(url):
        raise ValueError("Expected an HTTPS URL on www.custojusto.pt.")
    response = client.get(url)
    response.raise_for_status()
    if not is_official(str(response.url)):
        raise ValueError("Unexpected redirect outside the public CustoJusto website.")
    return response.text


class WebsiteMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.versions: list[str] = []
        self.faq_scripts: list[str] = []
        self._script_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "meta" and (attributes.get("name") or "").casefold() == "version":
            self.versions.append((attributes.get("content") or "").strip())
        if tag == "script" and attributes.get("id") == "faqs":
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_parts is not None:
            self.faq_scripts.append("".join(self._script_parts))
            self._script_parts = None


def website_version(html: str) -> str:
    parser = WebsiteMetadataParser()
    parser.feed(html)
    parser.close()
    if len(parser.versions) != 1 or not parser.versions[0]:
        raise ValueError("Expected exactly one nonempty meta[name=version] content attribute.")
    return parser.versions[0]


def require_version(html: str, expected: str) -> None:
    if website_version(html) != expected:
        raise ValueError("Website version changed during refresh; retry without updating snapshots.")
