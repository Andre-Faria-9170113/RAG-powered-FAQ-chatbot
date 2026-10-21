"""Refresh website datasets when their files are missing or the site version changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from scrape_contact_forms import SOURCE_URL as CONTACT_URL, scrape as scrape_contacts
from scrape_faqs import SOURCE_URL as FAQ_URL, scrape as scrape_faqs
from website_scraping import fetch_html, website_version

DATA_DIR = Path(__file__).resolve().parent / "data"
STATE_NAME = "website_snapshot.json"


def json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def publish(data_dir: Path, payloads: dict[str, bytes]) -> None:
    """Stage every file before replacement; publish the success marker last."""
    data_dir.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    try:
        for name, content in payloads.items():
            with tempfile.NamedTemporaryFile(dir=data_dir, delete=False) as handle:
                staged[name] = Path(handle.name)
                handle.write(content)
        for name, temporary in staged.items():
            os.replace(temporary, data_dir / name)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def refresh(client: httpx.Client, data_dir: Path = DATA_DIR, faq_url: str = FAQ_URL) -> bool:
    index_html = fetch_html(client, CONTACT_URL)
    version = website_version(index_html)
    state_path = data_dir / STATE_NAME
    missing_snapshots = any(
        not (data_dir / name).exists() for name in ("faqs.json", "contact_forms.json")
    )
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (
            not isinstance(state, dict) or not isinstance(state.get("website_version"), str)
            or not state["website_version"].strip() or not isinstance(state.get("files"), dict)
        ):
            raise ValueError("Invalid website snapshot state; restore the last successful state.")
        if state["website_version"] == version:
            if state.get("faq_source_url") != faq_url:
                raise ValueError("FAQ source differs from the saved snapshot; do not silently reuse another source.")
            for name in ("faqs.json", "contact_forms.json"):
                path = data_dir / name
                if not path.exists():
                    continue
                if not path.is_file() or content_hash(path.read_bytes()) != state["files"].get(name):
                    raise ValueError("Local snapshots are modified; restore them before checking this unchanged version.")
            if not missing_snapshots:
                print(f"Website version {version} is unchanged; both snapshots kept (no scraping).")
                return False

    # Nothing is published until both datasets have been fetched and validated.
    faqs = scrape_faqs(client, version, source_url=faq_url)
    contacts = scrape_contacts(client, version, index_html=index_html)
    payloads = {"faqs.json": json_bytes(faqs), "contact_forms.json": json_bytes(contacts)}
    state = {
        "website_version": version,
        "version_source_url": CONTACT_URL,
        "faq_source_url": faq_url,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": {name: content_hash(content) for name, content in payloads.items()},
    }
    payloads[STATE_NAME] = json_bytes(state)
    publish(data_dir, payloads)
    print(f"Saved both snapshots for website version {version} to {data_dir}.")
    print("Review evaluation labels if FAQ or contact content hashes changed; restart a running app to reload contact routing.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--faq-url", default=FAQ_URL, help="Official page containing script#faqs.")
    args = parser.parse_args()
    try:
        with httpx.Client(follow_redirects=True, timeout=20) as client:
            refresh(client, args.data_dir, args.faq_url)
    except (httpx.HTTPError, ValueError, OSError) as error:
        parser.exit(1, f"Refresh failed: {error}\n")


if __name__ == "__main__":
    main()
