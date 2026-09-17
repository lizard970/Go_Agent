"""Detect higher-level error events from per-move KataGo analysis."""

from dataclasses import dataclass

from game_scan import MoveAnalysis


@dataclass(frozen=True)
class ErrorDetectionConfig:
    # Temporary heuristic thresholds.
    # These are NOT final Go standards and will be calibrated later.
    min_winrate_loss: float = 0.05
    min_score_loss: float = 2.0

    # Number of non-candidate moves by the SAME player that may appear
    # between two candidate mistakes while still treating them as one event.
    max_intervening_own_moves: int = 1

    # Recovery break thresholds.
    recovery_winrate_break: float = 0.10
    recovery_score_break: float = 3.0

def _player_winrate_after(move: MoveAnalysis) -> float:
    if move.player == "B":
        return move.black_winrate_after
    return 1.0 - move.black_winrate_after


def _player_winrate_before(move: MoveAnalysis) -> float:
    if move.player == "B":
        return move.black_winrate_before
    return 1.0 - move.black_winrate_before

@dataclass(frozen=True)
class ErrorEvent:
    start_move: int
    end_move: int
    player: str

    # Candidate mistake moves contained in this event.
    move_numbers: list[int]

    # Sum of losses on candidate moves.
    sum_winrate_loss: float
    sum_score_loss: float

    # Actual deterioration from beginning of the event to the end.
    net_winrate_loss: float
    net_score_loss: float

    # Largest single-move losses inside the event.
    peak_winrate_loss: float
    peak_score_loss: float

    worst_move_number: int

    # Provisional ranking value only.
    # Do not treat this as a calibrated Go-strength metric.
    severity_score: float


def is_error_candidate(
    move: MoveAnalysis,
    config: ErrorDetectionConfig,
) -> bool:
    """Return True when a move crosses either provisional loss threshold."""
    return (
        move.player_winrate_loss >= config.min_winrate_loss
        or move.player_score_loss >= config.min_score_loss
    )


def _player_metrics_before(move: MoveAnalysis) -> tuple[float, float]:
    if move.player == "B":
        return move.black_winrate_before, move.black_score_lead_before
    return 1.0 - move.black_winrate_before, -move.black_score_lead_before


def _player_metrics_after(move: MoveAnalysis) -> tuple[float, float]:
    if move.player == "B":
        return move.black_winrate_after, move.black_score_lead_after
    return 1.0 - move.black_winrate_after, -move.black_score_lead_after


def _same_event(
    previous: MoveAnalysis,
    current: MoveAnalysis,
    config: ErrorDetectionConfig,
    moves_between: list[MoveAnalysis],
) -> bool:
    if previous.player != current.player:
        return False

    move_gap = current.move_number - previous.move_number

    if move_gap <= 0:
        return False

    intervening_own_moves = max(0, move_gap // 2 - 1)

    if intervening_own_moves > config.max_intervening_own_moves:
        return False

    previous_after_winrate, previous_after_score = _player_metrics_after(
        previous
    )

    best_recovered_winrate = previous_after_winrate
    best_recovered_score = previous_after_score

    for move in moves_between:
        if move.player != previous.player:
            continue

        before_winrate, before_score = _player_metrics_before(move)
        after_winrate, after_score = _player_metrics_after(move)

        best_recovered_winrate = max(
            best_recovered_winrate,
            before_winrate,
            after_winrate,
        )

        best_recovered_score = max(
            best_recovered_score,
            before_score,
            after_score,
        )

    winrate_recovery = (
        best_recovered_winrate
        - previous_after_winrate
    )

    score_recovery = (
        best_recovered_score
        - previous_after_score
    )

    if winrate_recovery >= config.recovery_winrate_break:
        return False

    if score_recovery >= config.recovery_score_break:
        return False

    return True


def _build_event(
    candidates: list[MoveAnalysis],
    config: ErrorDetectionConfig,
) -> ErrorEvent:
    first = candidates[0]
    last = candidates[-1]

    before_winrate, before_score = _player_metrics_before(first)
    after_winrate, after_score = _player_metrics_after(last)

    peak_winrate_move = max(
        candidates,
        key=lambda move: move.player_winrate_loss,
    )
    peak_score_move = max(
        candidates,
        key=lambda move: move.player_score_loss,
    )

    # Temporary severity score:
    # express each loss relative to the current candidate thresholds.
    # This exists only for ordering events in V1.
    severity_by_winrate = max(
        move.player_winrate_loss / config.min_winrate_loss
        for move in candidates
    )
    severity_by_score = max(
        move.player_score_loss / config.min_score_loss
        for move in candidates
    )
    severity_score = max(severity_by_winrate, severity_by_score)

    # Pick the move whose normalized loss is most severe.
    worst_move = max(
        candidates,
        key=lambda move: max(
            move.player_winrate_loss / config.min_winrate_loss,
            move.player_score_loss / config.min_score_loss,
        ),
    )

    return ErrorEvent(
        start_move=first.move_number,
        end_move=last.move_number,
        player=first.player,
        move_numbers=[move.move_number for move in candidates],
        sum_winrate_loss=sum(
            max(0.0, move.player_winrate_loss) for move in candidates
        ),
        sum_score_loss=sum(
            max(0.0, move.player_score_loss) for move in candidates
        ),
        net_winrate_loss=max(0.0, before_winrate - after_winrate),
        net_score_loss=max(0.0, before_score - after_score),
        peak_winrate_loss=max(
            0.0, peak_winrate_move.player_winrate_loss
        ),
        peak_score_loss=max(
            0.0, peak_score_move.player_score_loss
        ),
        worst_move_number=worst_move.move_number,
        severity_score=severity_score,
    )


def detect_error_events(
    moves: list[MoveAnalysis],
    config: ErrorDetectionConfig | None = None,
) -> list[ErrorEvent]:
    """Convert per-move losses into provisional continuous error events."""
    config = config or ErrorDetectionConfig()

    if config.min_winrate_loss <= 0:
        raise ValueError("min_winrate_loss must be positive")
    if config.recovery_score_break <= 0:
        raise ValueError("recovery_score_break must be positive")
    if config.min_score_loss <= 0:
        raise ValueError("min_score_loss must be positive")
    if config.max_intervening_own_moves < 0:
        raise ValueError("max_intervening_own_moves must be >= 0")
    if config.recovery_winrate_break <= 0:
        raise ValueError("recovery_winrate_break must be positive")

    candidates = [
        move for move in moves
        if is_error_candidate(move, config)
    ]

    if not candidates:
        return []

    groups: list[list[MoveAnalysis]] = []
    current_group = [candidates[0]]

    for candidate in candidates[1:]:
        previous = current_group[-1]

        moves_between = [
            move
            for move in moves
            if previous.move_number < move.move_number < candidate.move_number
        ]

        if _same_event(
            previous,
            candidate,
            config,
            moves_between,
        ):
            current_group.append(candidate)
        else:
            groups.append(current_group)
            current_group = [candidate]

    groups.append(current_group)

    return [
        _build_event(group, config)
        for group in groups
    ]