"""Opt-in, context-local API usage capture for reproducible evaluations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PRICING_PATH = Path(__file__).resolve().parent / "data" / "evaluations" / "model_pricing.json"


class ModelRate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["responses", "embeddings"]
    aliases: list[str] = Field(default_factory=list)
    input_usd_per_million: float = Field(ge=0)
    cached_input_usd_per_million: float = Field(ge=0)
    output_usd_per_million: float = Field(ge=0)


class EuroExchangeRate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    usd_per_eur: float = Field(gt=0)
    as_of: date
    source_url: str = Field(min_length=1)


class PricingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: Literal["USD"]
    reporting_currency: Literal["EUR"] = "EUR"
    eur_exchange_rate: EuroExchangeRate | None = None
    service_tier: Literal["standard"]
    verified_on: date
    sources: list[str] = Field(min_length=1)
    models: dict[str, ModelRate]

    @model_validator(mode="after")
    def unique_model_names(self) -> PricingSnapshot:
        names = [name for key, rate in self.models.items() for name in [key, *rate.aliases]]
        if len(names) != len(set(names)):
            raise ValueError("Pricing model names and aliases must be unique.")
        return self


class APICallMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    stage: str
    kind: Literal["responses", "embeddings"]
    requested_model: str
    response_model: str | None = None
    service_tier: str | None = None
    latency_ms: float = Field(default=0, ge=0)
    error_type: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    pricing_model: str | None = None
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    estimated_cost_eur: float | None = Field(default=None, ge=0)

    @property
    def usage_complete(self) -> bool:
        values = (self.input_tokens, self.cached_input_tokens, self.output_tokens, self.total_tokens)
        return (
            all(value is not None for value in values)
            and self.cached_input_tokens <= self.input_tokens
            and self.cached_input_tokens + (self.cache_write_tokens or 0) <= self.input_tokens
            and self.total_tokens == self.input_tokens + self.output_tokens
        )


def load_pricing(path: Path = PRICING_PATH) -> PricingSnapshot:
    return PricingSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def usd_to_eur(cost: float | None, rate: EuroExchangeRate | None) -> float | None:
    if cost is None or rate is None:
        return None
    return float(Decimal(str(cost)) / Decimal(str(rate.usd_per_eur)))


def estimate_cost(call: APICallMetrics, pricing: PricingSnapshot) -> float | None:
    if (
        not call.usage_complete or call.service_tier not in {None, "default"}
        or call.cache_write_tokens not in {None, 0}
    ):
        return None
    model = call.response_model or call.requested_model
    for name, rate in pricing.models.items():
        if model not in [name, *rate.aliases] or call.kind != rate.kind:
            continue
        call.pricing_model = name
        # Cached tokens are already included in input; reasoning is included in output.
        cost = (
            Decimal(call.input_tokens - call.cached_input_tokens) * Decimal(str(rate.input_usd_per_million))
            + Decimal(call.cached_input_tokens) * Decimal(str(rate.cached_input_usd_per_million))
            + Decimal(call.output_tokens) * Decimal(str(rate.output_usd_per_million))
        ) / Decimal(1_000_000)
        return float(cost)
    return None


_capture: ContextVar[tuple[PricingSnapshot, list[APICallMetrics]] | None] = ContextVar("api_capture", default=None)
_stage: ContextVar[str | None] = ContextVar("api_stage", default=None)


@contextmanager
def capture_api_metrics(pricing: PricingSnapshot):
    calls: list[APICallMetrics] = []
    token = _capture.set((pricing, calls))
    try:
        yield calls
    finally:
        _capture.reset(token)


@contextmanager
def api_stage(name: str):
    token = _stage.set(name)
    try:
        yield
    finally:
        _stage.reset(token)


def _field(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def api_call(stage: str, operation: Callable, **kwargs):
    capture = _capture.get()
    if capture is None:
        return operation(**kwargs)
    pricing, calls = capture
    kind = "embeddings" if stage == "embedding" else "responses"
    call = APICallMetrics(stage=_stage.get() or stage, kind=kind, requested_model=kwargs["model"])
    start = perf_counter()
    try:
        response = operation(**kwargs)
    except Exception as error:
        call.error_type = type(error).__name__
        raise
    else:
        model = _field(response, "model")
        tier = _field(response, "service_tier")
        call.response_model = model if isinstance(model, str) else None
        call.service_tier = tier if isinstance(tier, str) else None
        usage = _field(response, "usage")
        if kind == "embeddings":
            call.input_tokens = _count(_field(usage, "prompt_tokens"))
            call.output_tokens = call.cached_input_tokens = call.reasoning_tokens = 0
            call.cache_write_tokens = 0
        else:
            call.input_tokens = _count(_field(usage, "input_tokens"))
            call.cached_input_tokens = _count(_field(_field(usage, "input_tokens_details"), "cached_tokens"))
            call.cache_write_tokens = _count(_field(_field(usage, "input_tokens_details"), "cache_write_tokens"))
            call.output_tokens = _count(_field(usage, "output_tokens"))
            call.reasoning_tokens = _count(_field(_field(usage, "output_tokens_details"), "reasoning_tokens"))
        call.total_tokens = _count(_field(usage, "total_tokens"))
        call.estimated_cost_usd = estimate_cost(call, pricing)
        call.estimated_cost_eur = usd_to_eur(call.estimated_cost_usd, pricing.eur_exchange_rate)
        return response
    finally:
        call.latency_ms = round((perf_counter() - start) * 1000, 1)
        calls.append(call)


def summarize_calls(calls: list[APICallMetrics], captured: bool = True) -> dict:
    complete = captured and all(call.usage_complete for call in calls)
    costs_complete = captured and all(call.estimated_cost_usd is not None for call in calls)
    known_costs = [call.estimated_cost_usd for call in calls if call.estimated_cost_usd is not None]
    known_eur_costs = [call.estimated_cost_eur for call in calls if call.estimated_cost_eur is not None]
    eur_costs_complete = captured and len(known_eur_costs) == len(calls)
    return {
        "api_calls": len(calls),
        "calls_with_complete_usage": sum(call.usage_complete for call in calls),
        "calls_with_estimated_cost": len(known_costs),
        "calls_with_estimated_cost_eur": len(known_eur_costs),
        "input_tokens": sum(call.input_tokens for call in calls) if complete else None,
        "cached_input_tokens": sum(call.cached_input_tokens for call in calls) if complete else None,
        "cache_write_tokens": sum(call.cache_write_tokens for call in calls) if captured and all(call.cache_write_tokens is not None for call in calls) else None,
        "output_tokens": sum(call.output_tokens for call in calls) if complete else None,
        "total_tokens": sum(call.total_tokens for call in calls) if complete else None,
        "recorded_total_tokens_subtotal": sum(call.total_tokens for call in calls if call.total_tokens is not None) if calls or captured else None,
        "estimated_cost_usd": round(sum(known_costs), 12) if costs_complete else None,
        "known_cost_usd_subtotal": round(sum(known_costs), 12) if known_costs or (captured and not calls) else None,
        "estimated_cost_eur": round(sum(known_eur_costs), 12) if eur_costs_complete else None,
        "known_cost_eur_subtotal": round(sum(known_eur_costs), 12) if known_eur_costs or (captured and not calls) else None,
        "api_latency_ms": round(sum(call.latency_ms for call in calls), 1) if captured else None,
    }
