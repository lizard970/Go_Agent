"""Full-game analysis built from normalized per-position KataGo evidence."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import os

from dotenv import dotenv_values

from board_state import point_to_gtp
from katago_adapter import AnalysisResult, KataGoAdapter
from sgf_ingestion import SgfGame


ColorCode = Literal['B', 'W']
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
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GameScanResult:
    position_analyses: list[AnalysisResult]
    moves: list[MoveAnalysis]


def scan_visits_from_env(env_file=None) -> int:
    values = dotenv_values(env_file or Path(__file__).with_name('.env'))
    raw = os.environ.get('KATAGO_SCAN_VISITS', values.get('KATAGO_SCAN_VISITS') or DEFAULT_SCAN_VISITS)
    try:
        visits = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('KATAGO_SCAN_VISITS must be a positive integer') from exc
    if visits <= 0 or isinstance(raw, float) and not raw.is_integer():
        raise ValueError('KATAGO_SCAN_VISITS must be a positive integer')
    return visits


def metrics_for_color(black_winrate: float, black_score_lead: float,
                      color: ColorCode) -> tuple[float, float]:
    if color == 'B':
        return black_winrate, black_score_lead
    if color == 'W':
        return 1.0 - black_winrate, -black_score_lead
    raise ValueError("color must be 'B' or 'W'")


def _require_ok(result: AnalysisResult, position_number: int):
    if result.status != 'ok':
        raise GameScanError(f'Position {position_number} analysis failed: {result.error or result.status}')
    if (result.winrate is None or result.score_lead is None or result.best_move is None
            or result.visits is None or result.move_number != position_number):
        raise GameScanError(f'Position {position_number} returned incomplete analysis evidence')


def _warnings(before: AnalysisResult, after: AnalysisResult):
    return list(dict.fromkeys([*before.warnings, *after.warnings]))


def scan_game(game: SgfGame, adapter: KataGoAdapter, *,
              max_visits: int = DEFAULT_SCAN_VISITS) -> GameScanResult:
    if type(max_visits) is not int or max_visits <= 0:
        raise ValueError('max_visits must be a positive integer')
    analyses = []
    for position_number in range(len(game.moves) + 1):
        result = adapter.analyze(game.position(position_number), max_visits=max_visits)
        _require_ok(result, position_number)
        analyses.append(result)

    move_analyses = []
    for move in game.moves:
        before = analyses[move.number - 1]
        after = analyses[move.number]
        player = 'B' if move.color == 'black' else 'W'
        player_before = metrics_for_color(before.winrate, before.score_lead, player)
        player_after = metrics_for_color(after.winrate, after.score_lead, player)
        move_analyses.append(MoveAnalysis(
            move_number=move.number,
            player=player,
            actual_move=point_to_gtp(move.point, game.size),
            best_move_before=before.best_move,
            black_winrate_before=before.winrate,
            black_score_lead_before=before.score_lead,
            black_winrate_after=after.winrate,
            black_score_lead_after=after.score_lead,
            player_winrate_loss=player_before[0] - player_after[0],
            player_score_loss=player_before[1] - player_after[1],
            visits=before.visits,
            warnings=_warnings(before, after),
        ))
    return GameScanResult(analyses, move_analyses)
