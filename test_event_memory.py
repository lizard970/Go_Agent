import sqlite3

import pytest

import memory_store
from error_detection import ErrorEvent
from error_explanation import (
    ExplanationResult,
    LLMErrorEvidence,
    StructuredExplanation,
)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_store, "MEMORY_DB", tmp_path / "memory.db")


def make_event(start=10, end=12, worst=10, player="B"):
    return ErrorEvent(
        start_move=start,
        end_move=end,
        player=player,
        move_numbers=[worst, end] if worst != end else [worst],
        sum_winrate_loss=.18,
        sum_score_loss=4.2,
        net_winrate_loss=.15,
        net_score_loss=3.8,
        peak_winrate_loss=.12,
        peak_score_loss=3.0,
        worst_move_number=worst,
        severity_score=2.4,
    )


def make_evidence(event=None, **overrides):
    event = event or make_event()
    values = {
        "schema_version": "go-error-evidence.v1",
        "rank": 1,
        "player": event.player,
        "phase": "middlegame",
        "event_start_move": event.start_move,
        "event_end_move": event.end_move,
        "worst_move_number": event.worst_move_number,
        "actual_move": "C7",
        "deep_best_move_before": "Q10",
        "deep_black_winrate_before": .64,
        "deep_black_winrate_after": .52,
        "deep_black_score_lead_before": 4.5,
        "deep_black_score_lead_after": 1.5,
        "player_winrate_loss": .12,
        "player_score_loss": 3.0,
        "visits": 504,
        "warnings": ["warning"],
    }
    values.update(overrides)
    return LLMErrorEvidence(**values)


def make_explanation(label="方向选择"):
    return StructuredExplanation(
        title=label,
        summary="实战选择偏离了更稳健的候选方向。",
        why_it_matters="这次选择明显削弱了行棋方的局面。",
        better_plan="优先比较推荐着所代表的方向。",
        learning_point="关键处先列出候选着再判断方向。",
    )


def succeeded(evidence, label="方向选择"):
    return ExplanationResult(
        status="succeeded",
        evidence=evidence,
        explanation=make_explanation(label),
    )


def pending(evidence):
    return ExplanationResult(
        status="pending",
        evidence=evidence,
        explanation=None,
        error_code="provider_error",
    )


def vector_fn(vector):
    return lambda texts: [vector for _ in texts]


def save(
    game_content="(;GM[1]SZ[19];B[aa])",
    *,
    event=None,
    evidence=None,
    result=None,
    vector=None,
    belongs_to_user=True,
):
    event = event or make_event()
    evidence = evidence or make_evidence(event)
    result = result or succeeded(evidence)
    return memory_store.save_error_memory(
        game_content,
        19,
        event.player,
        event,
        evidence,
        result,
        belongs_to_user=belongs_to_user,
        embedding_fn=vector_fn(vector) if vector is not None else None,
        embedding_model="fake-embedding" if vector is not None else None,
    )


def test_save_event_evidence_and_successful_explanation():
    record = save(vector=[1.0, 0.0])

    assert record["schema_version"] == "go-error-evidence.v1"
    assert record["candidate_move_numbers"] == [10, 12]
    assert record["actual_move"] == "C7"
    assert record["recommended_move"] == "Q10"
    assert record["deep_black_winrate_before"] == pytest.approx(.64)
    assert record["explanation_status"] == "succeeded"
    assert record["title"] == "方向选择"
    assert record["embedding_status"] == "succeeded"


def test_save_pending_explanation_keeps_evidence_and_embedding_pending():
    event = make_event()
    evidence = make_evidence(event)

    record = save(event=event, evidence=evidence, result=pending(evidence))

    assert record["explanation_status"] == "pending"
    assert record["explanation_error_code"] == "provider_error"
    assert record["title"] is None
    assert record["embedding_status"] == "pending"
    assert record["embedding_text"] is None
    assert record["player_winrate_loss"] == pytest.approx(.12)


def test_duplicate_save_is_idempotent():
    first = save(vector=[1.0, 0.0])
    second = save(vector=[1.0, 0.0])

    assert first["id"] == second["id"]
    with sqlite3.connect(memory_store.MEMORY_DB) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_error_events"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_error_evidence"
        ).fetchone()[0] == 1


def test_event_evidence_explanation_write_is_transactional(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("forced explanation write failure")

    monkeypatch.setattr(memory_store, "_upsert_explanation", fail)

    with pytest.raises(RuntimeError, match="forced"):
        save()

    with sqlite3.connect(memory_store.MEMORY_DB) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_games"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_error_events"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_error_evidence"
        ).fetchone()[0] == 0


def test_pending_explanation_updates_same_event_in_place():
    event = make_event()
    evidence = make_evidence(event)
    pending_record = save(event=event, evidence=evidence, result=pending(evidence))

    updated = memory_store.update_error_explanation(
        pending_record["id"],
        succeeded(evidence, "已补充解释"),
        embedding_fn=vector_fn([.5, .5]),
        embedding_model="fake-embedding",
    )

    assert updated["id"] == pending_record["id"]
    assert updated["explanation_status"] == "succeeded"
    assert updated["title"] == "已补充解释"
    assert updated["embedding_status"] == "succeeded"
    with sqlite3.connect(memory_store.MEMORY_DB) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_error_events"
        ).fetchone()[0] == 1


def test_semantic_embedding_text_is_deterministic_and_has_no_telemetry():
    evidence = make_evidence()
    explanation = make_explanation()

    first = memory_store.build_semantic_mistake_text(evidence, explanation)
    second = memory_store.build_semantic_mistake_text(evidence, explanation)

    assert first == second
    assert first == (
        "围棋错误模式；阶段：middlegame；行棋方：黑棋；实战着：C7；推荐着：Q10；"
        "错误：方向选择；情况：实战选择偏离了更稳健的候选方向。；"
        "影响：这次选择明显削弱了行棋方的局面。；"
        "改进：优先比较推荐着所代表的方向。；"
        "学习点：关键处先列出候选着再判断方向。"
    )
    assert "0.64" not in first and "504" not in first


def test_embedding_failure_does_not_lose_durable_evidence():
    event = make_event()
    evidence = make_evidence(event)

    def fail(_texts):
        raise TimeoutError("offline")

    record = memory_store.save_error_memory(
        "embedding-failure-game",
        19,
        "B",
        event,
        evidence,
        succeeded(evidence),
        embedding_fn=fail,
        embedding_model="fake-embedding",
    )

    assert record["embedding_status"] == "failed"
    assert record["embedding_error_code"] == "TimeoutError"
    assert record["explanation_status"] == "succeeded"
    assert record["deep_black_score_lead_before"] == pytest.approx(4.5)


def test_similarity_order_exclusions_ownership_and_feedback():
    current = save("current", vector=[1.0, 0.0])
    close = save("close", vector=[.9, .1])
    far = save("far", vector=[0.0, 1.0])
    foreign = save(
        "foreign",
        vector=[1.0, 0.0],
        belongs_to_user=False,
    )
    same_game_event = make_event(start=20, end=20, worst=20)
    same_game_evidence = make_evidence(
        same_game_event,
        event_start_move=20,
        event_end_move=20,
        worst_move_number=20,
        actual_move="D6",
    )
    same_game = save(
        "current",
        event=same_game_event,
        evidence=same_game_evidence,
        result=succeeded(same_game_evidence),
        vector=[.95, .05],
    )

    matches = memory_store.search_similar_errors(
        current_event_id=current["id"],
        exclude_game_id=current["game_id"],
        top_k=10,
    )

    assert [item["event_id"] for item in matches] == [close["id"], far["id"]]
    assert matches[0]["similarity_score"] > matches[1]["similarity_score"]
    assert current["id"] not in {item["event_id"] for item in matches}
    assert same_game["id"] not in {item["event_id"] for item in matches}
    assert foreign["id"] not in {item["event_id"] for item in matches}

    memory_store.set_similarity_feedback(
        current["id"], close["id"], "not_same_error"
    )
    suppressed = memory_store.search_similar_errors(
        current_event_id=current["id"],
        exclude_game_id=current["game_id"],
        top_k=10,
    )
    assert [item["event_id"] for item in suppressed] == [far["id"]]

    reverse = memory_store.search_similar_errors(
        current_event_id=close["id"],
        exclude_game_id=close["game_id"],
        top_k=10,
    )
    assert current["id"] not in {item["event_id"] for item in reverse}

    recent = memory_store.list_personal_embedded_events(limit=10)
    recent_ids = {item["event_id"] for item in recent}
    assert current["id"] in recent_ids and close["id"] in recent_ids
    assert foreign["id"] not in recent_ids

    memory_store.set_error_importance(far["id"], True)
    assert memory_store.load_error_memory(far["id"])["is_important"] is True
