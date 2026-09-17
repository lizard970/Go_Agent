"""Ranking layer for selecting the most important detected Go errors."""

from dataclasses import dataclass

from error_detection import ErrorEvent
from game_scan import ColorCode, MoveAnalysis, PhaseCode


@dataclass(frozen=True)
class RankedErrorEvent:
    rank: int
    event: ErrorEvent

    # Phase of the event's worst individual move.
    phase: PhaseCode

    # Convenience fields for UI / later persistence.
    start_move: int
    end_move: int
    worst_move_number: int
    severity_score: float


def _move_lookup(
    moves: list[MoveAnalysis],
) -> dict[int, MoveAnalysis]:
    return {
        move.move_number: move
        for move in moves
    }


def rank_error_events(
    events: list[ErrorEvent],
    moves: list[MoveAnalysis],
    *,
    user_color: ColorCode,
    limit: int = 5,
) -> list[RankedErrorEvent]:
    """
    Select and rank the most important ErrorEvents.

    V1 ranking uses the detector's provisional severity_score.

    Phase is attached as context only. It does NOT yet change ranking
    weight because phase-specific weighting still needs calibration
    from real games.
    """

    if type(limit) is not int or limit <= 0:
        raise ValueError(
            "limit must be a positive integer"
        )

    if user_color not in {"B", "W"}:
        raise ValueError(
            "user_color must be 'B' or 'W'"
        )

    user_events = [
        event
        for event in events
        if event.player == user_color
    ]

    if not user_events:
        return []

    moves_by_number = _move_lookup(
        moves
    )

    # Highest severity first.
    #
    # Tie-breakers:
    # 1. larger peak winrate loss
    # 2. larger peak score loss
    # 3. earlier event
    #
    # This gives deterministic output instead of arbitrary ordering.
    ordered = sorted(
        user_events,
        key=lambda event: (
            -event.severity_score,
            -event.peak_winrate_loss,
            -event.peak_score_loss,
            event.start_move,
        ),
    )

    selected = ordered[:limit]

    ranked: list[RankedErrorEvent] = []

    for index, event in enumerate(
        selected,
        start=1,
    ):
        worst_move = moves_by_number.get(
            event.worst_move_number
        )

        if worst_move is None:
            raise ValueError(
                "ErrorEvent references missing "
                f"move {event.worst_move_number}"
            )

        ranked.append(
            RankedErrorEvent(
                rank=index,
                event=event,
                phase=worst_move.phase,
                start_move=event.start_move,
                end_move=event.end_move,
                worst_move_number=event.worst_move_number,
                severity_score=event.severity_score,
            )
        )

    return ranked
