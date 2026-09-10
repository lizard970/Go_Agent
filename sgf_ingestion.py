"""SGF mainline ingestion. UI coordinates have their origin at top left."""
from dataclasses import dataclass
from sgfmill import sgf, boards, sgf_grammar
from board_state import grid_to_stones

COLORS = {'b': 'black', 'w': 'white'}

@dataclass(frozen=True)
class Move:
    number: int
    color: str
    point: tuple | None  # x, y; None means pass

@dataclass
class Position:
    board_data: dict
    next_player: str
    move_number: int = 0
    initial_stones: list | None = None
    moves: list | None = None
    initial_player: str = 'black'
    rules: str = 'japanese'
    komi: float = 6.5

@dataclass
class SgfGame:
    size: int
    moves: list
    setup: tuple
    initial_player: str
    metadata: dict
    rules: str
    komi: float

    def position(self, move_number=None):
        number = len(self.moves) if move_number is None else move_number
        if type(number) is not int or not 0 <= number <= len(self.moves):
            raise ValueError('Move number out of range')
        board = boards.Board(self.size)
        if not board.apply_setup(*self.setup):
            raise ValueError('Invalid initial setup')
        initial = _board_data(board)['stones']
        next_player = self.initial_player
        for move in self.moves[:number]:
            if move.point is not None:
                x, y = move.point
                try:
                    board.play(self.size - 1 - y, x, move.color[0])
                except (ValueError, IndexError) as exc:
                    raise ValueError(f'Invalid move {move.number}') from exc
            next_player = 'white' if move.color == 'black' else 'black'
        return Position(_board_data(board), next_player, number, initial,
                        self.moves[:number], self.initial_player, self.rules, self.komi)

def _board_data(board):
    size = board.side
    grid = [[COLORS.get(board.get(size - 1 - y, x), 'empty')
             for x in range(size)] for y in range(size)]
    return {'board_size': size, 'stones': grid_to_stones(grid, size)}

def parse_sgf(data: bytes) -> SgfGame:
    try:
        collection = sgf_grammar.parse_sgf_collection(data)
        if len(collection) != 1:
            raise ValueError('Upload exactly one game')
        game = sgf.Sgf_game.from_coarse_game_tree(collection[0])
        root = game.get_root()
        if root.has_property('GM') and root.get('GM') != 1:
            raise ValueError('Only Go SGF is supported')
        size = game.get_size()
        if not 2 <= size <= 19:
            raise ValueError('Supported board sizes: 2 through 19')
        setup = root.get_setup_stones()
        if any(setup[i] & setup[j] for i, j in [(0,1),(0,2),(1,2)]):
            raise ValueError('Conflicting setup stones')
        player = COLORS[root.get('PL')] if root.has_property('PL') else (
            'white' if root.has_property('HA') and root.get('HA') >= 2 else 'black')
        metadata = {key: root.get(key) for key in ('PB','PW','BR','WR','DT','EV','GN','RE','RU','KM','HA')
                    if root.has_property(key)}
        moves = []
        for index, node in enumerate(game.get_main_sequence()):
            if index and (node.has_setup_stones() or node.has_property('PL')):
                raise ValueError('Midgame setup/player edits are not supported')
            if node.has_property('B') and node.has_property('W'):
                raise ValueError('A node cannot contain both B and W moves')
            color, point = node.get_move()
            if color:
                moves.append(Move(len(moves)+1, COLORS[color],
                                  None if point is None else (point[1], size-1-point[0])))
        result = SgfGame(size, moves, setup, player, metadata,
                         str(metadata.get('RU', 'japanese')).lower(), float(metadata.get('KM', 6.5)))
        result.position()  # Reject broken reconstruction before offering analysis.
        return result
    except (ValueError, IndexError, KeyError, TypeError) as exc:
        raise ValueError(f'Invalid or unsupported SGF: {exc}') from exc
