import json
import unittest
from collections import Counter
from pathlib import Path

from api_metrics import PricingSnapshot, estimate_cost, usd_to_eur
from evaluate import CaseResult, EvaluationDataset, file_hash, summarize


DIRECTORY = Path(__file__).resolve().parents[1] / "docs" / "evaluations"


class PublishedBaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads((DIRECTORY / "current-development.json").read_text(encoding="utf-8"))
        self.review = json.loads((DIRECTORY / "current-development-review.json").read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()
