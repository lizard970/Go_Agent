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
