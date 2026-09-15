"""Shared position types and existing screenshot/grid conversions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Move:
    number: int
    color: str
    point: tuple[int, int] | None  # x, y; None means pass

@dataclass
class Position:
    board_data: dict
    next_player: str
    move_number: int = 0
    initial_stones: list | None = None
    moves: list[Move] | None = None
    initial_player: str = 'black'
    rules: str = 'japanese'
    komi: float = 6.5


def point_to_gtp(point, board_size):
    if point is None:
        return 'pass'
    x, y = point
    if (type(x) is not int or type(y) is not int
            or not 2 <= board_size <= 19 or not (0 <= x < board_size and 0 <= y < board_size)):
        raise ValueError('Stone coordinate outside board')
    return 'ABCDEFGHJKLMNOPQRST'[x] + str(board_size - y)

def stones_to_grid(board_size, stones):
    grid = [["empty" for _ in range(board_size)] for _ in range(board_size)]
    for stone in stones:
        x, y, color = stone["x"], stone["y"], stone["color"]
        if 0 <= x < board_size and 0 <= y < board_size:
            grid[y][x] = color
    return grid


def grid_to_stones(grid, board_size):
    return [
        {"x": x, "y": y, "color": grid[y][x]}
        for y in range(board_size)
        for x in range(board_size)
        if grid[y][x] != "empty"
    ]
