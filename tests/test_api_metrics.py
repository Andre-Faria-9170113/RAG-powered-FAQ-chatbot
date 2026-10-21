import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from openai import OpenAI
from openai.types.responses import ResponseUsage
from pydantic import ValidationError

import chat_bot as bot
from api_metrics import (
    EuroExchangeRate, PricingSnapshot, api_call, api_stage, capture_api_metrics, load_pricing, summarize_calls,
    validation_issues,
)
from evaluate import (
    DATASET_PATH, CaseResult, EvaluationCase, file_hash, load_dataset, main,
    render_report, run_case, run_metadata, summarize, write_reports,
)


def response(model="gpt-4.1-mini-2025-04-14", **changes):
    values = {
        "model": model, "service_tier": "default",
        "usage": ResponseUsage(
            input_tokens=1200, input_tokens_details={"cached_tokens": 1000, "cache_write_tokens": 0},
            output_tokens=100, output_tokens_details={"reasoning_tokens": 50}, total_tokens=1300,
        ),
    }
    return SimpleNamespace(**(values | changes))


class UsageCaptureTest(unittest.TestCase):
    def setUp(self):
        self.pricing = load_pricing()

    def capture(self, result, stage="validation"):
        with capture_api_metrics(self.pricing) as calls:
            api_call(stage, Mock(return_value=result), model="gpt-4.1-mini")
        return calls[0]

    def test_cached_input_is_not_double_counted_and_reasoning_is_not_added_again(self):
        call = self.capture(response())
        self.assertTrue(call.usage_complete)
        self.assertAlmostEqual(call.estimated_cost_usd, 0.00034)
        self.assertEqual(call.reasoning_tokens, 50)
        self.assertEqual(call.pricing_model, "gpt-4.1-mini")
        self.assertEqual(summarize_calls([call])["total_tokens"], 1300)

    def test_euro_conversion_divides_by_usd_per_euro_and_retains_usd(self):
        rate = EuroExchangeRate(usd_per_eur=1.25, as_of="2026-10-20", source_url="https://www.ecb.europa.eu/")
        pricing = self.pricing.model_copy(update={"eur_exchange_rate": rate})
        with capture_api_metrics(pricing) as calls:
            api_call("validation", Mock(return_value=response()), model="gpt-4.1-mini")
        self.assertAlmostEqual(calls[0].estimated_cost_usd, 0.00034, places=12)
        self.assertAlmostEqual(calls[0].estimated_cost_eur, 0.000272, places=12)
        self.assertAlmostEqual(summarize_calls(calls)["estimated_cost_eur"], 0.000272, places=12)

    def test_missing_exchange_rate_keeps_euro_cost_unknown(self):
        pricing = self.pricing.model_copy(update={"eur_exchange_rate": None})
        with capture_api_metrics(pricing) as calls:
            api_call("validation", Mock(return_value=response()), model="gpt-4.1-mini")
        self.assertIsNotNone(calls[0].estimated_cost_usd)
        self.assertIsNone(calls[0].estimated_cost_eur)
        self.assertIsNone(summarize_calls(calls)["estimated_cost_eur"])

    def test_exchange_rate_must_be_positive_and_finite(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(rate=value), self.assertRaises(ValidationError):
                EuroExchangeRate(usd_per_eur=value, as_of="2026-10-20", source_url="https://www.ecb.europa.eu/")

    def test_embedding_input_uses_embedding_rate_and_context_stage(self):
        result = SimpleNamespace(model="text-embedding-3-small", usage={"prompt_tokens": 1000, "total_tokens": 1000})
        with capture_api_metrics(self.pricing) as calls:
            with api_stage("corpus_embedding"):
                api_call("embedding", Mock(return_value=result), model="text-embedding-3-small")
        self.assertEqual(calls[0].stage, "corpus_embedding")
        self.assertEqual(calls[0].output_tokens, 0)
        self.assertAlmostEqual(calls[0].estimated_cost_usd, 0.00002)

    def test_missing_usage_or_cached_detail_is_unknown_not_free(self):
        for usage in (None, {}, {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}):
            with self.subTest(usage=usage):
                call = self.capture(response(usage=usage))
                self.assertIsNone(call.estimated_cost_usd)
                summary = summarize_calls([call])
                self.assertIsNone(summary["total_tokens"])
                self.assertIsNone(summary["estimated_cost_usd"])

    def test_inconsistent_token_counts_cannot_be_priced(self):
        for usage in (
            {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 11}, "output_tokens": 1, "total_tokens": 11},
            {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1, "total_tokens": 999},
        ):
            call = self.capture(response(usage=usage))
            self.assertFalse(call.usage_complete)
            self.assertIsNone(call.estimated_cost_usd)

    def test_unknown_models_and_nonstandard_tiers_are_not_assigned_guessed_prices(self):
        for result in (response(model="gpt-4.1-mini-unknown"), response(service_tier="flex")):
            call = self.capture(result)
            self.assertEqual(call.total_tokens, 1300)
            self.assertIsNone(call.estimated_cost_usd)
        mixed = summarize_calls([self.capture(response()), self.capture(response(model="unpriced"))])
        self.assertIsNone(mixed["estimated_cost_usd"])
        self.assertAlmostEqual(mixed["known_cost_usd_subtotal"], 0.00034)

    def test_cache_write_usage_is_recorded_but_not_priced_without_write_rates(self):
        result = response()
        result.usage.input_tokens_details.cache_write_tokens = 100
        call = self.capture(result)
        self.assertEqual(call.cache_write_tokens, 100)
        self.assertTrue(call.usage_complete)
        self.assertIsNone(call.estimated_cost_usd)

    def test_failed_call_keeps_timing_and_safe_error_type_and_restores_context(self):
        with capture_api_metrics(self.pricing) as calls:
            with patch("api_metrics.perf_counter", side_effect=[1, 1.125]):
                with self.assertRaises(RuntimeError):
                    api_call("validation", Mock(side_effect=RuntimeError("secret-value")), model="gpt-4.1-mini")
        self.assertEqual(calls[0].latency_ms, 125)
        self.assertEqual(calls[0].error_type, "RuntimeError")
        self.assertNotIn("secret-value", calls[0].model_dump_json())
        self.assertIsNone(calls[0].estimated_cost_usd)
        operation = Mock(return_value=object())
        self.assertIs(api_call("validation", operation, model="example"), operation.return_value)
        self.assertEqual(len(calls), 1)

    def test_validation_diagnostics_exclude_values_messages_and_unknown_field_names(self):
        secret = "private-value-must-not-be-recorded"
        try:
            bot.FAQResponse.model_validate({
                "action": "answer", "confidence_score": "high", "suggested_response": secret,
                "relevant_contact_form": secret, secret: secret,
            })
        except ValidationError as error:
            issues = validation_issues(error)
            with capture_api_metrics(self.pricing) as calls:
                with self.assertRaises(ValidationError):
                    api_call("answer_generation", Mock(side_effect=error), model="gpt-4.1-mini")
        self.assertEqual({issue.code for issue in issues}, {"literal_error", "extra_forbidden"})
        self.assertEqual({issue.field for issue in issues}, {"relevant_contact_form", "unknown_field"})
        self.assertEqual(calls[0].validation_issues, issues)
        self.assertNotIn(secret, calls[0].model_dump_json())

    def test_nested_captures_and_threads_do_not_share_call_lists(self):
        with capture_api_metrics(self.pricing) as outer:
            self.capture(response())
            api_call("validation", Mock(return_value=response()), model="gpt-4.1-mini")
        self.assertEqual(len(outer), 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = list(pool.map(lambda _: self.capture(response()), range(2)))
        self.assertEqual(len(calls), 2)
        self.assertIsNot(calls[0], calls[1])

    def test_invalid_pricing_and_duplicate_aliases_are_rejected(self):
        for update in ({"input_usd_per_million": -1}, {"output_usd_per_million": float("nan")}, {"aliases": ["text-embedding-3-small"]}):
            payload = self.pricing.model_dump()
            payload["models"]["gpt-4.1-mini"].update(update)
            with self.assertRaises(ValidationError):
                PricingSnapshot.model_validate(payload)

    def test_unknown_historical_coverage_is_not_reported_as_zero_cost(self):
        result = CaseResult(case_id="case-01", inferred_action="error", latency_ms=125)
        dataset = load_dataset()
        metrics = summarize([dataset.cases[0]], [result])
        self.assertIsNone(result.api_calls)
        self.assertIsNone(metrics["usage"]["estimated_cost_usd"])
        self.assertIsNone(metrics["usage"]["total_tokens"])
        self.assertEqual(metrics["mean_latency_ms"], 125)

    def test_mixed_old_and_new_results_only_show_known_subtotals(self):
        dataset = load_dataset()
        with capture_api_metrics(self.pricing) as calls:
            api_call("validation", Mock(return_value=response()), model="gpt-4.1-mini")
        results = [
            CaseResult(case_id=dataset.cases[0].id, inferred_action="answer", api_calls=calls),
            CaseResult(case_id=dataset.cases[1].id, inferred_action="answer"),
        ]
        usage = summarize(dataset.cases[:2], results)["usage"]
        self.assertEqual(usage["instrumented_cases"], 1)
        self.assertIsNone(usage["total_tokens"])
        self.assertEqual(usage["recorded_total_tokens_subtotal"], 1300)
        self.assertIsNone(usage["estimated_cost_usd"])
        self.assertAlmostEqual(usage["known_cost_usd_subtotal"], 0.00034)


def response_body(parsed, usage):
    return {
        "id": "resp_test", "object": "response", "created_at": 1, "status": "completed",
        "model": "gpt-4.1-mini-2025-04-14", "service_tier": "default",
        "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(parsed), "annotations": []}]}],
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "usage": usage, "metadata": {},
    }


class EvaluationUsageTest(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_sdk_repairs_invalid_categories_and_urgency_without_retrieval(self):
        corrected = {
            "is_valid_question": True, "needs_clarification": True,
            "category": "pagamentos_servicos", "suggested_response": "O que aconteceu com o pagamento?",
            "urgency": "unknown", "urgency_reason": "Falta impacto.",
            "standalone_question": "",
        }
        missing = {key: value for key, value in corrected.items() if key != "category"}
        for invalid, code in (
            (missing, "missing"),
            (corrected | {"category": "private-invalid-category"}, "literal_error"),
            (corrected | {"category": "fora_ambito"}, "category_scope_mismatch"),
            ({key: value for key, value in corrected.items() if key != "urgency"}, "missing"),
            (corrected | {"urgency": "private-invalid-urgency"}, "literal_error"),
            (corrected | {"urgency": "not_applicable"}, "urgency_scope_mismatch"),
            (corrected | {"urgency_reason": " "}, "urgency_reason_missing"),
        ):
            with self.subTest(code=code):
                requests = []

                def respond(request):
                    requests.append(json.loads(request.content))
                    parsed = invalid if len(requests) == 1 else corrected
                    return httpx.Response(200, json=response_body(parsed, response().usage.model_dump()))

                sample = EvaluationCase(
                    id="payment", split="development", category="pagamentos_servicos",
                    question="Tenho um problema com um pagamento.", expected_action="clarify",
                    rubric="Clarify the payment symptom without assuming a charge or refund.",
                )
                with (
                    OpenAI(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client,
                    patch("chat_bot.ensure_vector_store") as index,
                ):
                    result = run_case(sample, client)
                self.assertEqual(result.inferred_action, "clarify")
                self.assertEqual(result.validation.category, "pagamentos_servicos")
                self.assertEqual([call.stage for call in result.api_calls], ["validation", "validation_repair"])
                self.assertEqual(result.api_calls[0].validation_issues[0].code, code)
                self.assertIn(code, requests[1]["instructions"])
                self.assertNotIn("private-invalid-category", requests[1]["instructions"])
                self.assertNotIn("private-invalid-urgency", requests[1]["instructions"])
                self.assertEqual(requests[0]["input"], requests[1]["input"])
                index.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_validation_sdk_early_returns_and_invalid_states_never_retrieve(self):
        scenarios = [
            ("clarify", {"is_valid_question": True, "needs_clarification": True,
                         "suggested_response": "A que opção se refere?", "standalone_question": ""}),
            ("reject", {"is_valid_question": False, "needs_clarification": False,
                        "suggested_response": "Só posso ajudar com questões sobre o website.", "standalone_question": ""}),
            ("error", {"is_valid_question": False, "needs_clarification": True,
                       "suggested_response": "", "standalone_question": ""}),
            ("error", {"is_valid_question": False, "needs_clarification": False,
                       "suggested_response": " ", "standalone_question": ""}),
        ]
        sample = EvaluationCase(
            id="ambiguous", split="development", category="indeterminado",
            question="E aquela opção, como funciona?", expected_action="clarify",
            rubric="Ask which option the user means without retrieving.",
        )
        for expected, parsed in scenarios:
            parsed["category"] = "indeterminado" if parsed["is_valid_question"] else "fora_ambito"
            parsed["urgency"] = "unknown" if parsed["is_valid_question"] else "not_applicable"
            parsed["urgency_reason"] = "Tópico desconhecido ou rejeitado."
            with self.subTest(expected=expected, parsed=parsed):
                requests = []

                def respond(request):
                    requests.append(request)
                    return httpx.Response(200, json=response_body(parsed, response().usage.model_dump()))

                with (
                    OpenAI(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client,
                    patch("chat_bot.ensure_vector_store") as index,
                    patch("chat_bot.retrieve_faqs") as retrieve,
                    patch("chat_bot.generate_faq_answer") as generate,
                ):
                    result = run_case(sample, client)
                self.assertEqual(result.inferred_action, expected)
                self.assertEqual(len(requests), 2 if expected == "error" else 1)
                stages = ["validation", "validation_repair"] if expected == "error" else ["validation"]
                self.assertEqual([call.stage for call in result.api_calls], stages)
                if expected == "error":
                    self.assertEqual(result.error_type, "ValidationError")
                    code = "clarification_rejected" if parsed["needs_clarification"] else "early_reply_missing"
                    self.assertEqual(result.validation_issues[0].code, code)
                    self.assertTrue(all(call.validation_issues[0].code == code for call in result.api_calls))
                else:
                    self.assertEqual(result.answer, parsed["suggested_response"])
                    self.assertTrue(result.answer.strip())
                    self.assertIsNotNone(result.validation)
                    self.assertEqual(result.validation.category, parsed["category"])
                    self.assertIsNone(result.structured_answer)
                index.assert_not_called()
                retrieve.assert_not_called()
                generate.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_sdk_repairs_ambiguous_reference_once_without_promoting_output_to_instructions(self):
        requests = []
        original = "E aquela opção, como funciona? private-user-context"
        invalid = {"is_valid_question": False, "needs_clarification": True,
                   "urgency": "not_applicable", "urgency_reason": "Recusa.",
                   "category": "fora_ambito", "suggested_response": "private-model-output", "standalone_question": ""}
        corrected = {"is_valid_question": True, "needs_clarification": True,
                     "urgency": "unknown", "urgency_reason": "Falta impacto.",
                     "category": "indeterminado", "suggested_response": "A que opção se refere?", "standalone_question": ""}

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            parsed = invalid if len(requests) == 1 else corrected
            return httpx.Response(200, json=response_body(parsed, response().usage.model_dump()))

        sample = EvaluationCase(id="case-11", split="development", category="indeterminado",
                                question=original, expected_action="clarify", rubric="Ask which option.")
        with (
            OpenAI(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client,
            patch("chat_bot.ensure_vector_store") as index,
        ):
            result = run_case(sample, client)
        self.assertEqual(result.inferred_action, "clarify")
        self.assertEqual(result.answer, corrected["suggested_response"])
        self.assertEqual([call.stage for call in result.api_calls], ["validation", "validation_repair"])
        self.assertEqual(result.api_calls[0].validation_issues[0].code, "clarification_rejected")
        self.assertIsNone(result.api_calls[1].error_type)
        self.assertEqual(requests[0]["input"], requests[1]["input"])
        self.assertIn("clarification_rejected", requests[1]["instructions"])
        self.assertNotIn("private-user-context", requests[1]["instructions"])
        self.assertNotIn("private-model-output", requests[1]["instructions"])
        self.assertIn(original, requests[1]["input"][-1]["content"])
        self.assertIsNone(summarize_calls(result.api_calls)["estimated_cost_eur"])
        self.assertIsNotNone(summarize_calls(result.api_calls)["known_cost_eur_subtotal"])
        payload = {"metadata": run_metadata(load_dataset(), DATASET_PATH, "development"), "results": [result.model_dump()]}
        self.assertIn("Validation diagnostics (validation)", render_report(load_dataset(), [sample], payload))
        index.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_sdk_repairs_only_generation_for_contact_and_suggestion_requests(self):
        scenarios = [
            ("case-19", "contactos_anuncios", "escalate", ["private-delivery-details"], "escalation_user_info"),
            ("case-31", "sugestoes", "answer", [], "non_escalation_destination"),
        ]
        dataset = load_dataset()
        for case_id, route, action, missing, rule in scenarios:
            with self.subTest(case_id=case_id):
                sample = next(case for case in dataset.cases if case.id == case_id)
                requests = []
                invalid = {"action": action, "confidence_score": "high", "suggested_response": "private-model-output",
                           "relevant_contact_form": route, "missing_info": missing, "missing_knowledge": [], "sources": [], "topics": []}
                corrected = invalid | {"action": "escalate", "missing_info": [], "suggested_response": "Contacte a equipa."}
                parsed = [{"is_valid_question": True, "needs_clarification": False, "category": route, "urgency": "normal", "urgency_reason": "Problema concreto.", "suggested_response": "", "standalone_question": sample.question}, invalid, corrected]

                def respond(request):
                    requests.append(json.loads(request.content))
                    return httpx.Response(200, json=response_body(parsed[len(requests) - 1], response().usage.model_dump()))

                with (
                    OpenAI(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client,
                    patch("chat_bot.ensure_vector_store") as index,
                    patch("chat_bot.retrieve_faqs", return_value=[]) as retrieve,
                ):
                    result = run_case(sample, client)
                self.assertEqual(result.inferred_action, "escalate")
                self.assertEqual(result.structured_answer.relevant_contact_form, route)
                self.assertIn(bot.CONTACT_FORMS[route], result.answer)
                self.assertEqual([call.stage for call in result.api_calls], ["validation", "answer_generation", "answer_generation_repair"])
                self.assertEqual(result.api_calls[1].validation_issues[0].code, rule)
                self.assertEqual(requests[1]["input"], requests[2]["input"])
                self.assertIn(rule, requests[2]["instructions"])
                self.assertNotIn("private-model-output", requests[2]["instructions"])
                self.assertNotIn("private-delivery-details", requests[2]["instructions"])
                self.assertEqual(index.call_count, 1)
                self.assertEqual(retrieve.call_count, 1)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_real_sdk_pipeline_measures_cold_and_warm_index_without_network(self):
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            if request.url.path.endswith("embeddings"):
                tokens = 400 if body["input"][0].startswith("Pergunta:") else 10
                return httpx.Response(200, json={
                    "object": "list", "model": "text-embedding-3-small",
                    "data": [{"object": "embedding", "embedding": [0.1], "index": 0}],
                    "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
                })
            if "Validas mensagens" in body["instructions"]:
                parsed = {"is_valid_question": True, "category": "outros", "urgency": "normal", "urgency_reason": "Problema concreto.", "suggested_response": "", "standalone_question": "Example request", "needs_clarification": False}
                inputs, cached, outputs = 1000, 0, 20
            else:
                parsed = {"action": "escalate", "sources": [], "topics": [], "confidence_score": "low", "missing_info": [], "missing_knowledge": [], "relevant_contact_form": "outros", "suggested_response": "Contact support."}
                inputs, cached, outputs = 1400, 1000, 80
            return httpx.Response(200, json=response_body(parsed, {
                "input_tokens": inputs, "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
                "output_tokens": outputs, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": inputs + outputs,
            }))

        sample = EvaluationCase(
            id="example", split="development", category="outros", question="Example request",
            expected_action="escalate", expected_contact_form="outros", rubric="Contact support.",
        )
        record = bot.FAQRecord(chunk_id="faq-001", question="Question", answer="Answer", text="Pergunta: Question\nResposta: Answer")
        collection = Mock()
        collection.count.return_value = 1
        collection.get.return_value = {"ids": []}
        collection.query.return_value = {"ids": [[]], "metadatas": [[]], "distances": [[]]}
        with (
            tempfile.TemporaryDirectory() as directory,
            OpenAI(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client,
            patch("chat_bot.GENERATION_MODEL", "gpt-4.1-mini"),
            patch("chat_bot.EMBEDDING_MODEL", "text-embedding-3-small"),
            patch("chat_bot.get_collection", return_value=collection),
            patch("chat_bot.load_faq_records", return_value=[record]),
            patch("chat_bot.VERSION_PATH", Path(directory) / "version.txt"),
        ):
            cold = run_case(sample, client)
            warm = run_case(sample, client)
        self.assertEqual(cold.inferred_action, "escalate", cold.error_type)
        self.assertEqual(warm.inferred_action, "escalate", warm.error_type)
        self.assertEqual([c.stage for c in cold.api_calls], ["validation", "corpus_embedding", "query_embedding", "answer_generation"])
        self.assertEqual([c.stage for c in warm.api_calls], ["validation", "query_embedding", "answer_generation"])
        self.assertAlmostEqual(summarize_calls(cold.api_calls)["estimated_cost_usd"], 0.0008282)
        self.assertAlmostEqual(summarize_calls(warm.api_calls)["estimated_cost_usd"], 0.0008202)
        collection.upsert.assert_called_once()
        self.assertEqual(len(requests), 7)
        for request in requests:
            if "instructions" in request and "Validas mensagens" not in request["instructions"]:
                schema = request["text"]["format"]["schema"]
                self.assertEqual(list(schema["properties"])[:2], ["action", "relevant_contact_form"])
                self.assertIn("action", schema["required"])
                self.assertIn("missing_knowledge", schema["required"])
                self.assertEqual(set(schema["properties"]["action"]["enum"]), {"answer", "clarify", "escalate"})
                self.assertFalse(schema["additionalProperties"])
            elif "instructions" in request:
                schema = request["text"]["format"]["schema"]
                self.assertEqual(list(schema["properties"])[:2], ["is_valid_question", "needs_clarification"])
                self.assertIn("category", schema["required"])
                self.assertEqual(set(schema["properties"]["category"]["enum"]), set(bot.RequestCategory.__args__))
                self.assertNotIn("none", schema["properties"]["category"]["enum"])
                self.assertIn("urgency", schema["required"])
                self.assertIn("urgency_reason", schema["required"])
                self.assertEqual(set(schema["properties"]["urgency"]["enum"]), set(bot.RequestUrgency.__args__))
        payload = {"metadata": run_metadata(load_dataset(), DATASET_PATH, "development"), "results": [cold.model_dump()]}
        report = render_report(load_dataset(), [sample], payload)
        self.assertIn("corpus_embedding", report)
        self.assertIn(f"{summarize_calls(cold.api_calls)['estimated_cost_eur']:.8f}", report)
        self.assertIn("Estimated EUR", report)
        self.assertNotIn("Estimated USD", report)
        self.assertIn("2026-10-21", report)
        restored = CaseResult.model_validate_json(cold.model_dump_json())
        self.assertEqual(restored.api_calls, cold.api_calls)

    def test_usage_before_a_later_failure_is_preserved_as_partial_cost(self):
        dataset = load_dataset()

        def failing_answer(question, history, client):
            api_call("validation", Mock(return_value=response()), model="gpt-4.1-mini")
            api_call("answer_generation", Mock(side_effect=RuntimeError("secret-value")), model="gpt-4.1-mini")

        result = run_case(dataset.cases[0], Mock(), answer_fn=failing_answer)
        self.assertEqual(result.inferred_action, "error")
        summary = summarize(dataset.cases[:1], [result])["usage"]
        self.assertEqual(summary["calls_with_complete_usage"], 1)
        self.assertIsNone(summary["estimated_cost_usd"])
        self.assertAlmostEqual(summary["known_cost_usd_subtotal"], 0.00034)
        self.assertIsNone(summary["estimated_cost_eur"])
        self.assertAlmostEqual(
            summary["known_cost_eur_subtotal"], 0.00034 / load_pricing().eur_exchange_rate.usd_per_eur,
            places=12,
        )
        self.assertNotIn("secret-value", result.model_dump_json())

    def test_report_regeneration_retains_saved_rates_and_costs_without_api_calls(self):
        dataset = load_dataset()
        pricing = load_pricing()
        rate = EuroExchangeRate(usd_per_eur=1.25, as_of="2026-10-20", source_url="https://www.ecb.europa.eu/")
        pricing = pricing.model_copy(update={"eur_exchange_rate": rate})
        with capture_api_metrics(pricing) as calls:
            api_call("validation", Mock(return_value=response()), model="gpt-4.1-mini")
        result = CaseResult(case_id=dataset.cases[0].id, inferred_action="answer", api_calls=calls)
        metadata = run_metadata(dataset, DATASET_PATH, "development", pricing)
        self.assertEqual(metadata["dataset_sha256"], file_hash(DATASET_PATH))
        payload = {"metadata": metadata, "results": [result.model_dump()]}
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original"
            regenerated = Path(directory) / "regenerated"
            write_reports(original, dataset, dataset.cases[:1], payload)
            with (
                patch("sys.argv", ["evaluate.py", "--report-from", str(original.with_suffix(".json")),
                                   "--output", str(regenerated), "--pricing", "nonexistent-pricing.json"]),
                patch("evaluate.load_pricing", side_effect=AssertionError("Must not reprice")),
                patch("evaluate.OpenAI", side_effect=AssertionError("Must not call API")),
                patch("sys.stdout", new_callable=StringIO),
            ):
                main()
            restored = json.loads(regenerated.with_suffix(".json").read_text())
            report = regenerated.with_suffix(".md").read_text()
        self.assertEqual(restored["metadata"]["pricing_snapshot"], pricing.model_dump(mode="json"))
        self.assertAlmostEqual(restored["metrics"]["usage"]["estimated_cost_usd"], 0.00034)
        self.assertAlmostEqual(restored["metrics"]["usage"]["estimated_cost_eur"], 0.000272)
        self.assertIn("0.00027200", report)
        self.assertIn("1 EUR = 1.25 USD", report)


if __name__ == "__main__":
    unittest.main()
