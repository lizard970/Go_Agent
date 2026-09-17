"""Narrow Streamlit-facing boundary for running and explicitly saving reviews."""

from dataclasses import dataclass
from typing import Any, Callable
import warnings

import analyzer
from error_explanation import ErrorExplanationService
from katago_adapter import PersistentKataGoAdapter
from move_commentary import MoveCommentaryService
from memory_store import (load_latest_review_run_cache, load_review_run_cache,
                          save_review_run_cache)
from review_agent import (OpenAIReviewController, ReviewAgent,
                          ReviewAgentContext, ReviewAgentResult,
                          default_tool_registry)
from sgf_ingestion import SgfGame, parse_sgf


@dataclass(frozen=True)
class HomeReviewRequest:
    sgf_content: bytes
    user_color: str
    goal: str
    target_move: int | None = None


@dataclass(frozen=True)
class HomeReviewRun:
    request: HomeReviewRequest
    game: SgfGame
    result: ReviewAgentResult


class _ExplicitSaveController:
    """Keep persistence behind the home page's explicit save button."""

    def __init__(self, controller: Any):
        self._controller = controller

    def decide(self, state, tools):
        plan = dict(self._controller.decide(state, tools))
        plan["save_memory"] = False
        return plan


def build_review_request(sgf_content, user_color, goal, target_move=None):
    if not isinstance(sgf_content, bytes) or not sgf_content:
        raise ValueError("请先上传 SGF 棋谱")
    if user_color not in {"B", "W"}:
        raise ValueError("执棋方必须是黑棋或白棋")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("请输入复盘目标")
    if target_move is not None and (type(target_move) is not int or target_move <= 0):
        raise ValueError("目标手数必须是正整数")
    return HomeReviewRequest(sgf_content, user_color, goal.strip(), target_move)


def run_review_request(
    request: HomeReviewRequest,
    *,
    adapter_factory: Callable[[], Any] | None = None,
    controller_factory: Callable[[], Any] | None = None,
    explanation_service_factory: Callable[[], Any] | None = None,
    agent_factory: Callable[..., Any] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    use_cache: bool | None = None,
) -> HomeReviewRun:
    """Run one submitted request and always close its KataGo process."""
    custom_dependencies = any((
        adapter_factory, controller_factory,
        explanation_service_factory, agent_factory,
    ))
    cache_enabled = not custom_dependencies if use_cache is None else use_cache
    if progress_callback is not None:
        progress_callback("解析棋谱…")
    if cache_enabled:
        cached = load_review_run_cache(
            request.sgf_content, request.user_color,
            request.goal, request.target_move,
        )
        if cached is not None:
            if progress_callback is not None:
                progress_callback("分析完成（已读取缓存）")
            return cached
    game = parse_sgf(request.sgf_content)
    adapter = (adapter_factory or PersistentKataGoAdapter.from_env)()
    try:
        context = ReviewAgentContext(
            game=game,
            game_content=request.sgf_content,
            adapter=adapter,
            explanation_service=(explanation_service_factory or ErrorExplanationService)(),
            commentary_service=MoveCommentaryService(),
            embedding_fn=analyzer.get_embeddings,
            embedding_model=analyzer.EMBEDDING_MODEL,
            progress_callback=progress_callback,
        )
        controller = _ExplicitSaveController((controller_factory or OpenAIReviewController)())
        agent = (agent_factory or ReviewAgent)(controller, context)
        result = agent.run(
            request.goal,
            user_color=request.user_color,
            target_move=request.target_move,
        )
        run = HomeReviewRun(request, game, result)
        if cache_enabled and result.status == "completed":
            try:
                save_review_run_cache(run, _review_route(run))
            except Exception as error:
                # A cache write must never invalidate a completed analysis.
                warnings.warn(f"Completed review cache write failed: {error}")
        if progress_callback is not None:
            progress_callback("分析完成")
        return run
    finally:
        adapter.close()


def _review_route(run):
    for entry in run.result.state.trace:
        if entry.action == "plan" and entry.outcome == "succeeded":
            return entry.arguments["route"]
    return "targeted_review" if run.request.target_move is not None else "full_review"


def latest_review_run():
    return load_latest_review_run_cache()


def persist_review_run(run):
    return save_review_run_cache(run, _review_route(run))


def save_review_run(run: HomeReviewRun) -> dict[str, Any]:
    """Persist finalized user events only after an explicit UI action."""
    state = run.result.state
    context = ReviewAgentContext(
        game=run.game,
        game_content=run.request.sgf_content,
        adapter=None,
        explanation_service=ErrorExplanationService(),
        embedding_fn=analyzer.get_embeddings,
        embedding_model=analyzer.EMBEDDING_MODEL,
    )
    if state.saved_event_ids:
        return {"status": "already_saved", "event_ids": list(state.saved_event_ids)}
    saveable_indexes = [
        index for index, event in enumerate(state.evidence_events)
        if event is not None and event.player == state.user_color
    ]
    if not saveable_indexes:
        return {"status": "not_saveable", "event_ids": []}
    registry = default_tool_registry()
    observations = []
    for index in saveable_indexes:
        observations.append(registry.execute(
            "save_error_memory",
            {"evidence_index": index},
            state,
            context,
            state.step_count + len(observations) + 1,
        ))
    errors = [item.message for item in observations if item.status == "error"]
    if errors:
        return {"status": "failed", "error": "；".join(errors), "event_ids": []}
    cache_warning = None
    try:
        persist_review_run(run)
    except Exception as error:
        # The mistake transaction already succeeded; cache refresh is secondary.
        cache_warning = str(error)
    return {
        "status": "saved",
        "event_ids": list(state.saved_event_ids),
        "cache_warning": cache_warning,
    }
