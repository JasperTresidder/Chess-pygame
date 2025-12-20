import datetime
import math
import io
import os
import re
import random
import sys
import threading
import time
import queue
import contextlib

from src.engine.settings import SettingsMenu, EndGameMenu
from src.functions.fen import *
import pygame as pg
from src.functions.timer import *
from src.pieces.queen import Queen
from src.pieces.base import Piece
from stockfish import Stockfish
import chess
import chess.engine
import chess.pgn
import pygame_menu as pm
import platform

# "8/8/8/2k5/2pP4/8/B7/4K3 b - d3 0 3" - can en passant out of check!
# "rnb2k1r/pp1Pbppp/2p5/q7/2B5/8/PPPQNnPP/RNB1K2R w KQ - 3 9" - 39 moves can promote to other pieces
# rnbq1bnr/ppp1p1pp/3p4/6P1/1k1PPp1P/1PP2P1B/PB6/RN1QK2R b KQkq - 0 13 - king cant go to a4 here


EVAL_ON = False


def print_eval(evaluation):
    if evaluation["type"] == "cp":
        return 'Evaluation = ' + str(round(evaluation["value"] / 100, 2))
    else:
        if evaluation["value"] < 0:
            return 'Mate in ' + str(-evaluation["value"])
        else:
            return 'Mate in ' + str(evaluation["value"])


class Engine:
    def __init__(self):
        self.player_vs_ai = None
        self.ai_vs_ai = None
        self.evaluation = ''
        self.best_move = ''
        self.end_popup_active = False
        self.end_popup_text = ''
        self.end_popup_pgn_path: str | None = None

        # Review / replay state
        self.review_active = False
        self.review_pgn_path: str | None = None
        self.review_name: str = ''
        self.review_fens: list[str] = []
        self.review_plies: list[chess.Move] = []
        self.review_sans: list[str] = []
        self.review_move_labels: list[str] = []  # per-ply labels aligned with review_sans
        self.review_index: int = 0  # index into review_fens
        self.review_best_move_uci: str = ''
        self.review_arrow = None  # ((row,col),(row,col))
        self.review_show_best_move: bool = True
        self.review_acpl_white: float | None = None
        self.review_acpl_black: float | None = None
        self.review_accuracy_white: float | None = None
        self.review_accuracy_black: float | None = None
        self.review_accuracy_overall: float | None = None
        self.review_analysis_progress: float | None = None
        self.review_last_error: str = ''
        self.review_move_scroll: int = 0
        self._review_move_hitboxes: list[tuple[pg.Rect, int]] = []
        self._layout_cache_key = None
        self.game_just_ended = False
        self.engine = 'stockfish'
        pg.init()
        pg.display.set_caption('Chess', 'chess')
        pg.font.init()
        self.last_move = []
        self.highlighted = []
        self.arrows = []
        self.hint_arrow = None  # ((row,col),(row,col))
        self.hint_arrow_colour = (0, 120, 255)
        self.flipped = False
        self.flip_enabled = True
        self.sound_enabled = True
        self.movement_click_enabled = True
        self.movement_drag_enabled = True
        self.eval_bar_enabled = True
        self.platform = None
        if 'Windows' in platform.platform():
            self.platform = 'Windows/' + self.engine + '.exe'
        if 'macOS' in platform.platform():
            self.platform = 'macOS/stockfish'
        if 'Linux' in platform.platform():
            self.platform = 'Linux/stockfish'
        print("lit/" + self.engine + "/" + self.platform)
        if self.ai_vs_ai:
            try:
                self.stockfish = Stockfish("lit/" + self.engine + "/" + self.platform,
                                           depth=99,
                                           parameters={"Threads": 6, "Minimum Thinking Time": 100, "Hash": 64,
                                                       "Skill Level": 20,
                                                       "UCI_Elo": 3000})
            except FileNotFoundError:
                print(
                    "Stockfish program located in '" + "lit/" + self.engine + "/" + self.platform + "' is non respondent please install stockfish here: https://stockfishchess.org/download/")
                sys.exit(0)
        else:
            try:
                self.stockfish = Stockfish("lit/" + self.engine + "/" + self.platform,
                                           depth=1,
                                           parameters={"Threads": 1, "Minimum Thinking Time": 1, "Hash": 2,
                                                       "Skill Level": 0.001,
                                                       "UCI_LimitStrength": "true",
                                                       "UCI_Elo": 0})
            except FileNotFoundError:
                print(
                    "Stockfish program located in '" + "lit/" + self.engine + "/" + self.platform + "' is non respondent please install stockfish here: https://stockfishchess.org/download/")
                sys.exit(0)
        self.stockfish.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        # AI strength is controlled via Elo (Stockfish UCI_LimitStrength/UCI_Elo)
        self.ai_strength = 0  # legacy (kept for backward-compat)
        self.ai_elo = 800

        # Apply initial AI limiting parameters (safe even if user changes later in Settings)
        self._apply_ai_strength()

        # Dedicated Stockfish instance for evaluation (keeps AI move-generation responsive)
        try:
            self.stockfish_eval = Stockfish(
                "lit/" + self.engine + "/" + self.platform,
                depth=12,
                parameters={"Threads": 1, "Minimum Thinking Time": 1, "Hash": 16},
            )
            self.stockfish_eval.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        except FileNotFoundError:
            self.stockfish_eval = None

        # Dedicated Stockfish instance for review analysis / best-move arrows
        try:
            self.stockfish_review = Stockfish(
                "lit/" + self.engine + "/" + self.platform,
                depth=12,
                parameters={"Threads": 1, "Minimum Thinking Time": 1, "Hash": 32},
            )
            self.stockfish_review.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        except FileNotFoundError:
            self.stockfish_review = None

        # Separate instance for review analysis so it doesn't fight with best-move arrows.
        try:
            self.stockfish_review_analysis = Stockfish(
                "lit/" + self.engine + "/" + self.platform,
                depth=12,
                parameters={"Threads": 1, "Minimum Thinking Time": 1, "Hash": 64},
            )
            self.stockfish_review_analysis.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        except FileNotFoundError:
            self.stockfish_review_analysis = None

        self._review_queue: queue.Queue[tuple[str, int]] = queue.Queue(maxsize=1)
        self._review_lock = threading.Lock()
        self._review_stop = threading.Event()
        self._review_thread = threading.Thread(target=self._review_worker, daemon=True)
        self._review_thread.start()

        self._eval_queue: queue.Queue[str] = queue.Queue(maxsize=1)
        self._eval_stop = threading.Event()
        self._eval_lock = threading.Lock()
        self._eval_score: float | None = None  # positive = white better
        self._eval_label: str = ''
        self._eval_is_mate: bool = False
        self._eval_depth: int | None = None
        self._eval_raw: dict | None = None
        self._eval_bar_rect: pg.Rect | None = None
        self._eval_depth_steps: list[int] = [6, 10, 14, 18]
        self._eval_thread = threading.Thread(target=self._eval_worker, daemon=True)
        self._eval_thread.start()

        # self.engine_ = chess.engine.SimpleEngine.popen_uci('lit/stockfish/Windows/stockfish.exe')
        self.game = chess.pgn.Game()

        if self.ai_vs_ai:
            self.game.headers["Event"] = "Computer Vs Computer"
            self.game.headers["Black"] = "Computer"
            self.game.headers["White"] = "Computer"
        elif self.player_vs_ai:
            self.game.headers["Event"] = "Player Vs Computer"
            self.game.headers["Black"] = "Computer"
            self.game.headers["White"] = "Player"
        else:
            self.game.headers["Event"] = "Player Vs Player"
            self.game.headers["Black"] = "Player"
            self.game.headers["White"] = "Player"

        self.game.headers["Site"] = "UK"
        self.game.headers["WhiteElo"] = "?"
        self.game.headers["BlackElo"] = "?"
        self.game.headers["Date"] = str(datetime.datetime.now().year) + '/' + str(
            datetime.datetime.now().month) + '/' + str(datetime.datetime.now().day)

        self.piece_type = 'chessmonk'
        self.board_style = 'marble.png'

        self.screen = pg.display.set_mode(
            (pg.display.get_desktop_sizes()[0][1] - 70, pg.display.get_desktop_sizes()[0][1] - 70), pg.RESIZABLE,
            vsync=1)
        self.settings = SettingsMenu(title='Settings', width=self.screen.get_width(), height=self.screen.get_height(),
                                     surface=self.screen, parent=self, theme=pm.themes.THEME_DARK)
        # "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        icon = pg.image.load('data/img/pieces/cardinal/bk.png').convert_alpha()
        pg.display.set_icon(icon)
        self.board, self.turn, self.castle_rights, self.en_passant_square, self.halfmoves_since_last_capture, self.fullmove_number = parse_FEN(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        self.game_fens = ['rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1']
        self.black_pieces = pg.sprite.Group()
        self.white_pieces = pg.sprite.Group()
        self.all_pieces = pg.sprite.Group()

        self.map = []
        for i, row in enumerate(self.board):
            for j, piece in enumerate(row):
                if piece != ' ':
                    self.all_pieces.add(piece)
                    if piece.colour == 'black':
                        self.black_pieces.add(piece)
                    else:
                        self.white_pieces.add(piece)

        self.size = int((pg.display.get_window_size()[1] - 200) / 8)
        self.default_size = int(pg.display.get_window_size()[1] - 200 / 8)
        self.font = pg.font.SysFont('segoescript', 30)
        self.eval_font = pg.font.SysFont('segoescript', 18)
        self.updates = False
        self.arrow_colour = (252, 177, 3)
        self.colours = [(118, 150, 86), (238, 238, 210)]
        # self.colours = [(50, 50, 50), (255, 255, 255)] 
        self.colours2 = [(150, 86, 86), (238, 215, 210)]
        self.colours3 = [(186, 202, 68), (255, 251, 171)]
        self.colours4 = [(252, 111, 76), (252, 137, 109)]
        self.tx = None
        self.ty = None
        self.txr = None
        self.tyr = None
        self.left = False
        self.selected_square: tuple[int, int] | None = None
        self._mouse_down_pos: tuple[int, int] | None = None
        self._drag_threshold_px = 6
        self.background = pg.image.load('data/img/background_dark.png').convert()
        self.background = pg.transform.smoothscale(self.background,
                                                   (pg.display.get_window_size()[0], pg.display.get_window_size()[1]))
        self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
        self.board_background = pg.transform.smoothscale(self.board_background,
                                                         (self.size * 8, self.size * 8))
        self.offset = [pg.display.get_window_size()[0] / 2 - 4 * self.size,
                       pg.display.get_window_size()[1] / 2 - 4 * self.size]
        self.update_legal_moves()
        self.prev_board = self.board
        self.debug = False
        self.node = self.game
        self.show_numbers = True
        self.knight_moves = [(1, 2), (1, -2), (-1, 2), (-1, -2), (2, 1), (2, -1), (-2, 1), (-2, -1)]
        if EVAL_ON:
            self.get_eval()
        self.clock = pg.time.Clock()
        self.settings.confirm()

        # Kick off initial evaluation
        self.request_eval()

    def _save_pgn(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print(self.game, file=open(path, "w"), end="\n\n")

    def _set_pgn_strength_headers(self) -> None:
        # Store bot strength for reference (Elo). Player Elo remains unknown.
        try:
            self.game.headers["AIElo"] = str(int(self.ai_elo))
        except Exception:
            self.game.headers["AIElo"] = "?"

        if self.ai_vs_ai:
            try:
                self.game.headers["WhiteElo"] = str(int(self.ai_elo))
                self.game.headers["BlackElo"] = str(int(self.ai_elo))
            except Exception:
                pass
        elif self.player_vs_ai:
            # In this project the player is White and the AI is Black.
            try:
                self.game.headers["BlackElo"] = str(int(self.ai_elo))
            except Exception:
                pass

    def set_eval_bar_enabled(self, enabled: bool) -> None:
        self.eval_bar_enabled = enabled
        if not enabled:
            self.selected_square = None
        else:
            self.request_eval()

    def set_movement_mode(self, mode: str) -> None:
        """Configure how the player moves pieces: 'click', 'drag', or 'click+drag'."""
        if mode == 'click':
            self.movement_click_enabled = True
            self.movement_drag_enabled = False
        elif mode == 'drag':
            self.movement_click_enabled = False
            self.movement_drag_enabled = True
        else:
            self.movement_click_enabled = True
            self.movement_drag_enabled = True

        if not self.movement_click_enabled:
            self.selected_square = None

    @staticmethod
    def _coords_from_chess_square(square: chess.Square) -> tuple[int, int]:
        file_ = chess.square_file(square)  # 0..7 for a..h
        rank_ = chess.square_rank(square)  # 0..7 for 1..8
        return 7 - rank_, file_

    @staticmethod
    def _chess_square_from_coords(row: int, col: int) -> chess.Square:
        rank_ = 7 - row
        file_ = col
        return chess.square(file_, rank_)

    def _current_fen(self) -> str:
        return create_FEN(self.board, self.turn, self.castle_rights, self.en_passant_square, self.fullmove_number)

    def _python_chess_board(self) -> chess.Board:
        return chess.Board(self._current_fen())

    def request_eval(self) -> None:
        """Queue an async evaluation of the current position."""
        if self.stockfish_eval is None or not self.eval_bar_enabled:
            return
        fen = self._current_fen()
        try:
            # Keep only the newest request.
            if self._eval_queue.full():
                _ = self._eval_queue.get_nowait()
            self._eval_queue.put_nowait(fen)
        except Exception:
            pass

    def _eval_worker(self) -> None:
        if self.stockfish_eval is None:
            return
        while not self._eval_stop.is_set():
            try:
                fen = self._eval_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            # Drain any queued updates so we only analyze the latest position.
            try:
                while True:
                    fen = self._eval_queue.get_nowait()
            except queue.Empty:
                pass

            # Iterative deepening: update UI as depth increases.
            while not self._eval_stop.is_set():
                if not self.eval_bar_enabled:
                    break

                for depth in self._eval_depth_steps:
                    if self._eval_stop.is_set() or not self.eval_bar_enabled:
                        break

                    # If a new position arrived, restart with the latest FEN.
                    try:
                        fen = self._eval_queue.get_nowait()
                        # Drain to latest
                        while True:
                            fen = self._eval_queue.get_nowait()
                    except queue.Empty:
                        pass

                    try:
                        with self._eval_lock:
                            self.stockfish_eval.set_fen_position(fen)
                            self.stockfish_eval.set_depth(int(depth))
                            evaluation = self.stockfish_eval.get_evaluation()

                        is_mate = False
                        if evaluation.get("type") == "cp":
                            score = float(evaluation.get("value", 0)) / 100.0
                        else:
                            is_mate = True
                            mate_val = int(evaluation.get("value", 0))
                            score = 100.0 if mate_val > 0 else -100.0

                        with self._eval_lock:
                            self._eval_score = score
                            self._eval_label = print_eval(evaluation)
                            self._eval_is_mate = is_mate
                            self._eval_depth = int(depth)
                            self._eval_raw = evaluation
                    except Exception:
                        continue

                    # Small pause so users can see it converge.
                    time.sleep(0.12)

                # Once we reached max depth, don't keep burning CPU.
                break

    def _format_eval_value(self) -> str:
        with self._eval_lock:
            evaluation = self._eval_raw
        if not evaluation:
            return ''
        if evaluation.get('type') == 'cp':
            try:
                val = float(evaluation.get('value', 0)) / 100.0
            except Exception:
                val = 0.0
            if val >= 0:
                return f"+{val:.2f}"
            return f"{val:.2f}"
        try:
            mate_val = int(evaluation.get('value', 0))
        except Exception:
            mate_val = 0
        if mate_val >= 0:
            return f"M{mate_val}"
        return f"M{mate_val}"

    def _draw_eval_bar(self) -> pg.Rect | None:
        if not self.eval_bar_enabled:
            self._eval_bar_rect = None
            return None

        # If an eval hasn't been computed yet for the current position,
        # draw a neutral bar instead of hiding it.
        score_val = 0.0 if self._eval_score is None else float(self._eval_score)

        # Clamp centipawn evaluation into a reasonable visual range
        cp_cap = 6.0  # +/- 6 pawns
        score = max(-cp_cap, min(cp_cap, score_val))
        t = 0.5 + (score / (2 * cp_cap))
        t = max(0.0, min(1.0, t))

        bar_w = 16
        bar_h = int(self.size * 8)
        if self.show_numbers:
            x = int(self.offset[0] - (self.size / 2) - bar_w - 10)
        else:
            x = int(self.offset[0] - bar_w - 10)
        # In review mode the board can be left-aligned; keep the bar on-screen.
        x = max(2, x)
        y = int(self.offset[1])

        bg = pg.Rect(x, y, bar_w, bar_h)
        self._eval_bar_rect = bg

        # Background
        pg.draw.rect(self.screen, (40, 40, 40), bg, border_radius=6)

        # White portion is at the bottom; black at the top
        white_h = int(bar_h * t)
        if white_h > 0:
            pg.draw.rect(self.screen, (235, 235, 235), pg.Rect(x, y + (bar_h - white_h), bar_w, white_h), border_radius=6)
        if white_h < bar_h:
            pg.draw.rect(self.screen, (30, 30, 30), pg.Rect(x, y, bar_w, bar_h - white_h), border_radius=6)

        # Outline
        pg.draw.rect(self.screen, (120, 120, 120), bg, width=2, border_radius=6)

        return bg

    def _mouse_to_square(self, pos: tuple[int, int]) -> tuple[int, int] | None:
        x = int((pos[0] - self.offset[0]) // self.size)
        y = int((pos[1] - self.offset[1]) // self.size)
        if self.flipped:
            x = 7 - x
            y = 7 - y
        if -1 < x < 8 and -1 < y < 8:
            return y, x
        return None

    def _handle_click_to_move(self, mouse_pos: tuple[int, int]) -> None:
        target = self._mouse_to_square(mouse_pos)
        if target is None:
            self.selected_square = None
            return

        target_row, target_col = target

        # First click: select a piece
        if self.selected_square is None:
            piece = self.board[target_row][target_col]
            if piece != ' ' and piece.colour[0] == self.turn:
                self.selected_square = (target_row, target_col)
            else:
                self.selected_square = None
            return

        sel_row, sel_col = self.selected_square
        if (sel_row, sel_col) == (target_row, target_col):
            self.selected_square = None
            return

        # Clicking another own piece switches selection
        target_piece = self.board[target_row][target_col]
        if target_piece != ' ' and target_piece.colour[0] == self.turn:
            self.selected_square = (target_row, target_col)
            return

        sel_piece = self.board[sel_row][sel_col]
        if sel_piece == ' ':
            self.selected_square = None
            return

        dx = target_col - sel_col
        dy = target_row - sel_row
        if (dx, dy) not in sel_piece.legal_positions:
            return

        # Apply the move (same semantics as drag-release path)
        prev_turn = self.turn
        if sel_piece.make_move(self.board, self.offset, self.turn, self.flipped, target_col, target_row):
            uci = translate_move(sel_row, sel_col, target_row, target_col)
            # auto-promote to queen
            if sel_piece.piece.lower() == 'p' and ((prev_turn == 'w' and target_row == 0) or (prev_turn == 'b' and target_row == 7)):
                uci += 'q'

            if prev_turn == 'w':
                self.turn = 'b'
                self.last_move.append(uci)
                self.node = self.node.add_variation(chess.Move.from_uci(uci))
            else:
                self.fullmove_number += 1
                self.turn = 'w'
                if not self.player_vs_ai:
                    self.last_move.append(uci)
                    self.node = self.node.add_variation(chess.Move.from_uci(uci))

            self.moved()

            if EVAL_ON:
                self.get_eval()

            if self.player_vs_ai:
                self.ai_make_move(target_row, sel_row, sel_col)
                if EVAL_ON:
                    self.get_eval()

        self.selected_square = None

    def run(self) -> None:
        self._ensure_layout()
        self.draw_board()
        if self.updates:
            self.update_board()
        piece_active = None
        for piece in self.all_pieces:
            if piece.clicked:
                piece_active = piece
                break
        if piece_active is not None:
            self.draw_pieces(piece_active)
        else:
            self.draw_pieces()

        # Top-most overlays (must render after pieces/arrows).
        if self.review_active:
            self._draw_review_move_quality_marker()
            self._draw_review_overlay()
        if self.end_popup_active:
            self._draw_end_popup()

        # Show legal moves for a selected piece (click-to-move)
        if self.movement_click_enabled and self.selected_square is not None and not self.updates:
            try:
                sel_row, sel_col = self.selected_square
                sel_piece = self.board[sel_row][sel_col]
                if sel_piece != ' ' and sel_piece.colour[0] == self.turn:
                    sel_piece.show_legal_moves(self.screen, self.offset, self.turn, self.flipped, self.board)
            except Exception:
                pass
        for event in pg.event.get():
            if event.type == pg.QUIT:
                pg.quit()
                sys.exit()

            # Review mode: only navigation controls.
            if self.review_active:
                if event.type == pg.KEYDOWN:
                    if event.key == pg.K_ESCAPE:
                        self.exit_review()
                    elif event.key == pg.K_LEFT:
                        self.review_step(-1)
                    elif event.key == pg.K_RIGHT:
                        self.review_step(1)
                    elif event.key == pg.K_b:
                        self.review_show_best_move = not bool(self.review_show_best_move)
                        if not self.review_show_best_move:
                            self.review_best_move_uci = ''
                            self.review_arrow = None
                            try:
                                while True:
                                    self._review_queue.get_nowait()
                            except Exception:
                                pass
                        else:
                            self._request_review_best()
                elif event.type == pg.MOUSEBUTTONDOWN:
                    # Mouse wheel scrolls the move list in review mode
                    if event.button == 4:
                        self._review_scroll_moves(-3)
                    elif event.button == 5:
                        self._review_scroll_moves(3)
                elif event.type == pg.MOUSEBUTTONUP and event.button == 1:
                    self._handle_review_click(pg.mouse.get_pos())
                elif event.type == pg.VIDEORESIZE:
                    # Keep resize responsive while in review mode.
                    self.screen = pg.display.set_mode((event.w, event.h), pg.RESIZABLE, vsync=1)
                    self.settings.resize_event()
                    self.background = pg.image.load('data/img/background_dark.png').convert()
                    self.background = pg.transform.smoothscale(self.background, (event.w, event.h))
                    self._ensure_layout(force=True)
                continue

            # End-game popup: only popup controls.
            if self.end_popup_active:
                if event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
                    # Reset on ESC for a quick exit.
                    self.end_popup_active = False
                    self.end_popup_text = ''
                    self.end_popup_pgn_path = None
                    self.reset_game()
                elif event.type == pg.MOUSEBUTTONUP and event.button == 1:
                    self._handle_end_popup_click(pg.mouse.get_pos())
                continue

            elif event.type == pg.MOUSEBUTTONDOWN:
                self.game_just_ended = False
                if event.button == 1 and not self.game_just_ended:
                    self.left = True
                    self._mouse_down_pos = pg.mouse.get_pos()
                    self.click_left()
                elif event.button == 3:
                    self.click_right()
                elif event.button == 4 or event.button == 5:
                    self.flip_board()
            elif event.type == pg.MOUSEMOTION:
                # Only start dragging after the cursor moves a bit.
                if self.movement_drag_enabled and self.left and not self.updates and self._mouse_down_pos is not None:
                    mx, my = pg.mouse.get_pos()
                    dx = mx - self._mouse_down_pos[0]
                    dy = my - self._mouse_down_pos[1]
                    if (dx * dx + dy * dy) >= (self._drag_threshold_px * self._drag_threshold_px):
                        self.updates = True
                        self.selected_square = None
            elif event.type == pg.MOUSEBUTTONUP:
                if event.button == 1 and self.updates:
                    self.left = False
                    self.un_click_left()
                elif event.button == 1:
                    self.left = False
                    # Always clear right-click arrows/highlights on a left click.
                    # Previously this happened inside un_click_left(), which only runs on drag-release.
                    self.highlighted.clear()
                    self.arrows.clear()
                    if self.movement_click_enabled and not self.game_just_ended:
                        self._handle_click_to_move(pg.mouse.get_pos())
                elif event.button == 2:
                    if len(self.game_fens) > 1:
                        self.undo_move(False)
                        self.un_click_right(False)
                    elif len(self.game_fens) == 1:
                        self.undo_move(True)
                        self.un_click_right(False)
                elif event.button == 3 and not self.left:
                    self.un_click_right(True)
                elif event.button == 3:
                    self.updates_kill()
                    self.left = False
                self.updates = False
                self._mouse_down_pos = None
            elif event.type == pg.KEYDOWN:
                if event.key == pg.K_s and pg.key.get_mods() & pg.KMOD_CTRL:
                    # Save and reset (not an end-game popup)
                    dt = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    self._set_pgn_strength_headers()
                    self._save_pgn("data/games/" + dt + ".pgn")
                    self.reset_game()
                if event.key == pg.K_f and pg.key.get_mods() & pg.KMOD_CTRL:
                    print(self.game_fens[-1])
                if event.key == pg.K_e and pg.key.get_mods() & pg.KMOD_CTRL:
                    self.evaluation = self.get_eval()
                if event.key == pg.K_r and pg.key.get_mods() & pg.KMOD_CTRL:
                    self.flip_board()
                if event.key == pg.K_h and pg.key.get_mods() & pg.KMOD_CTRL:
                    # Hint: temporarily ask for a strong move, then restore configured Elo strength.
                    try:
                        self.stockfish.update_engine_parameters({"UCI_LimitStrength": "false"})
                    except Exception:
                        pass
                    try:
                        self.stockfish.set_skill_level(20)
                    except Exception:
                        pass

                    best = self.stockfish.get_best_move_time(200)
                    self.best_move = str(best) if best else ''
                    if best and len(best) >= 4:
                        try:
                            start_sq = square_on(best[0:2])
                            end_sq = square_on(best[2:4])
                            self.hint_arrow = (start_sq, end_sq)
                        except Exception:
                            self.hint_arrow = None
                    self._apply_ai_strength()
                if event.key == pg.K_u:
                    if len(self.game_fens) > 1:
                        self.undo_move(False)
                        self.un_click_right(False)
                    elif len(self.game_fens) == 1:
                        self.undo_move(True)
                        self.un_click_right(False)
                if event.key == pg.K_ESCAPE:
                    self.settings.run()
            elif event.type == pg.VIDEORESIZE:
                # There's some code to add back window content here.
                self.screen = pg.display.set_mode((event.w, event.h), pg.RESIZABLE, vsync=1)
                self.settings.resize_event()
                self.background = pg.image.load('data/img/background_dark.png').convert()
                self.background = pg.transform.smoothscale(self.background,
                                                           (pg.display.get_window_size()[0],
                                                            pg.display.get_window_size()[1]))
                self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
                if self.default_size >= pg.display.get_window_size()[1] or self.default_size >= \
                        pg.display.get_window_size()[0]:
                    self.show_numbers = False
                    if pg.display.get_window_size()[0] < pg.display.get_window_size()[1]:
                        self.size = int((pg.display.get_window_size()[0]) / 8)
                    else:
                        self.size = int((pg.display.get_window_size()[1]) / 8)
                elif (self.default_size < pg.display.get_window_size()[1] < self.default_size + 200) or (
                        self.default_size < pg.display.get_window_size()[0] < self.default_size + 1000):
                    self.show_numbers = True
                    if pg.display.get_window_size()[0] < pg.display.get_window_size()[1]:
                        self.size = int((pg.display.get_window_size()[0] - 200) / 8)
                    else:
                        self.size = int((pg.display.get_window_size()[1] - 200) / 8)
                else:
                    self.show_numbers = True
                if self.size <= 1:
                    self.size = 1
                self.board_background = pg.transform.smoothscale(self.board_background,
                                                                 (self.size * 8, self.size * 8))
                self.offset = [pg.display.get_window_size()[0] / 2 - 4 * self.size,
                               pg.display.get_window_size()[1] / 2 - 4 * self.size]

        if self.ai_vs_ai:
            self.un_click_left()
        pg.display.flip()
        self.clock.tick(150)

    def get_eval(self) -> str:
        """
        Get board evaluation
        :return: Evaluation string
        """
        self.stockfish.set_depth(20)
        evaluation = print_eval(self.stockfish.get_evaluation())
        self.stockfish.set_depth(99)
        return evaluation

    def un_click_left(self) -> None:
        """
        Left click release event logic. Calls make_move which makes a move if it is legal
        :return: None
        """
        self.highlighted.clear()
        self.arrows.clear()
        if self.ai_vs_ai:
            self.ai_make_move(0, 0, 0)
            if EVAL_ON:
                self.get_eval()
        else:
            for piece in self.all_pieces:
                row = piece.position[0]
                col = piece.position[1]
                if self.board[row][col] != ' ':
                    if self.board[row][col].clicked:
                        # Make move if legal
                        if self.board[row][col].make_move(self.board, self.offset, self.turn, self.flipped, None,
                                                          None):
                            x = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
                            y = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
                            if self.flipped:
                                x = -x + 7
                                y = -y + 7
                            if self.turn == 'w':
                                self.turn = 'b'
                                move = translate_move(row, col, y, x)
                                if self.board[row][col] != ' ':
                                    if self.board[row][col].piece == 'P':
                                        if y == 0:
                                            move += 'q'

                                # add move to chess.pgn node
                                self.last_move.append(move)
                                self.node = self.node.add_variation(chess.Move.from_uci(move))
                            elif self.turn == 'b':
                                self.fullmove_number += 1
                                self.turn = 'w'
                                if not self.player_vs_ai:
                                    move = translate_move(row, col, y, x)
                                    if self.board[row][col] != ' ':
                                        if self.board[row][col].piece == 'p':
                                            if y == 7:
                                                move += 'q'

                                    # add move to chess.pgn node
                                    self.last_move.append(move)
                                    self.node = self.node.add_variation(chess.Move.from_uci(move))

                            self.moved()
                            if self.board[y][x] != ' ':
                                self.board[y][x].clicked = False
                            if EVAL_ON:
                                self.get_eval()
                            if self.player_vs_ai:
                                self.ai_make_move(y, row, col)
                                if EVAL_ON:
                                    self.get_eval()
                        else:
                            self.board[row][col].clicked = False
                        break

    def change_pieces(self, piece_type: str) -> None:
        """
        Changes the piece style.
        :param piece_type: string name of the piece type.
        :return: None
        """
        self.piece_type = piece_type
        for piece in self.all_pieces:
            piece.change_piece_set(piece_type)

    def change_board(self, board_type):
        """
        Changes the Board style.
        :param board_type: filename of the board located in 'data/img/boards/'
        :return: None
        """
        self.board_style = board_type
        self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
        self.board_background = pg.transform.smoothscale(self.board_background,
                                                         (self.size * 8, self.size * 8))

    def check_resize(self):
        """
        Checks if the window has been resized and handles resizing
        :return: None
        """
        self.screen = pg.display.set_mode((self.screen.get_width(), self.screen.get_height()), pg.RESIZABLE, vsync=1)
        self.background = pg.image.load('data/img/background_dark.png').convert()
        self.background = pg.transform.smoothscale(self.background,
                                                   (pg.display.get_window_size()[0], pg.display.get_window_size()[1]))
        self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
        if self.default_size >= pg.display.get_window_size()[1] or self.default_size >= pg.display.get_window_size()[0]:
            self.show_numbers = False
            if pg.display.get_window_size()[0] < pg.display.get_window_size()[1]:
                self.size = int((pg.display.get_window_size()[0]) / 8)
            else:
                self.size = int((pg.display.get_window_size()[1]) / 8)
        elif (self.default_size < pg.display.get_window_size()[1] < self.default_size + 200) or (
                self.default_size < pg.display.get_window_size()[0] < self.default_size + 1000):
            self.show_numbers = True
            if pg.display.get_window_size()[0] < pg.display.get_window_size()[1]:
                self.size = int((pg.display.get_window_size()[0] - 200) / 8)
            else:
                self.size = int((pg.display.get_window_size()[1] - 200) / 8)
        else:
            self.show_numbers = True
        if self.size <= 1:
            self.size = 1
        self.board_background = pg.transform.smoothscale(self.board_background,
                                                         (self.size * 8, self.size * 8))
        self.offset = [pg.display.get_window_size()[0] / 2 - 4 * self.size,
                       pg.display.get_window_size()[1] / 2 - 4 * self.size]

    def change_mode(self, mode: str):
        """
        Changes the game mode to Player vs Player, Player vs AI, or AI vs AI
        :param mode: String of the mode: 'pvp', 'pvai', or 'aivai'
        :return: None
        """
        if mode == 'pvp':
            self.ai_vs_ai = False
            self.player_vs_ai = False
        elif mode == 'aivai':
            self.ai_vs_ai = True
            self.player_vs_ai = False
        elif mode == 'pvai':
            self.ai_vs_ai = False
            self.player_vs_ai = True

    def ai_make_move(self, y: int, row: int, col: int):
        """
        :param y: the moves column number; For promotion logic
        :param row: Row position of the piece to move
        :param col: Column position of the piece to move
        :return: None
        """
        # Engine Moves
        self.draw_board()
        self.draw_pieces()
        pg.display.flip()
        time.sleep(0.15)
        move = self.move_strength(self.ai_elo)
        if move is not None:
            self.last_move.append(move)
            # Do NOT append promotion suffix here.
            # Stockfish already returns promotion in UCI when needed (e.g. a7a8q),
            # and the previous code used the player's last-clicked piece, corrupting PGNs.
            self.node = self.node.add_variation(chess.Move.from_uci(move))
            self.engine_make_move(move)  # Making the move
        else:
            print('Fault')
            self.end_game('Fault')
            self.reset_game()

    def move_strength(self, elo: int) -> str | None:
        """
        Get a move given an approximate Elo strength.
        Uses Stockfish's built-in Elo limiter when available.
        :param elo: requested Elo
        :return: Move - the algebraic notation of the move as a string
        """
        self._apply_ai_strength()

        # Simple think-time scaling. Smaller time at low Elo makes play weaker/less consistent.
        try:
            elo_i = int(elo)
        except Exception:
            elo_i = self.ai_elo

        if elo_i <= 800:
            think_ms = 40
        elif elo_i <= 1200:
            think_ms = 70
        elif elo_i <= 1600:
            think_ms = 120
        elif elo_i <= 2000:
            think_ms = 180
        else:
            think_ms = 260

        # Extra weakening below typical Stockfish Elo floor: pick from top moves randomly.
        # This helps achieve "beginner" strengths in practice.
        if elo_i < 1200:
            top_n = 4 if elo_i < 900 else 3
            try:
                top_moves = self.stockfish.get_top_moves(top_n) or []
                legal = [m.get('Move') for m in top_moves if isinstance(m, dict) and m.get('Move')]
                if legal:
                    # Bias toward the best move as Elo increases.
                    if elo_i < 800 and len(legal) >= 2:
                        return random.choice(legal[1:])
                    return random.choice(legal)
            except Exception:
                pass

        return self.stockfish.get_best_move_time(int(think_ms))

    def _apply_ai_strength(self) -> None:
        """Apply current AI-strength settings to the Stockfish instance."""
        try:
            self.stockfish.update_engine_parameters({"UCI_LimitStrength": "true"})
        except Exception:
            pass
        try:
            self.stockfish.set_elo_rating(int(self.ai_elo))
        except Exception:
            pass
        # Keep Skill Level low; Elo limiter does most of the work.
        try:
            self.stockfish.set_skill_level(0)
        except Exception:
            pass

    def change_ai_elo(self, elo: int) -> None:
        """Set AI strength to a target Elo rating."""
        try:
            self.ai_elo = int(elo)
        except Exception:
            return
        self._apply_ai_strength()

    def change_ai_strength(self, num: int) -> None:
        """
        Legacy entrypoint (used by older settings). Interprets input as an index-like value
        and maps it onto an Elo scale.
        """
        self.ai_strength = num
        # Map 0..20 roughly to 600..3000
        mapped = 600 + int(max(0, min(20, int(num)))) * 120
        self.change_ai_elo(mapped)

    def un_click_right(self, left_click: bool) -> None:
        """
        Handle right unclick event. Used for highlights and arrows
        :param left_click: is currently clicking left?
        :return: None
        """
        txr = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
        tyr = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
        if self.flipped:
            txr = 7 - txr
            tyr = 7 - tyr
        if left_click:
            if self.txr == txr and self.tyr == tyr:
                if (tyr, txr) in self.highlighted:
                    self.highlighted.remove((tyr, txr))
                else:
                    self.highlighted.append((tyr, txr))
            else:
                if ((self.tyr, self.txr), (tyr, txr)) in self.arrows:
                    self.arrows.remove(((self.tyr, self.txr), (tyr, txr)))
                else:
                    if -1 < self.txr < 8 and -1 < self.tyr < 8 and -1 < txr < 8 and -1 < tyr < 8:
                        self.arrows.append(((self.tyr, self.txr), (tyr, txr)))

        for pieces in self.all_pieces:
            pieces.clicked = False

    def updates_kill(self) -> None:
        """
        kill updating the clicked pieces. used to unclick all pieces
        :return: None
        """
        self.updates = False
        for pieces in self.all_pieces:
            pieces.clicked = False
        self.left = False

    def moved(self) -> None:
        """
        Called after make_move to update legal moves,
        check for the end of game, and play sounds
        :return: None
        """
        self.prev_board = self.board
        self.selected_square = None
        self.hint_arrow = None
        eps_moved_made = False
        for i, row in enumerate(self.board):
            for j, piece in enumerate(row):
                if piece != ' ':
                    if piece.position != (i, j):
                        # piece no longer on the square of the board
                        self.board[i][j] = ' '

                        # has a pawn moved 2 squares. en-passant target square (standard FEN like "e3")
                        if piece.piece.lower() == 'p' and abs(piece.position[0] - i) == 2:
                            target_row = (piece.position[0] + i) // 2
                            self.en_passant_square = board_letters[piece.position[1]] + str(8 - target_row)
                        else:
                            self.en_passant_square = '-'

                        # has a pawn been captured with enpassant
                        if piece.piece.lower() == 'p':
                            if piece.position[0] - i == piece.direction and (
                                    piece.position[1] - j == 1 or piece.position[1] - j == -1):
                                if self.board[piece.position[0]][piece.position[1]] == ' ':
                                    eps_moved_made = True
                                    self.board[piece.position[0] - piece.direction][piece.position[1]].dead = True
                                    self.board[piece.position[0] - piece.direction][piece.position[1]] = ' '

                        # king has castled
                        castle = False
                        if piece.piece.lower() == 'k':
                            if piece.position[1] - j == 2 or piece.position[1] - j == -2:
                                castle = True
                                if piece.position[1] < 4:
                                    self.board[piece.position[0]][3] = self.board[piece.position[0]][0]
                                    self.board[piece.position[0]][0] = ' '
                                    self.board[piece.position[0]][3].position = (piece.position[0], 3)
                                else:
                                    self.board[piece.position[0]][5] = self.board[piece.position[0]][7]
                                    self.board[piece.position[0]][7] = ' '
                                    self.board[piece.position[0]][5].position = (piece.position[0], 5)

                        piece_sound = self.board[piece.position[0]][piece.position[1]]

                        # update the board
                        if self.board[piece.position[0]][piece.position[1]] != ' ':
                            self.board[piece.position[0]][piece.position[1]].dead = True
                        self.board[piece.position[0]][piece.position[1]] = piece

                        # promotion
                        promote = False
                        if piece.piece.lower() == 'p':
                            if piece.position[0] == int(3.5 + piece.direction * 3.5):
                                self.promotion(piece)
                                promote = True

                        if (castle or promote) and self.sound_enabled:
                            pg.mixer.music.load('data/sounds/castle.mp3')
                            pg.mixer.music.play(1)
                        elif piece_sound == ' ' and not eps_moved_made and self.sound_enabled:
                            pg.mixer.music.load('data/sounds/move.mp3')
                            pg.mixer.music.play(1)
                        elif self.sound_enabled:
                            pg.mixer.music.load('data/sounds/capture.mp3')
                            pg.mixer.music.play(1)

                        break
        for p in self.all_pieces:
            if p.dead:
                self.all_pieces.remove(p)
        for p in self.black_pieces:
            if p.dead:
                self.black_pieces.remove(p)
        for p in self.white_pieces:
            if p.dead:
                self.white_pieces.remove(p)

        # update next players legal moves
        if self.update_legal_moves() and self.sound_enabled:
            pg.mixer.music.load('data/sounds/check.aiff')
            pg.mixer.music.play(1)

        legal_moves = self.count_legal_moves()
        # print('Number of legal moves', legal_moves)
        # print FEN notation of position
        self.game_fens.append(self._current_fen())
        self.stockfish.set_fen_position(self.game_fens[-1])

        # async eval update
        self.request_eval()
        # print(self.game_fens[-1])
        if not self.player_vs_ai and not self.ai_vs_ai and self.flip_enabled:
            self.flip_board()

        if self.node.board().is_repetition():
            if self.sound_enabled:
                pg.mixer.music.load('data/sounds/mate.wav')
                pg.mixer.music.play(1)
                time.sleep(0.15)
                pg.mixer.music.play(1)
            self.end_game("DRAW BY REPETITION")
        elif self.node.board().is_stalemate():
            if self.sound_enabled:
                pg.mixer.music.load('data/sounds/mate.wav')
                pg.mixer.music.play(1)
                time.sleep(0.15)
                pg.mixer.music.play(1)
            self.end_game("INSUFFICIENT MATERIAL")
        elif self.node.board().is_insufficient_material():
            if self.sound_enabled:
                pg.mixer.music.load('data/sounds/mate.wav')
                pg.mixer.music.play(1)
                time.sleep(0.15)
                pg.mixer.music.play(1)
            self.end_game("INSUFFICIENT MATERIAL")
        elif self.node.board().is_checkmate() or legal_moves == 0:
            if self.sound_enabled:
                pg.mixer.music.load('data/sounds/mate.wav')
                pg.mixer.music.play(1)
                time.sleep(0.15)
                pg.mixer.music.play(1)
            if self.node.board().outcome().winner:
                self.end_game("CHECKMATE WHITE WINS !!")
            else:
                self.end_game("CHECKMATE BLACK WINS !!")
        # pprint(self.board, indent=3)

    def end_game(self, end_text: str) -> None:
        """
        Called when the game has ended. Saves the game in 'data/games/' and displays the end game menu
        :param end_text: string of the end of match. i.e. "Checkmate White Wins!"
        :return: None
        """
        # Guard against double-triggering end_game (can happen if the game-over
        # condition is detected twice). This prevents duplicate PGN files.
        if self.end_popup_active or self.game_just_ended:
            return
        self.game_just_ended = True
        self.end_popup_active = True
        self.end_popup_text = end_text

        dt = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = "data/games/" + dt + ".pgn"
        self._set_pgn_strength_headers()
        self._save_pgn(path)
        self.end_popup_pgn_path = path

    def reset_game(self) -> None:
        """
        Resets the game to the starting FEN position
        :return: None
        """
        self.updates_kill()
        self.board, self.turn, self.castle_rights, self.en_passant_square, self.halfmoves_since_last_capture, self.fullmove_number = parse_FEN(
            self.game_fens[0])
        self.game_fens = [self.game_fens[0]]
        for p in self.all_pieces:
            self.all_pieces.remove(p)
        for p in self.black_pieces:
            self.black_pieces.remove(p)
        for p in self.white_pieces:
            self.white_pieces.remove(p)
        for i, row in enumerate(self.board):
            for j, piece in enumerate(row):
                try:
                    if piece != ' ':
                        self.all_pieces.add(piece)
                        if piece.colour == 'black':
                            self.black_pieces.add(piece)
                        else:
                            self.white_pieces.add(piece)
                except:
                    pass
        for piece in self.all_pieces:
            piece.change_piece_set(self.piece_type)
            piece.clicked = False
        self.last_move = []
        self.game = chess.pgn.Game()
        self.game.headers["Event"] = "Player Vs Computer"
        self.game.headers["Site"] = "UK"
        self.game.headers["Date"] = str(datetime.datetime.now().year) + '/' + str(
            datetime.datetime.now().month) + '/' + str(datetime.datetime.now().day)
        if self.ai_vs_ai:
            self.game.headers["Event"] = "Computer Vs Computer"
            self.game.headers["Black"] = "Computer"
            self.game.headers["White"] = "Computer"
        elif self.player_vs_ai:
            self.game.headers["Event"] = "Player Vs Computer"
            self.game.headers["Black"] = "Computer"
            self.game.headers["White"] = "Player"
        else:
            self.game.headers["Event"] = "Player Vs Player"
            self.game.headers["Black"] = "Player"
            self.game.headers["White"] = "Player"

        self.game.headers["WhiteElo"] = "?"
        self.game.headers["BlackElo"] = "?"
        self._set_pgn_strength_headers()
        self.node = self.game
        self.stockfish.set_fen_position(self.game_fens[0])
        self.update_legal_moves()
        self.request_eval()

        self.end_popup_active = False
        self.end_popup_text = ''
        self.end_popup_pgn_path = None
        self.game_just_ended = False

    def undo_move(self, one: bool) -> None:
        """
        Undo last move, and update legal moves
        :param one: Is the length of the game ONLY ONE move?
        :return: None
        """
        if len(self.last_move) > 0:
            if one:
                self.board, self.turn, self.castle_rights, self.en_passant_square, self.halfmoves_since_last_capture, self.fullmove_number = parse_FEN(
                    self.game_fens[0])
            else:
                self.game_fens.pop()
                self.board, self.turn, self.castle_rights, self.en_passant_square, self.halfmoves_since_last_capture, self.fullmove_number = parse_FEN(
                    self.game_fens[-1])
            for p in self.all_pieces:
                self.all_pieces.remove(p)
            for p in self.black_pieces:
                self.black_pieces.remove(p)
            for p in self.white_pieces:
                self.white_pieces.remove(p)
            for i, row in enumerate(self.board):
                for j, piece in enumerate(row):
                    try:
                        if piece != ' ':
                            self.all_pieces.add(piece)
                            if piece.colour == 'black':
                                self.black_pieces.add(piece)
                            else:
                                self.white_pieces.add(piece)
                    except:
                        pass
            for piece in self.all_pieces:
                piece.change_piece_set(self.piece_type)
            self.last_move.pop()
            self.node = self.node.parent  # allows for undoes to show in analysis on https://chess.com/analysis
            if not self.player_vs_ai and not self.ai_vs_ai and self.flip_enabled:
                self.flip_board()

            self.update_board()
            self.update_legal_moves()
            self.request_eval()

    def update_legal_moves(self) -> bool:
        """
        Update the all legal moves
        :return: True if in check, false if not in check
        """
        castle = []
        for piece in self.all_pieces:
            if piece.piece.lower() == 'k' and not piece.has_moved:
                castle.append(piece.colour)

        self.handle_fen_castle(castle)

        pc_board = self._python_chess_board()

        # clear all moves first (prevents stale highlights)
        for piece in self.all_pieces:
            piece.legal_positions = []

        # populate legal moves for side to move
        for move in pc_board.legal_moves:
            from_row, from_col = self._coords_from_chess_square(move.from_square)
            to_row, to_col = self._coords_from_chess_square(move.to_square)
            piece = self.board[from_row][from_col]
            if piece != ' ' and piece.colour[0] == self.turn:
                piece.legal_positions.append((to_col - from_col, to_row - from_row))

        # attacked squares (used by debug overlay)
        attacked: set[tuple[int, int]] = set()
        opponent = chess.BLACK if self.turn == 'w' else chess.WHITE
        for sq, pc_piece in pc_board.piece_map().items():
            if pc_piece.color == opponent:
                for target_sq in pc_board.attacks(sq):
                    attacked.add(self._coords_from_chess_square(target_sq))
        self.map = list(attacked)

        return pc_board.is_check()

    def make_move_board(self, move: tuple, piece: Piece) -> None:
        """
        Make the move on the board, and call "piece.make_move()" and "moved()" to handle sounds and end game checks
        :param move: square the piece is moving to
        :param piece: The piece that is moving
        :return: None
        """
        if self.board[piece.position[0]][piece.position[1]].make_move(self.board, self.offset, self.turn, self.flipped,
                                                                      piece.position[1] + move[0],
                                                                      piece.position[0] + move[1]):
            if self.turn == 'w':
                self.turn = 'b'
            else:
                self.fullmove_number += 1
                self.turn = 'w'
            self.moved()
            self.board[piece.position[0]][piece.position[1]].clicked = False

    def engine_make_move(self, move: str) -> None:
        """
        Engine makes the move. Used for AI moves where move notation is for example "a2a4" or "f1e3".
        This function is similar to "make_move_board".
        :param move: Move to make. e.g. "a2a4" or "f1e3"
        :return: None
        """
        try:
            square1 = square_on(move[0:2])
            square2 = square_on(move[2:4])
            the_move = (square2[0] - square1[0], square2[1] - square1[1])
            piece = self.board[square1[0]][square1[1]]
            if piece.make_move(self.board, self.offset, self.turn, self.flipped, piece.position[1] + the_move[1],
                               piece.position[0] + the_move[0]):
                if self.turn == 'w':
                    self.turn = 'b'
                else:
                    self.fullmove_number += 1
                    self.turn = 'w'
                self.moved()
                self.board[piece.position[0]][piece.position[1]].clicked = False

        except:
            pass

    def create_map(self, pieces: list[Piece]) -> list[tuple]:
        """
        Returns a list of squares the pieces attack
        :param pieces: list of pieces to check attacking squares
        :return: list of the attacked squares
        """
        pc_board = self._python_chess_board()
        attacked: set[tuple[int, int]] = set()
        for piece in pieces:
            try:
                sq = self._chess_square_from_coords(piece.position[0], piece.position[1])
            except Exception:
                continue
            for target_sq in pc_board.attacks(sq):
                attacked.add(self._coords_from_chess_square(target_sq))
        return list(attacked)

    def count_legal_moves(self) -> int:
        """
        Get the number of legal moves
        :return: Number of legal moves
        """
        return self._python_chess_board().legal_moves.count()

    def promotion(self, piece: Piece) -> None:
        """
        Promote the given piece to a queen
        :param piece: A piece to promote
        :return: None
        """
        self.all_pieces.remove(piece)
        self.board[piece.position[0]][piece.position[1]] = Queen(position=(piece.position[0], piece.position[1]),
                                                                 colour=piece.colour, piece_type=self.piece_type)
        self.all_pieces.add(self.board[piece.position[0]][piece.position[1]])
        if piece.colour == 'black':
            self.black_pieces.remove(piece)
            self.black_pieces.add(self.board[piece.position[0]][piece.position[1]])
        else:
            self.white_pieces.remove(piece)
            self.white_pieces.add(self.board[piece.position[0]][piece.position[1]])

    def handle_fen_castle(self, castle: list[str]) -> None:
        """
        Update castle rights
        :param castle: either ["black", "white"], ["black"], or ["white"]
        :return: None
        """
        if 'black' in castle and 'white' in castle:
            self.castle_rights = 'KQkq'
        elif 'black' in castle:
            self.castle_rights = 'kq'
        elif 'white' in castle:
            self.castle_rights = 'KQ'
        else:
            self.castle_rights = '-'

        try:
            if self.board[0][0] == ' ' and 'q' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('q', '')
            elif self.board[0][0].piece != 'r' and 'q' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('q', '')
            elif self.board[0][0].piece == 'r' and self.board[0][0].has_moved and 'q' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('q', '')
        except:
            pass
        try:
            if self.board[0][7] == ' ' and 'k' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('k', '')
            elif self.board[0][7].piece != 'r' and 'k' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('k', '')
            elif self.board[0][7].piece == 'r' and self.board[0][7].has_moved and 'k' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('k', '')
        except:
            pass
        try:
            if self.board[7][0] == ' ' and 'Q' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('Q', '')
            elif self.board[7][0].piece != 'R' and 'Q' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('Q', '')
            elif self.board[7][0].piece == 'R' and self.board[7][0].has_moved and 'Q' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('Q', '')
        except:
            pass
        try:
            if self.board[7][7] == ' ' and 'K' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('K', '')
            elif self.board[7][7].piece != 'R' and 'K' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('K', '')
            elif self.board[7][7].piece == 'R' and self.board[7][7].has_moved and 'K' in self.castle_rights:
                self.castle_rights = self.castle_rights.replace('K', '')
        except:
            pass

        if self.castle_rights == '':
            self.castle_rights = '-'

    def click_right(self) -> None:
        """
        handle Right click event. Stores the co-ordinates of the click. Used for highlighting and arrows
        :return: None
        """
        self.txr = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
        self.tyr = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
        if self.flipped:
            self.txr = 7 - self.txr
            self.tyr = 7 - self.tyr

    def click_left(self) -> None:
        """
        Handle left click event. Stores co-ordinates of mouse and sets updates to true to enable drawing of clicked piece.
        :return: None
        """
        self.tx = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
        self.ty = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
        self.updates = False

    def update_board(self) -> None:
        """
        If currently clicking a piece then update the pieces positions and show the legal moves
        :return: None
        """
        try:
            if not self.flipped:
                if -1 < self.tx < 8 and -1 < self.ty < 8:
                    if self.board[self.ty][self.tx] != ' ':
                        self.board[self.ty][self.tx].clicked = True
                        self.board[self.ty][self.tx].show_legal_moves(self.screen, self.offset, self.turn, self.flipped,
                                                                      self.board)
            else:
                if -1 < self.tx < 8 and -1 < self.ty < 8:
                    if self.board[-self.ty + 7][-self.tx + 7] != ' ':
                        self.board[-self.ty + 7][-self.tx + 7].clicked = True
                        self.board[-self.ty + 7][-self.tx + 7].show_legal_moves(self.screen, self.offset, self.turn,
                                                                                self.flipped, self.board)
        except:
            pass

    def draw_board(self) -> None:
        """
        Draw the board, with highlighted squares and last moves. Draw numbers on the sides of the board.
        :return: None
        """
        self.screen.blit(self.background, (0, 0))
        self.screen.blit(self.board_background, (self.offset[0], self.offset[1]))
        square1 = None
        square2 = None
        if len(self.last_move) > 1:
            square1 = square_on(self.last_move[-1][0:2])
            square2 = square_on(self.last_move[-1][2:4])
        elif len(self.last_move) == 1:
            square1 = square_on(self.last_move[0][0:2])
            square2 = square_on(self.last_move[0][2:4])
        count = 1
        for row in range(8):
            for col in range(8):
                if self.flipped:
                    row_new = -row + 7
                    col_new = -col + 7
                else:
                    row_new = row
                    col_new = col
                surface = pg.Surface((self.size, self.size))
                surface.set_alpha(200)
                if self.debug and (row_new, col_new) in self.map:
                    surface.fill(self.colours2[count % 2])
                    self.screen.blit(surface,
                                     (self.offset[0] + self.size * col_new, self.offset[1] + self.size * row_new))
                else:
                    if (row, col) in self.highlighted:
                        surface.fill(self.colours4[count % 2])
                        self.screen.blit(surface,
                                         (self.offset[0] + self.size * col_new, self.offset[1] + self.size * row_new))
                    else:
                        if len(self.last_move) != 0:
                            if (row, col) in [square1, square2]:
                                surface.fill(self.colours3[count % 2])
                                self.screen.blit(surface,
                                                 (self.offset[0] + self.size * col_new,
                                                  self.offset[1] + self.size * row_new))
                            else:
                                surface.fill(self.colours[count % 2])
                                self.screen.blit(surface,
                                                 (self.offset[0] + self.size * col_new,
                                                  self.offset[1] + self.size * row_new))
                        else:
                            surface.fill(self.colours[count % 2])
                            self.screen.blit(surface,
                                             (self.offset[0] + self.size * col_new,
                                              self.offset[1] + self.size * row_new))
                count += 1
            count += 1

        # draw letters + numbers
        if self.show_numbers:
            for i in range(8):
                number = 8 - i
                if self.flipped:
                    number = -number + 9
                surface = self.font.render(str(number), False, (255, 255, 255))
                self.screen.blit(surface, (self.offset[0] - self.size / 2,
                                           self.offset[1] + self.size / 2 + self.size * i - 13))  # draw numbers
            for i in range(8):
                letter = board_letters[i]
                if self.flipped:
                    letter = board_letters[7 - i]
                surface = self.font.render(str(letter), False, (255, 255, 255))
                self.screen.blit(surface, (self.offset[0] + self.size / 2 - 8 + self.size * i,
                                           self.offset[
                                               1] + 17 * self.size / 2 - 25))  # draw letters
            # Avoid overlapping HUD text during review/popup overlays.
            if not self.review_active and not self.end_popup_active:
                surface = self.font.render('Settings = ESC', False, (255, 255, 255))
                self.screen.blit(surface, (20, 20))
            # Eval bar: show only the bar; show numeric value on hover
            if self.eval_bar_enabled:
                rect = self._draw_eval_bar()
                if rect is not None and rect.collidepoint(pg.mouse.get_pos()):
                    value = self._format_eval_value()
                    if value:
                        surf = self.eval_font.render(value, False, (255, 255, 255))
                        x = rect.x - surf.get_width() - 6
                        if x < 0:
                            x = rect.x + rect.w + 6
                        self.screen.blit(surf, (x, rect.y + 18))

            if self.best_move != '':
                # Hint is now rendered as a blue arrow (Ctrl+H)
                pass

        # Note: review overlay and end-game popup are rendered in run() after pieces,
        # so they are not obscured by piece sprites.

    def _draw_end_popup(self) -> None:
        # Dim background
        overlay = pg.Surface(self.screen.get_size(), pg.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        w, h = self.screen.get_size()
        box_w = min(720, w - 80)
        box_h = 240
        box = pg.Rect((w - box_w) // 2, (h - box_h) // 2, box_w, box_h)
        pg.draw.rect(self.screen, (40, 40, 40), box, border_radius=10)
        pg.draw.rect(self.screen, (140, 140, 140), box, width=2, border_radius=10)

        title = self.font.render('Game Over', False, (255, 255, 255))
        self.screen.blit(title, (box.x + 20, box.y + 18))

        msg = self.eval_font.render(str(self.end_popup_text), False, (255, 255, 255))
        self.screen.blit(msg, (box.x + 20, box.y + 70))

        # Buttons
        btn_h = 44
        btn_w = 160
        gap = 18
        y = box.y + box.h - btn_h - 22
        x0 = box.x + 20
        btn_view = pg.Rect(x0, y, btn_w, btn_h)
        btn_review = pg.Rect(x0 + btn_w + gap, y, btn_w, btn_h)
        btn_reset = pg.Rect(x0 + (btn_w + gap) * 2, y, btn_w, btn_h)

        for rect, label, bg in [
            (btn_view, 'View PGN', (100, 100, 100)),
            (btn_review, 'Review', (100, 100, 100)),
            (btn_reset, 'Reset', (200, 0, 0)),
        ]:
            pg.draw.rect(self.screen, bg, rect, border_radius=8)
            pg.draw.rect(self.screen, (30, 30, 30), rect, width=2, border_radius=8)
            surf = self.eval_font.render(label, False, (0, 0, 0) if bg != (100, 100, 100) else (0, 0, 0))
            self.screen.blit(surf, (rect.x + (rect.w - surf.get_width()) // 2, rect.y + (rect.h - surf.get_height()) // 2))

        self._end_popup_buttons = {
            'view': btn_view,
            'review': btn_review,
            'reset': btn_reset,
        }

    def _handle_end_popup_click(self, pos: tuple[int, int]) -> None:
        btns = getattr(self, '_end_popup_buttons', {})
        if not btns:
            return
        if btns.get('reset') and btns['reset'].collidepoint(pos):
            self.end_popup_active = False
            self.end_popup_text = ''
            self.end_popup_pgn_path = None
            self.reset_game()
            return
        if btns.get('view') and btns['view'].collidepoint(pos):
            if self.end_popup_pgn_path:
                try:
                    os.system('notepad ' + self.end_popup_pgn_path)
                except Exception:
                    pass
            return
        if btns.get('review') and btns['review'].collidepoint(pos):
            if self.end_popup_pgn_path:
                try:
                    self.start_review(self.end_popup_pgn_path)
                    self.end_popup_active = False
                except Exception:
                    pass

    def _draw_review_overlay(self) -> None:
        # Draw review stats + move list in a right-side panel so it doesn't overlap the board.
        win_w, win_h = self.screen.get_size()
        board_rect = pg.Rect(int(self.offset[0]), int(self.offset[1]), int(self.size * 8), int(self.size * 8))

        margin = 18
        x = board_rect.right + margin
        y = board_rect.top
        w = win_w - x - margin
        h = board_rect.h
        # Fallback if the window is too narrow.
        if w < 220:
            x = margin
            y = margin
            w = max(220, win_w - 2 * margin)
            h = min(board_rect.h, win_h - 2 * margin)

        panel = pg.Rect(int(x), int(y), int(w), int(h))
        bg = pg.Surface((panel.w, panel.h), pg.SRCALPHA)
        bg.fill((0, 0, 0, 160))
        self.screen.blit(bg, (panel.x, panel.y))
        pg.draw.rect(self.screen, (140, 140, 140), panel, width=1, border_radius=6)

        pad = 10
        line_h = self.eval_font.get_linesize() + 2
        ply = max(0, self.review_index)
        total = max(0, len(self.review_fens) - 1)
        name = self.review_name or 'Review'

        lines: list[str] = [
            f"Review: {name}",
            f"Ply: {ply}/{total} (Left/Right, ESC exits)",
        ]
        if self.review_show_best_move and self.review_best_move_uci:
            lines.append(f"Best: {self.review_best_move_uci}")
        if self.review_acpl_white is not None and self.review_acpl_black is not None:
            lines.append(f"ACPL  White: {self.review_acpl_white:.0f}  Black: {self.review_acpl_black:.0f}")
            if (
                self.review_accuracy_white is not None
                and self.review_accuracy_black is not None
                and self.review_accuracy_overall is not None
            ):
                lines.append(
                    f"Accuracy  White: {self.review_accuracy_white:.0f}%  Black: {self.review_accuracy_black:.0f}%  Overall: {self.review_accuracy_overall:.0f}%"
                )
        elif self.review_analysis_progress is not None:
            pct = int(self.review_analysis_progress * 100)
            lines.append(f"Analyzing... {pct}%")

        xx = panel.x + pad
        yy = panel.y + pad
        for t in lines:
            surf = self.eval_font.render(t, False, (255, 255, 255))
            self.screen.blit(surf, (xx, yy))
            yy += line_h

        yy += 8
        title = self.eval_font.render("Moves:", False, (255, 255, 255))
        self.screen.blit(title, (xx, yy))
        yy += line_h

        list_top = yy
        list_bottom = panel.bottom - pad
        row_h = self.eval_font.get_linesize() + 6
        visible_rows = max(1, int((list_bottom - list_top) // row_h))

        # Build full-move rows from SAN list.
        sans = self.review_sans or []
        labels = getattr(self, 'review_move_labels', []) or []

        def decorate_san(i: int, san: str) -> tuple[str, tuple[int, int, int]]:
            tag = labels[i] if 0 <= i < len(labels) else ''
            if tag == 'Best':
                # Avoid Unicode glyphs that may render as empty boxes on some systems/fonts.
                return f"{san}*", (120, 200, 255)
            if tag == 'Good':
                return f"{san}+", (200, 255, 200)
            if tag == 'Book':
                return f"{san} (book)", (120, 200, 255)
            if tag == 'Amazing':
                return f"{san}!!", (120, 255, 140)
            if tag == 'Great':
                return f"{san}!", (170, 255, 170)
            if tag == 'Mistake':
                return f"{san}?", (255, 200, 120)
            if tag == 'Blunder':
                return f"{san}??", (255, 120, 120)
            return san, (255, 255, 255)

        fullmove_count = (len(sans) + 1) // 2
        max_scroll = max(0, fullmove_count - visible_rows)
        self.review_move_scroll = max(0, min(self.review_move_scroll, max_scroll))

        col2 = panel.x + (panel.w // 2)
        move_idx_at_pos = self.review_index - 1  # last move played to reach current position
        self._review_move_hitboxes = []

        for row in range(visible_rows):
            fm = row + 1 + self.review_move_scroll
            if fm > fullmove_count:
                break
            w_i = 2 * (fm - 1)
            b_i = w_i + 1
            w_san = sans[w_i] if w_i < len(sans) else ''
            b_san = sans[b_i] if b_i < len(sans) else ''

            y_row = list_top + row * row_h
            num = self.eval_font.render(f"{fm}.", False, (200, 200, 200))
            self.screen.blit(num, (xx, y_row))

            # White move
            if w_san:
                w_x = xx + num.get_width() + 8
                w_text, w_col = decorate_san(w_i, w_san)
                w_surf = self.eval_font.render(w_text, False, w_col)
                w_rect = pg.Rect(w_x - 3, y_row - 1, w_surf.get_width() + 6, row_h)
                if move_idx_at_pos == w_i:
                    hi = pg.Surface((w_rect.w, w_rect.h), pg.SRCALPHA)
                    hi.fill((255, 255, 255, 40))
                    self.screen.blit(hi, (w_rect.x, w_rect.y))
                self.screen.blit(w_surf, (w_x, y_row))
                self._review_move_hitboxes.append((w_rect, w_i + 1))  # ply index

            # Black move
            if b_san:
                b_x = col2
                b_text, b_col = decorate_san(b_i, b_san)
                b_surf = self.eval_font.render(b_text, False, b_col)
                b_rect = pg.Rect(b_x - 3, y_row - 1, b_surf.get_width() + 6, row_h)
                if move_idx_at_pos == b_i:
                    hi = pg.Surface((b_rect.w, b_rect.h), pg.SRCALPHA)
                    hi.fill((255, 255, 255, 40))
                    self.screen.blit(hi, (b_rect.x, b_rect.y))
                self.screen.blit(b_surf, (b_x, y_row))
                self._review_move_hitboxes.append((b_rect, b_i + 1))

        # Store panel rect for click handling.
        self._review_panel_rect = panel

    def _draw_review_move_quality_marker(self) -> None:
        """Draw a small move-quality symbol on the destination square in review mode."""
        if not self.review_active:
            return
        idx = int(self.review_index) - 1
        if idx < 0:
            return
        labels = getattr(self, 'review_move_labels', []) or []
        if idx >= len(labels):
            return
        tag = labels[idx]

        fg_map: dict[str, tuple[int, int, int]] = {
            'Best': (120, 200, 255),
            'Good': (200, 255, 200),
            'Book': (120, 200, 255),
            'Amazing': (120, 255, 140),
            'Great': (170, 255, 170),
            'Mistake': (255, 200, 120),
            'Blunder': (255, 120, 120),
        }
        if tag not in fg_map:
            return
        fg = fg_map[tag]

        try:
            uci = str(self.review_plies[idx].uci())
        except Exception:
            return
        if len(uci) < 4:
            return
        try:
            row, col = square_on(uci[2:4])
        except Exception:
            return

        if self.flipped:
            row = 7 - row
            col = 7 - col

        sq_x = int(self.offset[0] + self.size * col)
        sq_y = int(self.offset[1] + self.size * row)

        pad = max(2, int(self.size * 0.05))
        box = max(16, min(28, int(self.size * 0.42)))
        marker = pg.Surface((box, box), pg.SRCALPHA)
        marker.fill((0, 0, 0, 0))
        pg.draw.rect(marker, (0, 0, 0, 170), pg.Rect(0, 0, box, box), border_radius=6)
        pg.draw.rect(marker, fg, pg.Rect(0, 0, box, box), width=2, border_radius=6)

        # Draw symbol. Use vector shapes for Best/Good to avoid missing Unicode glyphs.
        if tag == 'Good':
            # Check mark
            a = (int(box * 0.25), int(box * 0.55))
            b = (int(box * 0.42), int(box * 0.72))
            c = (int(box * 0.78), int(box * 0.30))
            pg.draw.lines(marker, fg, False, [a, b, c], max(2, int(box * 0.10)))
        elif tag == 'Best':
            # 5-point star (outline)
            cx, cy = box / 2.0, box / 2.0
            r_outer = box * 0.36
            r_inner = box * 0.16
            pts: list[tuple[int, int]] = []
            for k in range(10):
                ang = math.radians(-90 + k * 36)
                r = r_outer if (k % 2 == 0) else r_inner
                pts.append((int(cx + r * math.cos(ang)), int(cy + r * math.sin(ang))))
            pg.draw.polygon(marker, fg, pts, width=max(2, int(box * 0.08)))
        else:
            symbol_map: dict[str, str] = {
                'Book': 'B',
                'Amazing': '!!',
                'Great': '!',
                'Mistake': '?',
                'Blunder': '??',
            }
            symbol = symbol_map.get(tag, '')
            if symbol:
                try:
                    txt = self.eval_font.render(symbol, False, fg)
                    marker.blit(txt, ((box - txt.get_width()) // 2, (box - txt.get_height()) // 2))
                except Exception:
                    pass

        # Top-right corner of the destination square.
        self.screen.blit(marker, (sq_x + self.size - box - pad, sq_y + pad))

    def _review_scroll_moves(self, delta_rows: int) -> None:
        try:
            self.review_move_scroll = max(0, int(self.review_move_scroll) + int(delta_rows))
        except Exception:
            self.review_move_scroll = 0

    def _handle_review_click(self, pos: tuple[int, int]) -> None:
        if not self.review_active:
            return
        for rect, ply_index in getattr(self, '_review_move_hitboxes', []):
            if rect.collidepoint(pos):
                self._review_set_index(int(ply_index), play_sound=True)
                return

    def _ensure_layout(self, force: bool = False) -> None:
        """Ensure the board layout matches the current mode.

        In review mode, shrink and left-align the board so the move list panel fits on the right.
        """
        try:
            win_w, win_h = self.screen.get_size()
        except Exception:
            return

        key = (bool(self.review_active), int(win_w), int(win_h), str(self.board_style), bool(self.show_numbers))
        if not force and self._layout_cache_key == key:
            return
        self._layout_cache_key = key

        if not self.review_active:
            return

        # Reserve space for the review panel.
        left_margin = 60 if self.show_numbers else 20
        gap = 18
        right_margin = 18
        panel_w = int(min(420, max(260, win_w * 0.33)))

        board_available_w = max(120, win_w - left_margin - gap - panel_w - right_margin)
        board_available_h = max(120, win_h - 40)
        board_px = int(min(board_available_w, board_available_h))
        new_size = max(1, board_px // 8)

        if new_size != int(self.size):
            self.size = int(new_size)
            try:
                self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
                self.board_background = pg.transform.smoothscale(self.board_background, (self.size * 8, self.size * 8))
            except Exception:
                pass

        self.offset = [
            float(left_margin),
            float(max(10, (win_h - self.size * 8) / 2)),
        ]

    def _restore_normal_layout(self) -> None:
        """Restore the default (non-review) centered board layout."""
        try:
            win_w, win_h = self.screen.get_size()
        except Exception:
            return

        self._layout_cache_key = None
        try:
            new_size = int((win_h - 200) / 8)
        except Exception:
            new_size = int(self.size)
        if new_size <= 1:
            new_size = 1
        self.size = int(new_size)
        try:
            self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
            self.board_background = pg.transform.smoothscale(self.board_background, (self.size * 8, self.size * 8))
        except Exception:
            pass
        self.offset = [
            pg.display.get_window_size()[0] / 2 - 4 * self.size,
            pg.display.get_window_size()[1] / 2 - 4 * self.size,
        ]

    def _play_review_navigation_sound(self, prev_index: int, new_index: int) -> None:
        if not self.sound_enabled:
            return
        try:
            # Step forward by one ply: play a contextual sound.
            if new_index == prev_index + 1 and 0 <= prev_index < len(self.review_plies):
                fen_before = self.review_fens[prev_index]
                board = chess.Board(fen_before)
                mv = self.review_plies[prev_index]
                is_capture = board.is_capture(mv)
                is_castle = board.is_castling(mv)
                is_promo = mv.promotion is not None
                board.push(mv)
                if board.is_checkmate():
                    pg.mixer.music.load('data/sounds/mate.wav')
                elif board.is_check():
                    pg.mixer.music.load('data/sounds/check.aiff')
                elif is_castle or is_promo:
                    pg.mixer.music.load('data/sounds/castle.mp3')
                elif is_capture:
                    pg.mixer.music.load('data/sounds/capture.mp3')
                else:
                    pg.mixer.music.load('data/sounds/move.mp3')
                pg.mixer.music.play(1)
                return

            # Any other navigation (backwards/jumps): simple move sound.
            pg.mixer.music.load('data/sounds/move.mp3')
            pg.mixer.music.play(1)
        except Exception:
            pass

    def _review_set_index(self, new_idx: int, play_sound: bool = False) -> None:
        if not self.review_active or not self.review_fens:
            return
        prev = int(self.review_index)
        new_idx = max(0, min(len(self.review_fens) - 1, int(new_idx)))
        if new_idx == prev:
            return
        if play_sound:
            self._play_review_navigation_sound(prev, new_idx)
        self.review_index = new_idx

        # Highlight last move like in the main game.
        try:
            if self.review_index <= 0:
                self.last_move = []
            else:
                self.last_move = [str(self.review_plies[self.review_index - 1].uci())]
        except Exception:
            self.last_move = []

        self._load_fen_into_ui(self.review_fens[self.review_index])
        # Update eval bar for the currently viewed review position.
        self.request_eval()
        # Update eval bar for the currently viewed review position.
        self.request_eval()
        if self.review_show_best_move:
            self._request_review_best()
        else:
            self.review_best_move_uci = ''
            self.review_arrow = None

    @staticmethod
    def _pgn_strip_comments(text: str) -> str:
        # Remove {...} comments and ';' line comments
        text = re.sub(r"\{[^}]*\}", " ", text, flags=re.DOTALL)
        text = re.sub(r";[^\n]*", " ", text)
        return text

    @staticmethod
    def _pgn_tokenize_movetext(text: str) -> list[str]:
        # Keep parentheses as tokens
        return re.findall(r"\(|\)|\S+", text)

    @staticmethod
    def _pgn_parse_nested(tokens: list[str]) -> list:
        root: list = []
        stack: list[list] = [root]
        for tok in tokens:
            if tok == '(':
                new_list: list = []
                stack[-1].append(new_list)
                stack.append(new_list)
            elif tok == ')':
                if len(stack) > 1:
                    stack.pop()
            else:
                stack[-1].append(tok)
        return root

    @staticmethod
    def _is_pgn_result(tok: str) -> bool:
        return tok in ('1-0', '0-1', '1/2-1/2', '*')

    @staticmethod
    def _is_move_number(tok: str) -> bool:
        return bool(re.match(r"^\d+\.(?:\.\.)?$", tok)) or tok.endswith('...') and tok[:-3].isdigit()

    @classmethod
    def _pgn_collect_san_prefer_variations(cls, node, out: list[str]) -> None:
        # Rule requested by user: whenever we see a (...) variation, ignore the move immediately before it
        # and instead take the moves inside the brackets.
        for item in node:
            if isinstance(item, list):
                # Drop the last SAN move (the one "before the brackets")
                if out:
                    out.pop()
                cls._pgn_collect_san_prefer_variations(item, out)
                continue

            tok = str(item)
            if cls._is_pgn_result(tok):
                return
            if tok.startswith('$'):
                continue
            if cls._is_move_number(tok):
                continue
            # Ignore move suffix annotations like !? etc (keep on SAN token itself, parse_san can handle many,
            # but some files may include standalone punctuation)
            if tok in ('!', '?', '!!', '??', '!?', '?!'):
                continue
            out.append(tok)

    @staticmethod
    def _pgn_headers_and_movetext(raw: str) -> tuple[dict[str, str], str]:
        headers: dict[str, str] = {}
        movelines: list[str] = []
        in_headers = True
        for line in raw.splitlines():
            s = line.strip()
            if in_headers and s.startswith('[') and s.endswith(']'):
                # [Key "Value"]
                m = re.match(r"^\[(\w+)\s+\"(.*)\"\]$", s)
                if m:
                    headers[m.group(1)] = m.group(2)
                continue
            if s == '' and in_headers:
                in_headers = False
                continue
            if not in_headers:
                movelines.append(line)
        return headers, "\n".join(movelines)

    def start_review(self, pgn_path: str) -> bool:
        """Enter review mode for a PGN file. Returns True on success.

        This loader is intentionally tolerant: it prefers moves inside (...) brackets and ignores the move
        immediately preceding the bracket, matching the user's PGN style.
        """
        self.review_last_error = ''

        try:
            raw = open(pgn_path, 'r', encoding='utf-8', errors='ignore').read()
        except Exception as e:
            self.review_last_error = str(e)
            return False

        headers, movetext = self._pgn_headers_and_movetext(raw)
        movetext = self._pgn_strip_comments(movetext)
        tokens = self._pgn_tokenize_movetext(movetext)
        nested = self._pgn_parse_nested(tokens)

        san_moves: list[str] = []
        self._pgn_collect_san_prefer_variations(nested, san_moves)

        # Determine starting position
        start_board = chess.Board()
        if headers.get('SetUp') == '1' and headers.get('FEN'):
            try:
                start_board = chess.Board(headers['FEN'])
            except Exception:
                start_board = chess.Board()

        # Convert SAN list to moves and FENs
        board = start_board.copy(stack=False)
        moves: list[chess.Move] = []
        sans_used: list[str] = []
        fens: list[str] = [board.fen()]
        for san in san_moves:
            # Some PGNs (especially with preferred-variation parsing) may contain extra moves
            # after a checkmate line. Once the position is terminal, ignore the rest.
            try:
                if board.is_game_over(claim_draw=True):
                    break
            except Exception:
                pass
            try:
                mv = board.parse_san(san)
            except Exception:
                # As a last resort, if the SAN is actually UCI, accept it.
                try:
                    mv = chess.Move.from_uci(san)
                    if mv not in board.legal_moves:
                        raise ValueError('illegal')
                except Exception:
                    self.review_last_error = f"Illegal move in PGN: {san}"
                    return False
            board.push(mv)
            moves.append(mv)
            sans_used.append(san)
            fens.append(board.fen())

            try:
                if board.is_game_over(claim_draw=True):
                    break
            except Exception:
                pass

        if len(fens) <= 1:
            self.review_last_error = 'No moves found in PGN.'
            return False

        self.review_active = True
        self.review_pgn_path = pgn_path
        self.review_name = os.path.basename(pgn_path)

        self.review_plies = moves
        self.review_fens = fens
        self.review_sans = sans_used
        self.review_move_labels = []
        self.review_move_scroll = 0

        self.review_index = 0
        self.review_best_move_uci = ''
        self.review_arrow = None
        self.review_acpl_white = None
        self.review_acpl_black = None
        self.review_accuracy_white = None
        self.review_accuracy_black = None
        self.review_accuracy_overall = None
        self.review_analysis_progress = 0.0

        self.last_move = []
        self._ensure_layout(force=True)

        self._load_fen_into_ui(self.review_fens[self.review_index])
        if self.review_show_best_move:
            self._request_review_best()
        # Run analysis on the parsed move list (not python-chess PGN mainline)
        self._start_review_analysis_thread(start_board, moves)

        # If the original PGN contains bracketed variations, overwrite it in-place
        # with a cleaned mainline (prevents duplicate "cleaned" files).
        try:
            if '(' in raw or ')' in raw:
                result_tok = headers.get('Result', '*') or '*'
                parts: list[str] = []
                for i, san in enumerate(sans_used):
                    if i % 2 == 0:
                        parts.append(f"{(i // 2) + 1}.")
                    parts.append(san)
                parts.append(result_tok)
                cleaned_movetext = ' '.join(parts).strip() + "\n\n"

                # Preserve the original header block as-is.
                header_lines: list[str] = []
                in_headers = True
                for line in raw.splitlines():
                    s = line.strip()
                    if in_headers and s.startswith('[') and s.endswith(']'):
                        header_lines.append(line)
                        continue
                    if in_headers and s == '':
                        break
                header_block = "\n".join(header_lines).rstrip() + "\n\n"

                tmp = pgn_path + ".tmp"
                with open(tmp, 'w', encoding='utf-8', errors='ignore') as f:
                    f.write(header_block)
                    f.write(cleaned_movetext)
                os.replace(tmp, pgn_path)
        except Exception:
            # Never fail review just because we couldn't rewrite the file.
            pass

        return True

    def exit_review(self) -> None:
        self.review_active = False
        self.review_pgn_path = None
        self.review_name = ''
        self.review_fens = []
        self.review_plies = []
        self.review_sans = []
        self.review_move_labels = []
        self.review_index = 0
        self.review_best_move_uci = ''
        self.review_arrow = None
        self.review_acpl_white = None
        self.review_acpl_black = None
        self.review_accuracy_white = None
        self.review_accuracy_black = None
        self.review_accuracy_overall = None
        self.review_analysis_progress = None
        self.review_move_scroll = 0
        self._review_move_hitboxes = []
        self._restore_normal_layout()
        self.reset_game()

    def review_step(self, delta: int) -> None:
        if not self.review_active or not self.review_fens:
            return
        new_idx = self.review_index + int(delta)
        self._review_set_index(new_idx, play_sound=True)

    def _load_fen_into_ui(self, fen: str) -> None:
        self.updates_kill()
        try:
            self.board, self.turn, self.castle_rights, self.en_passant_square, self.halfmoves_since_last_capture, self.fullmove_number = parse_FEN(fen)
        except Exception:
            return
        for p in list(self.all_pieces):
            self.all_pieces.remove(p)
        for p in list(self.black_pieces):
            self.black_pieces.remove(p)
        for p in list(self.white_pieces):
            self.white_pieces.remove(p)
        for i, row in enumerate(self.board):
            for j, piece in enumerate(row):
                if piece != ' ':
                    self.all_pieces.add(piece)
                    if piece.colour == 'black':
                        self.black_pieces.add(piece)
                    else:
                        self.white_pieces.add(piece)
        for piece in self.all_pieces:
            piece.change_piece_set(self.piece_type)
            piece.clicked = False
        self.selected_square = None
        self.update_legal_moves()

    def _request_review_best(self) -> None:
        if self.stockfish_review is None or not self.review_active or not self.review_fens or not self.review_show_best_move:
            return
        fen = self.review_fens[self.review_index]
        try:
            while True:
                self._review_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._review_queue.put_nowait((fen, int(self.review_index)))
        except Exception:
            pass

    def _review_worker(self) -> None:
        if self.stockfish_review is None:
            return
        while not self._review_stop.is_set():
            try:
                fen, idx = self._review_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self.stockfish_review.update_engine_parameters({"UCI_LimitStrength": "false"})
            except Exception:
                pass
            try:
                self.stockfish_review.set_skill_level(20)
            except Exception:
                pass
            try:
                self.stockfish_review.set_fen_position(fen)
                best = self.stockfish_review.get_best_move_time(150)
            except Exception:
                best = None

            with self._review_lock:
                if not best:
                    self.review_best_move_uci = ''
                    self.review_arrow = None
                    continue
                self.review_best_move_uci = str(best)
                if len(best) >= 4 and idx == self.review_index:
                    try:
                        start_sq = square_on(best[0:2])
                        end_sq = square_on(best[2:4])
                        self.review_arrow = (start_sq, end_sq)
                    except Exception:
                        self.review_arrow = None

    def _start_review_analysis_thread(self, start_board: chess.Board, moves: list[chess.Move]) -> None:
        # Fire-and-forget analysis thread to compute ACPL for both sides.
        if self.stockfish_review_analysis is None:
            self.review_analysis_progress = None
            return

        def eval_to_cp(e) -> int:
            if not isinstance(e, dict):
                return 0
            if e.get('type') == 'cp':
                try:
                    return int(e.get('value', 0))
                except Exception:
                    return 0
            # Mate: treat as a large, but bounded, value to avoid ACPL explosions.
            try:
                mv = int(e.get('value', 0))
            except Exception:
                mv = 0
            return 10000 if mv > 0 else -10000

        def clamp_loss(cp: int, max_loss: int = 500) -> int:
            try:
                return max(0, min(int(cp), int(max_loss)))
            except Exception:
                return 0

        def loss_unclamped(cp: int, cap: int = 2000) -> int:
            # For move quality categories we want a larger dynamic range than ACPL,
            # but still bounded so mate scores don't explode.
            try:
                return max(0, min(int(cp), int(cap)))
            except Exception:
                return 0

        def classify_move(
            ply_index: int,
            side_to_move_white: bool,
            before_eval: int,
            played_eval: int,
            best_eval: int,
            played_uci: str,
            best_uci: str | None,
        ) -> str:
            # Eval values are treated as centipawns from White's perspective.
            if side_to_move_white:
                loss = loss_unclamped(best_eval - played_eval)
                gain = played_eval - before_eval
                abs_before = abs(before_eval)
            else:
                loss = loss_unclamped(played_eval - best_eval)
                gain = before_eval - played_eval
                abs_before = abs(before_eval)

            # Best move: played move matches engine best move from this position.
            if best_uci and str(played_uci) == str(best_uci):
                return 'Best'

            # Book move (heuristic): early game and close to best.
            if ply_index <= 15 and loss <= 20 and abs_before <= 200:
                return 'Book'

            # Great / Amazing: close to best and creates a notable advantage swing.
            # (Heuristic approximation of chess.com style labels.)
            if loss <= 10 and gain >= 180 and abs_before <= 250:
                return 'Amazing'
            if loss <= 25 and gain >= 100 and abs_before <= 300:
                return 'Great'

            # Good: close to best but not a notable swing.
            if loss <= 15:
                return 'Good'

            # Mistake / Blunder based on eval loss.
            if loss >= 250:
                return 'Blunder'
            if loss >= 120:
                return 'Mistake'
            return ''

        def accuracy_from_acpl(acpl: float) -> float:
            # Heuristic mapping: 0 ACPL -> 100%, ~100 ACPL -> ~67%, 500 ACPL -> ~14%.
            try:
                a = float(acpl)
            except Exception:
                a = 0.0
            a = max(0.0, a)
            acc = 100.0 * math.exp(-0.004 * a)
            return max(0.0, min(100.0, acc))

        def worker():
            try:
                self.stockfish_review_analysis.update_engine_parameters({"UCI_LimitStrength": "false"})
            except Exception:
                pass
            try:
                self.stockfish_review_analysis.set_skill_level(20)
                self.stockfish_review_analysis.set_depth(12)
            except Exception:
                pass

            board = start_board.copy(stack=False)
            white_losses: list[int] = []
            black_losses: list[int] = []
            move_labels: list[str] = []
            total = max(1, len(moves))

            for i, mv in enumerate(moves):
                if not self.review_active:
                    return
                fen_before = board.fen()
                try:
                    self.stockfish_review_analysis.set_fen_position(fen_before)
                    try:
                        before_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                    except Exception:
                        before_eval = 0
                    best_uci = self.stockfish_review_analysis.get_best_move_time(150)
                except Exception:
                    before_eval = 0
                    best_uci = None

                # Evaluate played move result
                played_board = chess.Board(fen_before)
                try:
                    played_board.push(mv)
                except Exception:
                    pass
                try:
                    self.stockfish_review_analysis.set_fen_position(played_board.fen())
                    played_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                except Exception:
                    played_eval = 0

                # Evaluate best move result
                best_eval = played_eval
                if best_uci:
                    best_board = chess.Board(fen_before)
                    try:
                        best_board.push_uci(str(best_uci))
                    except Exception:
                        pass
                    try:
                        self.stockfish_review_analysis.set_fen_position(best_board.fen())
                        best_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                    except Exception:
                        best_eval = played_eval

                # Side to move at fen_before wants best_eval according to its objective.
                if board.turn == chess.WHITE:
                    loss = clamp_loss(best_eval - played_eval)
                    white_losses.append(int(loss))
                    move_labels.append(classify_move(i, True, before_eval, played_eval, best_eval, str(mv.uci()), str(best_uci) if best_uci else None))
                else:
                    loss = clamp_loss(played_eval - best_eval)
                    black_losses.append(int(loss))
                    move_labels.append(classify_move(i, False, before_eval, played_eval, best_eval, str(mv.uci()), str(best_uci) if best_uci else None))

                board.push(mv)
                self.review_analysis_progress = min(1.0, (i + 1) / total)

            self.review_acpl_white = (sum(white_losses) / max(1, len(white_losses)))
            self.review_acpl_black = (sum(black_losses) / max(1, len(black_losses)))
            self.review_accuracy_white = accuracy_from_acpl(self.review_acpl_white)
            self.review_accuracy_black = accuracy_from_acpl(self.review_acpl_black)
            self.review_accuracy_overall = (self.review_accuracy_white + self.review_accuracy_black) / 2.0
            try:
                self.review_move_labels = move_labels
            except Exception:
                pass
            self.review_analysis_progress = None

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def draw_pieces(self, piece_selected: Piece = None):
        """
        Draws all the pieces and the selected piece last so that it appears on top.
        Also draws the arrows.
        :param piece_selected:
        :return:
        """
        for piece in self.all_pieces:
            if piece != piece_selected:
                piece.draw(self.offset, self.screen, self.size, self.flipped)

        # Draw the piece last, if it is being clicked/dragged
        if piece_selected is not None:
            piece_selected.draw(self.offset, self.screen, self.size, False)

        self.draw_arrows()

    def draw_arrows(self):
        if self.hint_arrow is not None:
            old_colour = self.arrow_colour
            self.arrow_colour = self.hint_arrow_colour
            self._draw_arrow_set([self.hint_arrow])
            self.arrow_colour = old_colour

        self._draw_arrow_set(self.arrows)

    def _draw_arrow_set(self, arrows):
        off = (self.offset[0] + self.size / 2, self.offset[1] + self.size / 2)
        for start, end in arrows:
            diff = (end[0] - start[0], end[1] - start[1])
            surface = pg.Surface((pg.display.get_window_size()[0], pg.display.get_window_size()[1]), pg.SRCALPHA)
            surface.set_alpha(200)
            angle = math.atan2(((off[1] + self.size * start[0]) - (off[1] + self.size * end[0])),
                               ((off[0] + self.size * start[1]) - (off[0] + self.size * end[1])))
            # Knight arrows !
            if diff in self.knight_moves:
                if diff[0] in [2, -2]:
                    if self.flipped:
                        pg.draw.line(surface, self.arrow_colour,
                                     (off[0] + self.size * (7 - start[1]), off[1] + self.size * (7 - start[0])),
                                     (off[0] + self.size * (7 - start[1]), off[1] + self.size * (7 - start[0] - diff[0]) - (0.5*diff[0]*(int(self.size/6)/2))),
                                     int(self.size / 6))
                        pg.draw.line(surface, self.arrow_colour,
                                     (off[0] + self.size * (7 - start[1]),
                                      off[1] + self.size * (7 - start[0] - diff[0])),
                                     (off[0] + self.size * (7 - start[1] - diff[1]) + self.size*diff[1]/5,
                                      off[1] + self.size * (7 - start[0] - diff[0])),
                                     int(self.size / 6))
                        end_pos = (off[0] + self.size * (7 - start[1] - diff[1]),
                                      off[1] + self.size * (7 - start[0] - diff[0]))
                        angle = math.atan2(0, -diff[1])
                        pg.draw.polygon(surface,
                                        self.arrow_colour,
                                        [end_pos,
                                         (end_pos[0] - math.cos(angle + math.radians(35)) * self.size / 3,
                                          end_pos[1] - math.sin(angle + math.radians(35)) * self.size / 3),
                                         (end_pos[0] - math.cos(angle - math.radians(35)) * self.size / 3,
                                          end_pos[1] - math.sin(angle - math.radians(35)) * self.size / 3)])
                    else:
                        pg.draw.line(surface, self.arrow_colour,
                                     (off[0] + self.size * start[1], off[1] + self.size * start[0]),
                                     (off[0] + self.size * start[1], off[1] + self.size * (start[0] + diff[0]) + (0.5*diff[0]*(int(self.size/6)/2))),
                                     int(self.size / 6))
                        pg.draw.line(surface, self.arrow_colour,
                                     (off[0] + self.size * start[1], off[1] + self.size * (start[0] + diff[0])),
                                     (off[0] + self.size * (start[1] + diff[1]) - self.size*diff[1]/5, off[1] + self.size * (start[0] + diff[0])),
                                     int(self.size / 6))
                        end_pos = (off[0] + self.size * (start[1] + diff[1]), off[1] + self.size * (start[0] + diff[0]))
                        angle = math.atan2(0, diff[1])
                        pg.draw.polygon(surface,
                                        self.arrow_colour,
                                        [end_pos,
                                         (end_pos[0] - math.cos(angle + math.radians(35)) * self.size / 3,
                                          end_pos[1] - math.sin(angle + math.radians(35)) * self.size / 3),
                                         (end_pos[0] - math.cos(angle - math.radians(35)) * self.size / 3,
                                          end_pos[1] - math.sin(angle - math.radians(35)) * self.size / 3)])

                else:
                    if self.flipped:
                        pg.draw.line(surface, self.arrow_colour,
                                     (off[0] + self.size * (7 - start[1]), off[1] + self.size * (7 - start[0])),
                                     (off[0] + self.size * (7 - start[1] - diff[1]) - (0.5*diff[1]*(int(self.size/6)/2)),
                                      off[1] + self.size * (7 - start[0])),
                                     int(self.size / 6))
                        pg.draw.line(surface, self.arrow_colour,
                                     (off[0] + self.size * (7 - start[1] - diff[1]),
                                      off[1] + self.size * (7 - start[0])),
                                     (off[0] + self.size * (7 - start[1] - diff[1]),
                                      off[1] + self.size * (7 - start[0] - diff[0]) + self.size*diff[0]/5),
                                     int(self.size / 6))
                        end_pos = (off[0] + self.size * (7 - start[1] - diff[1]),
                                      off[1] + self.size * (7 - start[0] - diff[0]))
                        angle = math.atan2(-diff[0], 0)
                        pg.draw.polygon(surface,
                                        self.arrow_colour,
                                        [end_pos,
                                         (end_pos[0] - math.cos(angle + math.radians(35)) * self.size / 3,
                                          end_pos[1] - math.sin(angle + math.radians(35)) * self.size / 3),
                                         (end_pos[0] - math.cos(angle - math.radians(35)) * self.size / 3,
                                          end_pos[1] - math.sin(angle - math.radians(35)) * self.size / 3)])
                    else:
                        pg.draw.line(surface, self.arrow_colour,
                                     (off[0] + self.size * start[1], off[1] + self.size * start[0]),
                                     (off[0] + self.size * (start[1] + diff[1]) + (0.5*diff[1]*(int(self.size/6)/2)), off[1] + self.size * start[0]),
                                     int(self.size / 6))
                        pg.draw.line(surface, self.arrow_colour,
                                     (off[0] + self.size * (start[1] + diff[1]), off[1] + self.size * start[0]),
                                     (off[0] + self.size * (start[1] + diff[1]), off[1] + self.size * (start[0] + diff[0]) - self.size*diff[0]/5),
                                     int(self.size / 6))
                        end_pos = (off[0] + self.size * (start[1] + diff[1]), off[1] + self.size * (start[0] + diff[0]))
                        angle = math.atan2(diff[0], 0)
                        pg.draw.polygon(surface,
                                        self.arrow_colour,
                                        [end_pos,
                                         (end_pos[0] - math.cos(angle + math.radians(35)) * self.size / 3,
                                          end_pos[1] - math.sin(angle + math.radians(35)) * self.size / 3),
                                         (end_pos[0] - math.cos(angle - math.radians(35)) * self.size / 3,
                                          end_pos[1] - math.sin(angle - math.radians(35)) * self.size / 3)])
            # all other arrows
            else:
                if self.flipped:
                    # angle = math.atan2(((off[0] + self.size * (7 - start[1])) - (off[0] + self.size * (7 - end[1]))), ((off[1] + self.size * (7 - start[0])) - (off[1] + self.size * (7 - end[0]))))
                    pg.draw.line(surface, self.arrow_colour,
                                 (off[0] + self.size * (7 - start[1]), off[1] + self.size * (7 - start[0])),
                                 (off[0] + self.size * (7 - end[1]) - self.size*math.cos(angle)/5, off[1] + self.size * (7 - end[0]) - self.size*math.sin(angle)/5), int(self.size/6))
                    end_pos = (off[0] + self.size * (7 - end[1]), off[1] + self.size * (7 - end[0]))
                    pg.draw.polygon(surface,
                                    self.arrow_colour,
                                    [end_pos,
                                     (end_pos[0] - math.cos(angle + math.radians(35)) * self.size / 3,
                                      end_pos[1] - math.sin(angle + math.radians(35)) * self.size / 3),
                                     (end_pos[0] - math.cos(angle - math.radians(35)) * self.size / 3,
                                      end_pos[1] - math.sin(angle - math.radians(35)) * self.size / 3)])
                else:
                    pg.draw.line(surface, self.arrow_colour, (off[0] + self.size * start[1], off[1] + self.size * start[0]),
                                 (off[0] + self.size * end[1] + self.size*math.cos(angle)/5, off[1] + self.size * end[0] + self.size*math.sin(angle)/5),  int(self.size/6))
                    end_pos = (off[0] + self.size * end[1], off[1] + self.size * end[0])
                    pg.draw.polygon(surface,
                                    self.arrow_colour,
                                    [end_pos,
                                     (end_pos[0] + math.cos(angle + math.radians(35)) * self.size / 3,
                                      end_pos[1] + math.sin(angle + math.radians(35)) * self.size / 3),
                                     (end_pos[0] + math.cos(angle - math.radians(35)) * self.size / 3,
                                      end_pos[1] + math.sin(angle - math.radians(35)) * self.size / 3)])

            self.screen.blit(surface, (0, 0))
    
    def flip_enable(self, value):
        if value == 1:
            self.flip_enabled = True
        else:
            self.flip_enabled = False

    def sounds_enable(self, value):
        if value == 1:
            self.sound_enabled = True
        else:
            self.sound_enabled = False

    def flip_board(self):
        self.flipped = not self.flipped
