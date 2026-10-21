"""Evaluate the existing chatbot without changing its prompts or decision policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, Literal, get_args

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator

import chat_bot as bot
import contact_forms
import api_metrics
from api_metrics import APICallMetrics, PricingSnapshot, capture_api_metrics, load_pricing, summarize_calls

ROOT = Path(__file__).resolve().parent
DATASET_PATH = ROOT / "data" / "evaluations" / "triage_cases.json"
Action = Literal["answer", "clarify", "escalate", "reject"]
ObservedAction = Literal["answer", "clarify", "escalate", "reject", "unresolved", "error"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _LegacyFAQResponse(StrictModel):
    """Read pre-action reports without inventing decisions or enforcing new rules."""

    sources: list[bot.AnswerSource] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    confidence_score: Literal["low", "medium", "high"]
    missing_info: list[str] = Field(default_factory=list)
    relevant_contact_form: contact_forms.ContactFormKey
    suggested_response: str


class HistoryMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class EvaluationCase(StrictModel):
    id: str = Field(min_length=1)
    split: Literal["development", "holdout"]
    category: str = Field(min_length=1)
    question: str = Field(min_length=1)
    history: list[HistoryMessage] = Field(default_factory=list)
    expected_action: Action
    expected_contact_form: str | None = None
    expected_source_groups: list[list[str]] = Field(default_factory=list)
    critical: bool = False
    rubric: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_expectations(self) -> EvaluationCase:
        if self.expected_action == "escalate":
            if self.expected_contact_form not in bot.CONTACT_FORMS:
                raise ValueError("An escalation needs a known contact destination.")
        elif self.expected_contact_form is not None:
            raise ValueError("Only escalations should specify a contact destination.")
        if self.expected_action == "answer" and not self.expected_source_groups:
            raise ValueError("An expected answer needs supporting FAQ groups.")
        if any(not group for group in self.expected_source_groups):
            raise ValueError("Source alternative groups cannot be empty.")
        return self


class EvaluationDataset(StrictModel):
    version: str
    label_status: Literal["draft_for_human_review", "human_reviewed"]
    reviewed_by: str | None
    corpus_version: str
    contact_forms_version: str
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_labels(self) -> EvaluationDataset:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Evaluation case IDs must be unique.")
        if self.label_status == "human_reviewed" and not (self.reviewed_by or "").strip():
            raise ValueError("Human-reviewed labels must name their reviewer.")
        return self


class HumanReview(StrictModel):
    reviewer: str | None = None
    unsupported_claims: bool | None = None
    follow_up_relevant: bool | None = None
    notes: str = ""

    @model_validator(mode="after")
    def require_reviewer(self) -> HumanReview:
        judged = self.unsupported_claims is not None or self.follow_up_relevant is not None
        if judged and not (self.reviewer or "").strip():
            raise ValueError("Human judgments must name their reviewer.")
        return self


class CaseResult(StrictModel):
    case_id: str
    inferred_action: ObservedAction
    answer: str = ""
    validation: bot.QuestionValidation | None = None
    structured_answer: bot.FAQResponse | _LegacyFAQResponse | None = None
    latency_ms: float = 0
    api_calls: list[APICallMetrics] | None = None
    error_type: str | None = None
    error_status: int | None = None
    review: HumanReview = Field(default_factory=HumanReview)


def load_dataset(path: Path = DATASET_PATH) -> EvaluationDataset:
    dataset = EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))
    records = bot.load_faq_records()
    if dataset.corpus_version != bot.faq_version(records):
        raise ValueError("FAQ corpus changed: review the labels and update corpus_version first.")
    if dataset.contact_forms_version != contact_forms.CONTACT_FORMS_VERSION:
        raise ValueError("Contact forms changed: review the labels and update contact_forms_version first.")
    known_ids = {record.chunk_id for record in records}
    for case in dataset.cases:
        for group in case.expected_source_groups:
            if not set(group).issubset(known_ids):
                raise ValueError(f"{case.id} references an unknown FAQ.")
    return dataset


def infer_action(
    validation: bot.QuestionValidation, answer: bot.FAQResponse | None
) -> ObservedAction:
    """Use the production triage decision, not a semantic judgment of the text."""
    if not validation.is_valid_question:
        return "reject"
    if validation.needs_clarification:
        return "clarify"
    if answer is None:
        return "unresolved"
    return answer.action


def run_case(
    case: EvaluationCase,
    client: OpenAI,
    answer_fn: Callable | None = None,
    pricing: PricingSnapshot | None = None,
) -> CaseResult:
    with capture_api_metrics(pricing if pricing is not None else load_pricing()) as calls:
        start = perf_counter()
        try:
            text, answer, validation = (answer_fn or bot.answer_user_question)(
                case.question, [message.model_dump() for message in case.history], client=client
            )
            return CaseResult(
                case_id=case.id,
                inferred_action=infer_action(validation, answer),
                answer=text,
                validation=validation,
                structured_answer=answer,
                latency_ms=round((perf_counter() - start) * 1000, 1),
                api_calls=calls,
            )
        except Exception as error:
            # API error messages can contain credentials; store only type and HTTP status.
            return CaseResult(
                case_id=case.id,
                inferred_action="error",
                error_type=type(error).__name__,
                error_status=getattr(error, "status_code", None),
                latency_ms=round((perf_counter() - start) * 1000, 1),
                api_calls=calls,
            )


def ratio(passed: int, total: int) -> dict:
    return {"passed": passed, "total": total, "rate": passed / total if total else None}


def summarize(cases: list[EvaluationCase], results: list[CaseResult]) -> dict:
    by_id = {case.id: case for case in cases}
    if len({result.case_id for result in results}) != len(results):
        raise ValueError("Duplicate case results cannot be scored.")
    if any(result.case_id not in by_id for result in results):
        raise ValueError("Results include a case outside the selected split.")
    known_sources = {record.chunk_id for record in bot.load_faq_records()}
    confusion: dict[str, Counter] = {action: Counter() for action in get_args(Action)}
    matched = routed = escalated = false_answers = source_hits = source_groups = 0
    expected_escalations = non_answers = citation_passes = answer_count = 0
    judged_support: list[bool] = []
    judged_followups: list[bool] = []
    for result in results:
        case = by_id[result.case_id]
        action = result.inferred_action
        confusion[case.expected_action][action] += 1
        matched += action == case.expected_action
        expected_escalations += case.expected_action == "escalate"
        non_answers += case.expected_action != "answer"
        false_answers += case.expected_action != "answer" and action == "answer"
        answer = result.structured_answer
        cited = {source.chunk_id for source in answer.sources} if answer else set()
        if case.expected_action == "escalate":
            escalated += action == "escalate"
            routed += (
                action == "escalate"
                and answer is not None
                and answer.relevant_contact_form == case.expected_contact_form
            )
        for group in case.expected_source_groups:
            source_groups += 1
            source_hits += bool(cited.intersection(group))
        if action == "answer":
            answer_count += 1
            citation_passes += bool(cited) and cited.issubset(known_sources)
        if result.review.unsupported_claims is not None:
            judged_support.append(result.review.unsupported_claims)
        if action == "clarify" and result.review.follow_up_relevant is not None:
            judged_followups.append(result.review.follow_up_relevant)
    latency = sorted(result.latency_ms for result in results)
    instrumented = [result for result in results if result.api_calls is not None]
    calls = [call for result in instrumented for call in result.api_calls]
    usage = summarize_calls(calls, captured=bool(results) and len(instrumented) == len(results))
    usage["instrumented_cases"] = len(instrumented)
    usage["by_stage"] = {
        stage: summarize_calls([call for call in calls if call.stage == stage])
        for stage in sorted({call.stage for call in calls})
    }
    return {
        "cases_completed": len(results),
        "errors": sum(result.inferred_action == "error" for result in results),
        "action_accuracy": ratio(matched, len(results)),
        "escalation_recall": ratio(escalated, expected_escalations),
        "correct_escalation_destination": ratio(routed, expected_escalations),
        "answers_when_non_answer_expected": ratio(false_answers, non_answers),
        "expected_source_group_coverage": ratio(source_hits, source_groups),
        "answer_citation_id_validity": ratio(citation_passes, answer_count),
        "unsupported_claim_rate_human": ratio(sum(judged_support), len(judged_support)),
        "follow_up_relevance_human": ratio(sum(judged_followups), len(judged_followups)),
        "category_accuracy": None,
        "confusion": {action: dict(counts) for action, counts in confusion.items()},
        "mean_latency_ms": sum(latency) / len(latency) if latency else None,
        "p95_latency_ms": latency[math.ceil(len(latency) * 0.95) - 1] if latency else None,
        "usage": usage,
    }


def format_metric(metric: dict) -> str:
    if metric["rate"] is None:
        return "N/A (0 applicable or reviewed cases)"
    return f"{metric['passed']}/{metric['total']} ({metric['rate']:.1%})"


def format_value(value, decimals: int | None = None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{decimals}f}" if decimals is not None else str(value)


def render_report(dataset: EvaluationDataset, cases: list[EvaluationCase], payload: dict) -> str:
    results = [CaseResult.model_validate(item) for item in payload["results"]]
    metrics = summarize(cases, results)
    lines = [
        "# Triage evaluation", "",
        f"Run: `{payload['metadata']['started_at_utc']}`; split: `{payload['metadata']['split']}`.",
        f"Model: `{payload['metadata']['generation_model']}`. Label status: **{dataset.label_status}**.",
        f"Completed: {len(results)}/{len(cases)} cases. Runtime errors: {metrics['errors']}.", "",
        "Labels are assistant-authored drafts until reviewed by the project owner.",
        (
            "Actions use the explicit triage decision; validation handles rejected or ambiguous requests."
            if payload["metadata"].get("triage_decision_version") == 2
            else "Historical actions retain their saved inference from structured flags; they have not been rescored using explicit decisions."
        ),
        "Category accuracy is N/A: the chatbot has no dedicated category output yet.", "",
        "| Automatic check | Result |", "|---|---|",
    ]
    for key in (
        "action_accuracy", "escalation_recall", "correct_escalation_destination",
        "answers_when_non_answer_expected", "expected_source_group_coverage",
        "answer_citation_id_validity",
    ):
        lines.append(f"| {key} | {format_metric(metrics[key])} |")
    lines.extend([
        "", "Lower is better for answers_when_non_answer_expected; higher is better for the other automatic checks.",
        "Runtime errors count as action failures and missed required escalations; expected evidence gets zero credit.",
        "Citation ID validity and expected-source coverage do not establish factual support.",
        "", "## Latency, tokens and estimated cost", "",
        f"Mean / p95 case latency: {format_value(metrics['mean_latency_ms'], 1)} / {format_value(metrics['p95_latency_ms'], 1)} ms.",
        "Case latency includes validation, retrieval, generation and any first-use indexing, but excludes report writing and app startup scraping.",
    ])
    usage = metrics["usage"]
    lines.extend([
        f"Instrumented cases: {usage['instrumented_cases']}/{len(results)}; observed API calls: {usage['api_calls']}.",
        f"Calls with complete usage: {usage['calls_with_complete_usage']}/{usage['api_calls']}; priced calls: {usage['calls_with_estimated_cost']}/{usage['api_calls']}; EUR estimates: {usage['calls_with_estimated_cost_eur']}/{usage['api_calls']}.",
        f"Input tokens (including cached): {format_value(usage['input_tokens'])}; cached input: {format_value(usage['cached_input_tokens'])}; output: {format_value(usage['output_tokens'])}.",
        f"Cache-write tokens, where reported: {format_value(usage['cache_write_tokens'])}.",
        f"Total tokens: {format_value(usage['total_tokens'])}; recorded token subtotal: {format_value(usage['recorded_total_tokens_subtotal'])}.",
        f"Estimated total: EUR {format_value(usage['estimated_cost_eur'], 8)}; known-cost subtotal: EUR {format_value(usage['known_cost_eur_subtotal'], 8)}.",
        "Missing usage/pricing stays N/A, not zero; subtotals are not a complete run total when coverage is incomplete.",
        "Costs are public standard-rate estimates, not invoices; cached input is discounted and reasoning tokens are already included in output. Nonzero cache writes need separate rates and remain unpriced.",
        "First-use corpus embeddings are charged to their triggering case and shown separately below. Fixed history fixtures do not invoke history compaction.",
    ])
    if snapshot := payload["metadata"].get("pricing_snapshot"):
        lines.append(f"Pricing snapshot verified on: `{snapshot['verified_on']}`. Saved rates are retained when regenerating this report.")
        if rate := snapshot.get("eur_exchange_rate"):
            lines.append(
                f"EUR conversion: 1 EUR = {rate['usd_per_eur']} USD, reference date `{rate['as_of']}` "
                f"([source]({rate['source_url']})). Underlying USD estimates are retained in JSON."
            )
    if usage["by_stage"]:
        lines.extend([
            "", "| Stage | Calls | API latency sum (ms) | Total tokens | Estimated EUR | Known-cost subtotal EUR |",
            "|---|---:|---:|---:|---:|---:|",
        ])
        for stage, values in usage["by_stage"].items():
            lines.append(
                f"| {stage} | {values['api_calls']} | {format_value(values['api_latency_ms'], 1)} | "
                f"{format_value(values['total_tokens'])} | {format_value(values['estimated_cost_eur'], 8)} | "
                f"{format_value(values['known_cost_eur_subtotal'], 8)} |"
            )
    lines.extend([
        "", "## Human review", "",
        f"Unsupported claims: {format_metric(metrics['unsupported_claim_rate_human'])} (lower is better).",
        f"Relevant follow-up: {format_metric(metrics['follow_up_relevance_human'])} (higher is better).",
        "Judgments remain pending until a named reviewer fills the review fields in the JSON results.",
        "", "## Action confusion matrix", "",
        "| Expected / inferred | answer | clarify | escalate | reject | unresolved | error |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for expected, counts in metrics["confusion"].items():
        values = [str(counts.get(action, 0)) for action in get_args(ObservedAction)]
        lines.append(f"| {expected} | " + " | ".join(values) + " |")
    by_id = {case.id: case for case in cases}
    records = {record.chunk_id: record for record in bot.load_faq_records()}
    lines.extend(["", "## Case details", ""])
    for result in results:
        case = by_id[result.case_id]
        matched = case.expected_action == result.inferred_action
        lines.extend([
            f"### {case.id}: {'action match' if matched else 'ACTION MISMATCH'}", "",
            f"Category label: `{case.category}`; critical: `{case.critical}`.",
            f"Expected action: `{case.expected_action}`; inferred: `{result.inferred_action}`.",
            f"Expected contact: `{case.expected_contact_form or 'none'}`.",
            f"Elapsed case latency: {result.latency_ms:.1f} ms.",
        ])
        if result.api_calls is None:
            lines.append("Token/cost tracking: N/A (not recorded in this historical result).")
        else:
            consumption = summarize_calls(result.api_calls)
            lines.append(f"Estimated case cost: EUR {format_value(consumption['estimated_cost_eur'], 8)}.")
            if result.api_calls:
                lines.extend([
                    "", "| API stage | Model | Latency (ms) | Input | Cached input | Cache writes | Output | Estimated EUR | Status |",
                    "|---|---|---:|---:|---:|---:|---:|---:|---|",
                ])
                for call in result.api_calls:
                    lines.append(
                        f"| {call.stage} | {call.response_model or call.requested_model} | {call.latency_ms:.1f} | "
                        f"{format_value(call.input_tokens)} | {format_value(call.cached_input_tokens)} | {format_value(call.cache_write_tokens)} | "
                        f"{format_value(call.output_tokens)} | {format_value(call.estimated_cost_eur, 8)} | "
                        f"{call.error_type or 'response received'} |"
                    )
        if case.history:
            lines.append("\nProvided context:")
            lines.extend(f"- {message.role}: {message.content}" for message in case.history)
        lines.extend(["", f"Request: {case.question}", "", f"Review rubric: {case.rubric}"])
        if case.expected_source_groups:
            lines.append(f"Expected FAQ groups (one alternative per group): `{case.expected_source_groups}`.")
        if result.error_type:
            lines.append(f"\nRuntime error: `{result.error_type}`; HTTP status: `{result.error_status}`.")
        if result.validation:
            lines.append(f"\nStandalone question: {result.validation.standalone_question}")
        if result.structured_answer:
            answer = result.structured_answer
            lines.append(f"\nActual contact: `{answer.relevant_contact_form}`; confidence: `{answer.confidence_score}`.")
            if isinstance(answer, bot.FAQResponse):
                lines.append(f"Explicit action: `{answer.action}`.")
                lines.append(f"Missing user information: `{answer.missing_info}`.")
                lines.append(f"Missing knowledge or capabilities: `{answer.missing_knowledge}`.")
            else:
                lines.append(f"Missing information (legacy meaning): `{answer.missing_info}`.")
        lines.extend(["", "Response:", "", result.answer or "(No response)", ""])
        if result.structured_answer:
            for source in result.structured_answer.sources:
                record = records.get(source.chunk_id)
                if record:
                    lines.extend([f"Cited evidence [{record.chunk_id}]: {record.question}", "", record.answer, ""])
        lines.append(f"Human review: `{result.review.model_dump()}`\n")
    return "\n".join(lines) + "\n"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_metadata(
    dataset: EvaluationDataset, dataset_path: Path, split: str,
    pricing: PricingSnapshot | None = None,
) -> dict:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    return {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "split": split,
        "generation_model": bot.GENERATION_MODEL,
        "embedding_model": bot.EMBEDDING_MODEL,
        "top_k": bot.TOP_K,
        "lexical_top_k": bot.LEXICAL_TOP_K,
        "min_similarity": bot.MIN_SIMILARITY,
        "corpus_version": dataset.corpus_version,
        "contact_forms_version": dataset.contact_forms_version,
        "dataset_sha256": file_hash(dataset_path),
        "implementation_sha256": file_hash(Path(bot.__file__)),
        "contact_router_sha256": file_hash(Path(contact_forms.__file__)),
        "evaluator_sha256": file_hash(Path(__file__)),
        "telemetry_sha256": file_hash(Path(api_metrics.__file__)),
        "pricing_snapshot": (pricing if pricing is not None else load_pricing()).model_dump(mode="json"),
        "reporting_currency": "EUR",
        "usage_tracking_version": 2,
        "triage_decision_version": 2,
        "git_revision": revision,
        "label_status": dataset.label_status,
        "timeout_seconds": 30,
        "sdk_max_retries": 0,
    }


def write_reports(
    output: Path, dataset: EvaluationDataset, cases: list[EvaluationCase], payload: dict
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    results = [CaseResult.model_validate(item) for item in payload["results"]]
    payload["metrics"] = summarize(cases, results)
    output.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output.with_suffix(".md").write_text(render_report(dataset, cases, payload), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DATASET_PATH)
    parser.add_argument("--split", choices=["development", "holdout"], default="development")
    parser.add_argument("--dry-run", action="store_true", help="Validate and list cases without API calls.")
    parser.add_argument("--output", type=Path, help="Output filename stem for JSON and Markdown.")
    parser.add_argument("--report-from", type=Path, help="Regenerate reports from saved/reviewed JSON without API calls.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--pricing", type=Path, default=api_metrics.PRICING_PATH, help="Versioned token rates and EUR conversion; no live pricing/FX lookup.")
    args = parser.parse_args()
    dataset = load_dataset(args.cases)
    cases = [case for case in dataset.cases if case.split == args.split]
    if not cases:
        parser.error("No cases found in the selected split.")
    if args.dry_run:
        print(f"Validated {len(dataset.cases)} cases; selected {len(cases)} {args.split} cases.")
        print(f"Label status: {dataset.label_status}")
        for case in cases:
            print(f"{case.id}: {case.expected_action} / {case.category}")
        return
    output = args.output or ROOT / "reports" / f"triage-{args.split}"
    if not args.overwrite and any(output.with_suffix(suffix).exists() for suffix in (".json", ".md")):
        parser.error("Output exists; choose another --output or explicitly use --overwrite.")
    if args.report_from:
        payload = json.loads(args.report_from.read_text(encoding="utf-8"))
        if payload["metadata"]["dataset_sha256"] != file_hash(args.cases):
            parser.error("Saved results use a different dataset; do not compare different labels silently.")
        if payload["metadata"]["split"] != args.split:
            parser.error("Select the same --split as the saved run.")
        write_reports(output, dataset, cases, payload)
        print(f"Reports regenerated at {output} (no API calls).")
        return
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY in .env before running a live evaluation.")
    pricing = load_pricing(args.pricing)
    client = OpenAI(timeout=30, max_retries=0)
    payload = {"metadata": run_metadata(dataset, args.cases, args.split, pricing), "results": []}
    for index, case in enumerate(cases, start=1):
        result = run_case(case, client, pricing=pricing)
        payload["results"].append(result.model_dump())
        write_reports(output, dataset, cases, payload)
        print(f"[{index}/{len(cases)}] {case.id}: expected={case.expected_action} inferred={result.inferred_action}", flush=True)
        if result.error_type in {"APIConnectionError", "AuthenticationError", "PermissionDeniedError"} or result.error_status in {401, 403, 429}:
            print("Stopping after an API connection, authentication, permission or rate-limit failure; partial results are saved.")
            break
    print(json.dumps(payload["metrics"], indent=2))
    print(f"Reports saved at {output.with_suffix('.md')} and {output.with_suffix('.json')}.")
    if payload["metrics"]["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
