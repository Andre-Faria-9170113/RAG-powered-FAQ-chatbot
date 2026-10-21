import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from pydantic import ValidationError

from chat_bot import AnswerSource, CONTACT_FORMS, FAQResponse, QuestionValidation, RequestUrgency
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
    write_reports,
)


def faq_answer(**updates) -> FAQResponse:
    values = {
        "action": "answer",
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


URGENCY_CASES = Path(__file__).resolve().parents[1] / "docs" / "evaluations" / "datasets" / "urgency-cases-v1.0.json"
REVIEWED_CASES = Path(__file__).resolve().parents[1] / "docs" / "evaluations" / "datasets" / "triage-cases-v1.1-reviewed.json"


class DatasetTest(unittest.TestCase):
    def test_shipped_dataset_covers_all_ten_destinations(self) -> None:
        dataset = load_dataset()
        self.assertEqual(len(dataset.cases), 45)
        self.assertEqual(sum(c.split == "development" for c in dataset.cases), 31)
        self.assertEqual(sum(c.split == "holdout" for c in dataset.cases), 14)
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
        directory = DATASET_PATH.parents[2] / "docs" / "evaluations" / "2026-10-21_101221_initial-development"
        baseline = json.loads((directory / "report.json").read_text())
        self.assertEqual(
            file_hash(directory / "dataset.json"),
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
    def test_category_accuracy_uses_predictions_not_contact_forms_or_topics(self) -> None:
        samples = [
            case("answered"),
            case("clarified", "clarify", category="pagamentos_servicos"),
            case("rejected", "reject", category="fora_ambito"),
            case("misclassified", "escalate", expected_contact_form="problemas_anuncios"),
            case("failed"),
        ]
        validations = [
            QuestionValidation(is_valid_question=True, category="problemas_anuncios", urgency="low", urgency_reason="Informação.", suggested_response="", standalone_question="Pergunta"),
            QuestionValidation(is_valid_question=True, category="pagamentos_servicos", urgency="unknown", urgency_reason="Falta impacto.", needs_clarification=True, suggested_response="O que aconteceu?"),
            QuestionValidation(is_valid_question=False, category="fora_ambito", urgency="not_applicable", urgency_reason="Recusa.", suggested_response="Recusa"),
            QuestionValidation(is_valid_question=True, category="outros", urgency="normal", urgency_reason="Problema concreto.", suggested_response="", standalone_question="Pergunta"),
        ]
        results = [
            CaseResult(case_id=sample.id, inferred_action=sample.expected_action, validation=validation.model_dump())
            for sample, validation in zip(samples, validations)
        ]
        results[0].structured_answer = faq_answer(topics=["unrelated topic"])
        results[3].structured_answer = faq_answer(action="escalate", relevant_contact_form="problemas_anuncios")
        results.append(CaseResult(case_id="failed", inferred_action="error"))
        metrics = summarize(samples, results)
        self.assertEqual(metrics["category_accuracy"], {"passed": 3, "total": 5, "rate": 0.6})
        self.assertEqual(metrics["correct_escalation_destination"]["rate"], 1)
        payload = {
            "metadata": {"started_at_utc": "test", "split": "development", "generation_model": "test"},
            "results": [result.model_dump() for result in results],
        }
        markdown = render_report(load_dataset(), samples, payload)
        self.assertIn("| category_accuracy | 3/5 (60.0%) |", markdown)
        self.assertIn("Predicted category: `pagamentos_servicos`", markdown)

    def test_new_category_run_with_only_errors_scores_zero_not_historical_na(self) -> None:
        sample = case()
        result = CaseResult(case_id=sample.id, inferred_action="error")
        payload = {
            "metadata": {"started_at_utc": "test", "split": "development", "generation_model": "test",
                         "category_classification_version": 1},
            "results": [result.model_dump()],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "errors"
            write_reports(output, load_dataset(), [sample], payload)
            self.assertIn("| category_accuracy | 0/1 (0.0%) |", output.with_suffix(".md").read_text())
        self.assertEqual(payload["metrics"]["category_accuracy"]["rate"], 0)

    def test_old_reports_do_not_invent_categories_from_saved_destinations(self) -> None:
        sample = case(action="escalate", expected_contact_form="problemas_anuncios")
        result = CaseResult(
            case_id=sample.id, inferred_action="escalate",
            validation={"is_valid_question": True, "suggested_response": "", "standalone_question": "Pergunta"},
            structured_answer=faq_answer(action="escalate", relevant_contact_form="problemas_anuncios"),
        )
        self.assertIsNone(result.validation.category)
        self.assertIsNone(summarize([sample], [result])["category_accuracy"])

    def test_historical_validation_flags_remain_readable_without_rescoring(self) -> None:
        dataset = load_dataset()
        sample = case("case-11", "clarify")
        old_flags = {
            "is_valid_question": False, "needs_clarification": True,
            "suggested_response": "", "standalone_question": "",
        }
        payload = {
            "metadata": {"started_at_utc": "old-run", "split": "development", "generation_model": "test-model"},
            "results": [{"case_id": sample.id, "inferred_action": "reject", "answer": "", "validation": old_flags}],
        }
        with self.assertRaises(ValidationError):
            QuestionValidation.model_validate(old_flags)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "historical-validation"
            write_reports(output, dataset, [sample], payload)
            restored = json.loads(output.with_suffix(".json").read_text())
            markdown = output.with_suffix(".md").read_text()
        self.assertEqual(restored["results"][0]["validation"], old_flags)
        self.assertEqual(restored["results"][0]["inferred_action"], "reject")
        self.assertEqual(restored["metrics"]["action_accuracy"]["passed"], 0)
        self.assertIn("is_valid_question=`False`; needs_clarification=`True`", markdown)

    def test_historical_results_keep_saved_actions_and_do_not_gain_new_fields(self) -> None:
        dataset = load_dataset()
        samples = [case("legacy-low", "escalate", expected_contact_form="outros"),
                   case("legacy-high", "escalate", expected_contact_form="outros")]
        payload = {
            "metadata": {"started_at_utc": "old-run", "split": "development", "generation_model": "test-model"},
            "results": [
                {
                    "case_id": sample.id, "inferred_action": action,
                    "structured_answer": {
                        "confidence_score": confidence, "relevant_contact_form": "outros",
                        "missing_info": gaps, "suggested_response": "Contacte a equipa.",
                    },
                }
                for sample, action, confidence, gaps in (
                    (samples[0], "clarify", "low", ["Procedimento não documentado"]),
                    (samples[1], "answer", "high", []),
                )
            ],
        }
        for item in payload["results"]:
            parsed = CaseResult.model_validate(item)
            self.assertIsNotNone(parsed.structured_answer)
            self.assertNotIsInstance(parsed.structured_answer, FAQResponse)
            with self.assertRaises(ValidationError):
                FAQResponse.model_validate(item["structured_answer"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "historical"
            write_reports(output, dataset, samples, payload)
            restored = json.loads(output.with_suffix(".json").read_text())
            markdown = output.with_suffix(".md").read_text()
        self.assertEqual(restored["metrics"]["escalation_recall"]["passed"], 0)
        self.assertEqual(restored["metrics"]["correct_escalation_destination"]["passed"], 0)
        self.assertEqual([r["inferred_action"] for r in restored["results"]], ["clarify", "answer"])
        self.assertTrue(all("action" not in r["structured_answer"] for r in restored["results"]))
        self.assertIn("Historical actions retain their saved inference", markdown)
        self.assertIn("Missing information (legacy meaning)", markdown)

    def test_invalid_explicit_answers_cannot_fall_back_to_legacy_parsing(self) -> None:
        invalid = {
            "case_id": "example", "inferred_action": "escalate",
            "structured_answer": {
                "action": "escalate", "confidence_score": "high",
                "relevant_contact_form": "none", "suggested_response": "Contact support.",
            },
        }
        with self.assertRaises(ValidationError):
            CaseResult.model_validate(invalid)

    def test_new_reports_show_explicit_action_and_distinct_knowledge_limits(self) -> None:
        dataset = load_dataset()
        sample = case(action="escalate", expected_contact_form="problemas_tecnicos")
        answer = faq_answer(
            action="escalate", confidence_score="high", relevant_contact_form="problemas_tecnicos",
            missing_knowledge=["Cannot investigate server logs."],
        )
        result = run_case(sample, Mock(), answer_fn=Mock(return_value=(answer.suggested_response, answer,
                          QuestionValidation(is_valid_question=True, category="problemas_tecnicos", urgency="normal", urgency_reason="Problema concreto.", suggested_response="", standalone_question=sample.question))))
        payload = {
            "metadata": {"started_at_utc": "new-run", "split": "development",
                         "generation_model": "test-model", "triage_decision_version": 2},
            "results": [result.model_dump()],
        }
        restored = CaseResult.model_validate(result.model_dump())
        self.assertIsInstance(restored.structured_answer, FAQResponse)
        metrics = summarize([sample], [restored])
        self.assertEqual(metrics["escalation_recall"]["rate"], 1)
        self.assertEqual(metrics["correct_escalation_destination"]["rate"], 1)
        markdown = render_report(dataset, [sample], payload)
        self.assertIn("Actions use the explicit triage decision", markdown)
        self.assertIn("Explicit action: `escalate`", markdown)
        self.assertIn("Missing knowledge or capabilities", markdown)

    def test_explicit_action_is_independent_of_confidence_and_knowledge_gaps(self) -> None:
        valid = QuestionValidation(is_valid_question=True, category="problemas_anuncios", urgency="normal", urgency_reason="Problema concreto.", suggested_response="", standalone_question="Example request")
        for confidence in ("low", "medium", "high"):
            answer = faq_answer(
                action="escalate", confidence_score=confidence,
                relevant_contact_form="problemas_anuncios",
                missing_knowledge=["Manual account review is unavailable to the assistant."],
            )
            self.assertEqual(infer_action(valid, answer), "escalate")
        clarify = faq_answer(action="clarify", missing_info=["What is the problem?"])
        self.assertEqual(infer_action(valid, clarify), "clarify")
        self.assertEqual(infer_action(valid, faq_answer(confidence_score="low")), "answer")
        self.assertEqual(infer_action(valid, None), "unresolved")
        clarification = QuestionValidation(
            is_valid_question=True, needs_clarification=True,
            category="indeterminado",
            urgency="unknown", urgency_reason="Falta impacto.",
            suggested_response="A que opção se refere?",
        )
        rejection = QuestionValidation(is_valid_question=False, category="fora_ambito", urgency="not_applicable", urgency_reason="Recusa.", suggested_response="Só respondo sobre o website.")
        self.assertEqual(infer_action(clarification, None), "clarify")
        self.assertEqual(infer_action(rejection, None), "reject")

    def test_run_case_does_not_send_gold_labels_to_the_chatbot(self) -> None:
        sample = case(history=[{"role": "user", "content": "Earlier request"}])
        validation = QuestionValidation(is_valid_question=True, category="problemas_anuncios", urgency="low", urgency_reason="Informação.", suggested_response="", standalone_question=sample.question)
        answer_fn = Mock(return_value=("Example response", faq_answer(), validation))
        client = Mock()
        result = run_case(sample, client, answer_fn=answer_fn)
        answer_fn.assert_called_once_with(
            sample.question, [{"role": "user", "content": "Earlier request"}], client=client
        )
        self.assertEqual(result.inferred_action, "answer")
        self.assertEqual(result.validation.category, "problemas_anuncios")

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
                    action="escalate",
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


class UrgencyEvaluationTest(unittest.TestCase):
    def test_frozen_urgency_dataset_and_reviewed_snapshot_keep_original_hashes(self):
        dataset = load_dataset(URGENCY_CASES)
        self.assertEqual(dataset.label_status, "draft_for_human_review")
        self.assertIsNone(dataset.reviewed_by)
        self.assertEqual(len(dataset.cases), 12)
        self.assertEqual({case.expected_urgency for case in dataset.cases}, set(RequestUrgency.__args__))
        self.assertEqual(file_hash(URGENCY_CASES), "6d40dd79344aafb71b2bfaed87fbcc502ba16ff7dcfa4da799e023bc384c269c")
        self.assertEqual(file_hash(REVIEWED_CASES), "1bc88889b039a0fa6f7293036bfbf83b24b638d556dd9ee359a4c986e540d373")
        approved = load_dataset(REVIEWED_CASES)
        self.assertEqual(approved.label_status, "human_reviewed")
        self.assertTrue(all(case.expected_urgency is None for case in approved.cases))

    def test_merged_dataset_deduplicates_and_preserves_existing_expectations_and_holdout(self):
        merged = load_dataset()
        original = load_dataset(REVIEWED_CASES)
        urgency = load_dataset(URGENCY_CASES)
        self.assertEqual(merged.version, "1.6")
        self.assertEqual(merged.label_status, "draft_for_human_review")
        self.assertIsNone(merged.reviewed_by)
        self.assertEqual(len(merged.cases), 45)
        self.assertEqual([case.id for case in merged.cases], [f"case-{index:02d}" for index in range(1, 46)])
        by_id = {case.id: case for case in merged.cases}
        self.assertNotIn("case-46", by_id)
        self.assertEqual(by_id["case-01"].expected_urgency, "low")
        self.assertIn("URGENTE!!!", by_id["case-39"].question)
        for case in original.cases:
            expected = case.model_dump()
            expected["expected_urgency"] = by_id[case.id].expected_urgency
            self.assertEqual(by_id[case.id].model_dump(), expected)
        self.assertEqual([case.id for case in merged.cases[:34]], [case.id for case in original.cases])
        self.assertEqual(
            [case.model_dump(exclude={"expected_urgency"}) for case in merged.cases[:34] if case.split == "holdout"],
            [case.model_dump(exclude={"expected_urgency"}) for case in original.cases if case.split == "holdout"],
        )
        self.assertNotIn("urgency-06", by_id)
        self.assertNotIn("urgency-07", by_id)
        retained = [case for case in urgency.cases if case.id not in {"urgency-01", "urgency-06", "urgency-07"}]
        for index, case in enumerate(retained, start=35):
            renamed = case.model_copy(update={"id": f"case-{index:02d}"})
            self.assertEqual(by_id[renamed.id], renamed)
        labelled = [case for case in merged.cases if case.expected_urgency is not None]
        self.assertEqual(len(labelled), 45)
        self.assertEqual(sum(case.split == "development" for case in labelled), 31)

    def test_current_dataset_has_full_urgency_coverage_without_conflating_critical_and_high(self):
        dataset = load_dataset()
        by_id = {case.id: case for case in dataset.cases}
        self.assertTrue(all(case.expected_urgency is not None for case in dataset.cases))
        for case_id in ("case-04", "case-16", "case-18", "case-20", "case-28"):
            with self.subTest(case_id=case_id):
                self.assertEqual(by_id[case_id].expected_urgency, "normal")
        self.assertTrue(by_id["case-30"].critical)
        self.assertEqual(by_id["case-30"].expected_urgency, "low")
        for case_id in ("case-11", "case-12", "case-13", "case-14", "case-15"):
            self.assertEqual(by_id[case_id].expected_urgency, "unknown")
        for case in dataset.cases:
            self.assertEqual(case.expected_urgency == "not_applicable", case.expected_action == "reject")

    def test_urgency_accuracy_covers_every_completed_case_in_each_current_split(self):
        dataset = load_dataset()
        for split, expected_total in (("development", 31), ("holdout", 14)):
            with self.subTest(split=split):
                cases = [case for case in dataset.cases if case.split == split]
                results = [CaseResult(case_id=case.id, inferred_action="error") for case in cases]
                metric = summarize(cases, results, urgency_assessment_enabled=True)["urgency_accuracy"]
                self.assertEqual(metric, {"passed": 0, "total": expected_total, "rate": 0.0})

    def test_new_urgency_holdouts_are_distinct_and_not_in_development(self):
        dataset = load_dataset()
        development = [case for case in dataset.cases if case.split == "development"]
        holdout = [case for case in dataset.cases if case.id in {"case-44", "case-45"}]
        self.assertEqual(len(holdout), 2)
        self.assertTrue(all(case.split == "holdout" for case in holdout))
        self.assertEqual({case.expected_urgency for case in holdout}, {"high", "low"})
        self.assertTrue(all(case.expected_action == "escalate" for case in holdout))
        self.assertFalse({case.question for case in holdout} & {case.question for case in development})

    def test_urgency_labels_are_independent_of_actions_but_rejection_is_not_applicable(self):
        for action, urgency in (("clarify", "high"), ("escalate", "normal"), ("reject", "not_applicable")):
            sample = EvaluationCase(
                id="sample", split="development", category="outros", question="Pedido",
                expected_action=action, expected_urgency=urgency,
                expected_contact_form="outros" if action == "escalate" else None, rubric="Teste",
            )
            self.assertEqual(sample.expected_urgency, urgency)
        for action, urgency in (("clarify", "not_applicable"), ("reject", "high")):
            with self.subTest(action=action), self.assertRaises(ValidationError):
                EvaluationCase(id="bad", split="development", category="outros", question="Pedido",
                               expected_action=action, expected_urgency=urgency, rubric="Teste")

    def test_scoring_excludes_unlabeled_cases_and_counts_missing_predictions(self):
        dataset = load_dataset(URGENCY_CASES)
        cases = [dataset.cases[index] for index in (0, 2, 3, 5)]
        cases.append(cases[0].model_copy(update={"id": "unlabeled", "expected_urgency": None}))
        predictions = ("low", "normal", "high", None, "high")
        results = [
            CaseResult(
                case_id=case.id, inferred_action="error" if urgency is None else "clarify",
                validation={"is_valid_question": True, "suggested_response": "Pergunta",
                            "urgency": urgency, "urgency_reason": "Impacto relatado."} if urgency else None,
            )
            for case, urgency in zip(cases, predictions)
        ]
        metrics = summarize(cases, results)
        self.assertEqual(metrics["urgency_accuracy"], {"passed": 2, "total": 4, "rate": 0.5})
        self.assertEqual(metrics["high_urgency_recall"], {"passed": 1, "total": 2, "rate": 0.5})

    def test_new_run_errors_are_failures_and_old_runs_are_not_reassessed(self):
        dataset = load_dataset(URGENCY_CASES)
        sample = dataset.cases[2]
        result = CaseResult(case_id=sample.id, inferred_action="error")
        payload = {
            "metadata": {"started_at_utc": "test", "split": "development", "generation_model": "test",
                         "urgency_assessment_version": 1},
            "results": [result.model_dump()],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "urgency"
            write_reports(output, dataset, [sample], payload)
        self.assertEqual(payload["metrics"]["urgency_accuracy"]["rate"], 0)
        self.assertEqual(payload["metrics"]["high_urgency_recall"]["rate"], 0)
        self.assertIsNone(summarize([sample], [result])["urgency_accuracy"])
        markdown = render_report(dataset, [sample], payload)
        self.assertIn("Expected urgency: `high`; predicted: `not recorded`", markdown)

    def test_predictions_are_visible_without_inventing_labels(self):
        dataset = load_dataset()
        sample = dataset.cases[0].model_copy(update={"expected_urgency": None})
        result = CaseResult(
            case_id=sample.id, inferred_action="answer",
            validation={"is_valid_question": True, "suggested_response": "", "category": sample.category,
                        "urgency": "low", "urgency_reason": "Pedido informativo sem problema ativo."},
        )
        self.assertIsNone(summarize([sample], [result])["urgency_accuracy"])
        payload = {"metadata": {"started_at_utc": "test", "split": "development", "generation_model": "test"},
                   "results": [result.model_dump()]}
        markdown = render_report(dataset, [sample], payload)
        self.assertIn("Expected urgency: `not labeled`; predicted: `low`", markdown)
        self.assertIn(result.validation.urgency_reason, markdown)


if __name__ == "__main__":
    unittest.main()
