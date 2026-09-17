"""Versioned KataGo evidence and failure-safe LLM explanations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from typing import Any, Callable, Literal

from analyzer import GPT_MODEL, get_client
from deep_analysis import DeepAnalysisEvidence
from game_scan import ColorCode, PhaseCode


EVIDENCE_SCHEMA_VERSION = "go-error-evidence.v1"

EXPLANATION_FIELDS = (
    "title",
    "summary",
    "why_it_matters",
    "better_plan",
    "learning_point",
)

EXPLANATION_JSON_SCHEMA = {
    "name": "go_error_explanation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            field: {"type": "string"}
            for field in EXPLANATION_FIELDS
        },
        "required": list(EXPLANATION_FIELDS),
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class LLMErrorEvidence:
    """Stable, serializable evidence boundary consumed by the LLM layer."""

    schema_version: str
    rank: int
    player: ColorCode
    phase: PhaseCode
    event_start_move: int
    event_end_move: int
    worst_move_number: int
    actual_move: str
    deep_best_move_before: str
    deep_black_winrate_before: float
    deep_black_winrate_after: float
    deep_black_score_lead_before: float
    deep_black_score_lead_after: float
    player_winrate_loss: float
    player_score_loss: float
    visits: int
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredExplanation:
    title: str
    summary: str
    why_it_matters: str
    better_plan: str
    learning_point: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


ExplanationStatus = Literal["succeeded", "pending"]
ExplanationErrorCode = Literal[
    "configuration_error",
    "provider_error",
    "invalid_response",
]


@dataclass(frozen=True)
class ExplanationResult:
    """Keeps canonical evidence available even when explanation is pending."""

    status: ExplanationStatus
    evidence: LLMErrorEvidence
    explanation: StructuredExplanation | None
    error_code: ExplanationErrorCode | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "evidence": self.evidence.to_dict(),
            "explanation": (
                self.explanation.to_dict()
                if self.explanation is not None
                else None
            ),
            "error_code": self.error_code,
        }


def build_llm_evidence(source: DeepAnalysisEvidence) -> LLMErrorEvidence:
    """Map deep evidence without recalculating or reinterpreting Go facts."""

    return LLMErrorEvidence(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        rank=source.rank,
        player=source.player,
        phase=source.phase,
        event_start_move=source.event_start_move,
        event_end_move=source.event_end_move,
        worst_move_number=source.worst_move_number,
        actual_move=source.actual_move,
        deep_best_move_before=source.deep_best_move_before,
        deep_black_winrate_before=source.deep_black_winrate_before,
        deep_black_winrate_after=source.deep_black_winrate_after,
        deep_black_score_lead_before=source.deep_black_score_lead_before,
        deep_black_score_lead_after=source.deep_black_score_lead_after,
        player_winrate_loss=source.player_winrate_loss,
        player_score_loss=source.player_score_loss,
        visits=source.visits,
        warnings=list(source.warnings),
    )


def _prompt(evidence: LLMErrorEvidence) -> str:
    evidence_json = json.dumps(
        evidence.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""你是围棋复盘解释器。KataGo 是所有围棋事实和数值的唯一来源。

请根据下面的结构化证据，用简洁中文解释这次错误，供复盘看板展示。

规则：
1. 只能解释证据明确支持的内容；将数值事实与谨慎的解释区分开。
2. 不得自行计算、改写或在文字字段中复述精确胜率、目差等数值；界面会直接展示证据数值。
3. 不得虚构变化图、领地、棋块、提子、先后手、死活、历史习惯或证据未提供的战术/战略原因。
4. 若证据不足以判断具体原因，应明确说证据只能确认该手代价较高，并建议围绕推荐着复盘。
5. title 要短；其余每项一至两句，避免空泛鼓励和长篇文章。
6. 只输出符合 JSON schema 的对象。

证据：
{evidence_json}"""


def _batch_prompt(evidence: list[LLMErrorEvidence]) -> str:
    evidence_json = json.dumps(
        [item.to_dict() for item in evidence],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""你是围棋复盘解释器。KataGo 是所有围棋事实和数值的唯一来源。

请按输入顺序为每条结构化证据生成一条简洁中文解释。

规则：
1. 只能解释对应证据明确支持的内容，不得跨事件混用信息。
2. 不得自行计算、改写或在文字字段中复述精确胜率、目差等数值。
3. 不得虚构变化图、领地、棋块、提子、先后手、死活、历史习惯或未提供的原因。
4. 证据不足时，只说明该手代价较高，并建议围绕推荐着复盘。
5. 每条 title 要短；其余每项一至两句。
6. explanations 数量和顺序必须与证据完全一致，只输出符合 JSON schema 的对象。

证据：
{evidence_json}"""


def _validate_explanation(content: str) -> StructuredExplanation:
    parsed = json.loads(content)
    if not isinstance(parsed, dict) or set(parsed) != set(EXPLANATION_FIELDS):
        raise ValueError("Explanation response has an invalid object shape")
    for field in EXPLANATION_FIELDS:
        value = parsed[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Explanation field {field} must be a non-empty string")
    return StructuredExplanation(**{
        field: parsed[field].strip()
        for field in EXPLANATION_FIELDS
    })


class ErrorExplanationService:
    """OpenAI-compatible structured explanation service."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        client_factory: Callable[[], Any] = get_client,
        model: str | None = None,
    ):
        self._client = client
        self._client_factory = client_factory
        self._model = (
            model
            if model is not None
            else os.getenv("OPENAI_EXPLANATION_MODEL", GPT_MODEL)
        )

    def explain(self, evidence: LLMErrorEvidence) -> ExplanationResult:
        if not isinstance(self._model, str) or not self._model.strip():
            return self._pending(evidence, "configuration_error")
        if self._client is None and not os.getenv("OPENAI_API_KEY"):
            return self._pending(evidence, "configuration_error")

        try:
            client = self._client or self._client_factory()
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": _prompt(evidence)}],
                response_format={
                    "type": "json_schema",
                    "json_schema": EXPLANATION_JSON_SCHEMA,
                },
            )
        except Exception:
            return self._pending(evidence, "provider_error")

        try:
            content = response.choices[0].message.content
            if not isinstance(content, str):
                raise ValueError("Explanation response content is missing")
            explanation = _validate_explanation(content)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._pending(evidence, "invalid_response")

        return ExplanationResult(
            status="succeeded",
            evidence=evidence,
            explanation=explanation,
        )

    def explain_many(
        self,
        evidence: list[LLMErrorEvidence],
    ) -> list[ExplanationResult]:
        """Explain multiple finalized errors in one provider request."""
        if not evidence:
            return []
        if not isinstance(self._model, str) or not self._model.strip():
            return [self._pending(item, "configuration_error") for item in evidence]
        if self._client is None and not os.getenv("OPENAI_API_KEY"):
            return [self._pending(item, "configuration_error") for item in evidence]

        item_schema = EXPLANATION_JSON_SCHEMA["schema"]
        batch_schema = {
            "name": "go_error_explanation_batch",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "explanations": {
                        "type": "array",
                        "minItems": len(evidence),
                        "maxItems": len(evidence),
                        "items": item_schema,
                    },
                },
                "required": ["explanations"],
                "additionalProperties": False,
            },
        }
        try:
            client = self._client or self._client_factory()
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": _batch_prompt(evidence)}],
                response_format={
                    "type": "json_schema",
                    "json_schema": batch_schema,
                },
            )
        except Exception:
            return [self._pending(item, "provider_error") for item in evidence]

        try:
            parsed = json.loads(response.choices[0].message.content)
            if set(parsed) != {"explanations"}:
                raise ValueError("Batch response has an invalid object shape")
            values = parsed["explanations"]
            if not isinstance(values, list) or len(values) != len(evidence):
                raise ValueError("Batch response count does not match evidence")
            explanations = [
                _validate_explanation(json.dumps(value, ensure_ascii=False))
                for value in values
            ]
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return [self._pending(item, "invalid_response") for item in evidence]

        return [
            ExplanationResult(
                status="succeeded",
                evidence=item,
                explanation=explanation,
            )
            for item, explanation in zip(evidence, explanations)
        ]

    @staticmethod
    def _pending(
        evidence: LLMErrorEvidence,
        error_code: ExplanationErrorCode,
    ) -> ExplanationResult:
        return ExplanationResult(
            status="pending",
            evidence=evidence,
            explanation=None,
            error_code=error_code,
        )
