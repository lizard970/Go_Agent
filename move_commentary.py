"""Local move importance and one-call selective whole-game commentary."""

from dataclasses import asdict, dataclass, field
import json
import math
import os
from typing import Any, Literal

from analyzer import GPT_MODEL, get_client
from error_detection import ErrorDetectionConfig, ErrorEvent
from error_explanation import StructuredExplanation
from error_ranking import RankedErrorEvent
from game_scan import GameScanResult, MoveAnalysis


CommentaryLevel = Literal["quiet", "minor", "major", "critical"]
Polarity = Literal["positive", "negative", "neutral"]


@dataclass(frozen=True)
class MoveImportanceConfig:
    minor_winrate_loss: float = 0.02
    minor_score_loss: float = 0.75
    major_winrate_loss: float = 0.05
    major_score_loss: float = 2.0
    critical_winrate_loss: float = 0.10
    critical_score_loss: float = 4.0
    standout_winrate_gap: float = 0.02
    standout_score_gap: float = 1.0
    candidate_limit: int = 3


@dataclass(frozen=True)
class MoveImportance:
    move_number: int
    player: str
    importance_score: float
    commentary_level: CommentaryLevel
    polarity: Polarity
    is_top_error: bool
    error_event: ErrorEvent | None = None


@dataclass(frozen=True)
class MoveCommentaryEvidence:
    move_number: int
    player: str
    phase: str
    commentary_level: CommentaryLevel
    polarity: Polarity
    is_top_error: bool
    is_user_move: bool
    actual_move: str
    recommended_move: str
    black_winrate_before: float
    black_winrate_after: float
    black_score_lead_before: float
    black_score_lead_after: float
    player_winrate_loss: float
    player_score_loss: float
    visits: int
    game_context: dict[str, Any]
    pv: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    deep_evidence: dict[str, Any] | None = None

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class MoveCommentary:
    move_number: int
    status: Literal["succeeded", "pending"]
    headline: str | None = None
    commentary: str | None = None
    semantic_explanation: StructuredExplanation | None = None
    error_code: str | None = None


def _validate_config(config):
    values = (
        config.minor_winrate_loss, config.minor_score_loss,
        config.major_winrate_loss, config.major_score_loss,
        config.critical_winrate_loss, config.critical_score_loss,
        config.standout_winrate_gap, config.standout_score_gap,
    )
    if any(value <= 0 for value in values) or config.candidate_limit <= 0:
        raise ValueError("Move importance thresholds must be positive")


def _candidate_gap(before, player):
    if len(before.candidates) < 2:
        return 0.0, 0.0
    best, alternative = before.candidates[:2]
    direction = 1.0 if player == "B" else -1.0
    return (
        direction * (best.winrate - alternative.winrate),
        direction * (best.score_lead - alternative.score_lead),
    )


def build_move_importance(
    scan: GameScanResult,
    events: list[ErrorEvent],
    ranked_errors: list[RankedErrorEvent],
    *,
    config: MoveImportanceConfig | None = None,
) -> list[MoveImportance]:
    """Assign every scanned move local UI importance without an LLM."""
    config = config or MoveImportanceConfig()
    _validate_config(config)
    detection = ErrorDetectionConfig()
    event_by_move = {
        number: event for event in events for number in event.move_numbers
    }
    top_by_move = {
        number: ranked.event
        for ranked in ranked_errors
        for number in ranked.event.move_numbers
    }
    result = []
    for move in scan.moves:
        event = event_by_move.get(move.move_number)
        top_event = top_by_move.get(move.move_number)
        loss_ratio = max(
            max(0.0, move.player_winrate_loss) / detection.min_winrate_loss,
            max(0.0, move.player_score_loss) / detection.min_score_loss,
        )
        score = min(0.84, 1.0 - math.exp(-0.45 * loss_ratio))
        level: CommentaryLevel = "quiet"
        polarity: Polarity = "neutral"
        if (
            move.player_winrate_loss >= config.critical_winrate_loss
            or move.player_score_loss >= config.critical_score_loss
        ):
            score = max(score, 0.86)
            level, polarity = "critical", "negative"
        elif (
            move.player_winrate_loss >= config.major_winrate_loss
            or move.player_score_loss >= config.major_score_loss
        ):
            score = max(score, 0.58)
            level, polarity = "major", "negative"
        elif (
            move.player_winrate_loss >= config.minor_winrate_loss
            or move.player_score_loss >= config.minor_score_loss
        ):
            score = max(score, 0.28)
            level, polarity = "minor", "negative"
        else:
            before = scan.position_analyses[move.move_number - 1]
            winrate_gap, score_gap = _candidate_gap(before, move.player)
            if (
                move.actual_move == before.best_move
                and (winrate_gap >= config.standout_winrate_gap
                     or score_gap >= config.standout_score_gap)
            ):
                distinction = max(
                    winrate_gap / config.standout_winrate_gap,
                    score_gap / config.standout_score_gap,
                )
                score = min(0.78, 0.42 + 0.08 * distinction)
                level, polarity = "major", "positive"
        if top_event is not None:
            event = top_event
        result.append(MoveImportance(
            move.move_number, move.player, max(0.0, min(1.0, score)),
            level, polarity, top_event is not None, event,
        ))
    return result


def compact_game_context(game, scan=None):
    metadata = getattr(game, "metadata", {})
    return {
        "board_size": game.size,
        "total_moves": len(getattr(game, "moves", [])),
        "rules": getattr(game, "rules", None),
        "komi": getattr(game, "komi", None),
        "black_player": metadata.get("PB"),
        "white_player": metadata.get("PW"),
        "result": metadata.get("RE"),
        "termination": scan.termination if scan is not None else None,
    }


def build_commentary_evidence(
    game,
    scan: GameScanResult,
    importance: list[MoveImportance],
    *,
    user_color: str,
    deep_evidence=(),
    config: MoveImportanceConfig | None = None,
):
    config = config or MoveImportanceConfig()
    _validate_config(config)
    deep_by_move = {item.worst_move_number: item for item in deep_evidence}
    moves = {item.move_number: item for item in scan.moves}
    context = compact_game_context(game, scan)
    evidence = []
    for item in importance:
        if item.commentary_level == "quiet":
            continue
        is_user_move = item.player == user_color
        if not is_user_move and item.commentary_level == "minor":
            continue
        move = moves[item.move_number]
        before = scan.position_analyses[item.move_number - 1]
        candidates = [
            {
                "move": candidate.move,
                "black_winrate": candidate.winrate,
                "black_score_lead": candidate.score_lead,
                "probability": candidate.probability,
                "visits": candidate.visits,
                "pv": list(candidate.pv),
            }
            for candidate in before.candidates[:config.candidate_limit]
        ]
        deep = deep_by_move.get(item.move_number)
        evidence.append(MoveCommentaryEvidence(
            move_number=move.move_number, player=move.player, phase=move.phase,
            commentary_level=item.commentary_level, polarity=item.polarity,
            is_top_error=item.is_top_error, is_user_move=is_user_move,
            actual_move=move.actual_move,
            recommended_move=move.best_move_before,
            black_winrate_before=move.black_winrate_before,
            black_winrate_after=move.black_winrate_after,
            black_score_lead_before=move.black_score_lead_before,
            black_score_lead_after=move.black_score_lead_after,
            player_winrate_loss=move.player_winrate_loss,
            player_score_loss=move.player_score_loss, visits=move.visits,
            game_context=context, pv=list(before.pv), candidates=candidates,
            deep_evidence=asdict(deep) if deep is not None else None,
        ))
    return evidence


class MoveCommentaryService:
    """Generate all selected move comments in one structured provider call."""

    def __init__(
        self, *, client=None, client_factory=get_client, model=None,
        max_batch_size=None,
    ):
        self._client = client
        self._client_factory = client_factory
        self._model = model if model is not None else os.getenv("OPENAI_EXPLANATION_MODEL", GPT_MODEL)
        raw_batch_size = (
            max_batch_size if max_batch_size is not None
            else os.getenv("MOVE_COMMENTARY_MAX_BATCH_SIZE", "20")
        )
        try:
            self.max_batch_size = int(raw_batch_size)
        except (TypeError, ValueError) as error:
            raise ValueError("max_batch_size must be a positive integer") from error
        if self.max_batch_size <= 0 or isinstance(raw_batch_size, float) and not raw_batch_size.is_integer():
            raise ValueError("max_batch_size must be a positive integer")
        self.provider_call_count = 0

    def comment_many(self, evidence: list[MoveCommentaryEvidence]):
        if not evidence:
            return []
        if not self._model or (self._client is None and not os.getenv("OPENAI_API_KEY")):
            return self._pending(evidence, "configuration_error")
        self.provider_call_count = 0
        results = []
        for start in range(0, len(evidence), self.max_batch_size):
            results.extend(self._comment_batch(evidence[start:start + self.max_batch_size]))
        return results

    def _comment_batch(self, evidence):
        item = {
            "type": "object",
            "properties": {
                "move_number": {"type": "integer"},
                "headline": {"type": "string"},
                "commentary": {"type": "string"},
                "why_it_matters": {"type": "string"},
                "better_plan": {"type": "string"},
                "learning_point": {"type": "string"},
            },
            "required": ["move_number", "headline", "commentary", "why_it_matters", "better_plan", "learning_point"],
            "additionalProperties": False,
        }
        schema = {"name": "go_move_commentary_batch", "strict": True, "schema": {
            "type": "object", "properties": {"comments": {
                "type": "array", "minItems": len(evidence), "maxItems": len(evidence), "items": item,
            }}, "required": ["comments"], "additionalProperties": False,
        }}
        payload = json.dumps([item.to_dict() for item in evidence], ensure_ascii=False, separators=(",", ":"))
        prompt = f"""你是围棋复盘教练。KataGo 证据是数值与推荐着的唯一事实来源。
只点评输入中的手，按 commentary_level 控制篇幅：minor 通常一至两句；major 说明判断、影响和下次关注点；critical 要比较实战与推荐思路，在证据提供时使用 PV/候选着，并给出可复用学习点。targeted 的手即使不是错误也要认真解释为值得研究的选择，不得强称失误。is_user_move=false 表示对手行棋，必须明确写成对手的选择或给用户的应对学习点，绝不能称为用户的失误。
不得虚构证据未提供的棋块、死活、提子、先后手或变化。不要在自然语言中重新计算精确数值。每个 move_number 必须原样返回且只出现一次。headline 与 commentary 是用户可见的自然中文；其余字段供记忆层使用。只输出 schema JSON。
证据：{payload}"""
        try:
            client = self._client or self._client_factory()
            self.provider_call_count += 1
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_schema", "json_schema": schema},
            )
            parsed = json.loads(response.choices[0].message.content)
            values = parsed["comments"]
            expected = {item.move_number for item in evidence}
            if not isinstance(values, list) or {item.get("move_number") for item in values} != expected or len(values) != len(expected):
                raise ValueError("Commentary move numbers do not match evidence")
            by_move = {}
            for value in values:
                if set(value) != {"move_number", "headline", "commentary", "why_it_matters", "better_plan", "learning_point"}:
                    raise ValueError("Invalid commentary fields")
                if any(not isinstance(value[field], str) or not value[field].strip() for field in value if field != "move_number"):
                    raise ValueError("Commentary text must be non-empty")
                by_move[value["move_number"]] = MoveCommentary(
                    value["move_number"], "succeeded", value["headline"].strip(),
                    value["commentary"].strip(), StructuredExplanation(
                        value["headline"].strip(), value["commentary"].strip(),
                        value["why_it_matters"].strip(), value["better_plan"].strip(),
                        value["learning_point"].strip(),
                    ),
                )
            return [by_move[item.move_number] for item in evidence]
        except Exception:
            return self._pending(evidence, "provider_or_invalid_response")

    @staticmethod
    def _pending(evidence, code):
        return [MoveCommentary(item.move_number, "pending", error_code=code) for item in evidence]


def importance_timeline_data(importance, current_move):
    """Serializable chart rows spanning every analyzed move."""
    return [{
        "move_number": item.move_number,
        "importance_score": item.importance_score,
        "polarity": item.polarity,
        "commentary_level": item.commentary_level,
        "is_top_error": item.is_top_error,
        "is_current": item.move_number == current_move,
    } for item in importance]
