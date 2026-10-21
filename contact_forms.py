"""Verified contact destinations and routing context from the public website."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, get_args
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, model_validator

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "contact_forms.json"
ContactFormKey = Literal[
    "problemas_anuncios",
    "pagamentos_servicos",
    "contactos_anuncios",
    "problemas_tecnicos",
    "sugestoes",
    "outros",
    "imprensa_publicidade",
    "versao_mobile",
    "acesso_telefones",
    "conta_utilizador",
    "none",
]


class Subject(BaseModel):
    value: str
    label: str = Field(min_length=1)


class ContactForm(BaseModel):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    section: str
    url: str
    subjects: list[Subject]

    @model_validator(mode="after")
    def validate_destination(self) -> ContactForm:
        parsed = urlparse(self.url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "www.custojusto.pt"
            or parsed.path != "/ajuda-contacto"
            or parse_qs(parsed.query) != {"section": [self.section]}
            or not self.section.isdigit()
            or parsed.fragment
        ):
            raise ValueError("Expected an HTTPS CustoJusto contact URL with its section ID.")
        if self.key == "none":
            raise ValueError("'none' is a routing sentinel, not a contact form.")
        return self


def load_contact_forms(path: Path = CATALOG_PATH) -> list[ContactForm]:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    forms = [ContactForm.model_validate(item) for item in snapshot["forms"]]
    if not forms or len({form.key for form in forms}) != len(forms):
        raise ValueError("Contact form keys must be present and unique.")
    if len({form.section for form in forms}) != len(forms):
        raise ValueError("Contact form sections must be unique.")
    if {form.key for form in forms} != set(get_args(ContactFormKey)) - {"none"}:
        raise ValueError("Contact form keys must match the structured response schema.")
    return forms


def contact_forms_version(forms: list[ContactForm]) -> str:
    """Hash routing content independently of the snapshot's fetch timestamp."""
    payload = [form.model_dump() for form in sorted(forms, key=lambda form: form.key)]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def format_routing_context(forms: list[ContactForm]) -> str:
    lines = []
    for form in forms:
        subjects = "; ".join(subject.label for subject in form.subjects)
        lines.append(
            f"- {form.key}: {form.label}. "
            + (f"Assuntos no formulário: {subjects}." if subjects else "Sem sub-assuntos publicados.")
        )
    return "\n".join(lines)


try:
    CONTACT_CATALOG = load_contact_forms()
except FileNotFoundError:
    # Startup refresh will populate the catalog before chat is enabled.
    CONTACT_CATALOG = []
CONTACT_FORMS = {form.key: form.url for form in CONTACT_CATALOG}
CONTACT_LABELS = {form.key: form.label for form in CONTACT_CATALOG}
CONTACT_FORMS_VERSION = contact_forms_version(CONTACT_CATALOG) if CONTACT_CATALOG else ""
CONTACT_ROUTING_CONTEXT = format_routing_context(CONTACT_CATALOG)


def reload_contact_forms() -> None:
    """Reload a refreshed snapshot without invalidating imported mapping references."""
    global CONTACT_FORMS_VERSION, CONTACT_ROUTING_CONTEXT

    forms = load_contact_forms()
    CONTACT_CATALOG[:] = forms
    CONTACT_FORMS.clear()
    CONTACT_FORMS.update((form.key, form.url) for form in forms)
    CONTACT_LABELS.clear()
    CONTACT_LABELS.update((form.key, form.label) for form in forms)
    CONTACT_FORMS_VERSION = contact_forms_version(forms)
    CONTACT_ROUTING_CONTEXT = format_routing_context(forms)
