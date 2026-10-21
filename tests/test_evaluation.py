import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from pydantic import ValidationError

from chat_bot import AnswerSource, CONTACT_FORMS, FAQResponse, QuestionValidation
from evaluate import (
    CaseResult,
    EvaluationCase,
    EvaluationDataset,
    HumanReview,
    DATASET_PATH,
    file_hash,
    infer_action,
    load_dataset,
    render_report,
    run_case,
    summarize,
)


def faq_answer(**updates) -> FAQResponse:
    values = {
        "confidence_score": "high",
        "relevant_contact_form": "none",
        "suggested_response": "Example response",
    }
    return FAQResponse(**(values | updates))


def case(case_id="example", action="answer", **updates) -> EvaluationCase:
    values = {
        "id": case_id,
        "split": "development",
        "category": "problemas_anuncios",
        "question": "Example request",
        "expected_action": action,
        "expected_source_groups": [["faq-002"]] if action == "answer" else [],
        "rubric": "Check the actual meaning against the FAQ.",
    }
    return EvaluationCase(**(values | updates))


class DatasetTest(unittest.TestCase):
    def test_shipped_dataset_covers_all_ten_destinations(self) -> None:
        dataset = load_dataset()
        self.assertEqual(len(dataset.cases), 34)
        self.assertEqual(sum(c.split == "development" for c in dataset.cases), 22)
        self.assertEqual(sum(c.split == "holdout" for c in dataset.cases), 12)
        self.assertEqual(
            {c.expected_contact_form for c in dataset.cases if c.expected_action == "escalate"},
            set(CONTACT_FORMS),
        )
        self.assertTrue(any(c.history for c in dataset.cases))

    def test_stale_contact_catalog_requires_label_review(self) -> None:
        payload = load_dataset().model_dump()
        payload["contact_forms_version"] = "outdated"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Contact forms changed"):
                load_dataset(path)

    def test_historical_baseline_retains_its_exact_dataset(self) -> None:
        directory = DATASET_PATH.parents[2] / "docs" / "evaluations"
        baseline = json.loads((directory / "baseline-development.json").read_text())
        self.assertEqual(
            file_hash(directory / "triage-cases-v1.json"),
            baseline["metadata"]["dataset_sha256"],
        )

    def test_duplicate_cases_and_unattributed_human_labels_are_rejected(self) -> None:
        payload = load_dataset().model_dump()
        payload["cases"].append(payload["cases"][0])
        with self.assertRaisesRegex(ValidationError, "unique"):
            EvaluationDataset.model_validate(payload)
        payload = load_dataset().model_dump()
        payload.update(label_status="human_reviewed", reviewed_by=None)
        with self.assertRaisesRegex(ValidationError, "reviewer"):
            EvaluationDataset.model_validate(payload)

    def test_escalation_requires_a_known_destination(self) -> None:
        with self.assertRaises(ValidationError):
            case(action="escalate", expected_contact_form="not-a-form")


class EvaluationTest(unittest.TestCase):
    def test_inference_respects_clarification_before_contact_flag(self) -> None:
        valid = QuestionValidation(is_valid_question=True, suggested_response="")
        answer = faq_answer(
            confidence_score="low",
            relevant_contact_form="problemas_anuncios",
            missing_info=["What is the problem?"],
        )
        self.assertEqual(infer_action(valid, answer), "clarify")
        self.assertEqual(infer_action(valid, answer.model_copy(update={"missing_info": []})), "escalate")
        self.assertEqual(infer_action(valid, faq_answer(confidence_score="low")), "unresolved")

    def test_run_case_does_not_send_gold_labels_to_the_chatbot(self) -> None:
        sample = case(history=[{"role": "user", "content": "Earlier request"}])
        validation = QuestionValidation(is_valid_question=True, suggested_response="")
        answer_fn = Mock(return_value=("Example response", faq_answer(), validation))
        client = Mock()
        result = run_case(sample, client, answer_fn=answer_fn)
        answer_fn.assert_called_once_with(
            sample.question, [{"role": "user", "content": "Earlier request"}], client=client
        )
        self.assertEqual(result.inferred_action, "answer")

    def test_errors_are_preserved_and_cannot_expose_exception_text(self) -> None:
        result = run_case(case(), Mock(), answer_fn=Mock(side_effect=RuntimeError("secret-test-value")))
        self.assertEqual(result.inferred_action, "error")
        self.assertEqual(result.error_type, "RuntimeError")
        self.assertNotIn("secret-test-value", result.model_dump_json())

    def test_errors_and_wrong_destinations_do_not_inflate_success(self) -> None:
        samples = [
            case("answer"),
            case("escalate", "escalate", expected_contact_form="problemas_anuncios"),
        ]
        results = [
            CaseResult(case_id="answer", inferred_action="error", error_type="TimeoutError"),
            CaseResult(
                case_id="escalate", inferred_action="escalate",
                structured_answer=faq_answer(
                    confidence_score="low", relevant_contact_form="outros"
                ),
            ),
        ]
        metrics = summarize(samples, results)
        self.assertEqual(metrics["errors"], 1)
        self.assertEqual(metrics["action_accuracy"]["rate"], 0.5)
        self.assertEqual(metrics["escalation_recall"]["rate"], 1.0)
        self.assertEqual(metrics["correct_escalation_destination"]["rate"], 0.0)
        self.assertEqual(metrics["expected_source_group_coverage"]["rate"], 0.0)

    def test_source_id_validity_does_not_imply_expected_evidence_or_grounding(self) -> None:
        result = CaseResult(
            case_id="example", inferred_action="answer",
            structured_answer=faq_answer(
                sources=[AnswerSource(chunk_id="faq-003", question="Unrelated source")]
            ),
        )
        metrics = summarize([case()], [result])
        self.assertEqual(metrics["answer_citation_id_validity"]["rate"], 1.0)
        self.assertEqual(metrics["expected_source_group_coverage"]["rate"], 0.0)
        self.assertIsNone(metrics["unsupported_claim_rate_human"]["rate"])
        self.assertIsNone(metrics["category_accuracy"])

    def test_source_groups_allow_equivalent_faqs(self) -> None:
        sample = case(expected_source_groups=[["faq-051", "faq-056"], ["faq-003"]])
        result = CaseResult(
            case_id="example", inferred_action="answer",
            structured_answer=faq_answer(
                sources=[AnswerSource(chunk_id="faq-056", question="Chat changes")]
            ),
        )
        self.assertEqual(
            summarize([sample], [result])["expected_source_group_coverage"]["rate"], 0.5
        )

    def test_human_judgments_need_attribution_and_are_scored_separately(self) -> None:
        with self.assertRaisesRegex(ValidationError, "reviewer"):
            HumanReview(unsupported_claims=False)
        result = CaseResult(
            case_id="example", inferred_action="clarify",
            review=HumanReview(reviewer="Test reviewer", unsupported_claims=False, follow_up_relevant=True),
        )
        metrics = summarize([case(action="clarify")], [result])
        self.assertEqual(metrics["unsupported_claim_rate_human"]["rate"], 0.0)
        self.assertEqual(metrics["follow_up_relevance_human"]["rate"], 1.0)

    def test_duplicate_or_foreign_results_are_rejected(self) -> None:
        result = CaseResult(case_id="example", inferred_action="reject")
        with self.assertRaises(ValueError):
            summarize([case()], [result, result])
        with self.assertRaises(ValueError):
            summarize([case()], [result.model_copy(update={"case_id": "other-split"})])

    def test_report_shows_unreviewed_metrics_as_pending(self) -> None:
        dataset = load_dataset()
        sample = dataset.cases[0]
        result = CaseResult(case_id=sample.id, inferred_action="error", error_type="TimeoutError")
        payload = {
            "metadata": {"started_at_utc": "test-run", "split": "development", "generation_model": "test-model"},
            "results": [result.model_dump()],
        }
        report = render_report(dataset, [sample], payload)
        self.assertIn("N/A (0 applicable or reviewed cases)", report)
        self.assertIn("Category accuracy is N/A", report)
        self.assertIn("Runtime errors: 1", report)
        self.assertIn(sample.rubric, report)


if __name__ == "__main__":
    unittest.main()
