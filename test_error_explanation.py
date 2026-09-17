import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deep_analysis import DeepAnalysisEvidence
from error_explanation import (
    EVIDENCE_SCHEMA_VERSION,
    ErrorExplanationService,
    build_llm_evidence,
)


def make_deep(player="B", **overrides):
    values = {
        "rank": 1,
        "player": player,
        "phase": "middlegame",
        "event_start_move": 42,
        "event_end_move": 46,
        "worst_move_number": 44,
        "actual_move": "C7",
        "deep_best_move_before": "Q10",
        "deep_black_winrate_before": .64,
        "deep_black_score_lead_before": 4.5,
        "deep_black_winrate_after": .51,
        "deep_black_score_lead_after": 1.0,
        "player_winrate_loss": .13,
        "player_score_loss": 3.5,
        "visits": 504,
        "warnings": ["analysis warning"],
    }
    values.update(overrides)
    return DeepAnalysisEvidence(**values)


def fake_client(content=None, error=None):
    create = Mock()
    if error is not None:
        create.side_effect = error
    else:
        create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=content),
            )],
        )
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        ),
    )


VALID_EXPLANATION = {
    "title": "中盘方向偏差",
    "summary": "实战着与深度分析推荐着不同，局面评价明显下降。",
    "why_it_matters": "这次选择让行棋方失去了原有优势。",
    "better_plan": "优先围绕深度分析推荐着复盘，并比较两种选择的后续目标。",
    "learning_point": "关键局面先比较候选方向，再决定落点。",
}


@pytest.mark.parametrize(
    "source",
    [
        make_deep("B"),
        make_deep(
            "W",
            deep_black_winrate_before=.35,
            deep_black_winrate_after=.49,
            deep_black_score_lead_before=-3.0,
            deep_black_score_lead_after=.5,
            player_winrate_loss=.14,
            player_score_loss=3.5,
        ),
    ],
)
def test_deep_evidence_mapping_preserves_black_and_white_values(source):
    evidence = build_llm_evidence(source)

    assert evidence.schema_version == EVIDENCE_SCHEMA_VERSION
    assert evidence.player == source.player
    for field in (
        "deep_black_winrate_before",
        "deep_black_winrate_after",
        "deep_black_score_lead_before",
        "deep_black_score_lead_after",
        "player_winrate_loss",
        "player_score_loss",
    ):
        assert getattr(evidence, field) == getattr(source, field)
    assert evidence.warnings == source.warnings
    assert evidence.warnings is not source.warnings


def test_evidence_schema_is_versioned_and_json_serializable():
    serialized = json.loads(json.dumps(build_llm_evidence(make_deep()).to_dict()))

    assert serialized["schema_version"] == "go-error-evidence.v1"
    assert serialized["event_start_move"] == 42
    assert serialized["visits"] == 504


def test_valid_structured_response_succeeds_and_uses_only_evidence_boundary():
    evidence = build_llm_evidence(make_deep())
    client = fake_client(json.dumps(VALID_EXPLANATION, ensure_ascii=False))

    result = ErrorExplanationService(client=client, model="test-model").explain(evidence)

    assert result.status == "succeeded"
    assert result.error_code is None
    assert result.evidence is evidence
    assert result.explanation.to_dict() == VALID_EXPLANATION
    call = client.chat.completions.create.call_args
    assert call.kwargs["model"] == "test-model"
    assert call.kwargs["response_format"]["type"] == "json_schema"
    prompt = call.kwargs["messages"][0]["content"]
    assert EVIDENCE_SCHEMA_VERSION in prompt
    assert "rootInfo" not in prompt and "moveInfos" not in prompt


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"title": "missing other fields"}),
        json.dumps({**VALID_EXPLANATION, "extra": "not allowed"}),
        json.dumps({**VALID_EXPLANATION, "summary": "  "}),
    ],
)
def test_malformed_or_invalid_response_is_pending(content):
    evidence = build_llm_evidence(make_deep())

    result = ErrorExplanationService(
        client=fake_client(content),
        model="test-model",
    ).explain(evidence)

    assert result.status == "pending"
    assert result.error_code == "invalid_response"
    assert result.explanation is None
    assert result.evidence is evidence


@pytest.mark.parametrize("error", [TimeoutError("timeout"), OSError("network")])
def test_timeout_or_provider_failure_is_pending(error):
    evidence = build_llm_evidence(make_deep())

    result = ErrorExplanationService(
        client=fake_client(error=error),
        model="test-model",
    ).explain(evidence)

    assert result.status == "pending"
    assert result.error_code == "provider_error"
    assert result.explanation is None
    assert result.evidence.to_dict() == evidence.to_dict()


def test_missing_api_configuration_is_pending(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    factory = Mock()
    evidence = build_llm_evidence(make_deep())

    result = ErrorExplanationService(
        client_factory=factory,
        model="test-model",
    ).explain(evidence)

    assert result.status == "pending"
    assert result.error_code == "configuration_error"
    assert result.evidence is evidence
    factory.assert_not_called()


def test_missing_model_configuration_is_pending():
    client = fake_client(json.dumps(VALID_EXPLANATION))
    evidence = build_llm_evidence(make_deep())

    result = ErrorExplanationService(client=client, model="").explain(evidence)

    assert result.status == "pending"
    assert result.error_code == "configuration_error"
    assert result.evidence is evidence
    client.chat.completions.create.assert_not_called()


def test_batch_explanation_uses_one_call_and_preserves_one_result_per_evidence():
    evidence = [
        build_llm_evidence(make_deep("B")),
        build_llm_evidence(make_deep("W", rank=2, worst_move_number=45)),
    ]
    content = json.dumps({
        "explanations": [VALID_EXPLANATION, {
            **VALID_EXPLANATION,
            "title": "白棋方向偏差",
        }],
    }, ensure_ascii=False)
    client = fake_client(content)

    results = ErrorExplanationService(
        client=client, model="test-model"
    ).explain_many(evidence)

    assert client.chat.completions.create.call_count == 1
    assert [result.status for result in results] == ["succeeded", "succeeded"]
    assert [result.evidence for result in results] == evidence
    assert results[1].explanation.title == "白棋方向偏差"


def test_invalid_batch_response_keeps_all_evidence_pending():
    evidence = [
        build_llm_evidence(make_deep("B")),
        build_llm_evidence(make_deep("W", rank=2)),
    ]
    client = fake_client(json.dumps({"explanations": [VALID_EXPLANATION]}))

    results = ErrorExplanationService(
        client=client, model="test-model"
    ).explain_many(evidence)

    assert [result.status for result in results] == ["pending", "pending"]
    assert {result.error_code for result in results} == {"invalid_response"}
    assert [result.evidence for result in results] == evidence


def test_batch_provider_failure_is_one_call_and_keeps_evidence_pending():
    evidence = [build_llm_evidence(make_deep("B")), build_llm_evidence(make_deep("W"))]
    client = fake_client(error=TimeoutError("timeout"))

    results = ErrorExplanationService(
        client=client, model="test-model"
    ).explain_many(evidence)

    assert client.chat.completions.create.call_count == 1
    assert [result.status for result in results] == ["pending", "pending"]
    assert {result.error_code for result in results} == {"provider_error"}
