import chess

from typing import Callable, Optional


def perft(board: chess.Board, depth: int) -> int:
    """Return number of leaf nodes from this position at given depth.

    Definition used:
    - depth == 0 -> 1 (the current position)
    - depth == 1 -> number of legal moves
    """
    if depth < 0:
        raise ValueError('depth must be >= 0')
    if depth == 0:
        return 1

    nodes = 0
    for move in board.legal_moves:
        board.push(move)
        nodes += perft(board, depth - 1)
        board.pop()
    return nodes


def perft_with_progress(
    board: chess.Board,
    depth: int,
    progress_cb: Optional[Callable[[int, int, int], None]] = None,
    stop_event=None,
) -> int:
    """Perft with progress updates.

    Progress callback signature: (done_root_moves, total_root_moves, nodes_so_far)
    Updates are emitted once per root move so the UI can stay responsive.
    """
    if depth < 0:
        raise ValueError('depth must be >= 0')

    if depth == 0:
        if progress_cb is not None:
            progress_cb(1, 1, 1)
        return 1

    root_moves = list(board.legal_moves)
    total = len(root_moves)
    if depth == 1:
        if progress_cb is not None:
            progress_cb(total, total, total)
        return total

    nodes = 0
    done = 0
    for mv in root_moves:
        if stop_event is not None:
            try:
                if stop_event.is_set():
                    raise RuntimeError('Perft cancelled')
            except Exception:
                # If stop_event doesn't support is_set, ignore.
                pass

        board.push(mv)
        nodes += perft(board, depth - 1)
        board.pop()

        done += 1
        if progress_cb is not None:
            progress_cb(done, total, nodes)

    return nodes


def perft_nodes_from_fen(fen: str, depth: int) -> int:
    """Convenience wrapper: parse FEN and run perft."""
    if not isinstance(fen, str) or not fen.strip():
        raise ValueError('fen must be a non-empty string')
    board = chess.Board(fen.strip())
    return perft(board, int(depth))


def perft_nodes_from_fen_with_progress(
    fen: str,
    depth: int,
    progress_cb: Optional[Callable[[int, int, int], None]] = None,
    stop_event=None,
) -> int:
    if not isinstance(fen, str) or not fen.strip():
        raise ValueError('fen must be a non-empty string')
    board = chess.Board(fen.strip())
    return perft_with_progress(board, int(depth), progress_cb=progress_cb, stop_event=stop_event)
