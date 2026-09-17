"""Full-game analysis built from normalized per-position KataGo evidence."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import os

from dotenv import dotenv_values

from board_state import point_to_gtp
from katago_adapter import AnalysisResult, KataGoAdapter
from sgf_ingestion import SgfGame


ColorCode = Literal["B", "W"]
PhaseCode = Literal["opening", "middlegame", "endgame"]

TerminationCode = Literal[
    "scored",
    "resigned",
    "other_result",
    "incomplete",
]

DEFAULT_SCAN_VISITS = 100


class GameScanError(RuntimeError):
    """A position could not produce valid normalized analysis evidence."""


@dataclass(frozen=True)
class MoveAnalysis:
    move_number: int
    player: ColorCode
    actual_move: str
    best_move_before: str

    black_winrate_before: float
    black_score_lead_before: float
    black_winrate_after: float
    black_score_lead_after: float

    player_winrate_loss: float
    player_score_loss: float

    visits: int

    # Board-state features retained for later phase inference,
    # visualization and calibration.
    stones_on_board: int
    occupancy_ratio: float
    phase: PhaseCode

    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GameScanResult:
    position_analyses: list[AnalysisResult]
    moves: list[MoveAnalysis]
    termination: TerminationCode


def scan_visits_from_env(env_file=None) -> int:
    values = dotenv_values(
        env_file or Path(__file__).with_name(".env")
    )

    raw = os.environ.get(
        "KATAGO_SCAN_VISITS",
        values.get("KATAGO_SCAN_VISITS") or DEFAULT_SCAN_VISITS,
    )

    try:
        visits = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "KATAGO_SCAN_VISITS must be a positive integer"
        ) from exc

    if visits <= 0 or (
        isinstance(raw, float)
        and not raw.is_integer()
    ):
        raise ValueError(
            "KATAGO_SCAN_VISITS must be a positive integer"
        )

    return visits


def metrics_for_color(
    black_winrate: float,
    black_score_lead: float,
    color: ColorCode,
) -> tuple[float, float]:
    if color == "B":
        return black_winrate, black_score_lead

    if color == "W":
        return 1.0 - black_winrate, -black_score_lead

    raise ValueError("color must be 'B' or 'W'")


def infer_phase(
    move_number: int,
    occupancy_ratio: float,
) -> PhaseCode:
    """
    Provisional phase heuristic.

    V1 uses both move number and actual board occupancy.
    Thresholds are temporary and should be calibrated later
    using real game data.

    A game is NOT required to contain all three phases.
    """

    if move_number <= 40 and occupancy_ratio < 0.18:
        return "opening"

    if move_number >= 120 and occupancy_ratio >= 0.35:
        return "endgame"

    return "middlegame"


def infer_termination(
    game: SgfGame,
) -> TerminationCode:
    """
    Infer how the game ended from the SGF RE property.

    This is separate from phase detection.

    Example:
        move 90 -> middlegame
        RE[W+R] -> resigned

    Therefore a game can end in the middlegame and never
    contain an endgame phase.
    """

    raw_result = game.metadata.get("RE")

    if raw_result is None:
        return "incomplete"

    result = str(raw_result).strip()

    if not result or result in {"?", "*"}:
        return "incomplete"

    normalized = (
        result
        .lower()
        .replace(" ", "")
    )

    # Common resignation forms:
    # B+R
    # W+R
    # B+Resign
    # W+Resign
    if (
        normalized.endswith("+r")
        or normalized.endswith("+resign")
        or normalized.endswith("+resignation")
    ):
        return "resigned"

    # Normal numeric score:
    # B+3.5
    # W+12
    if "+" in result:
        _, suffix = result.split("+", 1)

        try:
            float(suffix)
            return "scored"
        except ValueError:
            pass

    # Draw / jigo.
    if normalized in {
        "0",
        "draw",
        "jigo",
    }:
        return "scored"

    # RE exists but represents another kind of ending,
    # such as timeout, forfeit, void, etc.
    return "other_result"


def _require_ok(
    result: AnalysisResult,
    position_number: int,
):
    if result.status != "ok":
        raise GameScanError(
            f"Position {position_number} analysis failed: "
            f"{result.error or result.status}"
        )

    if (
        result.winrate is None
        or result.score_lead is None
        or result.best_move is None
        or result.visits is None
        or result.move_number != position_number
    ):
        raise GameScanError(
            f"Position {position_number} returned "
            f"incomplete analysis evidence"
        )


def _warnings(
    before: AnalysisResult,
    after: AnalysisResult,
) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *before.warnings,
                *after.warnings,
            ]
        )
    )


def _board_features(
    game: SgfGame,
    move_number: int,
) -> tuple[int, float]:
    """
    Read the actual reconstructed board after move_number.

    board_data["stones"] reflects the real current board,
    so captures are naturally accounted for instead of
    assuming stones_on_board == number of moves played.
    """

    position = game.position(move_number)

    stones = position.board_data.get(
        "stones",
        [],
    )

    stones_on_board = len(stones)

    board_points = (
        game.size
        * game.size
    )

    occupancy_ratio = (
        stones_on_board
        / board_points
    )

    return (
        stones_on_board,
        occupancy_ratio,
    )


def scan_game(
    game: SgfGame,
    adapter: KataGoAdapter,
    *,
    max_visits: int = DEFAULT_SCAN_VISITS,
    progress_callback=None,
) -> GameScanResult:
    if type(max_visits) is not int or max_visits <= 0:
        raise ValueError(
            "max_visits must be a positive integer"
        )

    analyses: list[AnalysisResult] = []

    # N moves -> N + 1 analyzed positions:
    # initial position + position after every move.
    for position_number in range(
        len(game.moves) + 1
    ):
        if progress_callback is not None:
            progress_callback(
                f"KataGo 正在分析第 {position_number + 1} / "
                f"{len(game.moves) + 1} 个局面…"
            )
        result = adapter.analyze(
            game.position(
                position_number
            ),
            max_visits=max_visits,
        )

        _require_ok(
            result,
            position_number,
        )

        analyses.append(result)

    move_analyses: list[MoveAnalysis] = []

    for move in game.moves:
        before = analyses[
            move.number - 1
        ]

        after = analyses[
            move.number
        ]

        player: ColorCode = (
            "B"
            if move.color == "black"
            else "W"
        )

        player_before = metrics_for_color(
            before.winrate,
            before.score_lead,
            player,
        )

        player_after = metrics_for_color(
            after.winrate,
            after.score_lead,
            player,
        )

        (
            stones_on_board,
            occupancy_ratio,
        ) = _board_features(
            game,
            move.number,
        )

        move_analyses.append(
            MoveAnalysis(
                move_number=move.number,
                player=player,

                actual_move=point_to_gtp(
                    move.point,
                    game.size,
                ),

                best_move_before=before.best_move,

                black_winrate_before=before.winrate,
                black_score_lead_before=before.score_lead,

                black_winrate_after=after.winrate,
                black_score_lead_after=after.score_lead,

                player_winrate_loss=(
                    player_before[0]
                    - player_after[0]
                ),

                player_score_loss=(
                    player_before[1]
                    - player_after[1]
                ),

                visits=before.visits,

                stones_on_board=stones_on_board,
                occupancy_ratio=occupancy_ratio,

                phase=infer_phase(
                    move.number,
                    occupancy_ratio,
                ),

                warnings=_warnings(
                    before,
                    after,
                ),
            )
        )

    return GameScanResult(
        position_analyses=analyses,
        moves=move_analyses,
        termination=infer_termination(
            game
        ),
    )
