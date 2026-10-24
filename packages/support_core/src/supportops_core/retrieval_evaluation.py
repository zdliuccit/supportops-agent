"""固定检索样本的无正文计分与计量聚合。"""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from supportops_core.agent_config import StrictModel

_STRATEGY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RetrievalCaseObservation(StrictModel):
    """单条查询的相关分块、实际排序及可信运行计量，不含查询或分块正文。"""

    query_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Z0-9][A-Z0-9_-]{0,79}$")
    relevant_chunk_ids: list[UUID] = Field(min_length=1, max_length=100)
    returned_chunk_ids: list[UUID] = Field(max_length=100)
    retrieval_latency_ms: float | None = Field(default=None, ge=0, le=86_400_000, allow_inf_nan=False)
    context_tokens: int | None = Field(default=None, ge=0, le=2**31 - 1, strict=True)
    model_cost_source: Literal["no_model_call", "model_call_ledger_settled", "unknown"]
    model_cost_microusd: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)

    @model_validator(mode="after")
    def unique_chunks_and_cost_source(self) -> "RetrievalCaseObservation":
        if (len(self.relevant_chunk_ids) != len(set(self.relevant_chunk_ids))
            or len(self.returned_chunk_ids) != len(set(self.returned_chunk_ids))):
            raise ValueError("检索样本的分块 ID 不能重复")
        if self.model_cost_source == "no_model_call" and self.model_cost_microusd not in (None, 0):
            raise ValueError("无模型调用的检索样本不能有模型费用")
        if ((self.model_cost_source == "model_call_ledger_settled")
            != (self.model_cost_microusd is not None)):
            raise ValueError("模型费用必须来自已结算调用账本")
        return self


class RetrievalEvaluationMetrics(StrictModel):
    """可复现检索指标；缺少任一逐样本计量时保留 null。"""

    strategy_version: str = Field(pattern=_STRATEGY.pattern)
    cutoff: int = Field(ge=1, le=100, strict=True)
    query_count: int = Field(ge=1, le=100_000, strict=True)
    recall_at_k: float = Field(ge=0, le=1, allow_inf_nan=False)
    mean_reciprocal_rank: float = Field(ge=0, le=1, allow_inf_nan=False)
    latency_sample_count: int = Field(ge=0, le=100_000, strict=True)
    p95_retrieval_latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    context_token_sample_count: int = Field(ge=0, le=100_000, strict=True)
    context_tokens_total: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)
    model_cost_sample_count: int = Field(ge=0, le=100_000, strict=True)
    model_cost_microusd_total: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)

    @model_validator(mode="after")
    def complete_totals_match_samples(self) -> "RetrievalEvaluationMetrics":
        if (self.latency_sample_count == self.query_count) != (self.p95_retrieval_latency_ms is not None):
            raise ValueError("检索延迟汇总必须覆盖全部样本")
        if (self.context_token_sample_count == self.query_count) != (self.context_tokens_total is not None):
            raise ValueError("上下文 Token 汇总必须覆盖全部样本")
        if (self.model_cost_sample_count == self.query_count) != (self.model_cost_microusd_total is not None):
            raise ValueError("模型费用汇总必须覆盖全部样本")
        return self


def _nearest_rank_p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]


def aggregate_retrieval_evaluation(
    *, strategy_version: str, observations: list[RetrievalCaseObservation], cutoff: int = 5,
) -> RetrievalEvaluationMetrics:
    """按固定相关分块排序计算 Recall@k、MRR 和完整性受控的计量。"""
    if not _STRATEGY.fullmatch(strategy_version) or not observations or not 1 <= cutoff <= 100:
        raise ValueError("检索策略、样本集或 cutoff 无效")
    query_ids = [row.query_id for row in observations]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("检索评测查询 ID 不能重复")

    recall_sum = 0.0
    reciprocal_rank_sum = 0.0
    for row in observations:
        relevant = set(row.relevant_chunk_ids)
        top_k = row.returned_chunk_ids[:cutoff]
        recall_sum += len(relevant.intersection(top_k)) / len(relevant)
        first_relevant_rank = next(
            (rank for rank, chunk_id in enumerate(row.returned_chunk_ids, start=1) if chunk_id in relevant),
            None,
        )
        if first_relevant_rank is not None:
            reciprocal_rank_sum += 1 / first_relevant_rank

    query_count = len(observations)
    latencies = [row.retrieval_latency_ms for row in observations if row.retrieval_latency_ms is not None]
    context_values = [row.context_tokens for row in observations]
    costs_known = all(row.model_cost_source != "unknown" for row in observations)
    costs = [row.model_cost_microusd or 0 for row in observations]
    return RetrievalEvaluationMetrics(
        strategy_version=strategy_version,
        cutoff=cutoff,
        query_count=query_count,
        recall_at_k=recall_sum / query_count,
        mean_reciprocal_rank=reciprocal_rank_sum / query_count,
        latency_sample_count=len(latencies),
        p95_retrieval_latency_ms=_nearest_rank_p95(latencies) if len(latencies) == query_count else None,
        context_token_sample_count=sum(value is not None for value in context_values),
        context_tokens_total=sum(value or 0 for value in context_values) if all(
            value is not None for value in context_values) else None,
        model_cost_sample_count=sum(row.model_cost_source != "unknown" for row in observations),
        model_cost_microusd_total=sum(costs) if costs_known else None,
)
