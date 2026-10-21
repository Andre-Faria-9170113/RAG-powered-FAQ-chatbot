import json
import unittest
from collections import Counter
from pathlib import Path

import chat_bot as bot
import contact_forms
from api_metrics import PricingSnapshot, estimate_cost, usd_to_eur
from evaluate import CaseResult, EvaluationDataset, file_hash, infer_action, summarize


ARCHIVE = Path(__file__).resolve().parents[1] / "docs" / "evaluations"
DIRECTORY = ARCHIVE / "2026-10-21_150329_triage-development"
LATEST_DIRECTORY = ARCHIVE / "2026-10-21_181409_development-run-01"


class PublishedBaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads((DIRECTORY / "report.json").read_text(encoding="utf-8"))
        self.review = json.loads((DIRECTORY / "review.json").read_text(encoding="utf-8"))
        self.dataset = EvaluationDataset.model_validate_json(
            (DIRECTORY / self.review["dataset_snapshot"]).read_text(encoding="utf-8")
        )
        self.cases = [case for case in self.dataset.cases if case.split == "development"]
        self.results = [CaseResult.model_validate(item) for item in self.payload["results"]]

    def test_artifacts_keep_the_run_hashes_and_original_approval_status(self) -> None:
        self.assertEqual(file_hash(DIRECTORY / self.review["source_report"]), self.review["source_report_sha256"])
        self.assertEqual(file_hash(DIRECTORY / self.review["source_markdown"]), self.review["source_markdown_sha256"])
        dataset_hash = file_hash(DIRECTORY / self.review["dataset_snapshot"])
        self.assertEqual(dataset_hash, self.payload["metadata"]["dataset_sha256"])
        self.assertEqual(dataset_hash, self.review["dataset_sha256"])
        self.assertEqual(self.dataset.label_status, "draft_for_human_review")
        self.assertIsNone(self.dataset.reviewed_by)
        self.assertEqual(self.payload["metadata"]["label_status"], "draft_for_human_review")

    def test_complete_case_coverage_and_saved_metrics_are_reproducible(self) -> None:
        self.assertEqual(len(self.cases), 22)
        self.assertEqual(len(self.dataset.cases) - len(self.cases), 12)
        self.assertEqual([result.case_id for result in self.results], [case.id for case in self.cases])
        self.assertEqual(summarize(self.cases, self.results), self.payload["metrics"])
        mismatches = [case.id for case, result in zip(self.cases, self.results)
                      if case.expected_action != result.inferred_action]
        self.assertEqual(mismatches, ["case-13"])
        self.assertEqual(self.payload["metrics"]["errors"], 0)

    def test_ai_judgments_cover_every_case_without_populating_human_metrics(self) -> None:
        self.assertEqual(self.review["reviewer_type"], "ai")
        self.assertFalse(self.review["independent_human_review"])
        judgments = self.review["cases"]
        self.assertEqual([item["case_id"] for item in judgments], [case.id for case in self.cases])
        counts = Counter(item["outcome"] for item in judgments)
        self.assertEqual(counts, {"acceptable": 12, "acceptable_with_notes": 7, "needs_changes": 3})
        for outcome, count in counts.items():
            self.assertEqual(self.review["summary"][outcome], count)
        self.assertEqual([item["case_id"] for item in judgments if item["outcome"] == "needs_changes"],
                         self.review["summary"]["needs_changes_case_ids"])
        self.assertTrue(all(item["notes"].strip() for item in judgments))
        followups = [item for item in judgments if item["clarification_relevant"] is not None]
        self.assertEqual([item["case_id"] for item in followups], ["case-11", "case-12"])
        self.assertEqual(self.review["summary"]["clarification_relevance_ai"], {"passed": 2, "total": 2})
        for result in self.results:
            self.assertIsNone(result.review.reviewer)
            self.assertIsNone(result.review.unsupported_claims)
            self.assertIsNone(result.review.follow_up_relevant)
        for key in ("unsupported_claim_rate_human", "follow_up_relevance_human"):
            self.assertEqual(self.payload["metrics"][key], {"passed": 0, "total": 0, "rate": None})

    def test_usage_and_costs_match_the_saved_pricing_and_exchange_rate(self) -> None:
        pricing = PricingSnapshot.model_validate(self.payload["metadata"]["pricing_snapshot"])
        calls = [call for result in self.results for call in result.api_calls]
        self.assertEqual(len(calls), 56)
        self.assertEqual(sum(call.total_tokens for call in calls), 67079)
        for call in calls:
            self.assertTrue(call.usage_complete)
            self.assertIsNone(call.error_type)
            self.assertAlmostEqual(estimate_cost(call.model_copy(deep=True), pricing), call.estimated_cost_usd, places=14)
            self.assertAlmostEqual(usd_to_eur(call.estimated_cost_usd, pricing.eur_exchange_rate),
                                   call.estimated_cost_eur, places=14)
        self.assertAlmostEqual(sum(call.estimated_cost_eur for call in calls),
                               self.payload["metrics"]["usage"]["estimated_cost_eur"], places=11)


class LatestPublishedBaselineTest(unittest.TestCase):
    def setUp(self):
        self.directory = LATEST_DIRECTORY
        self.payload = json.loads((self.directory / "report.json").read_text(encoding="utf-8"))
        self.review = json.loads((self.directory / "review.json").read_text(encoding="utf-8"))
        self.dataset = EvaluationDataset.model_validate_json((self.directory / "dataset.json").read_text(encoding="utf-8"))
        self.cases = [case for case in self.dataset.cases if case.split == "development"]
        self.results = [CaseResult.model_validate(item) for item in self.payload["results"]]

    def test_artifacts_retain_source_hashes_and_draft_approval(self):
        self.assertEqual(file_hash(self.directory / "report.json"), "efaa0051dbe617e72f1b2f809b425fb30c655fba0a879700eaa7e6dae26690d4")
        self.assertEqual(file_hash(self.directory / "report.md"), "c6499c850e9401eae80022ae8d88bb9fb2ce5326263c13bf0c8fe5c4df9b8eff")
        self.assertEqual(file_hash(self.directory / "report.json"), self.review["source_report_sha256"])
        self.assertEqual(file_hash(self.directory / "report.md"), self.review["source_markdown_sha256"])
        self.assertEqual(file_hash(self.directory / "dataset.json"), self.payload["metadata"]["dataset_sha256"])
        self.assertEqual(self.review["dataset_sha256"], self.payload["metadata"]["dataset_sha256"])
        self.assertEqual(self.dataset.version, "1.6")
        self.assertEqual(self.dataset.label_status, "draft_for_human_review")
        self.assertIsNone(self.dataset.reviewed_by)
        self.assertEqual(self.payload["metadata"]["label_status"], self.dataset.label_status)

    def test_metrics_and_complete_development_coverage_are_reproducible(self):
        self.assertEqual(len(self.cases), 31)
        self.assertEqual(len(self.dataset.cases) - len(self.cases), 14)
        self.assertEqual([result.case_id for result in self.results], [case.id for case in self.cases])
        self.assertEqual(summarize(self.cases, self.results, category_classification_enabled=True,
                                   urgency_assessment_enabled=True), self.payload["metrics"])
        for result in self.results:
            self.assertEqual(infer_action(result.validation, result.structured_answer), result.inferred_action)

    def test_frozen_evidence_and_all_recommended_urls_match(self):
        for artifact in self.review["evidence_snapshots"].values():
            self.assertEqual(file_hash(self.directory / artifact["path"]), artifact["sha256"])
        records = bot.load_faq_records(self.directory / "faqs.json")
        self.assertEqual(bot.faq_version(records), self.payload["metadata"]["corpus_version"])
        by_id = {record.chunk_id: record for record in records}
        forms = contact_forms.load_contact_forms(self.directory / "contact_forms.json")
        self.assertEqual(contact_forms.contact_forms_version(forms), self.payload["metadata"]["contact_forms_version"])
        by_key = {form.key: form for form in forms}
        referrals = 0
        for result in self.results:
            answer = result.structured_answer
            if answer is None:
                self.assertEqual(result.answer, result.validation.suggested_response)
                continue
            for source in answer.sources:
                self.assertEqual(source.question, by_id[source.chunk_id].question)
            if answer.action == "escalate":
                referrals += 1
                form = by_key[answer.relevant_contact_form]
                expected = f"{answer.suggested_response.strip()}\n\nFormulário recomendado: [{form.label}]({form.url})"
            else:
                expected = answer.suggested_response.strip()
            self.assertEqual(result.answer, expected)
        self.assertEqual(referrals, 12)

    def test_ai_judgments_preserve_human_review_and_record_metadata_failures(self):
        self.assertEqual(self.review["reviewer_type"], "ai")
        self.assertFalse(self.review["independent_human_review"])
        judgments = self.review["cases"]
        self.assertEqual([item["case_id"] for item in judgments], [case.id for case in self.cases])
        counts = Counter(item["outcome"] for item in judgments)
        self.assertEqual(counts, {"acceptable": 18, "acceptable_with_notes": 9, "needs_changes": 4})
        for outcome, count in counts.items():
            self.assertEqual(self.review["summary"][outcome], count)
        self.assertEqual([item["case_id"] for item in judgments if item["outcome"] == "needs_changes"],
                         self.review["summary"]["needs_changes_case_ids"])
        by_id = {case.id: case for case in self.cases}
        categories, urgencies = [], []
        for result, judgment in zip(self.results, judgments):
            case = by_id[result.case_id]
            self.assertEqual(judgment["category_match"], result.validation.category == case.category)
            self.assertEqual(judgment["urgency_match"], result.validation.urgency == case.expected_urgency)
            if not judgment["category_match"]:
                categories.append(case.id)
            if not judgment["urgency_match"]:
                urgencies.append(case.id)
            self.assertEqual(result.review.model_dump(), {"reviewer": None, "unsupported_claims": None,
                                                         "follow_up_relevant": None, "notes": ""})
        self.assertEqual(categories, self.review["summary"]["category_mismatch_case_ids"])
        self.assertEqual(urgencies, self.review["summary"]["urgency_mismatch_case_ids"])
        followups = [item for item in judgments if item["clarification_relevant"] is not None]
        self.assertEqual([item["case_id"] for item in followups], ["case-11", "case-12", "case-13", "case-40"])
        self.assertTrue(all(item["clarification_relevant"] for item in followups))
        for key in ("unsupported_claim_rate_human", "follow_up_relevance_human"):
            self.assertIsNone(self.payload["metrics"][key]["rate"])

    def test_raw_output_defect_is_preserved_not_silently_repaired(self):
        affected = [result.case_id for result in self.results
                    if any(ord(character) < 32 and character not in "\n\r\t" for character in result.answer)]
        self.assertEqual(affected, ["case-03"])
        self.assertEqual(affected, self.review["summary"]["output_integrity_case_ids"])
        complaint = next(result for result in self.results if result.case_id == "case-21")
        self.assertIn("Encaminharei", complaint.validation.suggested_response)
        self.assertNotIn("Encaminharei", complaint.answer)

    def test_saved_pricing_usage_and_euro_costs_are_reproducible(self):
        pricing = PricingSnapshot.model_validate(self.payload["metadata"]["pricing_snapshot"])
        calls = [call for result in self.results for call in result.api_calls]
        self.assertEqual(len(calls), 77)
        self.assertEqual(sum(call.total_tokens for call in calls), 117281)
        for call in calls:
            self.assertTrue(call.usage_complete)
            self.assertIsNone(call.error_type)
            self.assertAlmostEqual(estimate_cost(call.model_copy(deep=True), pricing), call.estimated_cost_usd, places=14)
            self.assertAlmostEqual(usd_to_eur(call.estimated_cost_usd, pricing.eur_exchange_rate),
                                   call.estimated_cost_eur, places=14)
        self.assertAlmostEqual(sum(call.estimated_cost_eur for call in calls),
                               self.payload["metrics"]["usage"]["estimated_cost_eur"], places=11)


if __name__ == "__main__":
    unittest.main()
