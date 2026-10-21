"""Extract FAQPage JSON exclusively from the public script with id='faqs'."""

from __future__ import annotations

import json
import shutil
from urllib.parse import urlparse

import httpx

from website_scraping import WebsiteMetadataParser, fetch_html, require_version

SOURCE_URL = "https://www.custojusto.pt/ajuda?section=faq"


def render_faq_page(source_url: str) -> str:
    from playwright.sync_api import Error as BrowserError, sync_playwright

    try:
        with sync_playwright() as playwright:
            channel = "chrome" if shutil.which("google-chrome") else None
            browser = playwright.chromium.launch(headless=True, channel=channel)
            try:
                page = browser.new_page(service_workers="block")

                def allow_read_only(route) -> None:
                    request = route.request
                    parsed = urlparse(request.url)
                    if (
                        request.method != "GET" or parsed.scheme != "https"
                        or parsed.netloc != "www.custojusto.pt"
                        or request.resource_type in {"image", "font", "media"}
                    ):
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", allow_read_only)
                response = page.goto(source_url, wait_until="domcontentloaded", timeout=30_000)
                if response is None or not response.ok:
                    raise ValueError("The browser could not load the public FAQ page successfully.")
                page.locator("script#faqs").wait_for(state="attached", timeout=30_000)
                return page.content()
            finally:
                browser.close()
    except BrowserError as error:
        raise ValueError(
            "Could not read script#faqs in the browser. Install Chromium with "
            "'.venv/bin/playwright install chromium' or use installed Google Chrome. "
            f"Browser detail: {error}"
        ) from error


def parse_faqs(html: str) -> dict:
    parser = WebsiteMetadataParser()
    parser.feed(html)
    parser.close()
    if len(parser.faq_scripts) != 1:
        raise ValueError("Expected exactly one script#faqs; existing FAQ data was not replaced.")
    payload = json.loads(parser.faq_scripts[0])
    if not isinstance(payload, dict) or payload.get("@type") != "FAQPage":
        raise ValueError("script#faqs must contain a JSON-LD FAQPage object.")
    items = payload.get("mainEntity")
    if not isinstance(items, list) or not items:
        raise ValueError("The FAQPage must contain at least one Question.")
    for item in items:
        if not isinstance(item, dict) or item.get("@type") != "Question":
            raise ValueError("Each FAQ must be a Question object.")
        answer = item.get("acceptedAnswer")
        if not isinstance(answer, dict) or answer.get("@type") != "Answer":
            raise ValueError("Each FAQ must have an acceptedAnswer object.")
        if any(not isinstance(value, str) or not value.strip() for value in (item.get("name"), answer.get("text"))):
            raise ValueError("FAQ questions and answers must contain nonempty strings.")
    return payload


def scrape(client: httpx.Client, expected_version: str, source_url: str = SOURCE_URL) -> dict:
    html = fetch_html(client, source_url)
    require_version(html, expected_version)
    parser = WebsiteMetadataParser()
    parser.feed(html)
    if not parser.faq_scripts:
        html = render_faq_page(source_url)
        require_version(html, expected_version)
    return parse_faqs(html)


def main() -> None:
    from refresh_website_data import main as refresh_main

    refresh_main()


if __name__ == "__main__":
    main()
