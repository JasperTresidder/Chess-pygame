import datetime
import json
import math
import io
import os
import re
import random
import sys
import subprocess
import threading
import time
import queue
import contextlib

def _enable_stockfish_no_console_windows() -> None:
    """On Windows, prevent Stockfish subprocesses from spawning console windows.

    This is especially important for PyInstaller GUI builds where each UCI engine
    process may otherwise create its own visible console window.
    """
    try:
        if os.name != 'nt':
            return
    except Exception:
        return

    try:
        orig_popen = subprocess.Popen
    except Exception:
        return

    # If someone already patched Popen, don't patch again.
    try:
        if getattr(subprocess.Popen, '__name__', '') == 'PopenNoWindow':
            return
    except Exception:
        pass

    def looks_like_stockfish_cmd(cmd) -> bool:
        try:
            if cmd is None:
                return False
            if isinstance(cmd, (list, tuple)) and cmd:
                head = str(cmd[0])
            else:
                head = str(cmd)
            head_l = head.lower()
            return 'stockfish' in head_l and head_l.endswith('.exe')
        except Exception:
            return False

    # IMPORTANT: asyncio on Windows subclasses subprocess.Popen.
    # So we must keep subprocess.Popen as a *class*, not replace it with a function.
    class PopenNoWindow(orig_popen):
        def __init__(self, *popenargs, **kwargs):
            try:
                cmd = popenargs[0] if popenargs else kwargs.get('args')
                if looks_like_stockfish_cmd(cmd):
                    if 'creationflags' not in kwargs:
                        kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                    if 'startupinfo' not in kwargs:
                        si = subprocess.STARTUPINFO()
                        si.dwFlags |= getattr(subprocess, 'STARTF_USESHOWWINDOW', 1)
                        si.wShowWindow = getattr(subprocess, 'SW_HIDE', 0)
                        kwargs['startupinfo'] = si
            except Exception:
                pass
            super().__init__(*popenargs, **kwargs)

    subprocess.Popen = PopenNoWindow


_enable_stockfish_no_console_windows()


from src.engine.settings import SettingsMenu, EndGameMenu
from src.functions.fen import *
import pygame as pg
from src.functions.timer import *
from src.pieces.queen import Queen
from src.pieces.base import Piece
from stockfish import Stockfish
import chess
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

        # Puzzle Rush mode state
        self.puzzle_rush_active: bool = False
        self._puzzle_rush_pack_path: str | None = None
        self._puzzle_rush_puzzles: list[dict] = []
        self._puzzle_rush_index: int = 0
        self._puzzle_rush_solved: int = 0
        self._puzzle_rush_strikes: int = 0
        self._puzzle_rush_user_side: str = 'w'
        self._puzzle_rush_expected_uci: list[str] = []
        self._puzzle_rush_expected_i: int = 0
        self._puzzle_rush_autoplay: bool = False
        self._puzzle_rush_reply_delay_s: float = 0.5
        self._puzzle_rush_reply_delay_fast_s: float = 0.05
        self._puzzle_rush_pending_uci: str = ''
        self._puzzle_rush_pending_due_ts: float | None = None
        self._puzzle_rush_pending_advance: int = 0
        self._puzzle_rush_results: list[tuple[int, bool]] = []  # (rating, success)
        self._puzzle_rush_current_rating: int = 0
        self._puzzle_rush_highscore: int = 0
        self._puzzle_rush_highscore_path: str = os.path.join('data', 'settings', 'puzzle_rush_highscore.txt')
        self.evaluation = ''
        self.best_move = ''
        self.end_popup_active = False
        self.end_popup_text = ''
        self.end_popup_pgn_path: str | None = None

        # Premove UX
        self._premove_autoplay: bool = False

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
        # Depth used for review move-quality/ACPL analysis (Stockfish set_depth).
        # Default is intentionally modest so review stays responsive.
        self.review_analysis_depth_default: int = 10
        self.review_analysis_depth: int = int(self.review_analysis_depth_default)
        self.review_last_error: str = ''
        # Opening database (book) info aligned with review plies.
        self.review_opening_names: list[str] = []
        self._review_opening_rect: pg.Rect | None = None
        self._review_opening_suggestion_hitboxes: list[tuple[pg.Rect, str, str]] = []  # (rect, name, uci_line)
        self._review_opening_suggest_more_rect: pg.Rect | None = None
        self._review_opening_suggest_key: tuple[tuple[str, ...], str] = (tuple(), '')
        self._review_opening_suggest_offset: int = 0
        self.review_move_scroll: int = 0
        # Separate scroll for the top (stats + PV) area in review.
        self.review_top_scroll: int = 0
        # User-adjustable height for review top panel (stats + PV), in pixels.
        # If None, defaults to 30% of the screen height.
        self.review_top_h_px: int | None = None
        self._review_move_hitboxes: list[tuple[pg.Rect, int]] = []
        self._review_pv_hitboxes: list[tuple[pg.Rect, dict, int]] = []  # (rect, pv_item, move_index)
        self._review_variation_hitboxes: list[tuple[pg.Rect, int, int]] = []  # (rect, base_index, cursor_index)
        self._review_top_rect: pg.Rect | None = None
        self._review_splitter_rect: pg.Rect | None = None
        self._review_split_dragging: bool = False
        self._review_reanalyze_btn_rect: pg.Rect | None = None
        self._review_depth_minus_rect: pg.Rect | None = None
        self._review_depth_plus_rect: pg.Rect | None = None

        # Analysis mode (free analysis with move annotations).
        self.analysis_active: bool = False
        self.analysis_path: str | None = None
        self.analysis_name: str = ''
        self.analysis_fens: list[str] = []
        self.analysis_plies: list[chess.Move] = []
        self.analysis_sans: list[str] = []
        self.analysis_move_labels: list[str] = []
        self.analysis_opening_names: list[str] = []
        self._analysis_opening_rect: pg.Rect | None = None
        self._analysis_opening_suggestion_hitboxes: list[tuple[pg.Rect, str, str]] = []  # (rect, name, uci_line)
        self._analysis_opening_suggest_more_rect: pg.Rect | None = None
        self._analysis_opening_suggest_key: tuple[tuple[str, ...], str] = (tuple(), '')
        self._analysis_opening_suggest_offset: int = 0
        self.analysis_index: int = 0
        self.analysis_move_scroll: int = 0
        # Separate scroll for the top (stats + PV) area in analysis.
        self.analysis_top_scroll: int = 0
        # User-adjustable height for analysis top panel (stats + PV), in pixels.
        # If None, defaults to 30% of the screen height.
        self.analysis_top_h_px: int | None = None
        self.analysis_progress: float | None = None
        self.analysis_last_error: str = ''
        self.analysis_last_saved_path: str = ''
        self._analysis_move_hitboxes: list[tuple[pg.Rect, int]] = []
        self._analysis_pv_hitboxes: list[tuple[pg.Rect, dict, int]] = []
        self._analysis_variation_hitboxes: list[tuple[pg.Rect, int, int]] = []
        self._analysis_top_rect: pg.Rect | None = None
        self._analysis_splitter_rect: pg.Rect | None = None
        self._analysis_split_dragging: bool = False
        self._analysis_run_id: int = 0
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

        # Player color for Player vs AI. Default is white.
        self.player_colour: str = 'w'

        # Cache: prefix-uci tuple -> list of (name, uci_line, line_len).
        self._opening_suggestions_cache: dict[tuple[str, ...], list[tuple[str, str, int]]] = {}

        # Cache: exact prefix -> best (name, line_len) for that prefix.
        # Used to label "Book" by move-sequence prefix in Analysis (even without exact EPD/FEN match).
        self._openings_prefix_best: dict[tuple[str, ...], tuple[str, int]] = {}
        self._openings_prefix_best_ready: bool = False

        # Load persisted player colour (saved by StartMenu/SettingsMenu). This is separate from
        # SettingsMenu widgets (which do not expose this option).
        try:
            with open('data/settings/settings.txt', 'r') as f:
                _lines = f.readlines()
            # Backward compatibility:
            # - Old format (<=10 lines): index 9 stored player colour ('w'/'b')
            # - New format (>=11 lines): index 9 stores review analysis depth, index 10 stores player colour
            if len(_lines) >= 10:
                c9 = str(_lines[9]).strip().lower()
                if c9 in ('w', 'b'):
                    self.player_colour = 'b' if c9.startswith('b') else 'w'
                else:
                    # Newer files store colour at index 10.
                    if len(_lines) >= 11:
                        c = str(_lines[10]).strip().lower()
                        self.player_colour = 'b' if c.startswith('b') else 'w'

            # Load default review analysis depth (new format index 9), keep safe bounds.
            if len(_lines) >= 10:
                raw = str(_lines[9]).strip().lower()
                if raw not in ('w', 'b'):
                    try:
                        d = int(raw)
                        self.review_analysis_depth_default = max(6, min(20, int(d)))
                        self.review_analysis_depth = int(self.review_analysis_depth_default)
                    except Exception:
                        pass
        except Exception:
            pass

        # Time control / chess clock.
        self.time_control: str = '5|0'  # minutes|incrementSeconds
        self._tc_base_seconds: int = 5 * 60
        self._tc_increment_seconds: int = 0
        self._clock_white: float = float(self._tc_base_seconds)
        self._clock_black: float = float(self._tc_base_seconds)
        self._clock_running: bool = False
        self._clock_last_ts: float | None = None
        self._clock_font = None

        # Board sizing: window-fit base size * user scale (drag handle).
        # Default slightly smaller so clocks/labels never overlap.
        self._board_user_scale: float = 0.92
        self._board_base_size: int = int(getattr(self, 'size', 80)) if hasattr(self, 'size') else 80
        self._board_resize_active: bool = False
        self._board_resize_start_mouse: tuple[int, int] | None = None
        self._board_resize_start_size: int | None = None
        self._board_resize_handle_rect: pg.Rect | None = None

        # In-game move browsing (left/right arrows) for the current game.
        self._game_browse_active: bool = False
        self._game_browse_index: int | None = None  # index into game_fens
        self._game_browse_saved_fen: str | None = None
        self._game_browse_saved_clock_running: bool = False
        self._game_browse_live_turn: str | None = None

        # In-game action buttons
        self._undo_btn_rect: pg.Rect | None = None
        self._resign_btn_rect: pg.Rect | None = None
        self._toggle_movelist_btn_rect: pg.Rect | None = None

        # Normal-game move list (right side). Hidden via toggle button.
        self.game_movelist_visible: bool = True
        self._game_san_cache_n: int = -1
        self._game_san_cache: list[str] = []

        # Serialize access to the main playing Stockfish instance.
        # (Eval/review use separate Stockfish processes.)
        self._play_engine_lock = threading.RLock()

        # Async AI moves so the UI stays responsive (needed for premoves).
        self._ai_move_queue: 'queue.Queue[str]' = queue.Queue(maxsize=1)
        self._ai_stop = threading.Event()
        self._ai_thread: threading.Thread | None = None
        self.ai_thinking: bool = False

        # Premove state (Player vs AI): stored as UCI and highlighted squares.
        # Support multiple queued premoves (like chess.com).
        # Each entry: (uci, (from_row, from_col), (to_row, to_col), piece_ref)
        self._premove_queue: list[tuple[str, tuple[int, int], tuple[int, int], Piece]] = []
        self._premove_squares: list[tuple[int, int]] = []
        # Visual-only: show the next premove (first in queue) by snapping the piece.
        self._premove_from: tuple[int, int] | None = None
        self._premove_to: tuple[int, int] | None = None
        self._premove_piece: Piece | None = None
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
                                           # Use sane defaults. Strength is applied via _apply_ai_strength().
                                           depth=18,
                                           parameters={"Threads": 1, "Minimum Thinking Time": 20, "Hash": 64,
                                                       "Skill Level": 20,
                                                       "UCI_LimitStrength": "false",
                                                       "UCI_Elo": 1350})
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

        # Extra Stockfish instances are created lazily. This avoids spawning multiple
        # Stockfish console processes on startup (especially visible in PyInstaller builds).
        self.stockfish_eval = None
        self.stockfish_review = None
        self.stockfish_review_analysis = None

        # Review analysis (interactive variations) + top-lines (PV) analysis.
        self.stockfish_review_pv = None
        self._review_pv_queue: queue.Queue[str] = queue.Queue(maxsize=1)
        # NOTE: keep UI responsive by never holding the UI lock while Stockfish is thinking.
        self._review_pv_lock = threading.Lock()  # protects review_pv_lines/review_pv_pending
        self._review_pv_engine_lock = threading.Lock()  # protects stockfish_review_pv access
        self._review_pv_stop = threading.Event()
        self._review_pv_thread: threading.Thread | None = None
        # Published from PV worker thread; render reads snapshots.
        # Each item: {rank:int, eval:{type:cp|mate,value:int}, moves:[san...], fens:[fen_after_each_ply...]}
        self.review_pv_lines: list[dict] = []
        self.review_pv_pending: bool = False
        # Render-thread cache to avoid flicker when lock is contended.
        self._review_pv_render_cache: list[dict] = []
        self._review_pv_render_pending: bool = False

        # Persistent Top-lines iterative deepening.
        # Cache per-FEN depth so revisiting a position continues from the last depth reached.
        self._pv_depth_by_fen: dict[str, int] = {}
        # In-RAM cache of the most recently published PV lines per FEN.
        # This lets us show Top lines instantly when revisiting a position.
        self._pv_lines_by_fen: dict[str, list[dict]] = {}
        # Best-effort: track the depth currently being searched for a given FEN.
        # Used only for displaying a small depth indicator in the UI.
        self._pv_inflight_depth_by_fen: dict[str, int] = {}
        self._pv_min_depth: int = 8
        self._pv_max_depth: int = 20
        self._pv_depth_step: int = 2
        # Only start deeper PV searches if the displayed position has been stable
        # for a short moment. This avoids spending time deepening the previous
        # position when the user is quickly stepping through moves.
        self._pv_stable_delay_s: float = 0.35

        # PV responsiveness: when jumping to a brand-new position, we can be blocked
        # on an in-flight engine call for the previous position (the Stockfish wrapper
        # is synchronous). To keep UI feeling snappy, we clear stale PV lines immediately
        # and (for unseen positions) force-restart the PV engine process to interrupt
        # any in-flight search.
        self._review_pv_last_requested_fen: str = ''
        self._pv_last_interrupt_ts: float = 0.0
        self._pv_interrupt_cooldown_s: float = 0.20

        # Review-mode drag uses the same tx/ty + updates flag as the main game.

        self._review_analysis_active: bool = False
        self._review_analysis_base_index: int = 0
        self._review_analysis_fen: str = ''
        self._review_analysis_cursor: int = 0  # 0=base position, 1..N=variation ply index
        self._review_variations: dict[int, list[str]] = {}
        self._review_variation_fens: dict[int, list[str]] = {}
        self._review_variation_ucis: dict[int, list[str]] = {}
        self._review_variation_labels: dict[int, list[str]] = {}
        self._review_variation_analysis_run_ids: dict[int, int] = {}

        # Openings database index (EPD -> {name,eco,...}). Loaded lazily.
        self._openings_epd_index: dict[str, dict] | None = None
        self._openings_lock = threading.Lock()

        # Opening-line-as-variation state (used by both review and analysis).
        self._opening_variation_active: bool = False
        self._opening_variation_name: str = ''
        self._opening_variation_return_review_index: int = 0
        self._opening_variation_return_analysis_index: int = 0

        self._review_queue: queue.Queue[tuple[str, int]] = queue.Queue(maxsize=1)
        self._review_lock = threading.Lock()
        self._review_stop = threading.Event()
        self._review_thread: threading.Thread | None = None

        # Review analysis (ACPL/accuracy) runs in a background thread and uses a shared
        # Stockfish instance. If we start multiple analyses (e.g. open multiple PGNs
        # quickly), they can contend and slow the app. Use a run-id to cancel stale
        # threads and a lock to serialize engine access.
        self._review_analysis_run_id: int = 0
        self._review_analysis_engine_lock = threading.Lock()

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
        self._eval_thread: threading.Thread | None = None

        # Don't start eval/review worker threads until their engines are created.

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

        # Start large, but not monitor-sized (avoid a "can't exit fullscreen" feel).
        try:
            desktop_w, desktop_h = pg.display.get_desktop_sizes()[0]
        except Exception:
            try:
                info = pg.display.Info()
                desktop_w, desktop_h = int(info.current_w), int(info.current_h)
            except Exception:
                desktop_w, desktop_h = 1280, 720
        try:
            start_w = int(max(960, min(int(desktop_w) - 80, int(desktop_w * 0.92))))
            start_h = int(max(640, min(int(desktop_h) - 80, int(desktop_h * 0.92))))
        except Exception:
            start_w, start_h = 1280, 720
        try:
            # Best-effort: center window on startup (SDL2).
            cx = max(0, (int(desktop_w) - int(start_w)) // 2)
            cy = max(0, (int(desktop_h) - int(start_h)) // 2)
            os.environ['SDL_VIDEO_WINDOW_POS'] = f"{int(cx)},{int(cy)}"
        except Exception:
            pass
        self.screen = pg.display.set_mode((int(start_w), int(start_h)), pg.RESIZABLE, vsync=1)
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
        # Use a common font to avoid missing glyphs/odd sizing across systems.
        self.font = pg.font.SysFont('arial', 30)
        self.eval_font = pg.font.SysFont('arial', 18)
        self._clock_font = pg.font.SysFont('arial', 24)
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
        # When a drag is cancelled via right-click, a left-button release may still arrive.
        # Swallow that one-shot event so it doesn't select another piece.
        self._ignore_next_left_mouse_up = False
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
        # Clickable settings (cog) button rect in screen coords.
        self._settings_btn_rect: pg.Rect | None = None
        # Small flip-board button (near board bottom-right) for all modes.
        self._flip_board_btn_rect: pg.Rect | None = None

        # Kick off initial evaluation
        self.request_eval()

        # Load Puzzle Rush highscore
        try:
            self._puzzle_rush_highscore = int(self._puzzle_rush_load_highscore() or 0)
        except Exception:
            self._puzzle_rush_highscore = 0

        # Show a start popup on first frame.
        self._start_menu_shown = False

    def _open_settings_menu(self) -> None:
        """Open Settings using a fresh menu instance.

        The settings widgets (notably AI Elo) are initialized from disk defaults.
        Since the start menu can change those defaults after Engine init, we must
        recreate the SettingsMenu on each open to avoid showing stale values.
        """
        was_puzzle_rush = False
        try:
            was_puzzle_rush = bool(getattr(self, 'puzzle_rush_active', False))
        except Exception:
            was_puzzle_rush = False

        try:
            self.settings = SettingsMenu(
                title='Settings',
                width=self.screen.get_width(),
                height=self.screen.get_height(),
                surface=self.screen,
                parent=self,
                theme=pm.themes.THEME_DARK,
            )
        except Exception:
            return

        try:
            self.settings.run()
        except Exception:
            pass

        # If settings applied a mode change unintentionally, restore Puzzle Rush.
        try:
            if was_puzzle_rush and (not bool(getattr(self, 'puzzle_rush_active', False))):
                self.puzzle_rush_active = True
                self.player_vs_ai = False
                self.ai_vs_ai = False
        except Exception:
            pass

    def _clear_premove(self) -> None:
        self._premove_queue = []
        self._premove_squares = []
        self._premove_from = None
        self._premove_to = None
        self._premove_piece = None
        try:
            if self.selected_square is not None and self.player_vs_ai and (self.turn == self._ai_side()):
                self.selected_square = None
        except Exception:
            pass

    def _rebuild_premove_visuals(self) -> None:
        """Update derived premove highlight/visual fields from the queue."""
        squares: list[tuple[int, int]] = []
        try:
            for uci, frm, to, piece in self._premove_queue:
                squares.append(frm)
                squares.append(to)
        except Exception:
            squares = []

        # De-dup while preserving order.
        out: list[tuple[int, int]] = []
        seen = set()
        for s in squares:
            if s not in seen:
                seen.add(s)
                out.append(s)
        self._premove_squares = out

        if self._premove_queue:
            try:
                _, frm, to, piece = self._premove_queue[0]
                self._premove_from = frm
                self._premove_piece = piece
                # Visually snap the premoved piece to its *final* queued destination
                # (e.g. when chaining multiple premoves with the same piece).
                try:
                    self._premove_to = self._virtual_position_for_piece(piece)
                except Exception:
                    self._premove_to = to
            except Exception:
                self._premove_from = None
                self._premove_to = None
                self._premove_piece = None
        else:
            self._premove_from = None
            self._premove_to = None
            self._premove_piece = None

    def _virtual_position_for_piece(self, piece: Piece) -> tuple[int, int]:
        """Return the piece position after applying queued premoves (virtual board).

        Includes special handling for castling premoves (moves the rook too).
        """

        def castle_rook_squares(frm: tuple[int, int], to: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]] | None:
            try:
                fr, fc = int(frm[0]), int(frm[1])
                tr, tc = int(to[0]), int(to[1])
            except Exception:
                return None
            if fr != tr:
                return None
            if fc != 4:
                return None
            if abs(tc - fc) != 2:
                return None
            # Kingside: e->g (rook h->f). Queenside: e->c (rook a->d)
            if tc == 6:
                return (fr, 7), (fr, 5)
            if tc == 2:
                return (fr, 0), (fr, 3)
            return None

        try:
            base = (int(piece.position[0]), int(piece.position[1]))
        except Exception:
            base = (0, 0)

        # Simulate the premove queue on a virtual occupancy map.
        pos_by_piece: dict[Piece, tuple[int, int]] = {}
        sq_to_piece: dict[tuple[int, int], Piece] = {}
        try:
            for p in getattr(self, 'all_pieces', []) or []:
                try:
                    if p is None or getattr(p, 'dead', False):
                        continue
                    pr, pc = int(p.position[0]), int(p.position[1])
                    pos_by_piece[p] = (pr, pc)
                    sq_to_piece[(pr, pc)] = p
                except Exception:
                    continue

            for _uci, frm, to, p in getattr(self, '_premove_queue', []) or []:
                if p is None or p not in pos_by_piece:
                    continue
                frm_sq = (int(frm[0]), int(frm[1]))
                to_sq = (int(to[0]), int(to[1]))

                old_sq = pos_by_piece.get(p)
                if old_sq is not None and sq_to_piece.get(old_sq) == p:
                    sq_to_piece.pop(old_sq, None)

                victim = sq_to_piece.get(to_sq)
                if victim is not None and victim != p:
                    pos_by_piece.pop(victim, None)
                    sq_to_piece.pop(to_sq, None)

                pos_by_piece[p] = to_sq
                sq_to_piece[to_sq] = p

                # Castling premove: also move rook.
                try:
                    if str(getattr(p, 'piece', '')).lower() == 'k':
                        rook_sqs = castle_rook_squares(frm_sq, to_sq)
                        if rook_sqs is not None:
                            rook_from, rook_to = rook_sqs
                            rook_piece = sq_to_piece.get(rook_from)
                            if (
                                rook_piece is not None
                                and str(getattr(rook_piece, 'piece', '')).lower() == 'r'
                                and getattr(rook_piece, 'colour', '')[:1] == getattr(p, 'colour', '')[:1]
                            ):
                                sq_to_piece.pop(rook_from, None)
                                victim2 = sq_to_piece.get(rook_to)
                                if victim2 is not None and victim2 != rook_piece:
                                    pos_by_piece.pop(victim2, None)
                                    sq_to_piece.pop(rook_to, None)
                                pos_by_piece[rook_piece] = rook_to
                                sq_to_piece[rook_to] = rook_piece
                except Exception:
                    pass
        except Exception:
            return base

        return pos_by_piece.get(piece, base)

    def _build_virtual_player_piece_map(self) -> dict[tuple[int, int], Piece]:
        """Map virtual squares -> player's Piece objects (after applying queued premoves)."""
        # Use premove player side so Puzzle Rush (where the user can be Black) works correctly.
        side = self._premove_player_side()
        pos_by_piece: dict[Piece, tuple[int, int]] = {}
        sq_to_piece: dict[tuple[int, int], Piece] = {}

        try:
            for p in self.all_pieces:
                try:
                    if getattr(p, 'colour', '')[:1] != side:
                        continue
                    pr, pc = int(p.position[0]), int(p.position[1])
                    pos_by_piece[p] = (pr, pc)
                    sq_to_piece[(pr, pc)] = p
                except Exception:
                    continue
        except Exception:
            return {}

        def castle_rook_squares(frm: tuple[int, int], to: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]] | None:
            try:
                fr, fc = int(frm[0]), int(frm[1])
                tr, tc = int(to[0]), int(to[1])
            except Exception:
                return None
            if fr != tr:
                return None
            if fc != 4:
                return None
            if abs(tc - fc) != 2:
                return None
            if tc == 6:
                return (fr, 7), (fr, 5)
            if tc == 2:
                return (fr, 0), (fr, 3)
            return None

        try:
            for _uci, frm, to, p in self._premove_queue:
                if getattr(p, 'colour', '')[:1] != side:
                    continue

                frm_sq = (int(frm[0]), int(frm[1]))
                to_sq = (int(to[0]), int(to[1]))

                old = pos_by_piece.get(p)
                if old is not None and sq_to_piece.get(old) == p:
                    sq_to_piece.pop(old, None)

                victim = sq_to_piece.get(to_sq)
                if victim is not None and victim != p:
                    # Temporarily remove any piece that gets covered by a premove.
                    sq_to_piece.pop(to_sq, None)
                    pos_by_piece.pop(victim, None)

                pos_by_piece[p] = to_sq
                sq_to_piece[to_sq] = p

                # Castling premove moves the rook too.
                try:
                    if str(getattr(p, 'piece', '')).lower() == 'k':
                        rook_sqs = castle_rook_squares(frm_sq, to_sq)
                        if rook_sqs is not None:
                            rook_from, rook_to = rook_sqs
                            rook_piece = sq_to_piece.get(rook_from)
                            if (
                                rook_piece is not None
                                and str(getattr(rook_piece, 'piece', '')).lower() == 'r'
                                and getattr(rook_piece, 'colour', '')[:1] == side
                            ):
                                sq_to_piece.pop(rook_from, None)
                                victim2 = sq_to_piece.get(rook_to)
                                if victim2 is not None and victim2 != rook_piece:
                                    sq_to_piece.pop(rook_to, None)
                                    pos_by_piece.pop(victim2, None)
                                pos_by_piece[rook_piece] = rook_to
                                sq_to_piece[rook_to] = rook_piece
                except Exception:
                    pass
        except Exception:
            pass

        return sq_to_piece

    def _virtual_player_piece_at(self, row: int, col: int) -> Piece | None:
        try:
            return self._build_virtual_player_piece_map().get((int(row), int(col)))
        except Exception:
            return None

    def _pseudo_moves_for_piece(self, piece: Piece, from_row: int, from_col: int) -> tuple[list[tuple[int, int]], set[tuple[int, int]]]:
        """Pseudo legal moves on an empty board (no blockers, no checks)."""
        try:
            kind = str(getattr(piece, 'piece', '')).lower()
        except Exception:
            kind = ''

        positions: list[tuple[int, int]] = []
        captures: set[tuple[int, int]] = set()

        def add_square(r: int, c: int, cap: bool = True) -> None:
            if 0 <= r < 8 and 0 <= c < 8:
                dx = int(c) - int(from_col)
                dy = int(r) - int(from_row)
                positions.append((dx, dy))
                if cap:
                    captures.add((dx, dy))

        if kind == 'n':
            for dy, dx in [(2, 1), (2, -1), (-2, 1), (-2, -1), (1, 2), (1, -2), (-1, 2), (-1, -2)]:
                add_square(from_row + dy, from_col + dx, cap=True)
            return positions, captures

        if kind == 'k':
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    add_square(from_row + dy, from_col + dx, cap=True)

            # Allow premove castling (king moves two squares) when the king and rook
            # are still on their starting squares. Full legality is enforced later
            # when applying the premove using python-chess.
            try:
                side = getattr(piece, 'colour', '')[:1]
                start_row = 7 if side == 'w' else 0
                if int(from_row) == int(start_row) and int(from_col) == 4:
                    # Kingside rook at h-file.
                    rk = self.board[int(start_row)][7]
                    if (
                        rk != ' '
                        and rk is not None
                        and str(getattr(rk, 'piece', '')).lower() == 'r'
                        and getattr(rk, 'colour', '')[:1] == side
                    ):
                        add_square(from_row, from_col + 2, cap=False)

                    # Queenside rook at a-file.
                    rq = self.board[int(start_row)][0]
                    if (
                        rq != ' '
                        and rq is not None
                        and str(getattr(rq, 'piece', '')).lower() == 'r'
                        and getattr(rq, 'colour', '')[:1] == side
                    ):
                        add_square(from_row, from_col - 2, cap=False)
            except Exception:
                pass
            return positions, captures

        if kind == 'p':
            side = getattr(piece, 'colour', '')[:1]
            forward = -1 if side == 'w' else 1
            # forward moves (empty board)
            add_square(from_row + forward, from_col, cap=False)
            start_rank = 6 if side == 'w' else 1
            if int(from_row) == start_rank:
                add_square(from_row + 2 * forward, from_col, cap=False)
            # capture diagonals (what the pawn "sees")
            add_square(from_row + forward, from_col - 1, cap=True)
            add_square(from_row + forward, from_col + 1, cap=True)
            return positions, captures

        # Sliding pieces: rays to edge.
        directions: list[tuple[int, int]] = []
        if kind == 'b':
            directions = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
        elif kind == 'r':
            directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        elif kind == 'q':
            directions = [(1, 1), (1, -1), (-1, 1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)]

        for dy, dx in directions:
            for step in range(1, 8):
                r = from_row + dy * step
                c = from_col + dx * step
                if not (0 <= r < 8 and 0 <= c < 8):
                    break
                add_square(r, c, cap=True)

        return positions, captures

    def _capture_deltas_from_occupancy(
        self,
        from_row: int,
        from_col: int,
        deltas: list[tuple[int, int]],
        side: str,
    ) -> set[tuple[int, int]]:
        """Return dx/dy deltas that land on an occupied opponent square (current board only)."""
        out: set[tuple[int, int]] = set()
        try:
            for dx, dy in deltas:
                r = int(from_row) + int(dy)
                c = int(from_col) + int(dx)
                if not (0 <= r < 8 and 0 <= c < 8):
                    continue
                dst = self.board[r][c]
                if dst != ' ' and getattr(dst, 'colour', '')[:1] not in ('', side):
                    out.add((dx, dy))
        except Exception:
            return set()
        return out

    def _legal_moves_for_piece_side(self, row: int, col: int, side: str) -> tuple[list[tuple[int, int]], set[tuple[int, int]]]:
        """Compute legal move deltas for a piece at (row,col) for a given side (w/b) in the *current* position.

        This is used to show legal-move dots while premoving (AI to move), where Engine.update_legal_moves() only
        populated moves for the side to move.
        """
        fen = ''
        try:
            fen = self.game_fens[-1]
        except Exception:
            fen = self._current_fen()

        try:
            b = chess.Board(fen)
        except Exception:
            return [], set()

        try:
            b.turn = (side == 'w')
        except Exception:
            pass

        try:
            from_sq = self._chess_square_from_coords(row, col)
        except Exception:
            return [], set()

        positions: list[tuple[int, int]] = []
        captures: set[tuple[int, int]] = set()
        try:
            for mv in b.legal_moves:
                if mv.from_square != from_sq:
                    continue
                fr, fc = self._coords_from_chess_square(mv.from_square)
                tr, tc = self._coords_from_chess_square(mv.to_square)
                dx_dy = (tc - fc, tr - fr)
                positions.append(dx_dy)
                if b.is_capture(mv):
                    captures.add(dx_dy)
        except Exception:
            return [], set()

        return positions, captures

    def _ai_side(self) -> str:
        return 'b' if self.player_colour == 'w' else 'w'

    def _player_side(self) -> str:
        return self.player_colour if self.player_colour in ('w', 'b') else 'w'

    def _premove_player_side(self) -> str:
        if getattr(self, 'puzzle_rush_active', False):
            s = str(getattr(self, '_puzzle_rush_user_side', 'w') or 'w')
            return 'b' if s == 'b' else 'w'
        return self._player_side()

    def _premove_ai_side(self) -> str:
        s = self._premove_player_side()
        return 'b' if s == 'w' else 'w'

    def _premove_mode_active(self) -> bool:
        if self.review_active or getattr(self, 'analysis_active', False) or self.end_popup_active:
            return False
        if not (self.player_vs_ai or getattr(self, 'puzzle_rush_active', False)):
            return False
        try:
            return str(self.turn) == self._premove_ai_side()
        except Exception:
            return False

    @staticmethod
    def _format_clock(seconds: float) -> str:
        try:
            s = max(0, int(seconds + 0.0001))
        except Exception:
            s = 0
        m = s // 60
        sec = s % 60
        return f"{m}:{sec:02d}"

    @staticmethod
    def _parse_time_control_str(value: str) -> tuple[int, int, str]:
        """Parse 'min|inc' or 'min' into (baseSeconds, incSeconds, normalizedStr)."""
        s = str(value or '').strip()
        s = s.replace(' ', '')
        if not s:
            return 5 * 60, 0, '5|0'
        if '|' in s:
            mins_s, inc_s = s.split('|', 1)
        else:
            mins_s, inc_s = s, '0'
        try:
            mins = int(mins_s)
        except Exception:
            mins = 5
        try:
            inc = int(inc_s) if inc_s != '' else 0
        except Exception:
            inc = 0
        mins = max(0, mins)
        inc = max(0, inc)
        return mins * 60, inc, f"{mins}|{inc}"

    def set_time_control(self, value: str) -> None:
        base_s, inc_s, norm = self._parse_time_control_str(value)
        self.time_control = norm
        self._tc_base_seconds = int(base_s)
        self._tc_increment_seconds = int(inc_s)
        # Only reset clocks automatically if the game hasn't started yet.
        try:
            if len(self.game_fens) <= 1:
                self._reset_clocks(start_running=not self.end_popup_active and not self.review_active)
        except Exception:
            self._reset_clocks(start_running=False)

    def set_player_colour(self, colour: str) -> None:
        c = str(colour or '').lower().strip()
        self.player_colour = 'b' if c.startswith('b') else 'w'
        # In PvAI, default view should show the player's pieces at the bottom.
        if self.player_vs_ai:
            self.flipped = bool(self.player_colour == 'b')

    def _reset_clocks(self, start_running: bool = True) -> None:
        self._clock_white = float(self._tc_base_seconds)
        self._clock_black = float(self._tc_base_seconds)
        self._clock_running = bool(start_running)
        self._clock_last_ts = time.time()

    def _tick_clock(self) -> None:
        if getattr(self, 'puzzle_rush_active', False):
            return
        if not self._clock_running:
            return
        if self.review_active or self.end_popup_active or self.game_just_ended:
            return
        now = time.time()
        if self._clock_last_ts is None:
            self._clock_last_ts = now
            return
        dt = now - self._clock_last_ts
        self._clock_last_ts = now
        if dt <= 0:
            return

        # While browsing past moves we temporarily load historical FENs, which changes self.turn.
        # Keep ticking the live game's side-to-move so the clock doesn't appear to "stop".
        turn_for_clock = self.turn
        try:
            if getattr(self, '_game_browse_active', False) and self._game_browse_live_turn in ('w', 'b'):
                turn_for_clock = str(self._game_browse_live_turn)
        except Exception:
            turn_for_clock = self.turn

        if turn_for_clock == 'w':
            self._clock_white = max(0.0, self._clock_white - dt)
            if self._clock_white <= 0.0:
                self._clock_running = False
                self.end_game('TIMEOUT BLACK WINS !!')
        else:
            self._clock_black = max(0.0, self._clock_black - dt)
            if self._clock_black <= 0.0:
                self._clock_running = False
                self.end_game('TIMEOUT WHITE WINS !!')

    def _on_move_completed_clock(self, mover: str) -> None:
        """Apply increment to the side that just moved and restart tick baseline."""
        try:
            inc = int(self._tc_increment_seconds)
        except Exception:
            inc = 0
        if inc > 0:
            if mover == 'w':
                self._clock_white += inc
            else:
                self._clock_black += inc
        self._clock_last_ts = time.time()

    def _draw_clocks(self) -> None:
        if getattr(self, 'puzzle_rush_active', False):
            return
        if self.review_active or self.analysis_active:
            return
        try:
            font = self._clock_font or self.font
        except Exception:
            return
        board_rect = pg.Rect(int(self.offset[0]), int(self.offset[1]), int(self.size * 8), int(self.size * 8))
        margin = max(8, int(self.size * 0.12))
        box_h = max(26, int(self.size * 0.55))
        box_w = max(120, int(self.size * 2.2))

        def draw_box(side: str, seconds: float, x: int, y: int):
            txt = self._format_clock(seconds)
            fg = (220, 0, 0) if seconds < 10.0 else (230, 230, 230)
            bg = (20, 20, 20)
            rect = pg.Rect(x, y, box_w, box_h)
            try:
                pg.draw.rect(self.screen, bg, rect, border_radius=8)
                pg.draw.rect(self.screen, (80, 80, 80), rect, width=2, border_radius=8)
            except Exception:
                pass
            surf = font.render(txt, True, fg)
            self.screen.blit(surf, (rect.centerx - surf.get_width() // 2, rect.centery - surf.get_height() // 2))

        # Black clock above, white below. Keep white clock below file labels.
        x = board_rect.right - box_w
        y_black = max(6, board_rect.top - box_h - margin)
        label_h = int(self.size * (0.60 if self.show_numbers else 0.25))
        y_white = board_rect.bottom + label_h + margin
        # Safety clamp (should rarely hit because layout reserves space).
        y_white = min(self.screen.get_height() - box_h - 6, int(y_white))
        draw_box('b', float(self._clock_black), int(x), int(y_black))
        draw_box('w', float(self._clock_white), int(x), int(y_white))

    def _compute_normal_layout(self, win_w: int, win_h: int) -> tuple[int, list[float], bool, int]:
        """Compute (square_size, offset[x,y], show_numbers, base_size) for normal play.

        Reserves vertical space for clocks and file/rank labels so UI never overlaps.
        """
        show_numbers = True
        # Initial guess.
        guess = max(16, int(min(win_w, win_h) / 10))

        def compute_base(sz: int, show_nums: bool) -> int:
            margin = max(8, int(sz * 0.12))
            box_h = max(26, int(sz * 0.55))
            label_h = int(sz * (0.60 if show_nums else 0.25))
            left_label_w = (int(sz * 0.55) + 12) if show_nums else 18
            top_extra = box_h + margin + 6
            bottom_extra = label_h + margin + box_h + 6
            avail_w = max(120, int(win_w) - left_label_w - 18)
            avail_h = max(120, int(win_h) - top_extra - bottom_extra)
            return max(1, int(min(avail_w, avail_h) // 8))

        # Two-pass refinement.
        base = compute_base(guess, show_numbers)
        if base < 24:
            show_numbers = False
        base = compute_base(base, show_numbers)

        # Apply user scale but never exceed base.
        try:
            scale = float(self._board_user_scale)
        except Exception:
            scale = 0.92
        scale = max(0.45, min(1.0, scale))
        size = int(max(10, min(base, int(round(base * scale)))))

        # Recompute margins with final size.
        margin = max(8, int(size * 0.12))
        box_h = max(26, int(size * 0.55))
        label_h = int(size * (0.60 if show_numbers else 0.25))
        left_label_w = (int(size * 0.55) + 12) if show_numbers else 18
        top_extra = box_h + margin + 6
        bottom_extra = label_h + margin + box_h + 6

        board_px = size * 8
        # Center horizontally, but ensure room for left rank labels.
        x = (win_w - board_px) / 2
        x = max(left_label_w, min(x, win_w - board_px - 6))

        # Center vertically within the reserved band.
        y_min = float(top_extra)
        y_max = float(win_h - bottom_extra - board_px)
        y = (win_h - board_px) / 2
        y = max(y_min, min(y, y_max))

        return int(size), [float(x), float(y)], bool(show_numbers), int(base)

    def _play_premove_sound(self) -> None:
        if not self.sound_enabled:
            return
        try:
            pg.mixer.music.load('data/sounds/move.mp3')
            pg.mixer.music.play(1)
        except Exception:
            pass

    def _set_premove(self, from_row: int, from_col: int, to_row: int, to_col: int) -> None:
        """Store a premove (UCI) and highlight its start/end squares.

        Premove is allowed only while waiting for the AI (Player vs AI, black to move).
        """
        if not (self.player_vs_ai or getattr(self, 'puzzle_rush_active', False)) or self.review_active or self.end_popup_active:
            return
        if self.turn != self._premove_ai_side():
            return

        # Ignore drops outside the board.
        try:
            if not (0 <= int(to_row) < 8 and 0 <= int(to_col) < 8):
                return
        except Exception:
            return

        piece = self.board[from_row][from_col]
        if piece == ' ':
            # Allow selecting a premoved piece at its virtual square.
            piece = self._virtual_player_piece_at(from_row, from_col)
            if piece is None:
                return

        # Virtual from-square for this piece (supports premoving the same piece multiple times).
        try:
            from_row, from_col = self._virtual_position_for_piece(piece)
        except Exception:
            pass

        # Enforce pseudo-legal movement on an empty board (so premoves match what the piece
        # "would see" without blockers). This keeps drag-based premoves consistent too.
        try:
            pseudo_positions, _pseudo_caps = self._pseudo_moves_for_piece(piece, int(from_row), int(from_col))
            vdx = int(to_col) - int(from_col)
            vdy = int(to_row) - int(from_row)
            if (vdx, vdy) not in pseudo_positions:
                return
        except Exception:
            # If we can't validate, be conservative.
            return
        # Only premove your own pieces.
        player_side = self._premove_player_side()
        if getattr(piece, 'colour', '')[:1] != ('w' if player_side == 'w' else 'b'):
            return
        # Allow premoving onto squares currently occupied by your own pieces.
        # The premove will only execute if legal once the opponent moves.

        uci = translate_move(from_row, from_col, to_row, to_col)
        try:
            if piece.piece == 'P' and to_row == 0:
                uci += 'q'
        except Exception:
            pass

        # Allow queuing multiple premoves.
        try:
            if len(self._premove_queue) >= 5:
                return
        except Exception:
            pass

        try:
            self._premove_queue.append((str(uci), (from_row, from_col), (to_row, to_col), piece))
        except Exception:
            self._premove_queue = [(str(uci), (from_row, from_col), (to_row, to_col), piece)]

        self._rebuild_premove_visuals()
        self._play_premove_sound()

        # Puzzle Rush: if a delayed computer move is pending, make it very fast when the user premoves.
        try:
            if getattr(self, 'puzzle_rush_active', False) and str(getattr(self, '_puzzle_rush_pending_uci', '') or ''):
                fast = float(getattr(self, '_puzzle_rush_reply_delay_fast_s', 0.05) or 0.05)
                self._puzzle_rush_pending_due_ts = float(time.time()) + max(0.0, float(fast))
        except Exception:
            pass

    def _apply_premove_if_legal(self) -> None:
        """If the next premove is queued and is legal now, apply it. Otherwise discard all."""
        if not (self.player_vs_ai or getattr(self, 'puzzle_rush_active', False)) or self.review_active or self.end_popup_active:
            self._clear_premove()
            return
        if self.ai_thinking:
            return
        if not getattr(self, '_premove_queue', None):
            return
        # Only apply when it's the player's turn.
        if self.turn != self._premove_player_side():
            return

        fen = ''
        try:
            fen = self.game_fens[-1]
        except Exception:
            fen = self._current_fen()

        try:
            b = chess.Board(fen)
        except Exception:
            self._clear_premove()
            return

        try:
            uci = str(self._premove_queue[0][0])
        except Exception:
            self._clear_premove()
            return
        legal = False
        mv = None
        try:
            mv = chess.Move.from_uci(uci)
            legal = mv in b.legal_moves
        except Exception:
            legal = False

        # If promotion suffix is missing, try auto-queen.
        if not legal and len(uci) == 4:
            try:
                mv_q = chess.Move.from_uci(uci + 'q')
                if mv_q in b.legal_moves:
                    uci = uci + 'q'
                    mv = mv_q
                    legal = True
            except Exception:
                pass

        if not legal:
            # If the queued premove is no longer legal, drop the entire queue.
            self._clear_premove()
            return

        # Record in PGN + last-move markers, then apply on the internal board.
        try:
            self.last_move.append(uci)
        except Exception:
            self.last_move = [uci]
        try:
            self.node = self.node.add_variation(chess.Move.from_uci(uci))
        except Exception:
            pass

        self._premove_autoplay = True
        try:
            self.engine_make_move(uci)
        finally:
            self._premove_autoplay = False
        try:
            self._premove_queue.pop(0)
        except Exception:
            self._premove_queue = []
        self._rebuild_premove_visuals()

        # After a successful premove, immediately request the AI reply (PvAI only).
        if self.player_vs_ai and (not self.end_popup_active) and (not self.game_just_ended):
            self._request_ai_move_async()

    def _ensure_ai_thread(self) -> None:
        if self._ai_thread is not None and self._ai_thread.is_alive():
            return

        self._ai_stop.clear()

        def worker() -> None:
            while not self._ai_stop.is_set():
                try:
                    fen = self._ai_move_queue.get(timeout=0.25)
                except queue.Empty:
                    continue

                # Drain to latest
                try:
                    while True:
                        fen = self._ai_move_queue.get_nowait()
                except queue.Empty:
                    pass

                # Compute a move.
                move = None
                t0 = time.time()
                try:
                    with self._play_engine_lock:
                        try:
                            self.stockfish.set_fen_position(str(fen))
                        except Exception:
                            pass
                        move = self.move_strength(self.ai_elo)
                except Exception:
                    move = None
                t_compute = max(0.0, time.time() - t0)

                # Simulate human-like thinking time based on the game clock.
                # This is separate from Stockfish's internal movetime so we don't
                # accidentally make the engine stronger just by "thinking" longer.
                try:
                    delay_s = float(self._ai_delay_seconds_from_clock())
                except Exception:
                    delay_s = 0.0

                # Enforce a hard cap on total wall-clock time per AI move.
                # Total = engine compute time + delay.
                try:
                    delay_s = min(float(delay_s), max(0.0, 2.0 - float(t_compute)))
                except Exception:
                    delay_s = 0.0
                if delay_s > 0:
                    end_t = time.time() + delay_s
                    while (not self._ai_stop.is_set()) and time.time() < end_t:
                        time.sleep(0.02)

                # Publish the result back to the main thread.
                try:
                    if move:
                        if self._ai_move_queue.full():
                            # Note: this queue is reused; make sure it's empty for next request.
                            pass
                        # Use a separate attribute? Keep simple: stash result in a dedicated slot.
                except Exception:
                    pass

                try:
                    # Put result in a lightweight attribute for polling.
                    self._ai_result = str(move) if move else ''
                    self._ai_result_fen = str(fen)
                except Exception:
                    self._ai_result = str(move) if move else ''
                    self._ai_result_fen = ''

        self._ai_result = ''
        self._ai_result_fen = ''
        self._ai_thread = threading.Thread(target=worker, daemon=True)
        self._ai_thread.start()

    def _request_ai_move_async(self) -> None:
        """Request an AI move for the current position without blocking the UI."""
        if not self.player_vs_ai or self.review_active or self.end_popup_active:
            return
        if self.turn != self._ai_side():
            return
        self._ensure_ai_thread()
        fen = self._current_fen()
        self.ai_thinking = True
        try:
            self._ai_result = ''
            self._ai_result_fen = ''
        except Exception:
            pass
        try:
            if self._ai_move_queue.full():
                _ = self._ai_move_queue.get_nowait()
            self._ai_move_queue.put_nowait(fen)
        except Exception:
            pass

    def _poll_ai_result(self) -> None:
        """Apply a finished AI move (if any), then try to apply premove."""
        if not self.player_vs_ai or self.review_active or self.end_popup_active:
            return
        if not getattr(self, 'ai_thinking', False):
            return

        move = ''
        try:
            move = str(getattr(self, '_ai_result', '') or '')
        except Exception:
            move = ''
        if not move:
            return

        browsing = bool(getattr(self, '_game_browse_active', False))

        # If a stale result comes back after an undo/reset, ignore it.
        # While browsing, compare against the saved *live* FEN instead of the browsed FEN.
        try:
            fen_for_result = str(getattr(self, '_ai_result_fen', '') or '')
        except Exception:
            fen_for_result = ''
        try:
            live_fen = str(self._current_fen())
            if browsing:
                live_fen = str(getattr(self, '_game_browse_saved_fen', '') or live_fen)
        except Exception:
            live_fen = ''
        try:
            if fen_for_result and live_fen and fen_for_result != live_fen:
                self._ai_result = ''
                self._ai_result_fen = ''
                return
        except Exception:
            pass

        # Clear the result slot first (avoid double-apply if something throws).
        try:
            self._ai_result = ''
            self._ai_result_fen = ''
        except Exception:
            pass

        self.ai_thinking = False

        # If browsing, apply the AI move to the *live* position, not the browsed one.
        # Preserve the user's current browsed view.
        browse_index = None
        browse_fen = ''
        if browsing:
            try:
                browse_index = int(self._game_browse_index) if self._game_browse_index is not None else None
            except Exception:
                browse_index = None
            try:
                if browse_index is not None and 0 <= int(browse_index) < len(self.game_fens):
                    browse_fen = str(self.game_fens[int(browse_index)])
            except Exception:
                browse_fen = ''
            try:
                if live_fen:
                    self._load_fen_into_ui(str(live_fen))
            except Exception:
                pass

        # Record AI move to PGN + apply it (to the currently-loaded live board).
        try:
            self.last_move.append(move)
        except Exception:
            self.last_move = [move]
        try:
            self.node = self.node.add_variation(chess.Move.from_uci(move))
        except Exception:
            pass

        self.engine_make_move(move)

        # After AI moves, try to apply the queued premove (must happen on the live board).
        self._apply_premove_if_legal()

        # Update browse-mode live-turn/FEN so clocks tick correctly while browsing.
        if browsing:
            try:
                self._game_browse_saved_fen = str(self.game_fens[-1]) if self.game_fens else str(live_fen)
            except Exception:
                self._game_browse_saved_fen = str(live_fen)
            try:
                self._game_browse_live_turn = str(self.turn)
            except Exception:
                pass

            # Restore the browsed view.
            try:
                if browse_fen:
                    self._load_fen_into_ui(str(browse_fen))
                if browse_index is not None:
                    self._game_browse_index = int(browse_index)
            except Exception:
                pass

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
            try:
                if self._ai_side() == 'w':
                    self.game.headers["WhiteElo"] = str(int(self.ai_elo))
                else:
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

    def _stockfish_path(self) -> str:
        return "lit/" + self.engine + "/" + self.platform

    def _ensure_eval_engine(self) -> bool:
        if self.stockfish_eval is None:
            try:
                self.stockfish_eval = Stockfish(
                    self._stockfish_path(),
                    depth=12,
                    parameters={"Threads": 1, "Minimum Thinking Time": 1, "Hash": 16},
                )
                self.stockfish_eval.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
            except FileNotFoundError:
                self.stockfish_eval = None
                return False
            except Exception:
                self.stockfish_eval = None
                return False

        if self._eval_thread is None or not self._eval_thread.is_alive():
            self._eval_stop.clear()
            self._eval_thread = threading.Thread(target=self._eval_worker, daemon=True)
            self._eval_thread.start()
        return True

    def _ensure_review_engine(self) -> bool:
        if self.stockfish_review is None:
            try:
                self.stockfish_review = Stockfish(
                    self._stockfish_path(),
                    depth=12,
                    parameters={"Threads": 1, "Minimum Thinking Time": 1, "Hash": 32},
                )
                self.stockfish_review.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
            except FileNotFoundError:
                self.stockfish_review = None
                return False
            except Exception:
                self.stockfish_review = None
                return False

        if self._review_thread is None or not self._review_thread.is_alive():
            self._review_stop.clear()
            self._review_thread = threading.Thread(target=self._review_worker, daemon=True)
            self._review_thread.start()
        return True

    def _ensure_review_analysis_engine(self) -> bool:
        if self.stockfish_review_analysis is None:
            try:
                self.stockfish_review_analysis = Stockfish(
                    self._stockfish_path(),
                    depth=12,
                    parameters={"Threads": 1, "Minimum Thinking Time": 1, "Hash": 64},
                )
                self.stockfish_review_analysis.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
            except FileNotFoundError:
                self.stockfish_review_analysis = None
                return False
            except Exception:
                self.stockfish_review_analysis = None
                return False
        return True

    def _ensure_review_pv_engine(self) -> bool:
        def engine_alive(sf: Stockfish | None) -> bool:
            if sf is None:
                return False
            try:
                proc = getattr(sf, '_stockfish', None)
                if proc is not None and hasattr(proc, 'poll'):
                    return proc.poll() is None
            except Exception:
                return False
            # If we can't introspect, assume it's alive.
            return True

        if self.stockfish_review_pv is None or not engine_alive(self.stockfish_review_pv):
            try:
                self.stockfish_review_pv = Stockfish(
                    self._stockfish_path(),
                    depth=12,
                    parameters={"Threads": 1, "Minimum Thinking Time": 1, "Hash": 32},
                )
                self.stockfish_review_pv.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
            except FileNotFoundError:
                self.stockfish_review_pv = None
                return False
            except Exception:
                self.stockfish_review_pv = None
                return False

        if self._review_pv_thread is None or not self._review_pv_thread.is_alive():
            self._review_pv_stop.clear()
            self._review_pv_thread = threading.Thread(target=self._review_pv_worker, daemon=True)
            self._review_pv_thread.start()
        return True

    def _review_pv_interrupt_engine(self) -> None:
        """Best-effort interrupt of the PV engine.

        The Stockfish wrapper calls are synchronous, so the PV worker can be blocked.
        On a brand-new position, interrupting helps the worker switch quickly.
        """
        try:
            sf = getattr(self, 'stockfish_review_pv', None)
        except Exception:
            sf = None
        if sf is None:
            return

        # Throttle interrupts to avoid rapid kill/restart loops while scrubbing.
        try:
            now = float(time.time())
            last = float(getattr(self, '_pv_last_interrupt_ts', 0.0))
            cooldown = float(getattr(self, '_pv_interrupt_cooldown_s', 0.20))
            if cooldown < 0.0:
                cooldown = 0.0
            if (now - last) < cooldown:
                return
            self._pv_last_interrupt_ts = now
        except Exception:
            pass

        proc = None
        try:
            proc = getattr(sf, '_stockfish', None)
        except Exception:
            proc = None

        # Try to terminate the underlying Stockfish process.
        try:
            if proc is not None and hasattr(proc, 'terminate'):
                proc.terminate()
        except Exception:
            pass
        try:
            if proc is not None and hasattr(proc, 'kill'):
                proc.kill()
        except Exception:
            pass

    @staticmethod
    def _format_eval_short(e: dict | None) -> str:
        if not isinstance(e, dict):
            return ""
        if e.get('type') == 'cp':
            try:
                val = float(e.get('value', 0)) / 100.0
            except Exception:
                val = 0.0
            return f"+{val:.2f}" if val >= 0 else f"{val:.2f}"
        try:
            mate_val = int(e.get('value', 0))
        except Exception:
            mate_val = 0
        return f"M{mate_val}"

    @staticmethod
    def _epd_from_fen(fen: str) -> str:
        """Return the EPD-like key used by the openings database (first 4 FEN fields)."""
        try:
            parts = str(fen or '').strip().split()
        except Exception:
            parts = []
        if len(parts) < 4:
            return ''
        return ' '.join(parts[:4])

    def _openings_index_path(self) -> str:
        full_path = os.path.join('lit', 'database', 'chess-openings', 'openings_epd_full.json')
        try:
            if os.path.exists(full_path):
                return full_path
        except Exception:
            pass
        return os.path.join('lit', 'database', 'chess-openings', 'openings_epd.json')

    def _ensure_openings_index(self) -> bool:
        """Load openings EPD index into memory. Returns True on success."""
        try:
            with self._openings_lock:
                if isinstance(self._openings_epd_index, dict) and self._openings_epd_index:
                    return True
        except Exception:
            pass

        path = self._openings_index_path()
        try:
            if os.path.exists(path):
                import json
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    with self._openings_lock:
                        self._openings_epd_index = data
                    return True
        except Exception:
            pass

        # Fallback: if the json isn't present but pyarrow is installed, attempt to build it.
        try:
            parquet_path = os.path.join('lit', 'database', 'chess-openings', 'data', 'train-00000-of-00001.parquet')
            if not os.path.exists(parquet_path):
                return False
            import pyarrow.parquet as pq
            import json

            table = pq.read_table(parquet_path, columns=['epd', 'name', 'eco', 'eco-volume', 'uci', 'pgn'])
            epd = table.column('epd').to_pylist()
            name = table.column('name').to_pylist()
            eco = table.column('eco').to_pylist()
            vol = table.column('eco-volume').to_pylist()
            uci = table.column('uci').to_pylist()
            pgn = table.column('pgn').to_pylist()

            out: dict[str, dict] = {}
            for i in range(len(epd)):
                k = str(epd[i] or '').strip()
                if not k:
                    continue
                nm = str(name[i] or '').strip()
                if not nm:
                    continue
                if k not in out:
                    out[k] = {
                        'name': nm,
                        'eco': str(eco[i] or '').strip(),
                        'eco_volume': str(vol[i] or '').strip(),
                        'uci': str(uci[i] or '').strip(),
                        'pgn': str(pgn[i] or '').strip(),
                    }

            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(out, f, ensure_ascii=False)
            except Exception:
                pass

            with self._openings_lock:
                self._openings_epd_index = out
            return True
        except Exception:
            return False

    def _opening_name_for_fen(self, fen: str) -> str:
        if not self._ensure_openings_index():
            return ''
        k = self._epd_from_fen(fen)
        if not k:
            return ''
        try:
            with self._openings_lock:
                d = (self._openings_epd_index or {}).get(k)
        except Exception:
            d = None
        if not isinstance(d, dict):
            return ''
        try:
            return str(d.get('name', '') or '')
        except Exception:
            return ''

    def _opening_record_for_fen(self, fen: str) -> dict | None:
        if not self._ensure_openings_index():
            return None
        k = self._epd_from_fen(fen)
        if not k:
            return None
        try:
            with self._openings_lock:
                d = (self._openings_epd_index or {}).get(k)
        except Exception:
            d = None
        return d if isinstance(d, dict) else None

    def _ensure_openings_prefix_best(self) -> bool:
        if bool(getattr(self, '_openings_prefix_best_ready', False)) and isinstance(getattr(self, '_openings_prefix_best', None), dict):
            return True
        if not self._ensure_openings_index():
            return False

        best: dict[tuple[str, ...], tuple[str, int]] = {}
        try:
            with self._openings_lock:
                vals = list((self._openings_epd_index or {}).values())
        except Exception:
            vals = []

        for rec in vals:
            if not isinstance(rec, dict):
                continue
            try:
                uci_line = str(rec.get('uci', '') or '')
            except Exception:
                uci_line = ''
            if not uci_line:
                continue
            u = [str(s).strip() for s in str(uci_line).split() if str(s).strip()]
            if not u:
                continue
            try:
                nm = str(rec.get('name', '') or '')
            except Exception:
                nm = ''
            if not nm:
                continue
            ln = int(len(u))
            # For each prefix, keep the shortest matching opening line (most generic at that prefix).
            for k in range(1, ln + 1):
                key = tuple(u[:k])
                cur = best.get(key)
                if cur is None or int(ln) < int(cur[1]):
                    best[key] = (str(nm), int(ln))

        try:
            self._openings_prefix_best = dict(best)
            self._openings_prefix_best_ready = True
        except Exception:
            return False
        return True

    def _opening_name_for_uci_prefix(self, prefix_ucis: list[str]) -> str:
        if not self._ensure_openings_prefix_best():
            return ''
        try:
            key = tuple(str(x) for x in (prefix_ucis or []))
        except Exception:
            key = tuple()
        if not key:
            return ''
        try:
            v = (self._openings_prefix_best or {}).get(key)
        except Exception:
            v = None
        if not v or not isinstance(v, tuple) or not v[0]:
            return ''
        return str(v[0])

    def _compute_analysis_openings_by_prefix(self, moves: list[chess.Move]) -> list[str]:
        """Return opening names per ply based on move-sequence prefix matches to openings uci lines."""
        out: list[str] = []
        prefix: list[str] = []
        for mv in list(moves or []):
            try:
                prefix.append(str(mv.uci()))
            except Exception:
                prefix.append('')
            nm = ''
            try:
                nm = self._opening_name_for_uci_prefix(prefix)
            except Exception:
                nm = ''
            out.append(str(nm or ''))
        return out

    def _build_opening_line_variation(self, target_fen: str) -> tuple[str, list[str], list[str], list[str], int]:
        """Return (name, uci_list, san_list, fen_list, match_ply) for the opening line containing target_fen."""
        rec = self._opening_record_for_fen(str(target_fen))
        if not isinstance(rec, dict):
            return '', [], [], [], 0

        try:
            nm = str(rec.get('name', '') or '')
        except Exception:
            nm = ''
        try:
            uci_line = str(rec.get('uci', '') or '')
        except Exception:
            uci_line = ''

        if not uci_line:
            return nm, [], [], [], 0

        target_epd = self._epd_from_fen(str(target_fen))
        if not target_epd:
            return nm, [], [], [], 0

        uci_list: list[str] = []
        san_list: list[str] = []
        fen_list: list[str] = []
        match_ply = 0

        try:
            b = chess.Board()
        except Exception:
            return nm, [], [], [], 0

        for tok in str(uci_line).split():
            u = str(tok).strip()
            if not u:
                continue
            try:
                mv = chess.Move.from_uci(u)
            except Exception:
                break
            if mv not in b.legal_moves:
                break
            try:
                san = b.san(mv)
            except Exception:
                san = u
            try:
                b.push(mv)
            except Exception:
                break
            try:
                fen_after = str(b.fen())
            except Exception:
                fen_after = ''
            if not fen_after:
                break

            uci_list.append(u)
            san_list.append(str(san))
            fen_list.append(fen_after)

            if match_ply == 0:
                try:
                    if self._epd_from_fen(fen_after) == target_epd:
                        match_ply = len(fen_list)
                except Exception:
                    pass

        return nm, uci_list, san_list, fen_list, int(match_ply)

    def _enter_opening_variation(self, target_fen: str, mode: str) -> bool:
        nm, uci_list, san_list, fen_list, match_ply = self._build_opening_line_variation(str(target_fen))
        if not san_list or not fen_list:
            return False

        self._opening_variation_active = True
        self._opening_variation_name = str(nm or '')
        if mode == 'review':
            try:
                self._opening_variation_return_review_index = int(self.review_index)
            except Exception:
                self._opening_variation_return_review_index = 0
            try:
                self.review_move_scroll = 0
            except Exception:
                pass
        else:
            try:
                self._opening_variation_return_analysis_index = int(self.analysis_index)
            except Exception:
                self._opening_variation_return_analysis_index = 0
            try:
                self.analysis_move_scroll = 0
            except Exception:
                pass

        base = 0
        self._review_analysis_active = True
        self._review_analysis_base_index = int(base)
        self._review_analysis_fen = ''
        try:
            cur = int(match_ply) if int(match_ply) > 0 else 1
        except Exception:
            cur = 1
        self._review_analysis_cursor = max(1, min(int(cur), len(fen_list)))

        self._review_variations[int(base)] = list(san_list)
        self._review_variation_fens[int(base)] = list(fen_list)
        self._review_variation_ucis[int(base)] = list(uci_list)

        try:
            tgt_fen = str(fen_list[self._review_analysis_cursor - 1])
            self._load_fen_into_ui(tgt_fen)
            self.request_eval()
            self.request_review_pv()
        except Exception:
            pass
        return True

    def _exit_opening_variation(self, mode: str) -> None:
        if not bool(getattr(self, '_opening_variation_active', False)):
            return

        self._opening_variation_active = False
        self._opening_variation_name = ''

        base = 0
        try:
            self._review_variations.pop(int(base), None)
        except Exception:
            pass
        try:
            self._review_variation_fens.pop(int(base), None)
        except Exception:
            pass
        try:
            self._review_variation_ucis.pop(int(base), None)
        except Exception:
            pass

        try:
            self._review_analysis_active = False
            self._review_analysis_fen = ''
            self._review_analysis_cursor = 0
        except Exception:
            pass

        try:
            if mode == 'review':
                self._review_set_index(int(self._opening_variation_return_review_index), play_sound=False)
            else:
                self._analysis_set_index(int(self._opening_variation_return_analysis_index), play_sound=False)
        except Exception:
            pass

    def _opening_next_moves_preview(self, uci_line: str, prefix_len: int, max_moves: int = 2) -> str:
        """Return a small SAN preview of the next moves after the prefix (e.g. '... e5 Nf3')."""
        try:
            p = int(prefix_len)
        except Exception:
            p = 0
        try:
            mm = int(max_moves)
        except Exception:
            mm = 2
        if mm <= 0:
            return ''

        out: list[str] = []
        try:
            b = chess.Board()
        except Exception:
            return ''

        for i, tok in enumerate(str(uci_line or '').split()):
            u = str(tok).strip()
            if not u:
                continue
            try:
                mv = chess.Move.from_uci(u)
            except Exception:
                break
            if mv not in b.legal_moves:
                break
            try:
                san = str(b.san(mv))
            except Exception:
                san = u
            try:
                b.push(mv)
            except Exception:
                break
            if i >= p and len(out) < mm:
                out.append(san)
            if len(out) >= mm:
                break

        return ' '.join(out)

    def _opening_suggestions_for_prefix(self, prefix_ucis: list[str], limit: int = 3, exclude_name: str = '') -> list[tuple[str, str, int]]:
        """Return up to N (name, uci_line, line_len) where uci_line startswith prefix and is longer."""
        try:
            prefix = [str(x) for x in (prefix_ucis or [])]
        except Exception:
            prefix = []
        key = tuple(prefix)

        # Ensure index loaded.
        if not self._ensure_openings_index():
            return []

        cached = (getattr(self, '_opening_suggestions_cache', {}) or {}).get(key)
        if cached is None:
            cached = []

        if not cached and key not in (getattr(self, '_opening_suggestions_cache', {}) or {}):
            out: list[tuple[str, str, int]] = []
            pref_len = len(prefix)
            try:
                with self._openings_lock:
                    vals = list((self._openings_epd_index or {}).values())
            except Exception:
                vals = []
            for rec in vals:
                if not isinstance(rec, dict):
                    continue
                try:
                    uci_line = str(rec.get('uci', '') or '')
                except Exception:
                    uci_line = ''
                if not uci_line:
                    continue
                u = [s for s in str(uci_line).split() if str(s).strip()]
                if len(u) <= pref_len:
                    continue
                if pref_len > 0 and u[:pref_len] != prefix:
                    continue
                try:
                    nm = str(rec.get('name', '') or '')
                except Exception:
                    nm = ''
                if not nm:
                    continue
                out.append((nm, uci_line, int(len(u))))

            # Prefer the shortest continuations (fewest moves) for suggestions.
            out.sort(key=lambda t: (int(t[2]), str(t[0]).lower()))
            # Keep a small list per prefix for UI.
            out = out[:25]
            try:
                self._opening_suggestions_cache[key] = list(out)
            except Exception:
                pass
            cached = out

        if not cached:
            return []

        ex = str(exclude_name or '').strip().lower()
        out2: list[tuple[str, str, int]] = []
        seen: set[str] = set()
        for nm, uci_line, ln in cached:
            nml = str(nm).strip().lower()
            if ex and nml == ex:
                continue
            if nml in seen:
                continue
            seen.add(nml)
            out2.append((str(nm), str(uci_line), int(ln)))
            if len(out2) >= int(limit):
                break
        return out2

    def _build_opening_line_variation_from_uci(self, uci_line: str, target_fen: str) -> tuple[list[str], list[str], list[str], int]:
        """Return (uci_list, san_list, fen_list, match_ply) for an opening uci line containing target_fen."""
        target_epd = self._epd_from_fen(str(target_fen))
        if not target_epd:
            return [], [], [], 0

        uci_list: list[str] = []
        san_list: list[str] = []
        fen_list: list[str] = []
        match_ply = 0

        try:
            b = chess.Board()
        except Exception:
            return [], [], [], 0

        for tok in str(uci_line or '').split():
            u = str(tok).strip()
            if not u:
                continue
            try:
                mv = chess.Move.from_uci(u)
            except Exception:
                break
            if mv not in b.legal_moves:
                break
            try:
                san = b.san(mv)
            except Exception:
                san = u
            try:
                b.push(mv)
            except Exception:
                break
            try:
                fen_after = str(b.fen())
            except Exception:
                fen_after = ''
            if not fen_after:
                break

            uci_list.append(u)
            san_list.append(str(san))
            fen_list.append(fen_after)

            if match_ply == 0:
                try:
                    if self._epd_from_fen(fen_after) == target_epd:
                        match_ply = len(fen_list)
                except Exception:
                    pass

        return uci_list, san_list, fen_list, int(match_ply)

    def _enter_opening_variation_from_uci(self, target_fen: str, mode: str, opening_name: str, uci_line: str) -> bool:
        uci_list, san_list, fen_list, match_ply = self._build_opening_line_variation_from_uci(str(uci_line), str(target_fen))
        if not san_list or not fen_list:
            return False

        # Switch any existing opening variation to the new one.
        if bool(getattr(self, '_opening_variation_active', False)):
            try:
                self._exit_opening_variation(str(mode))
            except Exception:
                pass

        self._opening_variation_active = True
        self._opening_variation_name = str(opening_name or '')
        if mode == 'review':
            try:
                self._opening_variation_return_review_index = int(self.review_index)
            except Exception:
                self._opening_variation_return_review_index = 0
            try:
                self.review_move_scroll = 0
            except Exception:
                pass
        else:
            try:
                self._opening_variation_return_analysis_index = int(self.analysis_index)
            except Exception:
                self._opening_variation_return_analysis_index = 0
            try:
                self.analysis_move_scroll = 0
            except Exception:
                pass

        base = 0
        self._review_analysis_active = True
        self._review_analysis_base_index = int(base)
        self._review_analysis_fen = ''
        try:
            cur = int(match_ply) if int(match_ply) > 0 else 1
        except Exception:
            cur = 1
        self._review_analysis_cursor = max(1, min(int(cur), len(fen_list)))

        self._review_variations[int(base)] = list(san_list)
        self._review_variation_fens[int(base)] = list(fen_list)
        self._review_variation_ucis[int(base)] = list(uci_list)

        try:
            tgt_fen = str(fen_list[self._review_analysis_cursor - 1])
            self._load_fen_into_ui(tgt_fen)
            self.request_eval()
            self.request_review_pv()
        except Exception:
            pass
        return True

    def _enter_opening_as_saved_variation_from_uci(self, mode: str, opening_name: str, uci_line: str) -> bool:
        """Create/replace a saved user variation from an opening UCI line.

        This variation persists until deleted and can be extended with manual moves.
        """
        m = str(mode or '').strip().lower()
        if m not in ('review', 'analysis'):
            return False

        # Anchor at current mainline position.
        if m == 'review':
            if not self.review_active or not self.review_fens:
                return False
            try:
                base = int(self.review_index)
            except Exception:
                base = 0
            try:
                base = max(0, min(base, len(self.review_fens) - 1))
                base_fen = str(self.review_fens[base])
            except Exception:
                base = 0
                base_fen = ''
        else:
            if not self.analysis_active or not self.analysis_fens:
                return False
            try:
                base = int(self.analysis_index)
            except Exception:
                base = 0
            try:
                base = max(0, min(base, len(self.analysis_fens) - 1))
                base_fen = str(self.analysis_fens[base])
            except Exception:
                base = 0
                base_fen = ''

        if not base_fen:
            return False

        # Find where this position occurs in the opening line.
        uci_all, _san_all, _fen_all, match_ply = self._build_opening_line_variation_from_uci(str(uci_line), str(base_fen))
        try:
            mp = int(match_ply)
        except Exception:
            mp = 0
        if mp <= 0:
            # match_ply is only set when we match a post-move position; allow start position explicitly.
            try:
                if self._epd_from_fen(str(base_fen)) == self._epd_from_fen(str(chess.Board().fen())):
                    mp = 0
                else:
                    return False
            except Exception:
                return False

        cont_ucis = list(uci_all[mp:]) if uci_all else []
        if not cont_ucis:
            return False

        # Build SAN/FEN lists from the base position.
        try:
            b = chess.Board(str(base_fen))
        except Exception:
            return False

        cont_sans: list[str] = []
        cont_fens: list[str] = []
        valid_ucis: list[str] = []
        for u in cont_ucis:
            u = str(u).strip()
            if not u:
                break
            try:
                mv = chess.Move.from_uci(u)
            except Exception:
                break
            if mv not in b.legal_moves:
                break
            try:
                san = str(b.san(mv))
            except Exception:
                san = u
            try:
                b.push(mv)
            except Exception:
                break
            try:
                cont_sans.append(str(san))
                cont_fens.append(str(b.fen()))
                valid_ucis.append(str(u))
            except Exception:
                break

        if not cont_fens:
            return False

        # Save/replace the variation at this base.
        try:
            self._review_variations[int(base)] = list(cont_sans)
            self._review_variation_ucis[int(base)] = list(valid_ucis)
            self._review_variation_fens[int(base)] = list(cont_fens)
        except Exception:
            return False

        # Opening moves should be visibly labeled as Book.
        try:
            self._review_variation_labels[int(base)] = ['Book' for _ in range(len(cont_sans))]
        except Exception:
            pass

        # Switch into variation view at the end of the opening line.
        self._review_analysis_active = True
        self._review_analysis_base_index = int(base)
        self._review_analysis_fen = ''
        self._review_analysis_cursor = int(len(cont_fens))
        try:
            self._opening_variation_name = str(opening_name or '')
        except Exception:
            self._opening_variation_name = ''

        try:
            self.last_move = [str(valid_ucis[-1])] if valid_ucis else []
        except Exception:
            self.last_move = []

        try:
            self._load_fen_into_ui(str(cont_fens[-1]))
            self.request_eval()
            self.request_review_pv()
        except Exception:
            pass

        # Background labeling (also handles additional Book marking) + persistence in review.
        try:
            self._start_review_variation_analysis_thread(int(base))
        except Exception:
            pass
        try:
            if self.review_active and self.review_pgn_path:
                self._save_review_analysis_cache(str(self.review_pgn_path), self.review_plies or [])
        except Exception:
            pass
        return True

    def _mainline_remaining_ucis_from_position(self, mode: str, base_index: int) -> list[str]:
        m = str(mode or '').strip().lower()
        base = int(base_index)
        if m == 'review':
            try:
                plies = list(self.review_plies or [])
            except Exception:
                plies = []
            try:
                return [str(mv.uci()) for mv in plies[base:]]
            except Exception:
                return []
        if m == 'analysis':
            try:
                plies = list(self.analysis_plies or [])
            except Exception:
                plies = []
            try:
                return [str(mv.uci()) for mv in plies[base:]]
            except Exception:
                return []
        return []

    @staticmethod
    def _longest_prefix_match(a: list[str], b: list[str]) -> int:
        i = 0
        n = min(len(a), len(b))
        while i < n and str(a[i]) == str(b[i]):
            i += 1
        return int(i)

    def _append_uci_sequence_to_analysis_mainline_from_fen(self, start_fen: str, uci_seq: list[str]) -> bool:
        if not self.analysis_active or not self.analysis_fens:
            return False
        if bool(getattr(self, '_review_analysis_active', False)):
            return False
        try:
            if int(self.analysis_index) != int(len(self.analysis_fens) - 1):
                return False
        except Exception:
            return False
        try:
            b = chess.Board(str(start_fen))
        except Exception:
            return False

        applied = 0
        last_uci = ''
        last_fen = ''
        for u in (uci_seq or []):
            u = str(u).strip()
            if not u:
                break
            try:
                mv = chess.Move.from_uci(str(u))
            except Exception:
                break
            if mv not in b.legal_moves:
                break
            try:
                san = str(b.san(mv))
            except Exception:
                san = u
            try:
                b.push(mv)
            except Exception:
                break
            try:
                self.analysis_plies.append(mv)
                self.analysis_sans.append(str(san))
                self.analysis_fens.append(str(b.fen()))
                last_uci = str(u)
                last_fen = str(b.fen())
                applied += 1
                try:
                    self.analysis_move_labels.append('')
                except Exception:
                    pass
                try:
                    self.analysis_opening_names.append('')
                except Exception:
                    pass
            except Exception:
                break

        if applied <= 0:
            return False

        self.analysis_index = len(self.analysis_fens) - 1
        self._review_analysis_active = False
        self._review_analysis_base_index = int(self.analysis_index)
        self._review_analysis_fen = ''
        self._review_analysis_cursor = 0
        try:
            self.last_move = [str(last_uci)] if last_uci else []
        except Exception:
            self.last_move = []
        try:
            if last_fen:
                self._load_fen_into_ui(str(last_fen))
        except Exception:
            pass
        self.request_eval()
        self.request_review_pv()
        try:
            self._start_analysis_annotation_thread(chess.Board(str(self.analysis_fens[0])), list(self.analysis_plies))
        except Exception:
            pass
        return True

    def _create_saved_variation_from_uci_sequence(self, mode: str, base_index: int, start_fen: str, uci_seq: list[str], default_label: str = '') -> bool:
        m = str(mode or '').strip().lower()
        if m not in ('review', 'analysis'):
            return False
        if not start_fen:
            return False
        if not uci_seq:
            return False

        try:
            b = chess.Board(str(start_fen))
        except Exception:
            return False

        sans: list[str] = []
        fens: list[str] = []
        ucis: list[str] = []
        for u in uci_seq:
            u = str(u).strip()
            if not u:
                break
            try:
                mv = chess.Move.from_uci(str(u))
            except Exception:
                break
            if mv not in b.legal_moves:
                break
            try:
                san = str(b.san(mv))
            except Exception:
                san = u
            try:
                b.push(mv)
            except Exception:
                break
            sans.append(str(san))
            fens.append(str(b.fen()))
            ucis.append(str(u))

        if not fens:
            return False

        base = int(base_index)
        try:
            self._review_variations[base] = list(sans)
            self._review_variation_fens[base] = list(fens)
            self._review_variation_ucis[base] = list(ucis)
        except Exception:
            return False

        try:
            if default_label:
                self._review_variation_labels[base] = [str(default_label) for _ in range(len(sans))]
            else:
                self._review_variation_labels[base] = ['' for _ in range(len(sans))]
        except Exception:
            pass

        # Enter variation view at the end.
        self._review_analysis_active = True
        self._review_analysis_base_index = int(base)
        self._review_analysis_fen = ''
        self._review_analysis_cursor = int(len(fens))

        try:
            self.last_move = [str(ucis[-1])] if ucis else []
        except Exception:
            self.last_move = []

        try:
            self._load_fen_into_ui(str(fens[-1]))
        except Exception:
            pass
        self.request_eval()
        self.request_review_pv()

        try:
            self._start_review_variation_analysis_thread(int(base))
        except Exception:
            pass
        try:
            if self.review_active and self.review_pgn_path:
                self._save_review_analysis_cache(str(self.review_pgn_path), self.review_plies or [])
        except Exception:
            pass
        return True

    def _apply_uci_sequence_respecting_mainline(self, mode: str, base_index: int, uci_seq: list[str], default_label: str = '') -> bool:
        """Follow mainline as far as it matches uci_seq, branch only at first deviation.

        - If the full uci_seq matches mainline continuation, just jump forward on the mainline.
        - If it deviates, create a saved variation anchored at the deviation ply.
        - In Analysis, if deviation happens at the end of the mainline, extend the mainline instead.
        """
        m = str(mode or '').strip().lower()
        if m not in ('review', 'analysis'):
            return False
        if not uci_seq:
            return False

        base = int(base_index)
        mainline_rem = self._mainline_remaining_ucis_from_position(m, int(base))
        match_n = self._longest_prefix_match(list(mainline_rem), [str(x) for x in (uci_seq or [])])

        # If everything matches what's available on the mainline, just jump there.
        if match_n >= len(uci_seq):
            tgt = int(base + match_n)
            if m == 'review':
                try:
                    self._review_set_index(int(tgt), play_sound=True)
                except Exception:
                    pass
            else:
                try:
                    self._analysis_set_index(int(tgt), play_sound=True)
                except Exception:
                    pass
            return True

        # Advance along matching mainline prefix.
        anchor = int(base + match_n)
        if m == 'review':
            try:
                self._review_set_index(int(anchor), play_sound=True)
            except Exception:
                pass
        else:
            try:
                self._analysis_set_index(int(anchor), play_sound=True)
            except Exception:
                pass

        # Remaining moves from the deviation point.
        rem = [str(x) for x in (uci_seq or [])][match_n:]
        if not rem:
            return True

        # If we're in analysis mode and the deviation point is now at the end, extend mainline.
        if m == 'analysis':
            try:
                if (not bool(getattr(self, '_review_analysis_active', False))) and int(self.analysis_index) >= int(len(self.analysis_fens) - 1):
                    start_fen = str(self.analysis_fens[self.analysis_index])
                    return bool(self._append_uci_sequence_to_analysis_mainline_from_fen(str(start_fen), list(rem)))
            except Exception:
                pass

        # Otherwise, create a saved variation anchored at the deviation ply.
        start_fen = ''
        if m == 'review':
            try:
                start_fen = str(self.review_fens[anchor]) if 0 <= anchor < len(self.review_fens) else ''
            except Exception:
                start_fen = ''
        else:
            try:
                start_fen = str(self.analysis_fens[anchor]) if 0 <= anchor < len(self.analysis_fens) else ''
            except Exception:
                start_fen = ''
        if not start_fen:
            return False

        return bool(self._create_saved_variation_from_uci_sequence(m, int(anchor), str(start_fen), list(rem), default_label=str(default_label or '')))

    def _apply_opening_respecting_mainline_from_uci(self, mode: str, opening_name: str, uci_line: str) -> bool:
        """Apply an opening continuation: follow mainline while it matches, branch at deviation."""
        m = str(mode or '').strip().lower()
        if m not in ('review', 'analysis'):
            return False

        # Base position is the current mainline index for the mode.
        if m == 'review':
            if not self.review_active or not self.review_fens:
                return False
            try:
                base = int(self.review_index)
            except Exception:
                base = 0
            try:
                base_fen = str(self.review_fens[base])
            except Exception:
                base_fen = ''
        else:
            if not self.analysis_active or not self.analysis_fens:
                return False
            try:
                base = int(self.analysis_index)
            except Exception:
                base = 0
            try:
                base_fen = str(self.analysis_fens[base])
            except Exception:
                base_fen = ''

        if not base_fen:
            return False

        uci_all, _san_all, _fen_all, match_ply = self._build_opening_line_variation_from_uci(str(uci_line), str(base_fen))
        try:
            mp = int(match_ply)
        except Exception:
            mp = 0
        if mp <= 0:
            try:
                if self._epd_from_fen(str(base_fen)) == self._epd_from_fen(str(chess.Board().fen())):
                    mp = 0
                else:
                    return False
            except Exception:
                return False

        cont_ucis = [str(x) for x in (uci_all[mp:] if uci_all else [])]
        if not cont_ucis:
            return False

        # Default label for opening-created variations.
        return bool(self._apply_uci_sequence_respecting_mainline(m, int(base), list(cont_ucis), default_label='Book'))

    def _apply_opening_to_analysis_mainline_from_uci(self, opening_name: str, uci_line: str) -> bool:
        """Extend the analysis mainline using an opening UCI line.

        Used when the user is on the last mainline move in Analysis mode.
        """
        if not self.analysis_active or not self.analysis_fens:
            return False
        if bool(getattr(self, '_review_analysis_active', False)):
            # If already inside a variation overlay, don't mutate mainline here.
            return False
        try:
            if int(self.analysis_index) < int(len(self.analysis_fens) - 1):
                return False
        except Exception:
            return False

        try:
            base = int(self.analysis_index)
        except Exception:
            base = 0
        try:
            base = max(0, min(base, len(self.analysis_fens) - 1))
            base_fen = str(self.analysis_fens[base])
        except Exception:
            base_fen = ''
        if not base_fen:
            return False

        # Find where this position occurs in the opening line and take the continuation.
        uci_all, _san_all, _fen_all, match_ply = self._build_opening_line_variation_from_uci(str(uci_line), str(base_fen))
        try:
            mp = int(match_ply)
        except Exception:
            mp = 0
        if mp <= 0:
            try:
                if self._epd_from_fen(str(base_fen)) == self._epd_from_fen(str(chess.Board().fen())):
                    mp = 0
                else:
                    return False
            except Exception:
                return False

        cont_ucis = list(uci_all[mp:]) if uci_all else []
        if not cont_ucis:
            return False

        try:
            b = chess.Board(str(base_fen))
        except Exception:
            return False

        cont_sans: list[str] = []
        cont_fens: list[str] = []
        valid_ucis: list[str] = []
        for u in cont_ucis:
            u = str(u).strip()
            if not u:
                break
            try:
                mv = chess.Move.from_uci(u)
            except Exception:
                break
            if mv not in b.legal_moves:
                break
            try:
                san = str(b.san(mv))
            except Exception:
                san = u
            try:
                b.push(mv)
            except Exception:
                break
            cont_sans.append(str(san))
            cont_fens.append(str(b.fen()))
            valid_ucis.append(str(u))

        if not cont_fens:
            return False

        # Append to analysis mainline.
        try:
            for i in range(len(valid_ucis)):
                mv = chess.Move.from_uci(str(valid_ucis[i]))
                self.analysis_plies.append(mv)
                try:
                    self.analysis_sans.append(str(cont_sans[i]))
                except Exception:
                    pass
                self.analysis_fens.append(str(cont_fens[i]))
                try:
                    self.analysis_move_labels.append('')
                except Exception:
                    pass
                try:
                    self.analysis_opening_names.append('')
                except Exception:
                    pass
        except Exception:
            return False

        self.analysis_index = len(self.analysis_fens) - 1

        # Ensure we're in mainline view.
        self._review_analysis_active = False
        self._review_analysis_base_index = int(self.analysis_index)
        self._review_analysis_fen = ''
        self._review_analysis_cursor = 0
        try:
            self._opening_variation_name = ''
        except Exception:
            pass

        try:
            self.last_move = [str(valid_ucis[-1])] if valid_ucis else []
        except Exception:
            self.last_move = []

        try:
            self._load_fen_into_ui(str(cont_fens[-1]))
        except Exception:
            pass
        self.request_eval()
        self.request_review_pv()

        try:
            self._start_analysis_annotation_thread(chess.Board(str(self.analysis_fens[0])), list(self.analysis_plies))
        except Exception:
            pass
        return True

    def _compute_review_openings(self, start_board: chess.Board, moves: list[chess.Move]) -> list[str]:
        """Return opening names per ply (after applying each move)."""
        out: list[str] = []
        try:
            b = start_board.copy(stack=False)
        except Exception:
            b = chess.Board()
        for mv in moves:
            try:
                b.push(mv)
            except Exception:
                out.append('')
                continue
            out.append(self._opening_name_for_fen(b.fen()))
        return out

    @staticmethod
    def _review_cache_path(pgn_path: str) -> str:
        return str(pgn_path) + '.analysis.json'

    def _load_review_analysis_cache(self, pgn_path: str, moves: list[chess.Move]) -> bool:
        """Load cached review analysis (labels/acpl/accuracy/opening names) if it matches this PGN."""
        path = self._review_cache_path(pgn_path)
        if not os.path.exists(path):
            return False
        try:
            import json
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                data = json.load(f)
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        try:
            cached_uci = list(data.get('moves_uci', []))
        except Exception:
            cached_uci = []
        cur_uci = []
        try:
            cur_uci = [str(m.uci()) for m in moves]
        except Exception:
            cur_uci = []
        if cached_uci != cur_uci:
            return False

        try:
            self.review_move_labels = list(data.get('move_labels', []))
        except Exception:
            self.review_move_labels = []
        try:
            self.review_opening_names = list(data.get('opening_names', []))
        except Exception:
            self.review_opening_names = []

        # Upgrade cached openings/book labels to the new prefix-based opening detection.
        # This ensures "Book" moves still show even when the cache was created with exact EPD/FEN matching.
        try:
            prefix_openings = self._compute_analysis_openings_by_prefix(moves)
        except Exception:
            prefix_openings = []
        try:
            if any(bool(x) for x in (prefix_openings or [])):
                self.review_opening_names = list(prefix_openings)
                try:
                    if len(self.review_move_labels) < len(moves):
                        self.review_move_labels = list(self.review_move_labels) + ['' for _ in range(len(moves) - len(self.review_move_labels))]
                except Exception:
                    pass
                try:
                    for i, nm in enumerate(self.review_opening_names):
                        if not nm:
                            continue
                        if 0 <= i < len(self.review_move_labels) and self.review_move_labels[i] not in ('Blunder', 'Mistake'):
                            self.review_move_labels[i] = 'Book'
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.review_acpl_white = float(data.get('acpl_white')) if data.get('acpl_white') is not None else None
            self.review_acpl_black = float(data.get('acpl_black')) if data.get('acpl_black') is not None else None
            self.review_accuracy_white = float(data.get('acc_white')) if data.get('acc_white') is not None else None
            self.review_accuracy_black = float(data.get('acc_black')) if data.get('acc_black') is not None else None
            self.review_accuracy_overall = float(data.get('acc_overall')) if data.get('acc_overall') is not None else None
        except Exception:
            pass
        self.review_analysis_progress = None

        # Optional (v2+): restore saved user variations and their labels.
        try:
            ver = int(data.get('version', 1) or 1)
        except Exception:
            ver = 1
        if ver >= 2:
            try:
                variations = data.get('variations', [])
            except Exception:
                variations = []
            if isinstance(variations, list):
                try:
                    self._review_variations = {}
                    self._review_variation_fens = {}
                    self._review_variation_ucis = {}
                    self._review_variation_labels = {}
                except Exception:
                    pass
                for it in variations:
                    if not isinstance(it, dict):
                        continue
                    try:
                        base = int(it.get('base', -1))
                    except Exception:
                        base = -1
                    if base < 0:
                        continue
                    sans = it.get('sans') if isinstance(it.get('sans'), list) else []
                    ucis = it.get('ucis') if isinstance(it.get('ucis'), list) else []
                    fens = it.get('fens') if isinstance(it.get('fens'), list) else []
                    labels = it.get('labels') if isinstance(it.get('labels'), list) else []
                    try:
                        sans = [str(x) for x in sans]
                        ucis = [str(x) for x in ucis]
                        fens = [str(x) for x in fens]
                        labels = [str(x) for x in labels]
                    except Exception:
                        sans, ucis, fens, labels = [], [], [], []
                    n = min(len(sans), len(ucis), len(fens))
                    if n <= 0:
                        continue
                    sans = list(sans[:n])
                    ucis = list(ucis[:n])
                    fens = list(fens[:n])
                    labels = list(labels[:n])
                    if len(labels) < n:
                        labels.extend(['' for _ in range(n - len(labels))])
                    try:
                        self._review_variations[int(base)] = list(sans)
                        self._review_variation_ucis[int(base)] = list(ucis)
                        self._review_variation_fens[int(base)] = list(fens)
                        self._review_variation_labels[int(base)] = list(labels)
                    except Exception:
                        pass
        return True

    def _save_review_analysis_cache(self, pgn_path: str, moves: list[chess.Move]) -> None:
        """Persist review analysis results so re-opening the same PGN is instant."""
        path = self._review_cache_path(pgn_path)
        try:
            import json

            variations_payload: list[dict] = []
            try:
                for base, sans in (getattr(self, '_review_variations', {}) or {}).items():
                    try:
                        base_i = int(base)
                    except Exception:
                        continue
                    sans_list = list(sans or [])
                    ucis_list = list((getattr(self, '_review_variation_ucis', {}) or {}).get(base_i, []) or [])
                    fens_list = list((getattr(self, '_review_variation_fens', {}) or {}).get(base_i, []) or [])
                    labels_list = list((getattr(self, '_review_variation_labels', {}) or {}).get(base_i, []) or [])
                    try:
                        sans_list = [str(x) for x in sans_list]
                        ucis_list = [str(x) for x in ucis_list]
                        fens_list = [str(x) for x in fens_list]
                        labels_list = [str(x) for x in labels_list]
                    except Exception:
                        sans_list, ucis_list, fens_list, labels_list = [], [], [], []
                    n = min(len(sans_list), len(ucis_list), len(fens_list))
                    if n <= 0:
                        continue
                    sans_list = sans_list[:n]
                    ucis_list = ucis_list[:n]
                    fens_list = fens_list[:n]
                    labels_list = labels_list[:n]
                    if len(labels_list) < n:
                        labels_list.extend(['' for _ in range(n - len(labels_list))])
                    variations_payload.append({
                        'base': int(base_i),
                        'sans': list(sans_list),
                        'ucis': list(ucis_list),
                        'fens': list(fens_list),
                        'labels': list(labels_list),
                    })
            except Exception:
                variations_payload = []
            data = {
                'version': 2,
                'moves_uci': [str(m.uci()) for m in moves],
                'move_labels': list(getattr(self, 'review_move_labels', []) or []),
                'opening_names': list(getattr(self, 'review_opening_names', []) or []),
                'acpl_white': self.review_acpl_white,
                'acpl_black': self.review_acpl_black,
                'acc_white': self.review_accuracy_white,
                'acc_black': self.review_accuracy_black,
                'acc_overall': self.review_accuracy_overall,
                'variations': variations_payload,
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def _start_review_variation_analysis_thread(self, base_index: int) -> None:
        """Compute move-quality labels for a saved user variation at base_index."""
        if not (self.review_active or self.analysis_active):
            return
        if not self._ensure_review_analysis_engine() or self.stockfish_review_analysis is None:
            return

        try:
            base = int(base_index)
        except Exception:
            return

        ucis: list[str] = []
        try:
            ucis = [str(x) for x in (self._review_variation_ucis.get(base, []) or [])]
        except Exception:
            ucis = []
        if not ucis:
            try:
                self._review_variation_labels[base] = []
            except Exception:
                pass
            return

        try:
            if self.review_active and self.review_fens:
                start_fen = str(self.review_fens[base]) if 0 <= base < len(self.review_fens) else ''
            else:
                start_fen = str(self.analysis_fens[base]) if 0 <= base < len(self.analysis_fens) else ''
        except Exception:
            start_fen = ''
        if not start_fen:
            return

        # Per-variation run-id so edits cancel stale analysis.
        try:
            rid = int((self._review_variation_analysis_run_ids.get(base, 0) or 0) + 1)
        except Exception:
            rid = 1
        try:
            self._review_variation_analysis_run_ids[base] = int(rid)
        except Exception:
            pass

        def eval_to_cp(e) -> int:
            if not isinstance(e, dict):
                return 0
            if e.get('type') == 'cp':
                try:
                    return int(e.get('value', 0))
                except Exception:
                    return 0
            try:
                mv = int(e.get('value', 0))
            except Exception:
                mv = 0
            return 10000 if mv > 0 else -10000

        def loss_unclamped(cp: int, cap: int = 2000) -> int:
            try:
                return max(0, min(int(cp), int(cap)))
            except Exception:
                return 0

        def classify_move(side_to_move_white: bool, before_eval: int, played_eval: int, best_eval: int, played_uci: str, best_uci: str | None) -> str:
            if side_to_move_white:
                loss = loss_unclamped(best_eval - played_eval)
                gain = played_eval - before_eval
                abs_before = abs(before_eval)
            else:
                loss = loss_unclamped(played_eval - best_eval)
                gain = before_eval - played_eval
                abs_before = abs(before_eval)

            is_best = bool(best_uci and str(played_uci) == str(best_uci))

            # Great / Amazing: close to best and creates a notable swing from a roughly-equal position.
            # (Looser than before so these labels actually show up in typical games at depth ~10.)
            if loss <= 15 and gain >= 140 and abs_before <= 150:
                return 'Amazing'
            if loss <= 35 and gain >= 80 and abs_before <= 220:
                return 'Great'

            if is_best:
                return 'Best'
            if loss <= 15:
                return 'Good'
            if loss >= 250:
                return 'Blunder'
            if loss >= 120:
                return 'Mistake'
            return ''

        def worker():
            try:
                with self._review_analysis_engine_lock:
                    self.stockfish_review_analysis.update_engine_parameters({"UCI_LimitStrength": "false"})
                    self.stockfish_review_analysis.set_skill_level(20)
                    self.stockfish_review_analysis.set_depth(10)
            except Exception:
                pass

            try:
                board = chess.Board(str(start_fen))
            except Exception:
                return

            labels: list[str] = []
            for uci in ucis:
                try:
                    if (not (self.review_active or self.analysis_active)) or int(self._review_variation_analysis_run_ids.get(base, -1)) != int(rid):
                        return
                except Exception:
                    return

                fen_before = board.fen()
                try:
                    with self._review_analysis_engine_lock:
                        self.stockfish_review_analysis.set_fen_position(fen_before)
                        try:
                            before_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                        except Exception:
                            before_eval = 0
                        best_uci = self.stockfish_review_analysis.get_best_move_time(80)
                except Exception:
                    before_eval = 0
                    best_uci = None

                mv = None
                try:
                    mv = chess.Move.from_uci(str(uci))
                except Exception:
                    mv = None

                played_eval = 0
                played_board = chess.Board(fen_before)
                if mv is not None:
                    try:
                        played_board.push(mv)
                    except Exception:
                        pass
                try:
                    with self._review_analysis_engine_lock:
                        self.stockfish_review_analysis.set_fen_position(played_board.fen())
                        played_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                except Exception:
                    played_eval = 0

                best_eval = played_eval
                if best_uci:
                    best_board = chess.Board(fen_before)
                    try:
                        best_board.push_uci(str(best_uci))
                    except Exception:
                        pass
                    try:
                        with self._review_analysis_engine_lock:
                            self.stockfish_review_analysis.set_fen_position(best_board.fen())
                            best_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                    except Exception:
                        best_eval = played_eval

                if board.turn == chess.WHITE:
                    labels.append(classify_move(True, before_eval, played_eval, best_eval, str(uci), str(best_uci) if best_uci else None))
                else:
                    labels.append(classify_move(False, before_eval, played_eval, best_eval, str(uci), str(best_uci) if best_uci else None))

                if mv is not None:
                    try:
                        board.push(mv)
                    except Exception:
                        pass

            # Mark Book moves for this variation when the overall move prefix (mainline up to base + variation)
            # matches the start of any opening line in the openings database.
            try:
                if self._ensure_openings_prefix_best():
                    main_prefix: list[str] = []
                    try:
                        if self.review_active:
                            main_prefix = [str(m.uci()) for m in (self.review_plies[:base] if self.review_plies else [])]
                        else:
                            main_prefix = [str(m.uci()) for m in (self.analysis_plies[:base] if self.analysis_plies else [])]
                    except Exception:
                        main_prefix = []
                    combined: list[str] = list(main_prefix)
                    for i, u in enumerate(list(ucis)):
                        combined.append(str(u))
                        nm = ''
                        try:
                            nm = self._opening_name_for_uci_prefix(combined)
                        except Exception:
                            nm = ''
                        if nm and 0 <= i < len(labels) and labels[i] not in ('Blunder', 'Mistake'):
                            labels[i] = 'Book'
            except Exception:
                pass

            try:
                if int(self._review_variation_analysis_run_ids.get(base, -1)) != int(rid):
                    return
            except Exception:
                return

            try:
                self._review_variation_labels[base] = list(labels)
            except Exception:
                pass

            try:
                if self.review_active and self.review_pgn_path:
                    self._save_review_analysis_cache(str(self.review_pgn_path), self.review_plies or [])
            except Exception:
                pass

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _review_display_fen(self) -> str:
        if self._review_analysis_active:
            # Prefer a structured analysis line if we have one.
            try:
                base = int(self._review_analysis_base_index)
                cur = int(self._review_analysis_cursor)
                # If we're browsing a PV line, use the PV list; otherwise use the user-saved variation.
                if bool(getattr(self, '_review_pv_variation_active', False)) and int(getattr(self, '_review_pv_variation_base_index', -9999)) == base:
                    vfens = list(getattr(self, '_review_pv_variation_fens', []) or [])
                else:
                    vfens = self._review_variation_fens.get(base, [])
                if cur > 0 and 0 <= (cur - 1) < len(vfens):
                    return str(vfens[cur - 1])
            except Exception:
                pass
            return self._review_analysis_fen or (self.review_fens[self.review_index] if self.review_fens else '')
        return self.review_fens[self.review_index] if self.review_fens else ''

    def request_review_pv(self) -> None:
        """Queue an async top-3-lines analysis for the current review position."""
        if not (self.review_active or getattr(self, 'analysis_active', False)):
            return
        if not self._ensure_review_pv_engine():
            return
        fen = self._review_display_fen() if self.review_active else self._analysis_display_fen()
        if not fen:
            return

        # If we have cached PV lines for this FEN, publish them immediately so revisiting
        # a position shows Top lines instantly (even before a new/deeper search runs).
        try:
            cached = (getattr(self, '_pv_lines_by_fen', {}) or {}).get(str(fen))
        except Exception:
            cached = None
        if cached:
            try:
                with self._review_pv_lock:
                    self.review_pv_lines = list(cached)
                    # We'll still enqueue a request; mark pending so the UI can show '(analyzing...)'.
                    self.review_pv_pending = True
                try:
                    self._review_pv_render_cache = list(cached)
                    self._review_pv_render_pending = True
                except Exception:
                    pass
            except Exception:
                pass

        # If the displayed position changed, clear stale lines immediately so the UI
        # doesn't show PVs for the previous position while the new PV is still pending.
        try:
            if str(fen) != str(getattr(self, '_review_pv_last_requested_fen', '')):
                # If we already published cached lines above, don't clear them.
                if not cached:
                    with self._review_pv_lock:
                        self.review_pv_lines = []
                        self.review_pv_pending = True
                self._review_pv_last_requested_fen = str(fen)
        except Exception:
            pass

        # If we haven't visited this position before, aggressively interrupt any
        # in-flight PV search so a fresh engine can answer quickly.
        try:
            if str(fen) not in (getattr(self, '_pv_depth_by_fen', {}) or {}):
                self._review_pv_interrupt_engine()
        except Exception:
            pass
        try:
            with self._review_pv_lock:
                self.review_pv_pending = True
        except Exception:
            pass
        try:
            if self._review_pv_queue.full():
                _ = self._review_pv_queue.get_nowait()
            self._review_pv_queue.put_nowait(fen)
        except Exception:
            pass

    def _review_pv_worker(self) -> None:
        # Engine is created lazily; worker will recreate it if needed.

        PV_PLIES = 10  # show at least 5 full moves (10 plies)

        def eval_from_topmove(d: dict) -> dict:
            if not isinstance(d, dict):
                return {"type": "cp", "value": 0}
            if d.get('Mate') is not None:
                try:
                    return {"type": "mate", "value": int(d.get('Mate'))}
                except Exception:
                    return {"type": "mate", "value": 0}
            try:
                return {"type": "cp", "value": int(d.get('Centipawn', 0))}
            except Exception:
                return {"type": "cp", "value": 0}

        def compute_lines_for_depth(fen_in: str, depth_in: int) -> list[dict]:
            """Compute top 3 lines for a single depth. Runs Stockfish work under engine lock."""
            lines_out: list[dict] = []
            try:
                with self._review_pv_engine_lock:
                    # If the PV engine was interrupted/killed, recreate it.
                    try:
                        if not self._ensure_review_pv_engine():
                            return []
                    except Exception:
                        return []

                    try:
                        self.stockfish_review_pv.update_engine_parameters({"MultiPV": 3, "UCI_LimitStrength": "false"})
                    except Exception:
                        pass
                    try:
                        self.stockfish_review_pv.set_skill_level(20)
                    except Exception:
                        pass
                    try:
                        self.stockfish_review_pv.set_depth(int(depth_in))
                    except Exception:
                        pass

                    self.stockfish_review_pv.set_fen_position(fen_in)
                    top = self.stockfish_review_pv.get_top_moves(3) or []

                for i, d in enumerate(top[:3]):
                    if not isinstance(d, dict):
                        continue
                    mv0 = d.get('Move')
                    if not mv0:
                        continue

                    eval_d = eval_from_topmove(d)

                    pv_raw = d.get('Line')
                    if not pv_raw:
                        pv_raw = d.get('PV')

                    uci_seq: list[str] = []
                    if isinstance(pv_raw, str) and pv_raw.strip():
                        uci_seq = [p for p in pv_raw.strip().split() if p]
                    elif isinstance(pv_raw, (list, tuple)):
                        try:
                            uci_seq = [str(x) for x in pv_raw if str(x)]
                        except Exception:
                            uci_seq = []

                    if not uci_seq:
                        uci_seq = [str(mv0)]

                    # If the wrapper didn't provide a full PV, extend it by querying best moves
                    # from the subsequent positions. This runs only in the PV worker thread.
                    try:
                        if len(uci_seq) < PV_PLIES:
                            b_ext = chess.Board(fen_in)
                            for u in list(uci_seq):
                                try:
                                    b_ext.push(chess.Move.from_uci(str(u)))
                                except Exception:
                                    break
                            while len(uci_seq) < PV_PLIES:
                                try:
                                    fen_k = b_ext.fen()
                                except Exception:
                                    break
                                bm = None
                                try:
                                    with self._review_pv_engine_lock:
                                        self.stockfish_review_pv.set_fen_position(fen_k)
                                        bm = self.stockfish_review_pv.get_best_move()
                                except Exception:
                                    bm = None
                                if not bm:
                                    break
                                try:
                                    mv_k = chess.Move.from_uci(str(bm))
                                except Exception:
                                    break
                                try:
                                    b_ext.push(mv_k)
                                except Exception:
                                    break
                                uci_seq.append(str(bm))
                    except Exception:
                        pass

                    b = chess.Board(fen_in)
                    san_moves: list[str] = []
                    fen_after: list[str] = []
                    for u in uci_seq[:PV_PLIES]:
                        try:
                            mv = chess.Move.from_uci(str(u))
                        except Exception:
                            break
                        try:
                            san = b.san(mv)
                        except Exception:
                            san = str(u)
                        try:
                            b.push(mv)
                        except Exception:
                            break
                        san_moves.append(str(san))
                        try:
                            fen_after.append(str(b.fen()))
                        except Exception:
                            fen_after.append('')

                    lines_out.append(
                        {
                            "rank": int(i + 1),
                            "eval": eval_d,
                            "moves": san_moves,
                            "uci": [str(u) for u in uci_seq[: len(san_moves)]],
                            "fens": fen_after,
                        }
                    )
            except Exception:
                return []
            return lines_out

        while not self._review_pv_stop.is_set():
            try:
                fen = self._review_pv_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            # Drain to latest.
            try:
                while True:
                    fen = self._review_pv_queue.get_nowait()
            except queue.Empty:
                pass

            # If user navigated since this request, skip.
            try:
                cur_fen = self._review_display_fen() if self.review_active else (self._analysis_display_fen() if getattr(self, 'analysis_active', False) else '')
                if fen != cur_fen:
                    continue
            except Exception:
                pass

            # Persistent iterative deepening.
            try:
                min_d = int(getattr(self, '_pv_min_depth', 8))
            except Exception:
                min_d = 8
            try:
                max_d = int(getattr(self, '_pv_max_depth', 20))
            except Exception:
                max_d = 20
            try:
                step_d = int(getattr(self, '_pv_depth_step', 2))
            except Exception:
                step_d = 2
            if step_d <= 0:
                step_d = 2

            try:
                depth = int((getattr(self, '_pv_depth_by_fen', {}) or {}).get(str(fen), int(min_d)))
            except Exception:
                depth = int(min_d)
            if depth < int(min_d):
                depth = int(min_d)

            # Timestamp for this request; deeper searches are delayed until stable.
            try:
                stable_delay_s = float(getattr(self, '_pv_stable_delay_s', 0.35))
            except Exception:
                stable_delay_s = 0.35
            stable_delay_s = max(0.0, min(2.0, float(stable_delay_s)))
            fen_requested_at = float(time.time())

            while not self._review_pv_stop.is_set():
                # If user navigated away, stop deepening this position.
                try:
                    cur_fen = self._review_display_fen() if self.review_active else (self._analysis_display_fen() if getattr(self, 'analysis_active', False) else '')
                    if fen != cur_fen:
                        break
                except Exception:
                    break

                # If a newer request arrived, switch immediately.
                try:
                    newer = self._review_pv_queue.get_nowait()
                    fen = newer
                    try:
                        while True:
                            fen = self._review_pv_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        with self._review_pv_lock:
                            self.review_pv_pending = True
                    except Exception:
                        pass
                    try:
                        depth = int((getattr(self, '_pv_depth_by_fen', {}) or {}).get(str(fen), int(min_d)))
                    except Exception:
                        depth = int(min_d)
                    if depth < int(min_d):
                        depth = int(min_d)
                    fen_requested_at = float(time.time())
                    continue
                except queue.Empty:
                    pass
                except Exception:
                    pass

                # If the user is still stepping through moves, don't start deeper searches yet.
                # Always allow the base depth to compute immediately.
                try:
                    if int(depth) > int(min_d) and float(time.time() - fen_requested_at) < float(stable_delay_s):
                        time.sleep(0.02)
                        continue
                except Exception:
                    pass

                # Expose current search depth for UI.
                try:
                    self._pv_inflight_depth_by_fen[str(fen)] = int(depth)
                    if len(self._pv_inflight_depth_by_fen) > 128:
                        try:
                            self._pv_inflight_depth_by_fen.pop(next(iter(self._pv_inflight_depth_by_fen)))
                        except Exception:
                            pass
                except Exception:
                    pass

                lines_out = compute_lines_for_depth(str(fen), int(depth))

                # Only publish if we're still on the same displayed position.
                try:
                    cur_fen = self._review_display_fen() if self.review_active else (self._analysis_display_fen() if getattr(self, 'analysis_active', False) else '')
                    if fen != cur_fen:
                        break
                except Exception:
                    break
                try:
                    with self._review_pv_lock:
                        self.review_pv_lines = list(lines_out)
                        self.review_pv_pending = False
                except Exception:
                    pass

                # This depth is now completed for this FEN.
                try:
                    self._pv_inflight_depth_by_fen.pop(str(fen), None)
                except Exception:
                    pass

                # Cache latest lines for instant revisit.
                try:
                    self._pv_lines_by_fen[str(fen)] = list(lines_out)
                    if len(self._pv_lines_by_fen) > 128:
                        try:
                            self._pv_lines_by_fen.pop(next(iter(self._pv_lines_by_fen)))
                        except Exception:
                            pass
                except Exception:
                    pass

                # Cache depth for this FEN.
                try:
                    self._pv_depth_by_fen[str(fen)] = int(depth)
                    if len(self._pv_depth_by_fen) > 128:
                        try:
                            self._pv_depth_by_fen.pop(next(iter(self._pv_depth_by_fen)))
                        except Exception:
                            pass
                except Exception:
                    pass

                if int(depth) >= int(max_d):
                    break
                depth = min(int(max_d), int(depth) + int(step_d))
                try:
                    time.sleep(0.02)
                except Exception:
                    pass

    @staticmethod
    def _eval_side_for_rect(e: dict | None) -> str:
        """Return 'w' if eval favors white, 'b' if favors black, else 'e'."""
        if not isinstance(e, dict):
            return 'e'
        t = e.get('type')
        try:
            v = int(e.get('value', 0))
        except Exception:
            v = 0
        if t == 'mate':
            if v > 0:
                return 'w'
            if v < 0:
                return 'b'
            return 'e'
        # cp
        if v > 15:
            return 'w'
        if v < -15:
            return 'b'
        return 'e'

    def _review_jump_to_fen(self, fen: str) -> None:
        """Jump the review display to a specific FEN (analysis overlay)."""
        if not self.review_active:
            return
        if not fen:
            return
        # Ensure analysis overlay is active so _review_display_fen() returns this FEN.
        if not self._review_analysis_active:
            self._review_analysis_active = True
            self._review_analysis_base_index = int(self.review_index)
            self._review_analysis_cursor = 0
            self._review_variations[int(self._review_analysis_base_index)] = []
            self._review_variation_fens[int(self._review_analysis_base_index)] = []
            self._review_variation_ucis[int(self._review_analysis_base_index)] = []
        self._review_analysis_fen = str(fen)
        try:
            self.last_move = []
        except Exception:
            pass
        self._load_fen_into_ui(str(fen))
        self.request_eval()
        self.request_review_pv()
        # Best move from mainline doesn't apply in analysis.
        self.review_best_move_uci = ''
        self.review_arrow = None

    def _review_set_variation_cursor(self, base_index: int, cursor: int, play_sound: bool = True) -> None:
        """Set analysis overlay to a specific ply inside an existing variation.

        cursor: 0=base position, 1..N=variation ply index.
        """
        if not self.review_active:
            return

        base = int(base_index)
        try:
            cur = int(cursor)
        except Exception:
            cur = 0

        vfens = self._review_variation_fens.get(base, [])
        vucis = self._review_variation_ucis.get(base, [])

        if cur < 0:
            cur = 0
        if cur > len(vfens):
            cur = len(vfens)

        self._review_analysis_active = True
        self._review_analysis_base_index = int(base)
        self._review_analysis_cursor = int(cur)
        self._review_analysis_fen = ''
        # Switching to a user-saved variation: PV browsing is no longer active.
        try:
            self._review_pv_variation_active = False
        except Exception:
            pass

        # Base position.
        if cur == 0:
            try:
                tgt_fen = str(self.review_fens[base]) if 0 <= base < len(self.review_fens) else ''
            except Exception:
                tgt_fen = ''
            if not tgt_fen:
                return
            try:
                self.last_move = [] if base <= 0 else [str(self.review_plies[base - 1].uci())]
            except Exception:
                self.last_move = []
            self._load_fen_into_ui(tgt_fen)
            self.request_eval()
            self.request_review_pv()
            self.review_best_move_uci = ''
            self.review_arrow = None
            return

        # Inside variation.
        try:
            tgt_fen = str(vfens[cur - 1])
        except Exception:
            tgt_fen = ''
        if not tgt_fen:
            return

        uci = ''
        try:
            uci = str(vucis[cur - 1]) if 0 <= (cur - 1) < len(vucis) else ''
        except Exception:
            uci = ''

        if uci:
            try:
                self.last_move = [uci]
            except Exception:
                self.last_move = []

            if play_sound:
                try:
                    mv = chess.Move.from_uci(str(uci))
                    if cur == 1:
                        fen_before = str(self.review_fens[base]) if 0 <= base < len(self.review_fens) else ''
                    else:
                        fen_before = str(vfens[cur - 2])
                    if fen_before:
                        self._play_sound_for_move_from_fen(str(fen_before), mv)
                except Exception:
                    pass
        else:
            try:
                self.last_move = []
            except Exception:
                pass

        self._load_fen_into_ui(tgt_fen)
        self.request_eval()
        self.request_review_pv()
        self.review_best_move_uci = ''
        self.review_arrow = None

    def _review_start_pv_variation(self, pv_item: dict, move_index: int) -> None:
        """Start an analysis line from the current displayed position using a PV sequence."""
        if not self.review_active:
            return
        if not isinstance(pv_item, dict):
            return

        try:
            base_fen = str(self._review_display_fen() or '')
        except Exception:
            base_fen = ''
        if not base_fen:
            return

        # PV browsing is anchored at the current mainline ply.
        base = int(self.review_index)

        uci_list = pv_item.get('uci') if isinstance(pv_item.get('uci'), list) else []
        san_list = pv_item.get('moves') if isinstance(pv_item.get('moves'), list) else []
        fen_list = pv_item.get('fens') if isinstance(pv_item.get('fens'), list) else []
        try:
            uci_list = [str(x) for x in uci_list]
            san_list = [str(x) for x in san_list]
            fen_list = [str(x) for x in fen_list]
        except Exception:
            uci_list, san_list, fen_list = [], [], []
        if not fen_list:
            return

        # Activate PV browsing mode (do NOT overwrite the user's saved variation).
        self._review_analysis_active = True
        self._review_analysis_base_index = int(base)
        self._review_analysis_fen = ''
        self._review_analysis_cursor = max(1, min(int(move_index) + 1, len(fen_list)))
        try:
            self._review_pv_variation_active = True
            self._review_pv_variation_base_index = int(base)
            self._review_pv_variation_fens = list(fen_list)
            self._review_pv_variation_ucis = list(uci_list)
            self._review_pv_variation_sans = list(san_list)
        except Exception:
            pass

        # Determine target fen and the move to highlight/play.
        tgt_fen = str(fen_list[self._review_analysis_cursor - 1])
        uci = ''
        try:
            uci = str(uci_list[self._review_analysis_cursor - 1])
        except Exception:
            uci = ''

        # Highlight + sound like normal forward navigation.
        if uci:
            try:
                self.last_move = [uci]
            except Exception:
                self.last_move = []
            try:
                mv = chess.Move.from_uci(str(uci))
                # fen_before: base_fen for first move, otherwise previous PV fen.
                if self._review_analysis_cursor == 1:
                    fen_before = base_fen
                else:
                    fen_before = str(fen_list[self._review_analysis_cursor - 2])
                self._play_sound_for_move_from_fen(str(fen_before), mv)
            except Exception:
                pass

        self._load_fen_into_ui(tgt_fen)
        self.request_eval()
        self.request_review_pv()
        self.review_best_move_uci = ''
        self.review_arrow = None

    def _review_create_user_variation_from_pv(self, pv_item: dict, move_index: int) -> None:
        """Create/replace a user variation from a PV line and enter it at move_index.

        This is used when the user clicks a top line while not already inside a user variation.
        """
        if not self.review_active:
            return
        if not isinstance(pv_item, dict):
            return

        try:
            base_fen = str(self._review_display_fen() or '')
        except Exception:
            base_fen = ''
        if not base_fen:
            return

        base = int(self.review_index)

        uci_list = pv_item.get('uci') if isinstance(pv_item.get('uci'), list) else []
        san_list = pv_item.get('moves') if isinstance(pv_item.get('moves'), list) else []
        fen_list = pv_item.get('fens') if isinstance(pv_item.get('fens'), list) else []
        try:
            uci_list = [str(x) for x in uci_list]
            san_list = [str(x) for x in san_list]
            fen_list = [str(x) for x in fen_list]
        except Exception:
            uci_list, san_list, fen_list = [], [], []

        n_total = min(len(uci_list), len(san_list), len(fen_list))
        if n_total <= 0:
            return

        # Save only the PV prefix up to the clicked move as a user variation.
        n_keep = max(1, min(int(move_index) + 1, int(n_total)))
        try:
            self._review_variations[base] = list(san_list[:n_keep])
            self._review_variation_fens[base] = list(fen_list[:n_keep])
            self._review_variation_ucis[base] = list(uci_list[:n_keep])
            self._review_variation_labels[base] = ['' for _ in range(int(n_keep))]
        except Exception:
            return

        # Analyze labels for this variation in the background.
        try:
            self._start_review_variation_analysis_thread(int(base))
        except Exception:
            pass

        cur = int(n_keep)
        self._review_analysis_active = True
        self._review_analysis_base_index = int(base)
        self._review_analysis_cursor = int(cur)
        self._review_analysis_fen = ''
        # This is a user variation; PV browsing mode is not active.
        try:
            self._review_pv_variation_active = False
        except Exception:
            pass

        tgt_fen = ''
        try:
            tgt_fen = str(self._review_variation_fens.get(base, [])[cur - 1])
        except Exception:
            tgt_fen = ''
        if not tgt_fen:
            return

        uci = ''
        try:
            uci = str(self._review_variation_ucis.get(base, [])[cur - 1])
        except Exception:
            uci = ''

        if uci:
            try:
                self.last_move = [uci]
            except Exception:
                self.last_move = []
            try:
                mv = chess.Move.from_uci(str(uci))
                if cur == 1:
                    fen_before = base_fen
                else:
                    fen_before = str(self._review_variation_fens.get(base, [])[cur - 2])
                if fen_before:
                    self._play_sound_for_move_from_fen(str(fen_before), mv)
            except Exception:
                pass
        else:
            try:
                self.last_move = []
            except Exception:
                pass

        self._load_fen_into_ui(tgt_fen)
        self.request_eval()
        self.request_review_pv()
        self.review_best_move_uci = ''
        self.review_arrow = None

    def _review_append_pv_to_user_variation(self, pv_item: dict, move_index: int) -> bool:
        """Append a PV prefix (up to move_index) to the end of the current user variation."""
        if not self.review_active:
            return False
        if not isinstance(pv_item, dict):
            return False

        # Must be inside a user variation (cursor > 0) to append.
        try:
            if not bool(getattr(self, '_review_analysis_active', False)):
                return False
            if bool(getattr(self, '_review_pv_variation_active', False)):
                # If we were browsing PV, treat as not appending to a user variation.
                return False
            base = int(getattr(self, '_review_analysis_base_index', int(self.review_index)))
            cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)
            if cur <= 0:
                return False
        except Exception:
            return False

        uci_list = pv_item.get('uci') if isinstance(pv_item.get('uci'), list) else []
        san_list = pv_item.get('moves') if isinstance(pv_item.get('moves'), list) else []
        fen_list = pv_item.get('fens') if isinstance(pv_item.get('fens'), list) else []
        try:
            uci_list = [str(x) for x in uci_list]
            san_list = [str(x) for x in san_list]
            fen_list = [str(x) for x in fen_list]
        except Exception:
            uci_list, san_list, fen_list = [], [], []

        n = min(int(move_index) + 1, len(uci_list), len(san_list), len(fen_list))
        if n <= 0:
            return False

        # Truncate to current cursor (if the user stepped back) and then append the PV prefix.
        try:
            self._review_variations[base] = list((self._review_variations.get(base, []) or [])[:cur])
        except Exception:
            self._review_variations[base] = []
        try:
            self._review_variation_fens[base] = list((self._review_variation_fens.get(base, []) or [])[:cur])
        except Exception:
            self._review_variation_fens[base] = []
        try:
            self._review_variation_ucis[base] = list((self._review_variation_ucis.get(base, []) or [])[:cur])
        except Exception:
            self._review_variation_ucis[base] = []

        # Keep labels aligned with the truncated cursor.
        try:
            self._review_variation_labels[base] = list((self._review_variation_labels.get(base, []) or [])[:cur])
        except Exception:
            self._review_variation_labels[base] = []

        try:
            self._review_variations[base].extend([str(x) for x in san_list[:n]])
        except Exception:
            pass
        try:
            self._review_variation_fens[base].extend([str(x) for x in fen_list[:n]])
        except Exception:
            pass
        try:
            self._review_variation_ucis[base].extend([str(x) for x in uci_list[:n]])
        except Exception:
            pass

        # Extend label placeholders for appended plies.
        try:
            self._review_variation_labels[base].extend(['' for _ in range(int(n))])
        except Exception:
            pass

        # Move cursor to the new end and update display.
        try:
            self._review_analysis_cursor = len(self._review_variation_fens.get(base, []) or [])
        except Exception:
            self._review_analysis_cursor = cur + n
        try:
            self._review_analysis_fen = str(self._review_variation_fens.get(base, [])[-1])
        except Exception:
            self._review_analysis_fen = ''

        tgt_fen = ''
        try:
            tgt_fen = str(self._review_variation_fens.get(base, [])[-1])
        except Exception:
            tgt_fen = ''
        if not tgt_fen:
            return False

        last_uci = ''
        try:
            last_uci = str(self._review_variation_ucis.get(base, [])[-1])
            self.last_move = [last_uci]
        except Exception:
            last_uci = ''
            self.last_move = []

        # Play the sound for the final appended move.
        if last_uci:
            try:
                mv = chess.Move.from_uci(str(last_uci))
                vfens2 = list(self._review_variation_fens.get(base, []) or [])
                if len(vfens2) == 1:
                    fen_before = str(self.review_fens[base]) if 0 <= base < len(self.review_fens) else ''
                else:
                    fen_before = str(vfens2[-2])
                if fen_before:
                    self._play_sound_for_move_from_fen(str(fen_before), mv)
            except Exception:
                pass
        self._load_fen_into_ui(tgt_fen)
        self.request_eval()
        self.request_review_pv()
        self.review_best_move_uci = ''
        self.review_arrow = None

        # Analyze labels for this variation in the background.
        try:
            self._start_review_variation_analysis_thread(int(base))
        except Exception:
            pass
        return True

    def _play_sound_for_move_from_fen(self, fen_before: str, mv: chess.Move) -> None:
        if not self.sound_enabled:
            return
        try:
            b = chess.Board(fen_before)
        except Exception:
            return

        # Detect move traits before pushing.
        try:
            is_castle = bool(b.is_castling(mv))
        except Exception:
            is_castle = False
        try:
            is_promotion = bool(getattr(mv, 'promotion', None) is not None)
        except Exception:
            is_promotion = False
        try:
            is_capture = b.is_capture(mv)
        except Exception:
            is_capture = False

        try:
            b.push(mv)
        except Exception:
            return

        try:
            if b.is_checkmate():
                pg.mixer.music.load('data/sounds/mate.wav')
            elif b.is_check():
                pg.mixer.music.load('data/sounds/check.aiff')
            elif is_castle or is_promotion:
                pg.mixer.music.load('data/sounds/castle.mp3')
            elif is_capture:
                pg.mixer.music.load('data/sounds/capture.mp3')
            else:
                pg.mixer.music.load('data/sounds/move.mp3')
            pg.mixer.music.play(1)
        except Exception:
            pass

    def _python_chess_board(self) -> chess.Board:
        return chess.Board(self._current_fen())

    def request_eval(self) -> None:
        """Queue an async evaluation of the current position."""
        if getattr(self, 'puzzle_rush_active', False):
            return
        if not self.eval_bar_enabled:
            return
        if not self._ensure_eval_engine():
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

        premove_mode = bool(self._premove_mode_active())
        active_colour = self._premove_player_side() if premove_mode else self.turn

        # First click: select a piece
        if self.selected_square is None:
            if premove_mode:
                piece = self._virtual_player_piece_at(int(target_row), int(target_col))
            else:
                piece = self.board[target_row][target_col]
            if piece != ' ' and piece is not None and piece.colour[0] == active_colour:
                self.selected_square = (target_row, target_col)
            else:
                self.selected_square = None
            return

        sel_row, sel_col = self.selected_square
        if (sel_row, sel_col) == (target_row, target_col):
            self.selected_square = None
            return

        # Clicking another own piece switches selection (except in premove mode, where the user
        # may want to premove onto that square if the piece could move there on an empty board).
        if premove_mode:
            target_piece = self._virtual_player_piece_at(int(target_row), int(target_col))
        else:
            target_piece = self.board[target_row][target_col]
        if target_piece != ' ' and target_piece is not None and target_piece.colour[0] == active_colour:
            if premove_mode:
                # Try to treat this click as a premove destination first.
                try:
                    if premove_mode:
                        sel_piece = self._virtual_player_piece_at(int(sel_row), int(sel_col))
                    else:
                        sel_piece = self.board[sel_row][sel_col]
                    if sel_piece != ' ' and sel_piece is not None:
                        vr, vc = self._virtual_position_for_piece(sel_piece)
                        pseudo_positions, _pseudo_caps = self._pseudo_moves_for_piece(sel_piece, int(vr), int(vc))
                        vdx = int(target_col) - int(vc)
                        vdy = int(target_row) - int(vr)
                        if (vdx, vdy) in pseudo_positions:
                            self._set_premove(sel_row, sel_col, target_row, target_col)
                            self.selected_square = None
                            return
                except Exception:
                    pass

            # Otherwise, switch selection.
            self.selected_square = (target_row, target_col)
            return

        sel_piece = self.board[sel_row][sel_col]
        if premove_mode:
            sel_piece = self._virtual_player_piece_at(int(sel_row), int(sel_col))
        if sel_piece == ' ' or sel_piece is None:
            self.selected_square = None
            return

        dx = target_col - sel_col
        dy = target_row - sel_row
        if not premove_mode:
            if (dx, dy) not in sel_piece.legal_positions:
                return
        else:
            # In premove mode, use pseudo-legal moves on an empty board from the piece's virtual position.
            try:
                vr, vc = self._virtual_position_for_piece(sel_piece)
                pseudo_positions, _pseudo_caps = self._pseudo_moves_for_piece(sel_piece, int(vr), int(vc))
                # Validate relative to the *virtual* position (piece may already be premoved).
                vdx = int(target_col) - int(vc)
                vdy = int(target_row) - int(vr)
                if (vdx, vdy) not in pseudo_positions:
                    return
            except Exception:
                pass

        if premove_mode:
            self._set_premove(sel_row, sel_col, target_row, target_col)
            self.selected_square = None
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
                # This handler is only used for player input, so always record the move.
                # (In Player vs AI, the AI move is recorded in _poll_ai_result.)
                self.last_move.append(uci)
                self.node = self.node.add_variation(chess.Move.from_uci(uci))

            self.moved()

            if EVAL_ON:
                self.get_eval()

            if self.player_vs_ai:
                # If the player's move ended the game, do not let the AI move.
                # Otherwise Stockfish will return None (no legal moves) and the old
                # code path would reset the game immediately, preventing the popup.
                if not self.end_popup_active and not self.game_just_ended:
                    self._request_ai_move_async()
                if EVAL_ON:
                    self.get_eval()

        self.selected_square = None

    def run(self) -> None:
        # One-time start screen.
        if not getattr(self, '_start_menu_shown', False):
            try:
                self._clock_running = False
            except Exception:
                pass
            try:
                from src.engine.start_menu import StartMenu
                menu = StartMenu(
                    title='Start',
                    width=self.screen.get_width(),
                    height=self.screen.get_height(),
                    surface=self.screen,
                    parent=self,
                )
                menu.run()
            except Exception:
                pass
            self._start_menu_shown = True
            try:
                self._clock_last_ts = time.time()
                self._clock_running = True
            except Exception:
                pass

        # Always tick clocks (even while browsing history).
        try:
            self._tick_clock()
        except Exception:
            pass

        # Apply AI results even while browsing history.
        # (_poll_ai_result preserves the browsed view while updating the live game state.)
        try:
            self._poll_ai_result()
        except Exception:
            pass

        # Puzzle Rush: apply any scheduled computer move when its delay elapses.
        try:
            if getattr(self, 'puzzle_rush_active', False):
                self._puzzle_rush_pump_pending()
        except Exception:
            pass

        self._ensure_layout()
        # Clear the entire frame first. Without this, pixels outside the board (e.g. review/analysis
        # panels) can remain visible when switching modes or returning from menus.
        try:
            self.screen.fill((0, 0, 0))
        except Exception:
            pass
        self.draw_board()
        if self.updates:
            self.update_board()

        # Show legal moves for a selected piece (click-to-move).
        # Draw BEFORE pieces so indicators don't cover capture targets.
        if self.movement_click_enabled and self.selected_square is not None and not self.updates:
            try:
                premove_mode = bool(self._premove_mode_active())
                active_colour = self._premove_player_side() if premove_mode else self.turn

                sel_row, sel_col = self.selected_square
                if premove_mode:
                    sel_piece = self._virtual_player_piece_at(int(sel_row), int(sel_col))
                else:
                    sel_piece = self.board[sel_row][sel_col]
                if sel_piece != ' ' and sel_piece is not None and sel_piece.colour[0] == active_colour:
                    if premove_mode:
                        old_pos = list(getattr(sel_piece, 'legal_positions', []) or [])
                        old_caps = set(getattr(sel_piece, 'legal_captures', set()) or set())
                        old_piece_pos = tuple(getattr(sel_piece, 'position', (0, 0)))
                        try:
                            vr, vc = self._virtual_position_for_piece(sel_piece)
                            pos, _caps = self._pseudo_moves_for_piece(sel_piece, int(vr), int(vc))
                            caps = self._capture_deltas_from_occupancy(int(vr), int(vc), pos, active_colour)
                            sel_piece.position = (int(vr), int(vc))
                            sel_piece.legal_positions = pos
                            sel_piece.legal_captures = caps
                            sel_piece.show_legal_moves(self.screen, self.offset, active_colour, self.flipped, self.board)
                        finally:
                            sel_piece.position = old_piece_pos
                            sel_piece.legal_positions = old_pos
                            sel_piece.legal_captures = old_caps
                    else:
                        sel_piece.show_legal_moves(self.screen, self.offset, self.turn, self.flipped, self.board)
            except Exception:
                pass
        piece_active = None
        for piece in self.all_pieces:
            if piece.clicked:
                piece_active = piece
                break
        if piece_active is not None:
            self.draw_pieces(piece_active)
        else:
            self.draw_pieces()

        # Draw clocks after pieces.
        try:
            self._draw_clocks()
        except Exception:
            pass

        # Flip-board button sits on top of the board/pieces.
        try:
            self._draw_flip_board_button()
        except Exception:
            self._flip_board_btn_rect = None

        # Top-most overlays (must render after pieces/arrows).
        if not self.review_active and not self.analysis_active and not self.end_popup_active:
            if getattr(self, 'puzzle_rush_active', False):
                self._draw_puzzle_rush_overlay()
            else:
                self._draw_game_movelist_overlay()
        if self.review_active:
            self._draw_review_move_quality_marker()
            self._draw_review_overlay()
        if self.analysis_active:
            self._draw_analysis_move_quality_marker()
            self._draw_analysis_overlay()
        if self.end_popup_active:
            self._draw_end_popup()
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
                        try:
                            self._nav_hold_dir = -1
                            self._nav_hold_next_ms = int(pg.time.get_ticks()) + 240
                        except Exception:
                            pass
                    elif event.key == pg.K_RIGHT:
                        self.review_step(1)
                        try:
                            self._nav_hold_dir = 1
                            self._nav_hold_next_ms = int(pg.time.get_ticks()) + 240
                        except Exception:
                            pass
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
                            if not self._review_analysis_active:
                                self._request_review_best()
                    elif event.key == pg.K_DOWN:
                        # Enter a saved user variation under the current mainline ply.
                        # Only applies when not already inside a user variation.
                        try:
                            if not bool(getattr(self, '_review_analysis_active', False)):
                                base = int(self.review_index)
                                if (self._review_variations.get(base, []) or []) and (self._review_variation_fens.get(base, []) or []):
                                    self._review_set_variation_cursor(int(base), 1, play_sound=True)
                        except Exception:
                            pass
                    elif event.key == pg.K_UP:
                        # If currently inside a variation, go back to the mainline base position.
                        try:
                            if bool(getattr(self, '_review_analysis_active', False)):
                                base = int(getattr(self, '_review_analysis_base_index', int(self.review_index)))
                                # Fully exit analysis/variation overlay so arrow keys resume mainline navigation.
                                # (Even if the cursor is already at the base position.)
                                self._review_set_index(int(base), play_sound=False)
                        except Exception:
                            pass
                elif event.type == pg.KEYUP:
                    if event.key in (pg.K_LEFT, pg.K_RIGHT):
                        try:
                            self._nav_hold_dir = 0
                            self._nav_hold_next_ms = None
                        except Exception:
                            pass
                elif event.type == pg.MOUSEBUTTONDOWN:
                    # Mouse wheel scrolls either the top stats/PV area (when hovered)
                    # or the move list (default).
                    if event.button in (4, 5):
                        delta_rows = -3 if event.button == 4 else 3
                        try:
                            rtop = getattr(self, '_review_top_rect', None)
                            if rtop is not None and rtop.collidepoint(pg.mouse.get_pos()):
                                try:
                                    step_px = int(self.eval_font.get_linesize() + 6)
                                except Exception:
                                    step_px = 22
                                try:
                                    self.review_top_scroll = max(0, int(self.review_top_scroll) + int(delta_rows) * int(step_px))
                                except Exception:
                                    self.review_top_scroll = 0
                            else:
                                self._review_scroll_moves(int(delta_rows))
                        except Exception:
                            self._review_scroll_moves(int(delta_rows))
                    elif event.button == 1:
                        # Cog opens settings even in review mode.
                        try:
                            if self._settings_btn_rect is not None and self._settings_btn_rect.collidepoint(event.pos):
                                self._open_settings_menu()
                                self.left = False
                                self._mouse_down_pos = None
                                self.updates = False
                                self.selected_square = None
                                self._ensure_layout(force=True)
                                continue
                        except Exception:
                            pass

                        # Flip-board button (all modes).
                        try:
                            if self._flip_board_btn_rect is not None and self._flip_board_btn_rect.collidepoint(event.pos):
                                self.flip_board()
                                self.left = False
                                self._mouse_down_pos = None
                                self.updates = False
                                self.selected_square = None
                                continue
                        except Exception:
                            pass

                        # Drag the review panel splitter to resize top/bottom boxes.
                        try:
                            srect = getattr(self, '_review_splitter_rect', None)
                            if srect is not None and srect.collidepoint(event.pos):
                                self._review_split_dragging = True
                                # Don't start any board drag/click.
                                self.left = False
                                self._mouse_down_pos = None
                                self.updates = False
                                continue
                        except Exception:
                            pass
                        # Board resize handle (bottom-right). Consume the click.
                        try:
                            if self._handle_board_resize_begin(event.pos):
                                continue
                        except Exception:
                            pass
                        # Match main-game behavior: set left-down state and record tx/ty.
                        self.left = True
                        self._mouse_down_pos = pg.mouse.get_pos()
                        self.click_left()
                    elif event.button == 3:
                        # Right-click while dragging cancels the drag (match main-game behavior).
                        try:
                            if self.left or self.updates:
                                self._ignore_next_left_mouse_up = True
                                self.updates_kill()
                                self.selected_square = None
                                self._mouse_down_pos = None
                                self.tx = None
                                self.ty = None
                                self.txr = None
                                self.tyr = None
                                continue
                        except Exception:
                            pass
                        # Right-click highlights/arrows (board-only), like normal play.
                        try:
                            if self._mouse_to_square(event.pos) is not None:
                                self.click_right()
                        except Exception:
                            pass
                elif event.type == pg.MOUSEMOTION:
                    # While dragging the review splitter, update the top height.
                    try:
                        if bool(getattr(self, '_review_split_dragging', False)):
                            panel = getattr(self, '_review_panel_rect', None)
                            if panel is not None:
                                new_top = int(event.pos[1]) - int(panel.y)
                                min_top_h = 120
                                min_bottom_h = 140
                                new_top = max(int(min_top_h), min(int(new_top), int(panel.h - min_bottom_h)))
                                self.review_top_h_px = int(new_top)
                            continue
                    except Exception:
                        pass
                    if getattr(self, '_board_resize_active', False):
                        try:
                            self._handle_board_resize_drag(event.pos)
                        except Exception:
                            pass
                        continue
                    # Match main-game behavior: only start dragging after the cursor moves a bit.
                    if self.movement_drag_enabled and self.left and not self.updates and self._mouse_down_pos is not None:
                        mx, my = pg.mouse.get_pos()
                        dx = mx - self._mouse_down_pos[0]
                        dy = my - self._mouse_down_pos[1]
                        if (dx * dx + dy * dy) >= (self._drag_threshold_px * self._drag_threshold_px):
                            self.updates = True
                            # If the user started dragging, clear click-selection dots.
                            self.selected_square = None
                elif event.type == pg.MOUSEBUTTONUP and event.button == 1:
                    # Release splitter drag.
                    try:
                        if bool(getattr(self, '_review_split_dragging', False)):
                            self._review_split_dragging = False
                            self.left = False
                            self.updates = False
                            self._mouse_down_pos = None
                            continue
                    except Exception:
                        pass
                    # Swallow one-shot left mouse-up after a right-click drag cancel.
                    if getattr(self, '_ignore_next_left_mouse_up', False):
                        self._ignore_next_left_mouse_up = False
                        self.left = False
                        self.updates = False
                        self._mouse_down_pos = None
                        continue
                    if getattr(self, '_board_resize_active', False):
                        try:
                            self._handle_board_resize_end()
                        except Exception:
                            pass
                        self.updates = False
                        self.left = False
                        self._mouse_down_pos = None
                        continue
                    pos = pg.mouse.get_pos()
                    handled = False
                    try:
                        handled = bool(self._handle_review_click(pos))
                    except Exception:
                        handled = False
                    if handled:
                        # Always release left-click state even if the panel handled the click.
                        # Otherwise the app can get stuck thinking we're dragging.
                        self.left = False
                        self.updates = False
                        self._mouse_down_pos = None
                    if not handled:
                        self.left = False
                        # Always clear right-click arrows/highlights on a left click, like the main game.
                        try:
                            self.highlighted.clear()
                            self.arrows.clear()
                        except Exception:
                            pass

                        if self.updates:
                            # Drag-release path (piece clamped to cursor like main game)
                            self._un_click_left_review()
                        else:
                            # Click-to-move path
                            self._handle_review_board_click(pos)

                        self.updates = False
                        self._mouse_down_pos = None
                elif event.type == pg.MOUSEBUTTONUP and event.button == 3:
                    # Finish highlight/arrow (board-only)
                    try:
                        if not self.left and self._mouse_to_square(event.pos) is not None:
                            self.un_click_right(True)
                    except Exception:
                        pass
                elif event.type == pg.VIDEORESIZE:
                    # Keep resize responsive while in review mode.
                    self.screen = pg.display.set_mode((event.w, event.h), pg.RESIZABLE, vsync=1)
                    self.settings.resize_event()
                    self.background = pg.image.load('data/img/background_dark.png').convert()
                    self.background = pg.transform.smoothscale(self.background, (event.w, event.h))
                    self._ensure_layout(force=True)
                continue

            # Analysis mode: navigation + building a line.
            if self.analysis_active:
                if event.type == pg.KEYDOWN:
                    if event.key == pg.K_ESCAPE:
                        self.exit_analysis()
                    elif event.key == pg.K_LEFT:
                        self.analysis_step(-1)
                        try:
                            self._nav_hold_dir = -1
                            self._nav_hold_next_ms = int(pg.time.get_ticks()) + 240
                        except Exception:
                            pass
                    elif event.key == pg.K_RIGHT:
                        self.analysis_step(1)
                        try:
                            self._nav_hold_dir = 1
                            self._nav_hold_next_ms = int(pg.time.get_ticks()) + 240
                        except Exception:
                            pass
                    elif event.key == pg.K_DOWN:
                        # Enter a saved user variation under the current mainline ply.
                        # Only applies when not already inside a variation.
                        try:
                            if not bool(getattr(self, '_review_analysis_active', False)):
                                base = int(self.analysis_index)
                                if (self._review_variations.get(base, []) or []) and (self._review_variation_fens.get(base, []) or []):
                                    self._analysis_set_variation_cursor(int(base), 1, play_sound=True)
                        except Exception:
                            pass
                    elif event.key == pg.K_UP:
                        # If currently inside a variation, go back to the mainline base position.
                        try:
                            if bool(getattr(self, '_review_analysis_active', False)):
                                base = int(getattr(self, '_review_analysis_base_index', int(self.analysis_index)))
                                self._analysis_set_index(int(base), play_sound=False)
                        except Exception:
                            pass
                    elif event.key == pg.K_s and (pg.key.get_mods() & pg.KMOD_CTRL):
                        self.save_analysis()
                elif event.type == pg.KEYUP:
                    if event.key in (pg.K_LEFT, pg.K_RIGHT):
                        try:
                            self._nav_hold_dir = 0
                            self._nav_hold_next_ms = None
                        except Exception:
                            pass
                elif event.type == pg.MOUSEBUTTONDOWN:
                    if event.button in (4, 5):
                        delta_rows = -3 if event.button == 4 else 3
                        try:
                            rtop = getattr(self, '_analysis_top_rect', None)
                            if rtop is not None and rtop.collidepoint(pg.mouse.get_pos()):
                                try:
                                    step_px = int(self.eval_font.get_linesize() + 6)
                                except Exception:
                                    step_px = 22
                                try:
                                    self.analysis_top_scroll = max(0, int(self.analysis_top_scroll) + int(delta_rows) * int(step_px))
                                except Exception:
                                    self.analysis_top_scroll = 0
                            else:
                                self._analysis_scroll_moves(int(delta_rows))
                        except Exception:
                            self._analysis_scroll_moves(int(delta_rows))
                    elif event.button == 1:
                        # Cog opens settings in analysis mode.
                        try:
                            if self._settings_btn_rect is not None and self._settings_btn_rect.collidepoint(event.pos):
                                self._open_settings_menu()
                                self.left = False
                                self._mouse_down_pos = None
                                self.updates = False
                                self.selected_square = None
                                self._ensure_layout(force=True)
                                continue
                        except Exception:
                            pass

                        # Flip-board button (all modes).
                        try:
                            if self._flip_board_btn_rect is not None and self._flip_board_btn_rect.collidepoint(event.pos):
                                self.flip_board()
                                self.left = False
                                self._mouse_down_pos = None
                                self.updates = False
                                self.selected_square = None
                                continue
                        except Exception:
                            pass

                        # Drag the analysis panel splitter to resize top/bottom boxes.
                        try:
                            srect = getattr(self, '_analysis_splitter_rect', None)
                            if srect is not None and srect.collidepoint(event.pos):
                                self._analysis_split_dragging = True
                                self.left = False
                                self._mouse_down_pos = None
                                self.updates = False
                                continue
                        except Exception:
                            pass
                        # In analysis mode, allow the in-game Undo button (no Resign) to work.
                        try:
                            if self._undo_btn_rect is not None and self._undo_btn_rect.collidepoint(event.pos):
                                self._handle_undo_pressed()
                                continue
                        except Exception:
                            pass
                        # In analysis mode, allow opening saved analyses (next to Undo).
                        try:
                            r = getattr(self, '_open_saved_analysis_btn_rect', None)
                            if r is not None and r.collidepoint(event.pos):
                                self._open_saved_analysis_menu()
                                self.left = False
                                self._mouse_down_pos = None
                                self.updates = False
                                self.selected_square = None
                                self._ensure_layout(force=True)
                                continue
                        except Exception:
                            pass
                        # In analysis mode, allow saving via a button (same as Ctrl+S).
                        try:
                            r = getattr(self, '_save_analysis_btn_rect', None)
                            if r is not None and r.collidepoint(event.pos):
                                self.save_analysis()
                                self.left = False
                                self._mouse_down_pos = None
                                self.updates = False
                                self.selected_square = None
                                self._ensure_layout(force=True)
                                continue
                        except Exception:
                            pass
                        # Board resize handle (bottom-right). Consume the click.
                        try:
                            if self._handle_board_resize_begin(event.pos):
                                continue
                        except Exception:
                            pass
                        # Match main-game behavior: set left-down state and record tx/ty.
                        self.left = True
                        self._mouse_down_pos = pg.mouse.get_pos()
                        self.click_left()
                    elif event.button == 3:
                        # Right-click while dragging cancels the drag (match main-game behavior).
                        try:
                            if self.left or self.updates:
                                self._ignore_next_left_mouse_up = True
                                self.updates_kill()
                                self.selected_square = None
                                self._mouse_down_pos = None
                                self.tx = None
                                self.ty = None
                                self.txr = None
                                self.tyr = None
                                continue
                        except Exception:
                            pass
                        try:
                            if self._mouse_to_square(event.pos) is not None:
                                self.click_right()
                        except Exception:
                            pass
                elif event.type == pg.MOUSEMOTION:
                    # While dragging the analysis splitter, update the top height.
                    try:
                        if bool(getattr(self, '_analysis_split_dragging', False)):
                            panel = getattr(self, '_analysis_panel_rect', None)
                            if panel is not None:
                                new_top = int(event.pos[1]) - int(panel.y)
                                min_top_h = 120
                                min_bottom_h = 140
                                new_top = max(int(min_top_h), min(int(new_top), int(panel.h - min_bottom_h)))
                                self.analysis_top_h_px = int(new_top)
                            continue
                    except Exception:
                        pass
                    if getattr(self, '_board_resize_active', False):
                        try:
                            self._handle_board_resize_drag(event.pos)
                        except Exception:
                            pass
                        continue
                    if self.movement_drag_enabled and self.left and not self.updates and self._mouse_down_pos is not None:
                        mx, my = pg.mouse.get_pos()
                        dx = mx - self._mouse_down_pos[0]
                        dy = my - self._mouse_down_pos[1]
                        if (dx * dx + dy * dy) >= (self._drag_threshold_px * self._drag_threshold_px):
                            self.updates = True
                            self.selected_square = None
                elif event.type == pg.MOUSEBUTTONUP and event.button == 1:
                    # Release splitter drag.
                    try:
                        if bool(getattr(self, '_analysis_split_dragging', False)):
                            self._analysis_split_dragging = False
                            self.left = False
                            self.updates = False
                            self._mouse_down_pos = None
                            continue
                    except Exception:
                        pass
                    # Swallow one-shot left mouse-up after a right-click drag cancel.
                    if getattr(self, '_ignore_next_left_mouse_up', False):
                        self._ignore_next_left_mouse_up = False
                        self.left = False
                        self.updates = False
                        self._mouse_down_pos = None
                        continue
                    if getattr(self, '_board_resize_active', False):
                        try:
                            self._handle_board_resize_end()
                        except Exception:
                            pass
                        self.updates = False
                        self.left = False
                        self._mouse_down_pos = None
                        continue
                    pos = pg.mouse.get_pos()
                    handled = False
                    try:
                        handled = bool(self._handle_analysis_click(pos))
                    except Exception:
                        handled = False
                    was_dragging = bool(self.updates)

                    if handled:
                        # Always release left-click state even if the panel handled the click.
                        self.left = False
                        self.updates = False
                        self._mouse_down_pos = None
                    else:
                        self.left = False
                        try:
                            self.highlighted.clear()
                            self.arrows.clear()
                        except Exception:
                            pass

                        if was_dragging:
                            # Drag-release path
                            self._handle_analysis_drag_release(pos)
                        else:
                            # Click-to-move path
                            self._handle_analysis_board_click(pos)

                        self.updates = False
                        self._mouse_down_pos = None
                elif event.type == pg.MOUSEBUTTONUP and event.button == 3:
                    try:
                        if not self.left and self._mouse_to_square(event.pos) is not None:
                            self.un_click_right(True)
                    except Exception:
                        pass
                elif event.type == pg.VIDEORESIZE:
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
                    # Flip-board button (all modes).
                    try:
                        if self._flip_board_btn_rect is not None and self._flip_board_btn_rect.collidepoint(event.pos):
                            self.flip_board()
                            self.left = False
                            self._mouse_down_pos = None
                            self.updates = False
                            self.selected_square = None
                            continue
                    except Exception:
                        pass

                    # Board resize handle (bottom-right). Consume the click.
                    try:
                        if self._handle_board_resize_begin(event.pos):
                            continue
                    except Exception:
                        pass

                    # In-game action buttons.
                    try:
                        if self._undo_btn_rect is not None and self._undo_btn_rect.collidepoint(event.pos):
                            self._handle_undo_pressed()
                            continue
                        if self._resign_btn_rect is not None and self._resign_btn_rect.collidepoint(event.pos):
                            self._handle_resign_pressed()
                            continue
                        if self._toggle_movelist_btn_rect is not None and self._toggle_movelist_btn_rect.collidepoint(event.pos):
                            try:
                                self.game_movelist_visible = not bool(getattr(self, 'game_movelist_visible', True))
                            except Exception:
                                self.game_movelist_visible = True
                            self._ensure_layout(force=True)
                            continue
                    except Exception:
                        pass

                    # Move list clicks (jump-to-ply) should be handled before any board interaction.
                    try:
                        if self._handle_game_movelist_click(event.pos):
                            continue
                    except Exception:
                        pass

                    # While browsing history, ignore board interaction clicks.
                    if getattr(self, '_game_browse_active', False):
                        continue
                    # Clickable cog opens settings (instead of relying on ESC).
                    try:
                        if (
                            not self.review_active
                            and not self.end_popup_active
                            and self._settings_btn_rect is not None
                            and self._settings_btn_rect.collidepoint(event.pos)
                        ):
                            self._open_settings_menu()
                            self.left = False
                            self._mouse_down_pos = None
                            continue
                    except Exception:
                        pass
                    self.left = True
                    self._mouse_down_pos = pg.mouse.get_pos()
                    self.click_left()
                elif event.button == 3:
                    self.click_right()
                # Mouse wheel no longer flips the board in normal play.
            elif event.type == pg.MOUSEMOTION:
                if getattr(self, '_board_resize_active', False):
                    try:
                        self._handle_board_resize_drag(event.pos)
                    except Exception:
                        pass
                    continue
                # Only start dragging after the cursor moves a bit.
                if self.movement_drag_enabled and self.left and not self.updates and self._mouse_down_pos is not None:
                    mx, my = pg.mouse.get_pos()
                    dx = mx - self._mouse_down_pos[0]
                    dy = my - self._mouse_down_pos[1]
                    if (dx * dx + dy * dy) >= (self._drag_threshold_px * self._drag_threshold_px):
                        self.updates = True
                        self.selected_square = None
            elif event.type == pg.MOUSEBUTTONUP:
                if event.button == 1 and getattr(self, '_board_resize_active', False):
                    self._handle_board_resize_end()
                    self.updates = False
                    self.left = False
                    self._mouse_down_pos = None
                    continue
                if event.button == 1 and self.updates:
                    self.left = False
                    self.un_click_left()
                elif event.button == 1:
                    self.left = False
                    # Always clear right-click arrows/highlights on a left click.
                    # Previously this happened inside un_click_left(), which only runs on drag-release.
                    self.highlighted.clear()
                    self.arrows.clear()
                    if self._ignore_next_left_mouse_up:
                        self._ignore_next_left_mouse_up = False
                    # While browsing history, do not allow making moves.
                    elif getattr(self, '_game_browse_active', False):
                        pass
                    elif self.movement_click_enabled and not self.game_just_ended:
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
                    if self.updates:
                        self._ignore_next_left_mouse_up = True
                    self.updates_kill()
                    self.left = False
                self.updates = False
                self._mouse_down_pos = None
            elif event.type == pg.KEYDOWN:
                # In-game move browsing (left/right arrows)
                if event.key in (pg.K_LEFT, pg.K_RIGHT):
                    try:
                        if not getattr(self, '_game_browse_active', False):
                            self._enter_game_browse()
                        if getattr(self, '_game_browse_active', False):
                            idx = int(self._game_browse_index or 0)
                            if event.key == pg.K_LEFT:
                                self._set_game_browse_index(idx - 1, play_sound=True)
                                try:
                                    self._nav_hold_dir = -1
                                    self._nav_hold_next_ms = int(pg.time.get_ticks()) + 240
                                except Exception:
                                    pass
                            else:
                                # Right arrow: step forward; if at latest, exit browse.
                                if idx >= len(self.game_fens) - 1:
                                    self._exit_game_browse()
                                    try:
                                        self._nav_hold_dir = 0
                                        self._nav_hold_next_ms = None
                                    except Exception:
                                        pass
                                else:
                                    self._set_game_browse_index(idx + 1, play_sound=True)
                                    try:
                                        self._nav_hold_dir = 1
                                        self._nav_hold_next_ms = int(pg.time.get_ticks()) + 240
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                    continue

            elif event.type == pg.KEYUP:
                if event.key in (pg.K_LEFT, pg.K_RIGHT):
                    try:
                        self._nav_hold_dir = 0
                        self._nav_hold_next_ms = None
                    except Exception:
                        pass

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
                    # Hints are disabled in Puzzle Rush.
                    if bool(getattr(self, 'puzzle_rush_active', False)):
                        self.best_move = ''
                        self.hint_arrow = None
                    else:
                        # Hint: temporarily ask for a strong move, then restore configured Elo strength.
                        with self._play_engine_lock:
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
                        with self._play_engine_lock:
                            self._apply_ai_strength()
                if event.key == pg.K_u:
                    try:
                        self._handle_undo_pressed()
                    except Exception:
                        pass
                if event.key == pg.K_ESCAPE:
                    if getattr(self, '_game_browse_active', False):
                        try:
                            self._exit_game_browse()
                        except Exception:
                            pass
                    else:
                        self._open_settings_menu()
            elif event.type == pg.VIDEORESIZE:
                # Window resize: re-fit background + board layout.
                self.screen = pg.display.set_mode((event.w, event.h), pg.RESIZABLE, vsync=1)
                try:
                    self.settings.resize_event()
                except Exception:
                    pass
                try:
                    self.background = pg.image.load('data/img/background_dark.png').convert()
                    self.background = pg.transform.smoothscale(self.background, (event.w, event.h))
                except Exception:
                    pass
                try:
                    self._handle_board_resize_end()
                except Exception:
                    pass
                self._ensure_layout(force=True)

        if self.ai_vs_ai:
            self.un_click_left()

        # Fast key-hold navigation in review/analysis and in-game browse.
        try:
            if self.review_active or self.analysis_active or getattr(self, '_game_browse_active', False):
                hold_dir = int(getattr(self, '_nav_hold_dir', 0) or 0)
                next_ms = getattr(self, '_nav_hold_next_ms', None)
                if hold_dir != 0 and next_ms is not None:
                    now = int(pg.time.get_ticks())
                    interval = 55  # ~18 steps/sec
                    # Catch up in case of a slow frame.
                    while now >= int(next_ms):
                        if self.review_active:
                            self.review_step(hold_dir)
                        elif self.analysis_active:
                            self.analysis_step(hold_dir)
                        elif getattr(self, '_game_browse_active', False):
                            try:
                                idx = int(self._game_browse_index or 0)
                                if hold_dir < 0:
                                    self._set_game_browse_index(idx - 1, play_sound=True)
                                else:
                                    if idx >= len(self.game_fens) - 1:
                                        self._exit_game_browse()
                                        self._nav_hold_dir = 0
                                        self._nav_hold_next_ms = None
                                        break
                                    self._set_game_browse_index(idx + 1, play_sound=True)
                            except Exception:
                                pass
                        next_ms = int(next_ms) + interval
                    self._nav_hold_next_ms = int(next_ms)
            else:
                # Ensure we don't keep auto-stepping after leaving modes.
                self._nav_hold_dir = 0
                self._nav_hold_next_ms = None
        except Exception:
            pass
        pg.display.flip()
        self.clock.tick(150)

    def get_eval(self) -> str:
        """
        Get board evaluation
        :return: Evaluation string
        """
        with self._play_engine_lock:
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
                        # If it's the opponent's turn (PvAI or Puzzle Rush), treat this as a premove.
                        if self._premove_mode_active():
                            try:
                                x = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
                                y = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
                                if self.flipped:
                                    x = -x + 7
                                    y = -y + 7
                            except Exception:
                                x, y = col, row

                            try:
                                self.board[row][col].clicked = False
                            except Exception:
                                pass
                            self.updates = False
                            self.left = False
                            self._set_premove(row, col, y, x)
                            break

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
                                # This handler is only used for player input, so always record the move.
                                # (In Player vs AI, the AI move is recorded in _poll_ai_result.)
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
                                # If the player's move ended the game, do not let the AI move.
                                if not self.end_popup_active and not self.game_just_ended:
                                    self._request_ai_move_async()
                                if EVAL_ON:
                                    self.get_eval()
                        else:
                            self.board[row][col].clicked = False
                        break

    def _un_click_left_review(self) -> None:
        """Drag-release handler for review mode.

        Mirrors the main-game drag behavior (piece clamps to cursor) but records the move
        as an analysis variation instead of modifying the actual game/PGN.
        """
        if not self.review_active:
            return

        fen_before = self._review_display_fen()
        if not fen_before:
            # Ensure we stop dragging.
            for piece in self.all_pieces:
                piece.clicked = False
            return

        # Find the clicked piece (set by update_board once drag threshold is exceeded).
        moved_piece = None
        for piece in self.all_pieces:
            try:
                if piece.clicked:
                    moved_piece = piece
                    break
            except Exception:
                continue
        if moved_piece is None:
            return

        # Attempt to apply move using piece.make_move (same as main game).
        row = int(moved_piece.position[0])
        col = int(moved_piece.position[1])
        ok = False
        try:
            ok = bool(moved_piece.make_move(self.board, self.offset, self.turn, self.flipped, None, None))
        except Exception:
            ok = False

        # Compute destination square from current mouse position (same logic as un_click_left).
        try:
            x = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
            y = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
            if self.flipped:
                x = -x + 7
                y = -y + 7
        except Exception:
            x, y = col, row

        # Always stop dragging visuals.
        try:
            moved_piece.clicked = False
        except Exception:
            pass

        if not ok:
            # Re-sync UI to the original position.
            self._load_fen_into_ui(fen_before)
            self.selected_square = None
            return

        # Build UCI (auto-promote to queen like main game)
        uci = translate_move(row, col, y, x)
        try:
            if moved_piece.piece.lower() == 'p' and (y == 0 or y == 7):
                uci += 'q'
        except Exception:
            pass

        # Validate/apply via python-chess to derive SAN and new FEN.
        try:
            b = chess.Board(fen_before)
        except Exception:
            self._load_fen_into_ui(fen_before)
            self.selected_square = None
            return

        try:
            mv = chess.Move.from_uci(str(uci))
            if mv not in b.legal_moves:
                raise ValueError('illegal')
            san = b.san(mv)
            b.push(mv)
        except Exception:
            # Fall back: cancel move and restore.
            self._load_fen_into_ui(fen_before)
            self.selected_square = None
            return

        # If we're on a previous mainline position and the move matches the next mainline move,
        # do not create a variation; just move forward on the mainline.
        try:
            if (not bool(getattr(self, '_review_analysis_active', False))) and int(self.review_index) < int(len(self.review_fens) - 1):
                if 0 <= int(self.review_index) < len(self.review_plies) and str(mv.uci()) == str(self.review_plies[int(self.review_index)].uci()):
                    self._review_set_index(int(self.review_index) + 1, play_sound=True)
                    self.selected_square = None
                    return
        except Exception:
            pass

        # Start or continue an analysis branch.
        if not self._review_analysis_active:
            self._review_analysis_active = True
            self._review_analysis_base_index = int(self.review_index)
            self._review_analysis_cursor = 0
            self._review_variations[int(self.review_index)] = []
            self._review_variation_fens[int(self.review_index)] = []
            self._review_variation_ucis[int(self.review_index)] = []
            try:
                self._review_variation_labels[int(self.review_index)] = []
            except Exception:
                pass

        # This is a user-built line; PV browsing mode is not active.
        try:
            self._review_pv_variation_active = False
        except Exception:
            pass

        base = int(self._review_analysis_base_index)
        cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)

        # If the user stepped back within the variation and then makes a new move,
        # truncate the tail and continue from the current cursor.
        try:
            if cur < len(self._review_variations.get(base, [])):
                self._review_variations[base] = list(self._review_variations.get(base, [])[:cur])
        except Exception:
            pass
        try:
            if cur < len(self._review_variation_fens.get(base, [])):
                self._review_variation_fens[base] = list(self._review_variation_fens.get(base, [])[:cur])
        except Exception:
            pass
        try:
            if cur < len(self._review_variation_ucis.get(base, [])):
                self._review_variation_ucis[base] = list(self._review_variation_ucis.get(base, [])[:cur])
        except Exception:
            pass
        try:
            if cur < len((getattr(self, '_review_variation_labels', {}) or {}).get(base, []) or []):
                self._review_variation_labels[base] = list((self._review_variation_labels.get(base, []) or [])[:cur])
        except Exception:
            pass

        new_fen = b.fen()
        self._review_analysis_fen = str(new_fen)
        try:
            self._review_variations[int(base)].append(str(san))
        except Exception:
            pass
        try:
            self._review_variation_fens[int(base)].append(str(new_fen))
        except Exception:
            pass
        try:
            self._review_variation_ucis[int(base)].append(str(uci))
        except Exception:
            pass
        try:
            self._review_variation_labels[int(base)].append('')
        except Exception:
            pass
        try:
            self._review_analysis_cursor = len(self._review_variation_fens.get(int(base), []) or [])
        except Exception:
            pass

        self.last_move = [str(uci)]
        self._load_fen_into_ui(new_fen)
        self.request_eval()
        self.request_review_pv()

        # Best-move arrow from mainline no longer applies.
        self.review_best_move_uci = ''
        self.review_arrow = None

        # Sound feedback.
        try:
            self._play_sound_for_move_from_fen(fen_before, mv)
        except Exception:
            pass

        # Persist the variation immediately (labels may fill in asynchronously).
        try:
            if self.review_active and self.review_pgn_path:
                self._save_review_analysis_cache(str(self.review_pgn_path), self.review_plies or [])
        except Exception:
            pass

        # Analyze labels for this variation in the background.
        try:
            self._start_review_variation_analysis_thread(int(base))
        except Exception:
            pass

        self.selected_square = None

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
        w, h = self.screen.get_width(), self.screen.get_height()
        self.screen = pg.display.set_mode((w, h), pg.RESIZABLE, vsync=1)
        try:
            self.settings.resize_event()
        except Exception:
            pass

        try:
            self.background = pg.image.load('data/img/background_dark.png').convert()
            self.background = pg.transform.smoothscale(self.background, (w, h))
        except Exception:
            pass

        try:
            self._handle_board_resize_end()
        except Exception:
            pass

        self._ensure_layout(force=True)

    def change_mode(self, mode: str):
        """
        Changes the game mode to Player vs Player, Player vs AI, or AI vs AI
        :param mode: String of the mode: 'pvp', 'pvai', or 'aivai'
        :return: None
        """
        # Any non-rush mode disables Puzzle Rush state.
        if mode != 'puzzlerush':
            self.puzzle_rush_active = False

        if mode == 'pvp':
            self.ai_vs_ai = False
            self.player_vs_ai = False
        elif mode == 'aivai':
            self.ai_vs_ai = True
            self.player_vs_ai = False
        elif mode == 'pvai':
            self.ai_vs_ai = False
            self.player_vs_ai = True
        elif mode == 'puzzlerush':
            self.ai_vs_ai = False
            self.player_vs_ai = False
            self.puzzle_rush_active = True

    def start_puzzle_rush_new(self) -> None:
        """Start a new Puzzle Rush run (separate from the normal mode dropdown)."""
        try:
            if getattr(self, 'review_active', False):
                self.exit_review(return_to_start_menu=False)
        except Exception:
            pass
        try:
            self.analysis_active = False
        except Exception:
            pass

        try:
            self.change_mode('puzzlerush')
        except Exception:
            self.puzzle_rush_active = True
            self.player_vs_ai = False
            self.ai_vs_ai = False

        # No clocks in Puzzle Rush.
        try:
            self._clock_running = False
        except Exception:
            pass

        try:
            self.reset_game()
        except Exception:
            pass

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
        # Legacy synchronous path (AI vs AI). Serialize access to the engine.
        with self._play_engine_lock:
            move = self.move_strength(self.ai_elo)
        if move is not None:
            self.last_move.append(move)
            # Do NOT append promotion suffix here.
            # Stockfish already returns promotion in UCI when needed (e.g. a7a8q),
            # and the previous code used the player's last-clicked piece, corrupting PGNs.
            self.node = self.node.add_variation(chess.Move.from_uci(move))
            self.engine_make_move(move)  # Making the move
        else:
            # No legal moves available: this usually means the game is already over
            # (checkmate/stalemate). Show the end popup rather than resetting.
            try:
                b = self.node.board()
                if b.is_repetition():
                    self.end_game("DRAW BY REPETITION")
                elif b.is_insufficient_material():
                    self.end_game("INSUFFICIENT MATERIAL")
                elif b.is_stalemate():
                    self.end_game("STALEMATE")
                elif b.is_checkmate():
                    outcome = b.outcome()
                    if outcome and outcome.winner is True:
                        self.end_game("CHECKMATE WHITE WINS !!")
                    elif outcome and outcome.winner is False:
                        self.end_game("CHECKMATE BLACK WINS !!")
                    else:
                        self.end_game("CHECKMATE")
                else:
                    self.end_game('Fault')
            except Exception:
                self.end_game('Fault')

    def move_strength(self, elo: int) -> str | None:
        """
        Get a move given an approximate Elo strength.
        Uses Stockfish's built-in Elo limiter when available.
        :param elo: requested Elo
        :return: Move - the algebraic notation of the move as a string
        """
        with self._play_engine_lock:
            self._apply_ai_strength()

        # Simple think-time scaling. Smaller time at low Elo makes play weaker/less consistent.
        try:
            elo_i = int(elo)
        except Exception:
            elo_i = self.ai_elo

        # IMPORTANT: movetime heavily affects playing strength.
        # Keep this modest; the "real-time" thinking delay is handled separately
        # (see _ai_delay_seconds_from_clock).
        if elo_i <= 800:
            think_ms = 70
        elif elo_i <= 1200:
            think_ms = 110
        elif elo_i <= 1600:
            think_ms = 170
        elif elo_i <= 2000:
            think_ms = 260
        elif elo_i <= 2400:
            think_ms = 420
        elif elo_i <= 2800:
            think_ms = 650
        else:
            think_ms = 900

        # Add human-like imperfection by sometimes choosing from the top moves.
        # This helps prevent "perfect" play at mid Elos even with tiny movetimes.
        # Lower Elo -> more randomness; higher Elo -> mostly best move.
        top_n = 1
        if elo_i < 900:
            top_n = 4
        elif elo_i < 1400:
            top_n = 3
        elif elo_i < 2000:
            top_n = 2
        else:
            top_n = 1

        if top_n > 1:
            try:
                # get_top_moves uses MultiPV. Ensure it's high enough for this query.
                try:
                    self.stockfish.update_engine_parameters({"MultiPV": int(top_n)})
                except Exception:
                    pass
                top_moves = self.stockfish.get_top_moves(top_n) or []
                # Keep evaluations so we can avoid choosing obvious blunders.
                scored: list[tuple[str, int | None, int | None]] = []  # (uci, centipawn, mate)
                for m in top_moves:
                    if not isinstance(m, dict):
                        continue
                    uci = m.get('Move')
                    if not uci:
                        continue
                    cp = None
                    mate = None
                    try:
                        if m.get('Centipawn') is not None:
                            cp = int(m.get('Centipawn'))
                        elif m.get('Centipawns') is not None:
                            cp = int(m.get('Centipawns'))
                    except Exception:
                        cp = None
                    try:
                        if m.get('Mate') is not None:
                            mate = int(m.get('Mate'))
                    except Exception:
                        mate = None
                    scored.append((str(uci), cp, mate))

                # Always assume Stockfish returns best move first.
                best_cp = scored[0][1] if scored else None
                best_mate = scored[0][2] if scored else None

                # Max allowed eval drop (centipawns) when intentionally deviating.
                # Smaller at higher Elo so the bot doesn't throw games.
                if elo_i < 900:
                    max_loss_cp = 260
                elif elo_i < 1400:
                    max_loss_cp = 160
                elif elo_i < 2000:
                    max_loss_cp = 90
                else:
                    max_loss_cp = 60

                def acceptable(uci: str, cp: int | None, mate: int | None) -> bool:
                    # Never pick a move that walks into a forced mate against us
                    # if the best line isn't also losing by mate.
                    if best_mate is None:
                        if mate is not None and mate < 0:
                            return False
                    else:
                        # If best is a winning mate, only pick other winning mates.
                        if best_mate > 0:
                            return mate is not None and mate > 0
                        # If best is a losing mate, avoid making it much faster.
                        if best_mate < 0:
                            if mate is None or mate >= 0:
                                return False
                            try:
                                # Allow at most 2 moves faster mate against us.
                                return abs(int(mate)) >= (abs(int(best_mate)) - 2)
                            except Exception:
                                return False

                    # Centipawn based filtering.
                    if best_cp is None or cp is None:
                        # If we don't have cp scores, be conservative.
                        return uci == scored[0][0]
                    try:
                        loss = int(best_cp) - int(cp)
                    except Exception:
                        return uci == scored[0][0]
                    return loss <= int(max_loss_cp)

                legal = [uci for (uci, cp, mate) in scored if acceptable(uci, cp, mate)]
                try:
                    self.stockfish.update_engine_parameters({"MultiPV": 1})
                except Exception:
                    pass
                if legal:
                    # Bias toward the best move as Elo increases.
                    if len(legal) == 2:
                        # (best, second)
                        p_best = 0.55 if elo_i < 900 else (0.75 if elo_i < 1400 else 0.88)
                        return legal[0] if random.random() < p_best else legal[1]
                    if len(legal) >= 3:
                        # (best, second, third, ...)
                        if elo_i < 900:
                            weights = [0.40, 0.30, 0.30]
                        elif elo_i < 1400:
                            weights = [0.65, 0.25, 0.10]
                        else:
                            weights = [0.82, 0.15, 0.03]
                        r = random.random()
                        if r < weights[0]:
                            return legal[0]
                        if r < weights[0] + weights[1]:
                            return legal[1]
                        return legal[2]
            except Exception:
                pass

        return self.stockfish.get_best_move_time(int(think_ms))

    def _apply_ai_strength(self) -> None:
        """Apply current AI-strength settings to the Stockfish instance."""
        try:
            elo_i = int(self.ai_elo)
        except Exception:
            elo_i = 800
            self.ai_elo = elo_i

        # Per stockfish-python docs:
        # - set_elo_rating() ignores skill level
        # - set_skill_level() ignores ELO rating
        # - UCI_LimitStrength must be set appropriately.

        # Treat the top end as effectively "max strength".
        if elo_i >= 2900:
            try:
                self.stockfish.update_engine_parameters({"UCI_LimitStrength": "false"})
            except Exception:
                pass
            try:
                self.stockfish.set_skill_level(20)
            except Exception:
                pass
            return

        # Otherwise limit strength.
        # We apply BOTH:
        # - UCI_LimitStrength/UCI_Elo (when supported by the engine)
        # - Skill Level mapping (fallback if UCI Elo limiting is ignored)
        try:
            self.stockfish.update_engine_parameters({"UCI_LimitStrength": "true", "UCI_Elo": int(elo_i)})
        except Exception:
            try:
                self.stockfish.update_engine_parameters({"UCI_LimitStrength": "true"})
            except Exception:
                pass

        try:
            self.stockfish.set_elo_rating(int(elo_i))
        except Exception:
            pass

    def _ai_delay_seconds_from_clock(self) -> float:
        """Return a human-like think delay based on remaining clock time.

        Capped at 2 seconds, and also capped by remaining time to avoid going negative.
        """
        try:
            side = self._ai_side()
            remaining = float(self._clock_white if side == 'w' else self._clock_black)
        except Exception:
            return 0.75

        try:
            inc = float(getattr(self, '_tc_increment_seconds', 0) or 0)
        except Exception:
            inc = 0.0

        # Base "human" delay from clock: ~remaining/30 plus a bit of increment.
        delay = (remaining / 30.0) + (0.60 * inc)
        delay = max(0.20, min(2.0, delay))

        # Never exceed remaining time (leave a tiny buffer).
        if remaining <= 0.25:
            return 0.0
        delay = min(delay, max(0.0, remaining - 0.10))
        return float(delay)

    def change_ai_elo(self, elo: int) -> None:
        """Set AI strength to a target Elo rating."""
        try:
            self.ai_elo = int(elo)
        except Exception:
            return
        with self._play_engine_lock:
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
        # If right-click was used only to cancel premoves, txr/tyr may be None.
        if self.txr is None or self.tyr is None:
            return

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
        # Clear hint arrow after any move.
        self.hint_arrow = None

        # If the user was click-selecting a piece during premove mode (opponent to move),
        # keep that selection when the opponent moves so the user can still complete the
        # click-click premove/instant reply instead of losing the highlighted legal moves.
        keep_selection = False
        try:
            if self.selected_square is not None and (self.player_vs_ai or getattr(self, 'puzzle_rush_active', False)):
                mover = 'b' if str(self.turn) == 'w' else 'w'
                if bool(getattr(self, '_premove_autoplay', False)):
                    # A queued premove just auto-applied. If we're back to premove-mode now
                    # (opponent to move again), preserve the user's selection so dots stay visible.
                    if bool(self._premove_mode_active()):
                        r, c = self.selected_square
                        p = self._virtual_player_piece_at(int(r), int(c))
                        if p is not None and p != ' ' and getattr(p, 'colour', [''])[0] == self._premove_player_side():
                            keep_selection = True
                elif mover == self._premove_ai_side():
                    r, c = self.selected_square
                    # Selection may refer to the *virtual* premove position (especially with >1 premove).
                    # Use the virtual board so we don't drop selection just because the real board differs.
                    p = self._virtual_player_piece_at(int(r), int(c))
                    if p is not None and p != ' ' and getattr(p, 'colour', [''])[0] == str(self.turn):
                        keep_selection = True
        except Exception:
            keep_selection = False
        if not keep_selection:
            self.selected_square = None

        # Apply increment to the side that just moved.
        try:
            mover = 'b' if self.turn == 'w' else 'w'
            self._on_move_completed_clock(mover)
        except Exception:
            pass
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
        try:
            with self._play_engine_lock:
                self.stockfish.set_fen_position(self.game_fens[-1])
        except Exception:
            pass

        # async eval update
        self.request_eval()
        # print(self.game_fens[-1])

        # Puzzle Rush: validate against the expected line and avoid normal game-over logic.
        # (Many puzzles end in mate, which would otherwise trigger end_game and stop the run.)
        if getattr(self, 'puzzle_rush_active', False):
            try:
                uci = str(self.last_move[-1]) if self.last_move else ''
            except Exception:
                uci = ''
            try:
                mover = 'b' if self.turn == 'w' else 'w'
            except Exception:
                mover = 'w'
            try:
                self._puzzle_rush_on_move(mover=mover, uci=uci)
            except Exception:
                pass
            return

        if not self.player_vs_ai and not self.ai_vs_ai and self.flip_enabled:
            self.flip_board()

        b = None
        try:
            b = self.node.board()
        except Exception:
            try:
                b = chess.Board(str(self.game_fens[-1] if self.game_fens else self._current_fen()))
            except Exception:
                b = None

        if b is not None and b.is_repetition():
            if self.sound_enabled:
                pg.mixer.music.load('data/sounds/mate.wav')
                pg.mixer.music.play(1)
                time.sleep(0.15)
                pg.mixer.music.play(1)
            self.end_game("DRAW BY REPETITION")
        elif b is not None and b.is_stalemate():
            if self.sound_enabled:
                pg.mixer.music.load('data/sounds/mate.wav')
                pg.mixer.music.play(1)
                time.sleep(0.15)
                pg.mixer.music.play(1)
            self.end_game("INSUFFICIENT MATERIAL")
        elif b is not None and b.is_insufficient_material():
            if self.sound_enabled:
                pg.mixer.music.load('data/sounds/mate.wav')
                pg.mixer.music.play(1)
                time.sleep(0.15)
                pg.mixer.music.play(1)
            self.end_game("INSUFFICIENT MATERIAL")
        elif (b is not None and b.is_checkmate()) or legal_moves == 0:
            if self.sound_enabled:
                pg.mixer.music.load('data/sounds/mate.wav')
                pg.mixer.music.play(1)
                time.sleep(0.15)
                pg.mixer.music.play(1)
            try:
                if b is not None and b.outcome() and b.outcome().winner is True:
                    self.end_game("CHECKMATE WHITE WINS !!")
                else:
                    self.end_game("CHECKMATE BLACK WINS !!")
            except Exception:
                self.end_game("CHECKMATE")
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
        try:
            self._clock_running = False
        except Exception:
            pass
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
        # Puzzle Rush has its own reset flow (random pack, then ordered puzzles).
        if getattr(self, 'puzzle_rush_active', False):
            try:
                self._puzzle_rush_start_new_run()
            except Exception:
                # Fall back to normal reset if anything goes wrong.
                self.puzzle_rush_active = False
            else:
                return

        # If the previous game ended, a reset should take the user back to the start setup.
        # The run-loop shows the StartMenu when _start_menu_shown is False.
        try:
            if bool(getattr(self, 'game_just_ended', False)):
                self._start_menu_shown = False
                self._clock_running = False
                self._clock_last_ts = None
        except Exception:
            pass

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
            if self._ai_side() == 'w':
                self.game.headers["White"] = "Computer"
                self.game.headers["Black"] = "Player"
            else:
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
        self._clear_premove()
        try:
            # Reset clocks for a new game.
            self._reset_clocks(start_running=True)
        except Exception:
            pass
        try:
            self.ai_thinking = False
            self._ai_result = ''
            self._ai_result_fen = ''
        except Exception:
            pass
        try:
            with self._play_engine_lock:
                self.stockfish.set_fen_position(self.game_fens[0])
        except Exception:
            pass
        self.update_legal_moves()
        self.request_eval()

        # If PvAI and it's the AI side to move (e.g., player chose Black), start AI immediately.
        try:
            if self.player_vs_ai and not self.end_popup_active and not self.game_just_ended and self.turn == self._ai_side():
                self._request_ai_move_async()
        except Exception:
            pass

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
            # Ensure no piece remains in a dragged/clicked visual state.
            try:
                self.updates_kill()
                self.selected_square = None
                self._mouse_down_pos = None
            except Exception:
                pass
            if one:
                self.board, self.turn, self.castle_rights, self.en_passant_square, self.halfmoves_since_last_capture, self.fullmove_number = parse_FEN(
                    self.game_fens[0])
            else:
                self.game_fens.pop()
                self.board, self.turn, self.castle_rights, self.en_passant_square, self.halfmoves_since_last_capture, self.fullmove_number = parse_FEN(
                    self.game_fens[-1])
            for p in list(self.all_pieces):
                self.all_pieces.remove(p)
            for p in list(self.black_pieces):
                self.black_pieces.remove(p)
            for p in list(self.white_pieces):
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
                try:
                    piece.clicked = False
                except Exception:
                    pass
            self.last_move.pop()
            self.node = self.node.parent  # allows for undoes to show in analysis on https://chess.com/analysis
            if not self.player_vs_ai and not self.ai_vs_ai and self.flip_enabled:
                self.flip_board()

            # Do NOT call update_board() here. It can latch onto the mouse cursor and
            # make a piece follow the cursor even when the mouse button isn't held.
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
            if hasattr(piece, 'legal_captures'):
                piece.legal_captures = set()

        # populate legal moves for side to move
        for move in pc_board.legal_moves:
            from_row, from_col = self._coords_from_chess_square(move.from_square)
            to_row, to_col = self._coords_from_chess_square(move.to_square)
            piece = self.board[from_row][from_col]
            if piece != ' ' and piece.colour[0] == self.turn:
                dx_dy = (to_col - from_col, to_row - from_row)
                piece.legal_positions.append(dx_dy)
                if hasattr(piece, 'legal_captures') and pc_board.is_capture(move):
                    piece.legal_captures.add(dx_dy)

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
        # If a premove is queued, right-click cancels all premoves (and does not create arrows/highlights).
        try:
            if self.player_vs_ai and not self.review_active and not self.end_popup_active and self._premove_queue:
                self._clear_premove()
                self.selected_square = None
                self.txr = None
                self.tyr = None
                return
        except Exception:
            pass
        self.txr = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
        self.tyr = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
        if self.flipped:
            self.txr = 7 - self.txr
            self.tyr = 7 - self.tyr

    def _board_rect(self) -> pg.Rect:
        return pg.Rect(int(self.offset[0]), int(self.offset[1]), int(self.size * 8), int(self.size * 8))

    def _handle_board_resize_begin(self, pos: tuple[int, int]) -> bool:
        """Start a board resize drag if the user clicked the bottom-right handle."""
        if self.end_popup_active:
            return False
        rect = getattr(self, '_board_resize_handle_rect', None)
        if rect is None:
            return False
        try:
            if rect.collidepoint(pos):
                self._board_resize_active = True
                self._board_resize_start_mouse = (int(pos[0]), int(pos[1]))
                self._board_resize_start_size = int(self.size)
                # Cancel any in-progress piece drag/selection.
                self.updates_kill()
                self.selected_square = None
                return True
        except Exception:
            return False
        return False

    def _handle_board_resize_drag(self, pos: tuple[int, int]) -> None:
        if not getattr(self, '_board_resize_active', False):
            return
        if self._board_resize_start_mouse is None or self._board_resize_start_size is None:
            return
        try:
            mx0, my0 = self._board_resize_start_mouse
            dx = int(pos[0]) - int(mx0)
            dy = int(pos[1]) - int(my0)
            delta = max(dx, dy)
            # Convert pixel delta to per-square delta.
            desired_size = int(self._board_resize_start_size + round(delta / 8.0))
        except Exception:
            return

        base = int(getattr(self, '_board_base_size', max(1, int(self.size))))
        desired_size = max(10, min(base, desired_size))
        try:
            self._board_user_scale = max(0.45, min(1.0, float(desired_size) / float(max(1, base))))
        except Exception:
            pass
        self._layout_cache_key = None
        self._ensure_layout(force=True)

    def _handle_board_resize_end(self) -> None:
        self._board_resize_active = False
        self._board_resize_start_mouse = None
        self._board_resize_start_size = None

    def _draw_game_action_buttons(self) -> None:
        """Draw small Undo/Resign buttons on the game screen."""
        if getattr(self, 'puzzle_rush_active', False) or self.end_popup_active or self.review_active:
            self._undo_btn_rect = None
            self._resign_btn_rect = None
            self._toggle_movelist_btn_rect = None
            try:
                self._open_saved_analysis_btn_rect = None
                self._save_analysis_btn_rect = None
            except Exception:
                pass
            return

        # In analysis mode, show Undo + Open Saved Analysis (no Resign).
        analysis_only = bool(getattr(self, 'analysis_active', False))

        board_rect = self._board_rect()
        margin = max(8, int(self.size * 0.12))
        box_h = max(26, int(self.size * 0.55))
        btn_h = max(24, int(box_h * 0.92))

        # Prefer above the board; if it doesn't fit, place below the board to avoid overlap.
        try:
            win_h = int(self.screen.get_height())
        except Exception:
            win_h = int(board_rect.bottom + 100)
        y_above = int(board_rect.top - btn_h - margin)

        # Normal game mode shows clocks above/below the board.
        # Place the buttons *above the top clock* so they never overlap.
        if not analysis_only:
            try:
                y_black = int(board_rect.top - box_h - margin)
                gap2 = max(6, int(margin * 0.8))
                y_above = int(y_black - btn_h - gap2)
            except Exception:
                pass

        if y_above >= 6:
            y = int(y_above)
        else:
            y = int(board_rect.bottom + margin)
            if y + btn_h > win_h - 6:
                y = max(6, min(int(board_rect.top), win_h - btn_h - 6))
        btn_w = max(88, int(self.size * 1.55))
        gap = max(8, int(self.size * 0.16))
        x0 = int(board_rect.left)

        disabled = bool(getattr(self, '_game_browse_active', False))
        fg = (210, 210, 210) if not disabled else (140, 140, 140)
        bg = (20, 20, 20)
        border = (80, 80, 80)

        def draw_btn(label: str, x: int, w: int | None = None) -> pg.Rect:
            ww = int(w if w is not None else btn_w)
            r = pg.Rect(int(x), int(y), int(ww), int(btn_h))
            try:
                pg.draw.rect(self.screen, bg, r, border_radius=8)
                pg.draw.rect(self.screen, border, r, width=2, border_radius=8)
            except Exception:
                pass
            try:
                surf = self.font.render(label, True, fg)
                self.screen.blit(surf, (r.centerx - surf.get_width() // 2, r.centery - surf.get_height() // 2))
            except Exception:
                pass
            return r

        if analysis_only:
            self._resign_btn_rect = None
            self._toggle_movelist_btn_rect = None
            try:
                # In analysis mode, provide larger buttons so text never clips.
                pad = max(18, int(self.size * 0.35))
                w_undo = int(self.font.size('Undo')[0]) + int(pad)
                w_open = int(self.font.size('Open Saved Analysis')[0]) + int(pad)
                w_save = int(self.font.size('Save')[0]) + int(pad)

                self._undo_btn_rect = draw_btn('Undo', x0, w=w_undo)
                x1 = x0 + int(w_undo) + gap
                self._open_saved_analysis_btn_rect = draw_btn('Open Saved Analysis', x1, w=w_open)
                x2 = x1 + int(w_open) + gap
                self._save_analysis_btn_rect = draw_btn('Save', x2, w=w_save)
            except Exception:
                # Safe fallbacks.
                self._undo_btn_rect = draw_btn('Undo', x0)
                self._open_saved_analysis_btn_rect = None
                self._save_analysis_btn_rect = None
        else:
            self._undo_btn_rect = draw_btn('Undo', x0)
            try:
                self._open_saved_analysis_btn_rect = None
                self._save_analysis_btn_rect = None
            except Exception:
                pass
            self._resign_btn_rect = draw_btn('Resign', x0 + btn_w + gap)

            # Normal game: toggle move list panel.
            try:
                label = 'Hide Moves' if bool(getattr(self, 'game_movelist_visible', True)) else 'Show Moves'
                pad = max(18, int(self.size * 0.35))
                w_t = int(self.font.size(label)[0]) + int(pad)
                self._toggle_movelist_btn_rect = draw_btn(label, x0 + (btn_w + gap) * 2, w=w_t)
            except Exception:
                self._toggle_movelist_btn_rect = None

    def _game_sans(self) -> list[str]:
        """Return SAN list for the current main game (cached by ply count)."""
        try:
            n = int(len(self.last_move or []))
        except Exception:
            n = 0
        try:
            if int(getattr(self, '_game_san_cache_n', -1)) == n:
                return list(getattr(self, '_game_san_cache', []) or [])
        except Exception:
            pass

        sans: list[str] = []
        try:
            start_fen = str(self.game_fens[0]) if getattr(self, 'game_fens', None) else chess.STARTING_FEN
        except Exception:
            start_fen = chess.STARTING_FEN

        try:
            b = chess.Board(start_fen)
        except Exception:
            b = chess.Board()

        for uci in list(self.last_move or []):
            try:
                mv = chess.Move.from_uci(str(uci))
                san = b.san(mv)
                sans.append(str(san))
                b.push(mv)
            except Exception:
                # Fallback to UCI token if SAN fails.
                try:
                    sans.append(str(uci))
                except Exception:
                    pass
                try:
                    mv = chess.Move.from_uci(str(uci))
                    if mv in b.legal_moves:
                        b.push(mv)
                except Exception:
                    pass

        self._game_san_cache_n = int(n)
        self._game_san_cache = list(sans)
        return list(sans)

    def _game_movelist_rows(self) -> list[str]:
        """Format current game moves as rows like '1. e4 e5'."""
        try:
            start_fen = str(self.game_fens[0]) if getattr(self, 'game_fens', None) else chess.STARTING_FEN
            b0 = chess.Board(start_fen)
            start_fullmove = int(b0.fullmove_number)
            start_turn_white = bool(b0.turn == chess.WHITE)
        except Exception:
            start_fullmove = 1
            start_turn_white = True

        # Always show the full move list; browsing does not delete/hide moves.
        sans = self._game_sans()

        rows: list[str] = []
        i = 0
        move_no = int(start_fullmove)
        if not start_turn_white and i < len(sans):
            rows.append(f"{move_no}... {sans[i]}")
            i += 1
            move_no += 1
        while i < len(sans):
            white = sans[i] if i < len(sans) else ''
            black = sans[i + 1] if (i + 1) < len(sans) else ''
            row = f"{move_no}. {white}"
            if black:
                row = f"{row} {black}"
            rows.append(row)
            i += 2
            move_no += 1
        return rows

    def _draw_game_movelist_overlay(self) -> None:
        """Draw a clickable move list panel for normal gameplay."""
        # Reset hitboxes every frame.
        try:
            self._game_move_hitboxes = []
        except Exception:
            pass
        try:
            self._game_movelist_prev_rect = None
            self._game_movelist_next_rect = None
        except Exception:
            pass
        try:
            self._game_movelist_panel_rect = None
        except Exception:
            pass

        try:
            if not bool(getattr(self, 'game_movelist_visible', True)):
                return
        except Exception:
            return

        try:
            win_w, win_h = self.screen.get_size()
        except Exception:
            return

        # Fixed 60/40 split: board left 60%, move list in right 40%.
        # Panel height is based on the window, not the board, so shrinking the board
        # (via the resize handle) doesn't shrink the move list.
        margin = 14
        gap = 18
        split_x = int(win_w * 0.60)
        x = int(split_x + gap)
        y = int(margin)
        w = int(win_w - x - gap)
        h = int(win_h - 2 * margin)

        if w < 220 or h < 140:
            return

        panel = pg.Rect(int(x), int(y), int(w), int(h))
        try:
            self._game_movelist_panel_rect = panel
        except Exception:
            pass

        bg = pg.Surface((panel.w, panel.h), pg.SRCALPHA)
        bg.fill((0, 0, 0, 160))
        self.screen.blit(bg, (panel.x, panel.y))
        pg.draw.rect(self.screen, (140, 140, 140), panel, width=1, border_radius=6)

        pad = 10
        row_h = self.eval_font.get_linesize() + 6
        xx = int(panel.x + pad)
        yy = int(panel.y + pad)

        title = self.eval_font.render('Moves:', False, (255, 255, 255))
        self.screen.blit(title, (xx, yy))
        yy += int(row_h)

        # Determine which ply should be highlighted.
        current_ply = -1
        try:
            if getattr(self, '_game_browse_active', False) and self._game_browse_index is not None:
                current_ply = int(self._game_browse_index) - 1
            else:
                current_ply = int(len(self.last_move or [])) - 1
        except Exception:
            current_ply = -1

        # Build a 2-column move table with ply indices.
        try:
            start_fen = str(self.game_fens[0]) if getattr(self, 'game_fens', None) else chess.STARTING_FEN
            b0 = chess.Board(start_fen)
            move_no = int(b0.fullmove_number)
            start_turn_white = bool(b0.turn == chess.WHITE)
        except Exception:
            move_no = 1
            start_turn_white = True

        # Always show the full move list; browsing only changes the highlighted ply.
        sans = self._game_sans()

        items: list[tuple[str, str, int | None, str, int | None]] = []
        ply_to_row: dict[int, int] = {}

        ply = 0
        if not start_turn_white and ply < len(sans):
            items.append((f"{move_no}...", '', None, str(sans[ply]), int(ply)))
            ply_to_row[int(ply)] = int(len(items) - 1)
            ply += 1
            move_no += 1

        while ply < len(sans):
            w_san = str(sans[ply]) if ply < len(sans) else ''
            w_ply = int(ply) if ply < len(sans) else None
            if w_ply is not None:
                ply_to_row[int(w_ply)] = int(len(items))
            ply += 1

            b_san = ''
            b_ply: int | None = None
            if ply < len(sans):
                b_san = str(sans[ply])
                b_ply = int(ply)
                ply_to_row[int(b_ply)] = int(len(items))
                ply += 1

            items.append((f"{move_no}.", w_san, w_ply, b_san, b_ply))
            move_no += 1

        list_top = int(yy)
        # Reserve footer space for prev/next arrows.
        footer_h = max(28, int(row_h))
        footer_y = int(panel.bottom - pad - footer_h)
        list_bottom = int(footer_y - 6)
        visible_rows = max(1, int((list_bottom - list_top) // row_h))
        if visible_rows <= 0:
            return

        max_start = max(0, len(items) - visible_rows)
        if current_ply >= 0 and current_ply in ply_to_row:
            row_idx = int(ply_to_row[int(current_ply)])
            start = max(0, min(max_start, row_idx - visible_rows + 1))
        else:
            start = max_start

        col2 = int(panel.x + (panel.w // 2))
        try:
            num_col_w = int(self.eval_font.size('999...')[0]) + 12
        except Exception:
            num_col_w = 56

        y_cursor = int(list_top)
        for row in items[start : start + visible_rows]:
            if y_cursor + row_h > list_bottom:
                break

            num_txt, w_txt, w_ply, b_txt, b_ply = row
            num_surf = self.eval_font.render(str(num_txt), False, (200, 200, 200))
            self.screen.blit(num_surf, (xx, y_cursor))

            # White move (left half)
            if w_txt:
                w_x = int(xx + num_col_w)
                w_surf = self.eval_font.render(str(w_txt), False, (230, 230, 230))
                w_rect = pg.Rect(int(w_x - 3), int(y_cursor - 1), int(w_surf.get_width() + 6), int(row_h))
                if w_ply is not None and int(w_ply) == int(current_ply):
                    hi = pg.Surface((w_rect.w, w_rect.h), pg.SRCALPHA)
                    hi.fill((255, 255, 255, 40))
                    self.screen.blit(hi, (w_rect.x, w_rect.y))
                self.screen.blit(w_surf, (w_x, y_cursor))
                try:
                    if w_ply is not None:
                        self._game_move_hitboxes.append((w_rect, int(w_ply)))
                except Exception:
                    pass

            # Black move (right half)
            if b_txt:
                b_x = int(col2)
                b_surf = self.eval_font.render(str(b_txt), False, (230, 230, 230))
                b_rect = pg.Rect(int(b_x - 3), int(y_cursor - 1), int(b_surf.get_width() + 6), int(row_h))
                if b_ply is not None and int(b_ply) == int(current_ply):
                    hi = pg.Surface((b_rect.w, b_rect.h), pg.SRCALPHA)
                    hi.fill((255, 255, 255, 40))
                    self.screen.blit(hi, (b_rect.x, b_rect.y))
                self.screen.blit(b_surf, (b_x, y_cursor))
                try:
                    if b_ply is not None:
                        self._game_move_hitboxes.append((b_rect, int(b_ply)))
                except Exception:
                    pass

            y_cursor += int(row_h)

        # Footer nav buttons (◀ / ▶) at bottom of move list.
        try:
            btn_w = max(40, int(footer_h * 1.35))
            btn_h = int(footer_h)
            gap_btn = 12
            total_w = int(btn_w * 2 + gap_btn)
            bx = int(panel.centerx - total_w // 2)
            by = int(footer_y)
            prev_rect = pg.Rect(int(bx), int(by), int(btn_w), int(btn_h))
            next_rect = pg.Rect(int(bx + btn_w + gap_btn), int(by), int(btn_w), int(btn_h))
            self._game_movelist_prev_rect = prev_rect
            self._game_movelist_next_rect = next_rect

            # Divider line above footer.
            try:
                pg.draw.line(self.screen, (140, 140, 140), (panel.x + pad, footer_y - 3), (panel.right - pad, footer_y - 3), width=1)
            except Exception:
                pass

            def draw_arrow_btn(r: pg.Rect, direction: str) -> None:
                pg.draw.rect(self.screen, (25, 25, 25), r, border_radius=8)
                pg.draw.rect(self.screen, (110, 110, 110), r, width=1, border_radius=8)
                cx, cy = r.centerx, r.centery
                sx = max(8, int(r.w * 0.18))
                sy = max(8, int(r.h * 0.28))
                if direction == 'left':
                    pts = [(cx + sx, cy - sy), (cx + sx, cy + sy), (cx - sx, cy)]
                else:
                    pts = [(cx - sx, cy - sy), (cx - sx, cy + sy), (cx + sx, cy)]
                pg.draw.polygon(self.screen, (220, 220, 220), pts)

            draw_arrow_btn(prev_rect, 'left')
            draw_arrow_btn(next_rect, 'right')
        except Exception:
            self._game_movelist_prev_rect = None
            self._game_movelist_next_rect = None

    def _handle_game_movelist_click(self, pos: tuple[int, int]) -> bool:
        """Handle clicks on the normal-game move list (jump to ply)."""
        if self.review_active or getattr(self, 'analysis_active', False) or self.end_popup_active:
            return False
        try:
            if not bool(getattr(self, 'game_movelist_visible', True)):
                return False
        except Exception:
            return False

        panel = getattr(self, '_game_movelist_panel_rect', None)
        if panel is None:
            return False
        try:
            if not panel.collidepoint(pos):
                return False
        except Exception:
            return False

        # Footer arrows: step backward/forward through positions.
        try:
            prev_r = getattr(self, '_game_movelist_prev_rect', None)
            next_r = getattr(self, '_game_movelist_next_rect', None)
            if prev_r is not None and prev_r.collidepoint(pos):
                try:
                    cur = int(getattr(self, '_game_browse_index', None) or (len(self.game_fens) - 1))
                except Exception:
                    cur = max(0, int(len(self.game_fens) - 1))
                if cur > 0:
                    if not getattr(self, '_game_browse_active', False):
                        self._enter_game_browse()
                        try:
                            cur = int(getattr(self, '_game_browse_index', cur))
                        except Exception:
                            pass
                    self._set_game_browse_index(int(cur - 1), play_sound=True)
                return True
            if next_r is not None and next_r.collidepoint(pos):
                if getattr(self, '_game_browse_active', False):
                    try:
                        cur = int(getattr(self, '_game_browse_index', 0) or 0)
                    except Exception:
                        cur = 0
                    self._set_game_browse_index(int(cur + 1), play_sound=True)
                return True
        except Exception:
            pass

        for rect, ply_index in getattr(self, '_game_move_hitboxes', []):
            try:
                if rect.collidepoint(pos):
                    if not getattr(self, '_game_browse_active', False):
                        self._enter_game_browse()
                    # game_fens index is ply+1 (index 0 is start position)
                    self._set_game_browse_index(int(ply_index) + 1, play_sound=True)
                    return True
            except Exception:
                continue

        # Consume clicks inside the panel even if not on a move.
        return True

    def _open_saved_analysis_menu(self) -> None:
        """Open the SavedAnalysisMenu (loads analysis JSON from data/analysis)."""
        try:
            from src.engine.analysis_menu import SavedAnalysisMenu
            m = SavedAnalysisMenu(
                title='Open Saved Analysis',
                width=self.screen.get_width(),
                height=self.screen.get_height(),
                surface=self.screen,
                parent=self,
                engine=self,
                theme=pm.themes.THEME_DARK,
            )
            m.run()
        except Exception:
            return

    def _handle_undo_pressed(self) -> None:
        if self.end_popup_active or self.review_active:
            return

        # Analysis mode: undo deletes the last move in the current line and goes back a position.
        if getattr(self, 'analysis_active', False):
            # If we're inside a saved variation overlay, undo should ONLY affect that variation.
            try:
                if bool(getattr(self, '_review_analysis_active', False)):
                    base = int(getattr(self, '_review_analysis_base_index', int(self.analysis_index)))
                    cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)
                    if cur > 0:
                        # Remove the move that led to the currently viewed variation position.
                        new_cur = int(cur - 1)
                        try:
                            self._review_variations[base] = list((self._review_variations.get(base, []) or [])[:new_cur])
                        except Exception:
                            pass
                        try:
                            self._review_variation_fens[base] = list((self._review_variation_fens.get(base, []) or [])[:new_cur])
                        except Exception:
                            pass
                        try:
                            self._review_variation_ucis[base] = list((self._review_variation_ucis.get(base, []) or [])[:new_cur])
                        except Exception:
                            pass
                        try:
                            self._review_variation_labels[base] = list((self._review_variation_labels.get(base, []) or [])[:new_cur])
                        except Exception:
                            pass

                        self._review_analysis_cursor = int(new_cur)
                        self._review_analysis_fen = ''

                        # Update highlight + display.
                        if new_cur == 0:
                            tgt_fen = ''
                            try:
                                tgt_fen = str(self.analysis_fens[base]) if 0 <= base < len(self.analysis_fens) else ''
                            except Exception:
                                tgt_fen = ''
                            if not tgt_fen:
                                return
                            try:
                                self.last_move = [] if base <= 0 else [str(self.analysis_plies[base - 1].uci())]
                            except Exception:
                                self.last_move = []
                        else:
                            tgt_fen = ''
                            try:
                                tgt_fen = str(self._review_variation_fens.get(base, [])[new_cur - 1])
                            except Exception:
                                tgt_fen = ''
                            if not tgt_fen:
                                return
                            try:
                                uci = str(self._review_variation_ucis.get(base, [])[new_cur - 1])
                            except Exception:
                                uci = ''
                            try:
                                self.last_move = [uci] if uci else []
                            except Exception:
                                self.last_move = []

                        try:
                            self._load_fen_into_ui(str(tgt_fen))
                        except Exception:
                            pass
                        self.request_eval()
                        self.request_review_pv()

                        # Re-run variation classification for the shortened line.
                        try:
                            self._start_review_variation_analysis_thread(int(base))
                        except Exception:
                            pass
                        return
            except Exception:
                pass

            try:
                if not self.analysis_fens or len(self.analysis_fens) <= 1:
                    return
            except Exception:
                return

            # Remove the move that led to the currently viewed position.
            try:
                if int(self.analysis_index) <= 0:
                    return
                remove_ply_index = int(self.analysis_index) - 1
            except Exception:
                return

            try:
                self.analysis_plies = list(self.analysis_plies[:remove_ply_index])
                self.analysis_sans = list(self.analysis_sans[:remove_ply_index])
                self.analysis_move_labels = list(self.analysis_move_labels[:remove_ply_index])
                self.analysis_opening_names = list(self.analysis_opening_names[:remove_ply_index])
                self.analysis_fens = list(self.analysis_fens[: remove_ply_index + 1])
                self.analysis_index = int(remove_ply_index)
            except Exception:
                return

            # Clear any PV/variation overlay state.
            self._review_analysis_active = False
            self._review_analysis_base_index = int(self.analysis_index)
            self._review_analysis_fen = ''
            self._review_analysis_cursor = 0

            # Preserve saved variations when undoing on the mainline.
            # Only prune variations that were anchored beyond the new end of the mainline.
            try:
                max_base = int(len(self.analysis_fens) - 1)
            except Exception:
                max_base = -1

            try:
                self._review_variations = {
                    int(k): v for k, v in (getattr(self, '_review_variations', {}) or {}).items() if int(k) <= max_base
                }
            except Exception:
                pass
            try:
                self._review_variation_fens = {
                    int(k): v for k, v in (getattr(self, '_review_variation_fens', {}) or {}).items() if int(k) <= max_base
                }
            except Exception:
                pass
            try:
                self._review_variation_ucis = {
                    int(k): v for k, v in (getattr(self, '_review_variation_ucis', {}) or {}).items() if int(k) <= max_base
                }
            except Exception:
                pass
            try:
                self._review_variation_labels = {
                    int(k): v for k, v in (getattr(self, '_review_variation_labels', {}) or {}).items() if int(k) <= max_base
                }
            except Exception:
                pass

            # Update last-move highlight.
            try:
                if self.analysis_index <= 0:
                    self.last_move = []
                else:
                    self.last_move = [str(self.analysis_plies[self.analysis_index - 1].uci())]
            except Exception:
                self.last_move = []

            # Reload UI to the new position and refresh eval/PV.
            try:
                self._load_fen_into_ui(self.analysis_fens[self.analysis_index])
            except Exception:
                pass
            self.request_eval()
            self.request_review_pv()

            # Restart async annotations for the shortened line.
            try:
                self._start_analysis_annotation_thread(chess.Board(str(self.analysis_fens[0])), list(self.analysis_plies))
            except Exception:
                pass
            return

        if getattr(self, '_game_browse_active', False):
            return

        # Cancel any in-progress click/drag immediately so no piece can get "stuck" to the cursor.
        try:
            self._ignore_next_left_mouse_up = True
        except Exception:
            pass
        try:
            self.updates_kill()
            self.selected_square = None
            self._mouse_down_pos = None
        except Exception:
            pass
        try:
            for p in self.all_pieces:
                p.clicked = False
        except Exception:
            pass
        # Cancel any pending AI work so we don't apply a move for an old position.
        try:
            self.ai_thinking = False
        except Exception:
            pass
        try:
            self._ai_result = ''
            self._ai_result_fen = ''
        except Exception:
            pass
        try:
            while True:
                self._ai_move_queue.get_nowait()
        except Exception:
            pass

        # In PvAI:
        # - If AI hasn't moved yet (AI to move / AI thinking), undo ONLY the player's last move (1 ply).
        # - Otherwise (AI already replied), undo both plies to return to the player's turn.
        try:
            if self.player_vs_ai:
                ai_pending = bool(getattr(self, 'ai_thinking', False)) or (self.turn == self._ai_side())
                plies = 1 if ai_pending else 2
                for _ in range(int(plies)):
                    if len(self.last_move) <= 0:
                        break
                    self.undo_move(one=bool(len(self.last_move) <= 1))
            else:
                self.undo_move(one=bool(len(self.last_move) <= 1))
        except Exception:
            pass
        # Clear premoves + any pending AI result for the old position.
        try:
            self._clear_premove()
        except Exception:
            pass
        # (ai_thinking / results already cleared above)
        try:
            self._clock_last_ts = time.time()
        except Exception:
            pass

        # If after undo it's the AI to move, request again.
        try:
            if self.player_vs_ai and self.turn == self._ai_side() and not self.end_popup_active and not self.game_just_ended:
                self._request_ai_move_async()
        except Exception:
            pass

    def _handle_resign_pressed(self) -> None:
        if self.end_popup_active or self.review_active:
            return
        if getattr(self, '_game_browse_active', False):
            return

        # Swallow the corresponding mouse-up and cancel any in-progress drag.
        try:
            self._ignore_next_left_mouse_up = True
        except Exception:
            pass
        try:
            self.updates_kill()
            self.selected_square = None
            self._mouse_down_pos = None
        except Exception:
            pass
        # In PvAI, resign the player's side. In PvP, resign side-to-move.
        side = 'w'
        try:
            side = self._player_side() if self.player_vs_ai else self.turn
        except Exception:
            side = 'w'
        if side == 'w':
            self.end_game('WHITE RESIGNS — BLACK WINS')
        else:
            self.end_game('BLACK RESIGNS — WHITE WINS')

    def _enter_game_browse(self) -> None:
        if self.end_popup_active or self.review_active:
            return
        if not getattr(self, 'game_fens', None):
            return
        self._game_browse_active = True
        self._game_browse_index = max(0, len(self.game_fens) - 1)
        try:
            self._game_browse_live_turn = str(self.turn)
        except Exception:
            self._game_browse_live_turn = None
        try:
            self._game_browse_saved_fen = self._current_fen()
        except Exception:
            try:
                self._game_browse_saved_fen = self.game_fens[-1]
            except Exception:
                self._game_browse_saved_fen = None
        # Do not stop clocks while browsing.
        try:
            self.updates_kill()
            self.selected_square = None
        except Exception:
            pass

    def _exit_game_browse(self) -> None:
        if not getattr(self, '_game_browse_active', False):
            return
        self._game_browse_active = False
        self._game_browse_index = None
        try:
            fen = self._game_browse_saved_fen or (self.game_fens[-1] if self.game_fens else '')
            if fen:
                self._load_fen_into_ui(fen)
        except Exception:
            pass
        # Clocks continue running while browsing; just reset tick baseline.
        try:
            self._clock_last_ts = time.time()
        except Exception:
            pass
        self._game_browse_saved_fen = None
        self._game_browse_live_turn = None

    def _set_game_browse_index(self, new_index: int, play_sound: bool = True) -> None:
        if not getattr(self, '_game_browse_active', False):
            return
        if not self.game_fens:
            return
        old = int(self._game_browse_index or 0)
        new = int(max(0, min(int(new_index), len(self.game_fens) - 1)))
        if new == old:
            return

        if play_sound:
            try:
                # Use the move that transitions between positions.
                if new > old:
                    # stepping forward: move index == old
                    mi = old
                    fen_before = self.game_fens[mi]
                    uci = self.last_move[mi] if mi < len(self.last_move) else ''
                else:
                    # stepping back: move that was played from new -> new+1
                    mi = new
                    fen_before = self.game_fens[mi]
                    uci = self.last_move[mi] if mi < len(self.last_move) else ''
                if uci:
                    mv = chess.Move.from_uci(str(uci))
                    self._play_sound_for_move_from_fen(str(fen_before), mv)
            except Exception:
                pass

        try:
            self._game_browse_index = new
            self._load_fen_into_ui(self.game_fens[new])
        except Exception:
            pass

        # If we've returned to the latest position, leave browse mode so the user can play.
        try:
            if int(new) >= int(len(self.game_fens) - 1):
                self._exit_game_browse()
        except Exception:
            pass

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
            premove_mode = bool(self._premove_mode_active())
            active_colour = self._premove_player_side() if premove_mode else self.turn

            if not self.flipped:
                if -1 < self.tx < 8 and -1 < self.ty < 8:
                    virt_pos = None
                    if premove_mode:
                        piece = self._virtual_player_piece_at(int(self.ty), int(self.tx))
                        if piece is not None:
                            virt_pos = (int(self.ty), int(self.tx))
                        else:
                            piece = self.board[self.ty][self.tx]
                    else:
                        piece = self.board[self.ty][self.tx]
                    if piece != ' ' and piece is not None:
                        piece.clicked = True
                        if premove_mode and getattr(piece, 'colour', '')[:1] == active_colour:
                            old_pos = list(getattr(piece, 'legal_positions', []) or [])
                            old_caps = set(getattr(piece, 'legal_captures', set()) or set())
                            old_piece_pos = tuple(getattr(piece, 'position', (0, 0)))
                            try:
                                if virt_pos is None:
                                    virt_pos = self._virtual_position_for_piece(piece)
                                pos, _caps = self._pseudo_moves_for_piece(piece, int(virt_pos[0]), int(virt_pos[1]))
                                caps = self._capture_deltas_from_occupancy(int(virt_pos[0]), int(virt_pos[1]), pos, active_colour)
                                piece.position = (int(virt_pos[0]), int(virt_pos[1]))
                                piece.legal_positions = pos
                                piece.legal_captures = caps
                                piece.show_legal_moves(self.screen, self.offset, active_colour, self.flipped, self.board)
                            finally:
                                piece.position = old_piece_pos
                                piece.legal_positions = old_pos
                                piece.legal_captures = old_caps
                        else:
                            piece.show_legal_moves(self.screen, self.offset, self.turn, self.flipped, self.board)
            else:
                if -1 < self.tx < 8 and -1 < self.ty < 8:
                    rr, cc = (-self.ty + 7), (-self.tx + 7)
                    virt_pos = None
                    if premove_mode:
                        # Mouse is over the virtual square in flipped coords.
                        piece = self._virtual_player_piece_at(int(rr), int(cc))
                        if piece is not None:
                            virt_pos = (int(rr), int(cc))
                        else:
                            piece = self.board[rr][cc]
                    else:
                        piece = self.board[rr][cc]
                    if piece != ' ' and piece is not None:
                        piece.clicked = True
                        if premove_mode and getattr(piece, 'colour', '')[:1] == active_colour:
                            old_pos = list(getattr(piece, 'legal_positions', []) or [])
                            old_caps = set(getattr(piece, 'legal_captures', set()) or set())
                            old_piece_pos = tuple(getattr(piece, 'position', (0, 0)))
                            try:
                                if virt_pos is None:
                                    virt_pos = self._virtual_position_for_piece(piece)
                                pos, _caps = self._pseudo_moves_for_piece(piece, int(virt_pos[0]), int(virt_pos[1]))
                                caps = self._capture_deltas_from_occupancy(int(virt_pos[0]), int(virt_pos[1]), pos, active_colour)
                                piece.position = (int(virt_pos[0]), int(virt_pos[1]))
                                piece.legal_positions = pos
                                piece.legal_captures = caps
                                piece.show_legal_moves(self.screen, self.offset, active_colour, self.flipped, self.board)
                            finally:
                                piece.position = old_piece_pos
                                piece.legal_positions = old_pos
                                piece.legal_captures = old_caps
                        else:
                            piece.show_legal_moves(self.screen, self.offset, self.turn, self.flipped, self.board)
        except Exception:
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

        # While browsing history, show last-move markers for the browsed position.
        last_moves = self.last_move
        try:
            if getattr(self, '_game_browse_active', False) and self._game_browse_index is not None:
                i = int(self._game_browse_index)
                if i <= 0:
                    last_moves = []
                else:
                    last_moves = self.last_move[: min(len(self.last_move), i)]
        except Exception:
            last_moves = self.last_move

        if len(last_moves) > 1:
            square1 = square_on(last_moves[-1][0:2])
            square2 = square_on(last_moves[-1][2:4])
        elif len(last_moves) == 1:
            square1 = square_on(last_moves[0][0:2])
            square2 = square_on(last_moves[0][2:4])
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
                    premove_sq = False
                    try:
                        premove_sq = (row, col) in self._premove_squares and (not self.review_active) and (not getattr(self, 'analysis_active', False))
                    except Exception:
                        premove_sq = False
                    if (row, col) in self.highlighted or premove_sq:
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

        # Settings cog: always available (including review), but hide during end popup.
        if not self.end_popup_active:
            self._settings_btn_rect = self._draw_settings_button((20, 20), size=34)
            # Eval bar: show only the bar; show numeric value on hover
            if self.eval_bar_enabled and not getattr(self, 'puzzle_rush_active', False):
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

            # In-game action buttons (normal play only)
            try:
                if not self.review_active:
                    self._draw_game_action_buttons()
            except Exception:
                pass

        # Note: review overlay and end-game popup are rendered in run() after pieces,
        # so they are not obscured by piece sprites.

        # Board resize handle (bottom-right). Kept subtle and within the board.
        try:
            # The start menu runs in its own blocking loop, so during actual gameplay/review/analysis
            # this should always be available unless an end popup is active.
            if not self.end_popup_active:
                br = self._board_rect()
                hs = max(11, int(self.size * 0.22))
                grip = pg.Rect(br.right - hs, br.bottom - hs, hs, hs)

                # Keep a slightly larger hitbox than the drawn grip, but don't draw any solid fill
                # so we don't obscure the square underneath.
                hit = max(hs, int(hs * 1.35))
                handle = pg.Rect(br.right - hit, br.bottom - hit, hit, hit)
                self._board_resize_handle_rect = handle

                # Draw a small diagonal "grip" using alpha so it's visible but not blocking.
                surf = pg.Surface((grip.w, grip.h), pg.SRCALPHA)
                light = (235, 235, 235, 200)
                dark = (0, 0, 0, 110)
                step = max(4, int(grip.w / 4))
                pad = 2
                for i in range(3):
                    off = pad + i * step
                    # shadow line
                    pg.draw.line(surf, dark, (grip.w - 1 - off, grip.h - 1), (grip.w - 1, grip.h - 1 - off), width=1)
                    # highlight line (slightly inset)
                    pg.draw.line(surf, light, (grip.w - 2 - off, grip.h - 2), (grip.w - 2, grip.h - 2 - off), width=1)
                self.screen.blit(surf, (grip.x, grip.y))
            else:
                self._board_resize_handle_rect = None
        except Exception:
            pass

    def _draw_settings_button(self, pos: tuple[int, int], size: int = 34) -> pg.Rect:
        """Draw a small cog icon at pos (top-left). Returns its clickable rect."""
        x, y = int(pos[0]), int(pos[1])
        rect = pg.Rect(x, y, size, size)

        # Background hit area (subtle)
        bg = pg.Surface((size, size), pg.SRCALPHA)
        bg.fill((0, 0, 0, 0))
        pg.draw.rect(bg, (0, 0, 0, 80), bg.get_rect(), border_radius=max(6, size // 5))
        self.screen.blit(bg, rect.topleft)

        cx = x + size // 2
        cy = y + size // 2
        outer_r = max(9, size // 2 - 6)
        inner_r = max(4, outer_r // 2)

        # Teeth
        tooth_len = max(4, size // 6)
        tooth_w = max(2, size // 10)
        for i in range(8):
            ang = i * (3.14159265 / 4.0)
            vec = pg.math.Vector2(1, 0).rotate_rad(ang)
            tx = cx + int(vec.x * (outer_r + tooth_len // 2))
            ty = cy + int(vec.y * (outer_r + tooth_len // 2))
            tooth_surf = pg.Surface((tooth_len, tooth_w), pg.SRCALPHA)
            tooth_surf.fill((220, 220, 220, 230))
            tooth_surf = pg.transform.rotate(tooth_surf, -i * 45)
            tr = tooth_surf.get_rect(center=(tx, ty))
            self.screen.blit(tooth_surf, tr.topleft)

        # Rings
        pg.draw.circle(self.screen, (230, 230, 230), (cx, cy), outer_r, width=2)
        pg.draw.circle(self.screen, (230, 230, 230), (cx, cy), inner_r, width=2)

        # Hover outline
        try:
            if rect.collidepoint(pg.mouse.get_pos()):
                pg.draw.rect(
                    self.screen,
                    (120, 120, 120),
                    rect,
                    width=1,
                    border_radius=max(6, size // 5),
                )
        except Exception:
            pass

        return rect

    def _draw_flip_board_button(self) -> None:
        """Draw a small flip-board button near the bottom-right of the board (all modes)."""
        self._flip_board_btn_rect = None
        if self.end_popup_active:
            return

        br = self._board_rect()

        # Keep the button small and readable across board sizes.
        btn_s = max(18, min(28, int(self.size * 0.45)))
        pad = max(6, int(self.size * 0.10))

        # Position just to the RIGHT of the board (not inside it).
        # Clamp so it doesn't overlap the right-side panel (review/analysis) when present.
        try:
            win_w = int(self.screen.get_width())
        except Exception:
            win_w = int(br.right + btn_s + 20)

        panel = None
        try:
            if self.review_active:
                panel = getattr(self, '_review_panel_rect', None)
            elif getattr(self, 'analysis_active', False):
                panel = getattr(self, '_analysis_panel_rect', None)
        except Exception:
            panel = None

        x_target = int(br.right + pad)
        if panel is not None:
            x_max = int(panel.left - pad - btn_s)
        else:
            x_max = int(win_w - pad - btn_s)
        x = int(min(x_target, x_max))
        y = int(br.bottom - btn_s - pad)

        # Keep on-screen vertically.
        y = max(6, min(int(y), int(self.screen.get_height() - btn_s - 6)))

        rect = pg.Rect(int(x), int(y), int(btn_s), int(btn_s))
        self._flip_board_btn_rect = rect

        hovered = False
        try:
            hovered = rect.collidepoint(pg.mouse.get_pos())
        except Exception:
            hovered = False

        bg = (20, 20, 20)
        border = (110, 110, 110) if hovered else (80, 80, 80)
        fg = (235, 235, 235)

        try:
            pg.draw.rect(self.screen, bg, rect, border_radius=max(8, btn_s // 4))
            pg.draw.rect(self.screen, border, rect, width=2, border_radius=max(8, btn_s // 4))
        except Exception:
            pass

        # Label: use plain text so it renders on all systems.
        try:
            desired = max(11, min(16, int(btn_s * 0.55)))
        except Exception:
            desired = 12
        try:
            cur = int(getattr(self, '_flip_btn_font_size', 0) or 0)
        except Exception:
            cur = 0
        if cur != int(desired) or getattr(self, '_flip_btn_font', None) is None:
            try:
                self._flip_btn_font = pg.font.SysFont('arial', int(desired))
                self._flip_btn_font_size = int(desired)
            except Exception:
                self._flip_btn_font = self.eval_font
                self._flip_btn_font_size = int(desired)

        try:
            font = getattr(self, '_flip_btn_font', None) or self.eval_font or self.font
        except Exception:
            font = self.font
        try:
            surf = font.render('Flip', True, fg)
            self.screen.blit(surf, (rect.centerx - surf.get_width() // 2, rect.centery - surf.get_height() // 2))
        except Exception:
            pass

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
        btn_w = 180
        gap = 18
        y = box.y + box.h - btn_h - 22
        x0 = box.x + 20
        rush = bool(getattr(self, 'puzzle_rush_active', False))
        if rush:
            btn_reset = pg.Rect(box.centerx - btn_w // 2, y, btn_w, btn_h)
            for rect, label, bg in [
                (btn_reset, 'Reset', (200, 0, 0)),
            ]:
                pg.draw.rect(self.screen, bg, rect, border_radius=8)
                pg.draw.rect(self.screen, (30, 30, 30), rect, width=2, border_radius=8)
                surf = self.eval_font.render(label, False, (0, 0, 0))
                self.screen.blit(surf, (rect.x + (rect.w - surf.get_width()) // 2, rect.y + (rect.h - surf.get_height()) // 2))
            self._end_popup_buttons = {
                'reset': btn_reset,
            }
        else:
            btn_review = pg.Rect(x0, y, btn_w, btn_h)
            btn_reset = pg.Rect(x0 + btn_w + gap, y, btn_w, btn_h)

            for rect, label, bg in [
                (btn_review, 'Review', (100, 100, 100)),
                (btn_reset, 'Reset', (200, 0, 0)),
            ]:
                pg.draw.rect(self.screen, bg, rect, border_radius=8)
                pg.draw.rect(self.screen, (30, 30, 30), rect, width=2, border_radius=8)
                surf = self.eval_font.render(label, False, (0, 0, 0))
                self.screen.blit(surf, (rect.x + (rect.w - surf.get_width()) // 2, rect.y + (rect.h - surf.get_height()) // 2))

            self._end_popup_buttons = {
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
        if btns.get('review') and btns['review'].collidepoint(pos):
            if self.end_popup_pgn_path:
                try:
                    # Clear the popup first so review layout reserves the correct top margin.
                    self.end_popup_active = False
                    self._layout_cache_key = None
                    self._ensure_layout(force=True)
                    self.start_review(self.end_popup_pgn_path)
                    self._layout_cache_key = None
                    self._ensure_layout(force=True)
                except Exception:
                    pass

    def _draw_review_overlay(self) -> None:
        # Draw review stats + move list in a right-side panel so it doesn't overlap the board.
        win_w, win_h = self.screen.get_size()

        margin = 14
        x = int(win_w * 0.60) + margin
        y = int(margin)
        w = max(180, int(win_w * 0.40) - (margin * 2))
        h = int(win_h - 2 * margin)
        # Fallback if the window is too narrow.
        if w < 220:
            x = margin
            y = margin
            w = max(220, win_w - 2 * margin)
            h = max(140, int(win_h - 2 * margin))

        panel = pg.Rect(int(x), int(y), int(w), int(h))
        bg = pg.Surface((panel.w, panel.h), pg.SRCALPHA)
        bg.fill((0, 0, 0, 160))
        self.screen.blit(bg, (panel.x, panel.y))
        pg.draw.rect(self.screen, (140, 140, 140), panel, width=1, border_radius=6)

        # Split panel into top stats/PV area and bottom move list area.
        # Default top area is ~30% of the screen height, but user can drag the divider.
        try:
            if getattr(self, 'review_top_h_px', None) is not None:
                top_h_target = int(self.review_top_h_px)
            else:
                top_h_target = int(win_h * 0.35)
        except Exception:
            top_h_target = int(panel.h * 0.35)
        # Keep both areas usable.
        min_top_h = 120
        min_bottom_h = 140
        top_h = max(int(min_top_h), min(int(top_h_target), int(panel.h - min_bottom_h)))
        split_y = int(panel.y + top_h)
        top_rect = pg.Rect(int(panel.x), int(panel.y), int(panel.w), int(top_h))
        bottom_rect = pg.Rect(int(panel.x), int(split_y), int(panel.w), int(panel.bottom - split_y))
        self._review_top_rect = top_rect
        # Draggable splitter bar (a small hitbox around the divider line).
        try:
            self._review_splitter_rect = pg.Rect(int(panel.x), int(split_y - 5), int(panel.w), 10)
        except Exception:
            self._review_splitter_rect = None
        try:
            pg.draw.line(self.screen, (140, 140, 140), (panel.x, split_y), (panel.right, split_y), width=1)
        except Exception:
            pass

        # More-visible draggable splitter handle (center grip).
        try:
            grip_w = 44
            grip_h = 14
            gx = int(panel.centerx - grip_w // 2)
            gy = int(split_y - grip_h // 2)
            grip_rect = pg.Rect(gx, gy, grip_w, grip_h)
            pg.draw.rect(self.screen, (25, 25, 25), grip_rect, border_radius=7)
            pg.draw.rect(self.screen, (110, 110, 110), grip_rect, width=1, border_radius=7)
            # Three small horizontal lines inside.
            cx = int(grip_rect.centerx)
            for k in (-3, 0, 3):
                yk = int(grip_rect.centery + k)
                pg.draw.line(self.screen, (200, 200, 200), (cx - 12, yk), (cx + 12, yk), width=2)
        except Exception:
            pass

        # Clip top section so stats/PV never render over the move list.
        prev_clip = None
        try:
            prev_clip = self.screen.get_clip()
            self.screen.set_clip(top_rect)
        except Exception:
            prev_clip = None

        try:
            top_scroll = max(0, int(getattr(self, 'review_top_scroll', 0)))
        except Exception:
            top_scroll = 0

        pad = 10
        line_h = self.eval_font.get_linesize() + 2
        ply = max(0, self.review_index)
        total = max(0, len(self.review_fens) - 1)
        name = self.review_name or 'Review'

        lines: list[str] = [
            f"Review: {name}",
            f"Ply: {ply}/{total} (Left/Right, ESC exits)",
        ]

        # Show opening name.
        # - If the temporary opening-variation mode is active, show its chosen opening.
        # - If we're browsing a user variation, derive the opening from the combined UCI prefix.
        # - Otherwise (mainline), show the opening for a Book move.
        try:
            nm = ''
            if bool(getattr(self, '_opening_variation_active', False)):
                nm = str(getattr(self, '_opening_variation_name', '') or '')
            elif bool(getattr(self, '_review_analysis_active', False)):
                try:
                    base = int(getattr(self, '_review_analysis_base_index', -1))
                    cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)
                    vucis = list((getattr(self, '_review_variation_ucis', {}) or {}).get(base, []) or [])
                    main_prefix = [str(m.uci()) for m in (self.review_plies[: max(0, base)] if self.review_plies else [])]
                    combined = list(main_prefix) + [str(x) for x in vucis[: max(0, cur)]]
                    nm = str(self._opening_name_for_uci_prefix(combined) or '')
                except Exception:
                    nm = ''
            elif (not self._review_analysis_active) and self.review_index > 0:
                i = int(self.review_index) - 1
                if 0 <= i < len(getattr(self, 'review_move_labels', []) or []) and (self.review_move_labels[i] == 'Book'):
                    try:
                        nm = str((getattr(self, 'review_opening_names', []) or [])[i] or '')
                    except Exception:
                        nm = ''
            if nm:
                lines.append(f"Opening: {nm}")
        except Exception:
            pass
        if (not self._review_analysis_active) and self.review_show_best_move and self.review_best_move_uci:
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

        # Render top section (stats + PV) inside top_rect.
        self._review_opening_rect = None
        self._review_opening_suggestion_hitboxes = []
        self._review_opening_suggest_more_rect = None
        xx = top_rect.x + pad
        yy = top_rect.y + pad - int(top_scroll)
        for t in lines:
            if str(t).startswith('Opening:'):
                # Draw as a button so it's obviously clickable.
                try:
                    ts = str(t)
                    surf = self.eval_font.render(ts, False, (220, 220, 220))
                    bx_pad = 10
                    by_pad = 5
                    bw = int(surf.get_width() + 2 * bx_pad)
                    bh = int(surf.get_height() + 2 * by_pad)
                    btn = pg.Rect(int(xx), int(yy), int(bw), int(bh))
                    self._review_opening_rect = btn
                    pg.draw.rect(self.screen, (25, 25, 25), btn, border_radius=10)
                    border_col = (200, 200, 200) if bool(getattr(self, '_opening_variation_active', False)) else (110, 110, 110)
                    pg.draw.rect(self.screen, border_col, btn, width=1, border_radius=10)
                    self.screen.blit(surf, (int(btn.x + bx_pad), int(btn.y + by_pad)))
                    yy += int(max(line_h, btn.h))
                except Exception:
                    surf = self.eval_font.render(str(t), False, (255, 255, 255))
                    self.screen.blit(surf, (xx, yy))
                    yy += line_h

                # Suggestions: other openings sharing the current move prefix with longer lines.
                try:
                    opening_nm = ''
                    try:
                        opening_nm = str(str(t).split(':', 1)[1]).strip()
                    except Exception:
                        opening_nm = ''
                    prefix_ucis: list[str] = []
                    try:
                        if bool(getattr(self, '_review_analysis_active', False)):
                            base = int(getattr(self, '_review_analysis_base_index', -1))
                            cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)
                            vucis = list((getattr(self, '_review_variation_ucis', {}) or {}).get(base, []) or [])
                            main_prefix = [str(m.uci()) for m in (self.review_plies[: max(0, base)] if self.review_plies else [])]
                            prefix_ucis = list(main_prefix) + [str(x) for x in vucis[: max(0, cur)]]
                        else:
                            prefix_ucis = [str(m.uci()) for m in (self.review_plies[: int(self.review_index)] if self.review_plies else [])]
                    except Exception:
                        prefix_ucis = []

                    all_suggestions = self._opening_suggestions_for_prefix(prefix_ucis, limit=25, exclude_name=opening_nm)
                    n_sug = len(all_suggestions)
                    if n_sug > 3:
                        try:
                            key = (tuple(prefix_ucis), str(opening_nm or '').strip().lower())
                        except Exception:
                            key = (tuple(), '')
                        try:
                            if key != (getattr(self, '_review_opening_suggest_key', (tuple(), '')) or (tuple(), '')):
                                self._review_opening_suggest_key = key
                                self._review_opening_suggest_offset = 0
                        except Exception:
                            self._review_opening_suggest_key = key
                            self._review_opening_suggest_offset = 0

                        try:
                            lab = self.eval_font.render('See more', False, (200, 200, 200))
                            sbx = 8
                            sby = 4
                            orect = getattr(self, '_review_opening_rect', None)
                            if orect is None:
                                bx = int(xx + 12)
                                by = int(yy + 2)
                            else:
                                bx = int(orect.right + 8)
                                by = int(orect.y)
                            brect = pg.Rect(int(bx), int(by), int(lab.get_width() + 2 * sbx), int(lab.get_height() + 2 * sby))

                            # Clamp into the top panel; if it would overlap, fall back below.
                            try:
                                max_x = int(top_rect.right - pad - brect.w)
                                brect.x = int(min(int(brect.x), int(max_x)))
                            except Exception:
                                pass
                            if orect is not None and brect.x < int(orect.right + 4):
                                brect.x = int(xx + 12)
                                brect.y = int(yy + 2)

                            self._review_opening_suggest_more_rect = brect
                            pg.draw.rect(self.screen, (25, 25, 25), brect, border_radius=9)
                            pg.draw.rect(self.screen, (110, 110, 110), brect, width=1, border_radius=9)
                            self.screen.blit(lab, (int(brect.x + sbx), int(brect.y + sby)))
                            if orect is None or brect.y >= int(yy):
                                yy = int(brect.bottom + 2)
                        except Exception:
                            self._review_opening_suggest_more_rect = None

                        try:
                            off = int(getattr(self, '_review_opening_suggest_offset', 0) or 0)
                        except Exception:
                            off = 0
                        off = int(off) % int(n_sug)
                        suggestions = [all_suggestions[(off + i) % n_sug] for i in range(3)]
                    else:
                        suggestions = list(all_suggestions)

                    for sidx, (snm, suci_line, _slen) in enumerate(suggestions):
                        preview = self._opening_next_moves_preview(str(suci_line), prefix_len=len(prefix_ucis), max_moves=2)
                        try:
                            if n_sug > 0 and n_sug > 3:
                                num = int(((off + sidx) % n_sug) + 1)
                            else:
                                num = int(sidx + 1)
                        except Exception:
                            num = int(sidx + 1)
                        label = f"{num}. {snm}"
                        if preview:
                            label = f"{label}  (next: {preview})"

                        ss = self.eval_font.render(str(label), False, (200, 200, 200))
                        sx = int(xx + 12)
                        sy = int(yy + 2)
                        sbx = 8
                        sby = 4
                        srect = pg.Rect(int(sx), int(sy), int(ss.get_width() + 2 * sbx), int(ss.get_height() + 2 * sby))
                        pg.draw.rect(self.screen, (25, 25, 25), srect, border_radius=9)
                        pg.draw.rect(self.screen, (110, 110, 110), srect, width=1, border_radius=9)
                        self.screen.blit(ss, (int(srect.x + sbx), int(srect.y + sby)))
                        self._review_opening_suggestion_hitboxes.append((srect, str(snm), str(suci_line)))
                        yy = int(srect.bottom + 2)
                except Exception:
                    pass
                continue

            surf = self.eval_font.render(str(t), False, (255, 255, 255))
            self.screen.blit(surf, (xx, yy))
            yy += line_h

        # Button: enter Analysis mode from the currently displayed position.
        try:
            btn_h = max(24, int(line_h + 6))
            btn_w = max(140, int(min(panel.w - 2 * pad, 220)))
            btn_rect = pg.Rect(int(xx), int(yy + 2), int(btn_w), int(btn_h))
            self._review_analyze_btn_rect = btn_rect
            pg.draw.rect(self.screen, (25, 25, 25), btn_rect, border_radius=8)
            pg.draw.rect(self.screen, (110, 110, 110), btn_rect, width=1, border_radius=8)
            lab = self.eval_font.render('Analyse position', False, (220, 220, 220))
            self.screen.blit(lab, (btn_rect.x + (btn_rect.w - lab.get_width()) // 2, btn_rect.y + (btn_rect.h - lab.get_height()) // 2))
            yy = int(btn_rect.bottom + 6)
        except Exception:
            self._review_analyze_btn_rect = None

        # Review analysis depth controls (in-review move classification / ACPL analysis).
        try:
            btn_h = max(22, int(line_h + 4))
            btn_w = max(140, int(min(panel.w - 2 * pad, 220)))
            # Row: [-]  d10  [+]    Re-analyze
            yrow = int(yy + 2)
            xrow = int(xx)

            small_w = max(28, int(btn_h))
            minus_rect = pg.Rect(int(xrow), int(yrow), int(small_w), int(btn_h))
            plus_rect = pg.Rect(int(xrow + small_w + 6 + 46), int(yrow), int(small_w), int(btn_h))
            depth_rect = pg.Rect(int(xrow + small_w + 6), int(yrow), 46, int(btn_h))

            run_rect = pg.Rect(int(plus_rect.right + 10), int(yrow), int(max(90, btn_w - (plus_rect.right - xrow) - 10)), int(btn_h))

            self._review_depth_minus_rect = minus_rect
            self._review_depth_plus_rect = plus_rect
            self._review_reanalyze_btn_rect = run_rect

            # Draw buttons
            pg.draw.rect(self.screen, (25, 25, 25), minus_rect, border_radius=6)
            pg.draw.rect(self.screen, (110, 110, 110), minus_rect, width=1, border_radius=6)
            pg.draw.rect(self.screen, (25, 25, 25), plus_rect, border_radius=6)
            pg.draw.rect(self.screen, (110, 110, 110), plus_rect, width=1, border_radius=6)
            pg.draw.rect(self.screen, (25, 25, 25), run_rect, border_radius=6)
            pg.draw.rect(self.screen, (110, 110, 110), run_rect, width=1, border_radius=6)

            m = self.eval_font.render('-', False, (220, 220, 220))
            self.screen.blit(m, (minus_rect.x + (minus_rect.w - m.get_width()) // 2, minus_rect.y + (minus_rect.h - m.get_height()) // 2))
            p = self.eval_font.render('+', False, (220, 220, 220))
            self.screen.blit(p, (plus_rect.x + (plus_rect.w - p.get_width()) // 2, plus_rect.y + (plus_rect.h - p.get_height()) // 2))

            try:
                d = int(getattr(self, 'review_analysis_depth', 10))
            except Exception:
                d = 10
            d = max(6, min(20, int(d)))
            ds = self.eval_font.render(f"d{d}", False, (200, 220, 255))
            self.screen.blit(ds, (depth_rect.x + (depth_rect.w - ds.get_width()) // 2, depth_rect.y + (depth_rect.h - ds.get_height()) // 2))

            lab = self.eval_font.render('Re-analyze', False, (220, 220, 220))
            self.screen.blit(lab, (run_rect.x + (run_rect.w - lab.get_width()) // 2, run_rect.y + (run_rect.h - lab.get_height()) // 2))

            yy = int(run_rect.bottom + 6)
        except Exception:
            self._review_reanalyze_btn_rect = None
            self._review_depth_minus_rect = None
            self._review_depth_plus_rect = None

        # Top 3 engine lines (PV) for the currently displayed position.
        yy += 6
        pv_title = self.eval_font.render("Top lines:", False, (255, 255, 255))
        self.screen.blit(pv_title, (xx, yy))
        # Depth indicator (last completed or in-flight if pending).
        try:
            fen_for_depth = str(self._review_display_fen())
        except Exception:
            fen_for_depth = ''
        try:
            d_done = int((getattr(self, '_pv_depth_by_fen', {}) or {}).get(fen_for_depth, 0) or 0)
        except Exception:
            d_done = 0
        try:
            d_inflight = int((getattr(self, '_pv_inflight_depth_by_fen', {}) or {}).get(fen_for_depth, 0) or 0)
        except Exception:
            d_inflight = 0
        try:
            depth_to_show = int(d_inflight if (d_inflight > 0 and bool(getattr(self, 'review_pv_pending', False))) else d_done)
        except Exception:
            depth_to_show = int(d_done)
        if int(depth_to_show) > 0:
            try:
                ds = self.eval_font.render(f"d{int(depth_to_show)}", False, (160, 180, 200))
                self.screen.blit(ds, (int(xx + pv_title.get_width() + 10), int(yy)))
            except Exception:
                pass
        yy += line_h

        # PV rendering (never block; keep old lines while a new request is pending).
        self._review_pv_hitboxes = []
        pv_lines: list[dict] = []
        pv_pending = False
        try:
            if self._review_pv_lock.acquire(False):
                try:
                    pv_lines = list(self.review_pv_lines or [])
                    pv_pending = bool(self.review_pv_pending)
                    self._review_pv_render_cache = list(pv_lines)
                    self._review_pv_render_pending = bool(pv_pending)
                finally:
                    self._review_pv_lock.release()
            else:
                pv_lines = list(getattr(self, '_review_pv_render_cache', []) or [])
                pv_pending = bool(getattr(self, '_review_pv_render_pending', False))
        except Exception:
            pv_lines = list(getattr(self, '_review_pv_render_cache', []) or [])
            pv_pending = bool(getattr(self, '_review_pv_render_pending', False))

        # If PV hasn't started yet (common at ply 0), kick it once.
        try:
            if (not pv_lines) and (not pv_pending):
                self.request_review_pv()
                pv_pending = True
        except Exception:
            pass

        if not pv_lines and pv_pending:
            pv_lines = [{"rank": 0, "eval": {"type": "cp", "value": 0}, "moves": ["Analyzing..."], "fens": ['']}]
        if not pv_lines:
            pv_lines = [{"rank": 0, "eval": {"type": "cp", "value": 0}, "moves": ["(click board to analyze)"], "fens": ['']}]

        max_x = top_rect.right - pad
        token_gap = 10
        token_gap_small = 6

        def draw_tokens_row(
            x0: int,
            y0: int,
            tokens: list[tuple[str, tuple[int, int, int], tuple[dict, int] | None]],
            max_x_: int,
        ) -> int:
            """Draw tokens with wrapping. token = (text,color,(pv_item,move_index)|None). Returns new y."""
            x = int(x0)
            y = int(y0)
            row_h2 = self.eval_font.get_linesize() + 4
            for txt, col, meta in tokens:
                try:
                    surf = self.eval_font.render(str(txt), False, col)
                except Exception:
                    continue
                if x + surf.get_width() > max_x_ and x != int(x0):
                    x = int(x0)
                    y += row_h2
                rect = pg.Rect(x - 2, y - 1, surf.get_width() + 4, row_h2)
                self.screen.blit(surf, (x, y))
                if meta:
                    try:
                        pv_item, mv_i = meta
                        self._review_pv_hitboxes.append((rect, pv_item, int(mv_i)))
                    except Exception:
                        pass
                x += surf.get_width() + token_gap
            return y + row_h2

        # Limit to top 3 lines, but each line can wrap.
        for item in pv_lines[:3]:
            if not isinstance(item, dict):
                continue
            rank = int(item.get('rank', 0) or 0)
            eval_d = item.get('eval') if isinstance(item.get('eval'), dict) else {"type": "cp", "value": 0}
            moves = item.get('moves') if isinstance(item.get('moves'), list) else []
            fens = item.get('fens') if isinstance(item.get('fens'), list) else []
            ucis = item.get('uci') if isinstance(item.get('uci'), list) else []

            # Prefix: "1." and eval rectangle.
            x = int(xx)
            y_line = int(yy)

            if rank > 0:
                pfx = self.eval_font.render(f"{rank}.", False, (200, 220, 255))
                self.screen.blit(pfx, (x, y_line))
                x += pfx.get_width() + token_gap_small

            score_txt = self._format_eval_short(eval_d)
            side = self._eval_side_for_rect(eval_d)
            if side == 'w':
                box_bg = (230, 230, 230)
                box_fg = (0, 0, 0)
                box_bd = (180, 180, 180)
            elif side == 'b':
                box_bg = (25, 25, 25)
                box_fg = (245, 245, 245)
                box_bd = (110, 110, 110)
            else:
                box_bg = (140, 140, 140)
                box_fg = (0, 0, 0)
                box_bd = (180, 180, 180)

            score_surf = self.eval_font.render(str(score_txt), False, box_fg)
            box_pad_x = 8
            box_pad_y = 4
            box_rect = pg.Rect(x, y_line - 1, score_surf.get_width() + box_pad_x * 2, score_surf.get_height() + box_pad_y)
            try:
                pg.draw.rect(self.screen, box_bg, box_rect, border_radius=6)
                pg.draw.rect(self.screen, box_bd, box_rect, width=1, border_radius=6)
            except Exception:
                pass
            self.screen.blit(score_surf, (box_rect.x + box_pad_x, box_rect.y + (box_rect.h - score_surf.get_height()) // 2))
            x = box_rect.right + token_gap

            # Moves (clickable): each SAN token jumps to the resulting FEN after that ply.
            tokens: list[tuple[str, tuple[int, int, int], tuple[dict, int] | None]] = []
            for j, san in enumerate(moves):
                # Only make tokens clickable if we have a valid resulting fen.
                fen_to = ''
                try:
                    fen_to = str(fens[j]) if 0 <= j < len(fens) else ''
                except Exception:
                    fen_to = ''
                _ = ucis  # (kept for potential future UI, but we use stored pv_item)
                tokens.append((str(san), (220, 220, 220), (item, int(j)) if fen_to else None))

            # Draw tokens with wrapping. Use per-token spacing rather than joining into one string.
            if tokens:
                # First row continues after eval; subsequent rows align under the moves column.
                y_after = draw_tokens_row(x, y_line, tokens, int(max_x))
                yy = int(y_after)
            else:
                yy += line_h

            # Extra spacing between PV lines.
            yy += 2

        # While pending, show a subtle status without clearing the lines.
        if pv_pending:
            try:
                status = self.eval_font.render("(analyzing...)" , False, (160, 180, 200))
                self.screen.blit(status, (xx, yy))
                yy += line_h
            except Exception:
                pass

        # Clamp top scroll based on total content height.
        try:
            content_h = int((yy + int(top_scroll)) - int(top_rect.y) + int(pad))
            max_scroll = max(0, int(content_h - top_rect.h))
            try:
                self.review_top_scroll = max(0, min(int(top_scroll), int(max_scroll)))
            except Exception:
                self.review_top_scroll = 0
        except Exception:
            pass

        # Restore clip before drawing the move list section.
        try:
            if prev_clip is not None:
                self.screen.set_clip(prev_clip)
            else:
                self.screen.set_clip(None)
        except Exception:
            pass

        # Render move list inside bottom_rect (separate from PV/stats).
        xx = bottom_rect.x + pad
        yy = bottom_rect.y + pad
        title = self.eval_font.render("Moves:", False, (255, 255, 255))
        self.screen.blit(title, (xx, yy))
        yy += line_h

        list_top = yy
        # Reserve footer space for prev/next arrows.
        footer_h = max(28, int(self.eval_font.get_linesize() + 6))
        footer_y = int(bottom_rect.bottom - pad - footer_h)
        list_bottom = int(footer_y - 6)
        row_h = self.eval_font.get_linesize() + 6
        visible_rows = max(1, int((list_bottom - list_top) // row_h))

        # Footer nav buttons hitboxes (used by click handler).
        try:
            self._review_movelist_prev_rect = None
            self._review_movelist_next_rect = None
        except Exception:
            pass

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

        col2 = bottom_rect.x + (bottom_rect.w // 2)
        move_idx_at_pos = self.review_index - 1  # last move played to reach current position
        self._review_move_hitboxes = []
        self._review_variation_hitboxes = []
        self._review_variation_delete_hitboxes = []

        # Determine variation content to show under its anchor move.
        # - If actively viewing a variation, show that base and highlight the cursor.
        # - Otherwise, show any saved variation for the currently selected mainline ply.
        var_base = -1
        var_cursor_active = 0
        var_sans: list[str] = []
        var_fens: list[str] = []
        var_labels: list[str] = []
        try:
            if self._review_analysis_active:
                var_base = int(self._review_analysis_base_index)
                var_cursor_active = int(getattr(self, '_review_analysis_cursor', 0) or 0)
            else:
                var_base = int(self.review_index)
                var_cursor_active = 0
            var_sans = list(self._review_variations.get(var_base, []) or [])
            var_fens = list(self._review_variation_fens.get(var_base, []) or [])
            var_labels = list((getattr(self, '_review_variation_labels', {}) or {}).get(var_base, []) or [])
        except Exception:
            var_base = -1
            var_cursor_active = 0
            var_sans = []
            var_fens = []
            var_labels = []

        def decorate_var_san(j: int, san: str) -> tuple[str, tuple[int, int, int]]:
            tag = var_labels[j] if 0 <= j < len(var_labels) else ''
            if tag == 'Best':
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
            return san, (220, 220, 220)

        # Anchor ply is the last move that led to base position.
        # If base is 0 (start position), anchor under move 1.
        try:
            anchor_ply = 1 if var_base <= 0 else int(var_base)
        except Exception:
            anchor_ply = 1

        y_cursor = int(list_top)
        fm = 1 + int(self.review_move_scroll)
        while fm <= fullmove_count and (y_cursor + row_h) <= int(list_bottom):
            w_i = 2 * (fm - 1)
            b_i = w_i + 1
            w_san = sans[w_i] if w_i < len(sans) else ''
            b_san = sans[b_i] if b_i < len(sans) else ''

            y_row = int(y_cursor)
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

            y_cursor += int(row_h)

            # Insert the active variation line under the move where the branch starts.
            try:
                if var_base >= 0 and var_sans and (int(anchor_ply) in (w_i + 1, b_i + 1)) and (y_cursor < int(list_bottom)):
                    # Prefix "Var:" and indent.
                    prefix = self.eval_font.render("Var:", False, (180, 180, 180))
                    x0 = int(xx + 14)
                    y0 = int(y_cursor)
                    self.screen.blit(prefix, (x0, y0))
                    y = int(y0)
                    max_x2 = int(bottom_rect.right - pad)
                    row_h2 = self.eval_font.get_linesize() + 4

                    # Small delete button next to the variation.
                    x = int(x0 + prefix.get_width() + 10)
                    try:
                        del_surf = self.eval_font.render('x', False, (255, 140, 140))
                        del_rect = pg.Rect(int(x), int(y - 1), int(del_surf.get_width() + 10), int(row_h2))
                        pg.draw.rect(self.screen, (25, 25, 25), del_rect, border_radius=6)
                        pg.draw.rect(self.screen, (110, 110, 110), del_rect, width=1, border_radius=6)
                        self.screen.blit(del_surf, (del_rect.x + 5, del_rect.y + (del_rect.h - del_surf.get_height()) // 2))
                        self._review_variation_delete_hitboxes.append((del_rect, int(var_base)))
                        x = int(del_rect.right + 10)
                    except Exception:
                        x = int(x0 + prefix.get_width() + 10)

                    for j, san in enumerate(var_sans):
                        try:
                            txt, col = decorate_var_san(int(j), str(san))
                            surf = self.eval_font.render(str(txt), False, col)
                        except Exception:
                            continue

                        if x + surf.get_width() > max_x2 and x != int(x0):
                            x = int(x0)
                            y += int(row_h2)
                            # Stop drawing if we'd run out of panel.
                            if y + int(row_h2) > int(list_bottom):
                                break

                        rect = pg.Rect(x - 2, y - 1, surf.get_width() + 4, row_h2)

                        # Highlight the current variation move when we're inside the variation.
                        try:
                            if bool(getattr(self, '_review_analysis_active', False)) and int(var_cursor_active) == int(j + 1):
                                hi = pg.Surface((rect.w, rect.h), pg.SRCALPHA)
                                hi.fill((120, 200, 255, 55))
                                self.screen.blit(hi, (rect.x, rect.y))
                        except Exception:
                            pass

                        self.screen.blit(surf, (x, y))

                        try:
                            fen_to = str(var_fens[j]) if 0 <= j < len(var_fens) else ''
                        except Exception:
                            fen_to = ''
                        if fen_to:
                            # cursor index is 1-based into variation.
                            self._review_variation_hitboxes.append((rect, int(var_base), int(j + 1)))

                        x += surf.get_width() + 10

                    y_cursor = int(y + row_h2)
            except Exception:
                pass

            fm += 1

        # Footer nav buttons (◀ / ▶) at bottom of move list.
        try:
            btn_w = max(40, int(footer_h * 1.35))
            btn_h = int(footer_h)
            gap_btn = 12
            total_w = int(btn_w * 2 + gap_btn)
            bx = int(bottom_rect.centerx - total_w // 2)
            by = int(footer_y)
            prev_rect = pg.Rect(int(bx), int(by), int(btn_w), int(btn_h))
            next_rect = pg.Rect(int(bx + btn_w + gap_btn), int(by), int(btn_w), int(btn_h))
            self._review_movelist_prev_rect = prev_rect
            self._review_movelist_next_rect = next_rect

            try:
                pg.draw.line(self.screen, (140, 140, 140), (bottom_rect.x + pad, footer_y - 3), (bottom_rect.right - pad, footer_y - 3), width=1)
            except Exception:
                pass

            def draw_arrow_btn(r: pg.Rect, direction: str) -> None:
                pg.draw.rect(self.screen, (25, 25, 25), r, border_radius=8)
                pg.draw.rect(self.screen, (110, 110, 110), r, width=1, border_radius=8)
                cx, cy = r.centerx, r.centery
                sx = max(8, int(r.w * 0.18))
                sy = max(8, int(r.h * 0.28))
                if direction == 'left':
                    pts = [(cx + sx, cy - sy), (cx + sx, cy + sy), (cx - sx, cy)]
                else:
                    pts = [(cx - sx, cy - sy), (cx - sx, cy + sy), (cx + sx, cy)]
                pg.draw.polygon(self.screen, (220, 220, 220), pts)

            draw_arrow_btn(prev_rect, 'left')
            draw_arrow_btn(next_rect, 'right')
        except Exception:
            self._review_movelist_prev_rect = None
            self._review_movelist_next_rect = None

        # Store panel rect for click handling.
        self._review_panel_rect = panel

    def _draw_review_move_quality_marker(self) -> None:
        """Draw a small move-quality symbol on the destination square in review mode."""
        if not self.review_active:
            return
        tag = ''
        uci = ''

        in_variation_move = False

        # If we're inside a variation move, use variation labels instead of mainline labels.
        try:
            if bool(getattr(self, '_review_analysis_active', False)):
                base = int(getattr(self, '_review_analysis_base_index', -1))
                cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)
                if cur > 0 and base >= 0:
                    in_variation_move = True
                    vlabels = list((getattr(self, '_review_variation_labels', {}) or {}).get(base, []) or [])
                    vucis = list((getattr(self, '_review_variation_ucis', {}) or {}).get(base, []) or [])
                    if 0 <= (cur - 1) < len(vlabels):
                        tag = str(vlabels[cur - 1])
                    if 0 <= (cur - 1) < len(vucis):
                        uci = str(vucis[cur - 1])
        except Exception:
            tag = ''
            uci = ''
            in_variation_move = False

        # Never fall back to mainline while showing a variation position. If we don't have
        # a label/uci for this variation move yet, draw nothing (prevents mainline tags
        # appearing on the wrong square).
        if in_variation_move:
            if not tag or not uci:
                return

        # Fallback to mainline marker.
        if not tag or not uci:
            idx = int(self.review_index) - 1
            if idx < 0:
                return
            labels = getattr(self, 'review_move_labels', []) or []
            if idx >= len(labels):
                return
            tag = labels[idx]
            try:
                uci = str(self.review_plies[idx].uci())
            except Exception:
                return

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

    def _draw_analysis_move_quality_marker(self) -> None:
        """Draw a small move-quality symbol on the destination square in analysis mode."""
        if not self.analysis_active:
            return

        tag = ''
        uci = ''

        in_variation_move = False

        # If we're inside a variation move, use variation labels instead of mainline labels.
        try:
            if bool(getattr(self, '_review_analysis_active', False)):
                base = int(getattr(self, '_review_analysis_base_index', -1))
                cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)
                if cur > 0 and base >= 0:
                    in_variation_move = True
                    vlabels = list((getattr(self, '_review_variation_labels', {}) or {}).get(base, []) or [])
                    vucis = list((getattr(self, '_review_variation_ucis', {}) or {}).get(base, []) or [])
                    if 0 <= (cur - 1) < len(vlabels):
                        tag = str(vlabels[cur - 1])
                    if 0 <= (cur - 1) < len(vucis):
                        uci = str(vucis[cur - 1])
        except Exception:
            tag = ''
            uci = ''
            in_variation_move = False

        # Never fall back to mainline while showing a variation position.
        if in_variation_move:
            if not tag or not uci:
                return

        # Fallback to mainline marker.
        if not tag or not uci:
            idx = int(self.analysis_index) - 1
            if idx < 0:
                return
            labels = getattr(self, 'analysis_move_labels', []) or []
            if idx >= len(labels):
                return
            tag = labels[idx]
            try:
                uci = str(self.analysis_plies[idx].uci())
            except Exception:
                return

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
            a = (int(box * 0.25), int(box * 0.55))
            b = (int(box * 0.42), int(box * 0.72))
            c = (int(box * 0.78), int(box * 0.30))
            pg.draw.lines(marker, fg, False, [a, b, c], max(2, int(box * 0.10)))
        elif tag == 'Best':
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
            return False

        # Footer arrows: step backward/forward through positions.
        try:
            prev_r = getattr(self, '_review_movelist_prev_rect', None)
            next_r = getattr(self, '_review_movelist_next_rect', None)
            if prev_r is not None and prev_r.collidepoint(pos):
                self.review_step(-1)
                return True
            if next_r is not None and next_r.collidepoint(pos):
                self.review_step(1)
                return True
        except Exception:
            pass
        # Clicking an opening suggestion switches to that opening line.
        try:
            top_r = getattr(self, '_review_top_rect', None)
            if top_r is None or top_r.collidepoint(pos):
                more_r = getattr(self, '_review_opening_suggest_more_rect', None)
                if more_r is not None and more_r.collidepoint(pos):
                    try:
                        self._review_opening_suggest_offset = int(getattr(self, '_review_opening_suggest_offset', 0) or 0) + 3
                    except Exception:
                        self._review_opening_suggest_offset = 0
                    return True
                for rect, nm, uci_line in getattr(self, '_review_opening_suggestion_hitboxes', []):
                    if rect.collidepoint(pos):
                        self._apply_opening_respecting_mainline_from_uci('review', str(nm), str(uci_line))
                        return True
        except Exception:
            pass
        # Clicking the Opening line creates a saved, extendable variation (delete to remove).
        try:
            top_r = getattr(self, '_review_top_rect', None)
            orect = getattr(self, '_review_opening_rect', None)
            if orect is not None and orect.collidepoint(pos) and (top_r is None or top_r.collidepoint(pos)):
                rec = None
                try:
                    rec = self._opening_record_for_fen(str(self._review_display_fen()))
                except Exception:
                    rec = None
                nm = ''
                uci_line = ''
                if isinstance(rec, dict):
                    try:
                        nm = str(rec.get('name', '') or '')
                    except Exception:
                        nm = ''
                    try:
                        uci_line = str(rec.get('uci', '') or '')
                    except Exception:
                        uci_line = ''
                if uci_line:
                    self._apply_opening_respecting_mainline_from_uci('review', str(nm), str(uci_line))
                return True
        except Exception:
            pass
        # Review analysis controls (depth +/- and re-run).
        try:
            top_r = getattr(self, '_review_top_rect', None)
            if top_r is None or top_r.collidepoint(pos):
                rminus = getattr(self, '_review_depth_minus_rect', None)
                rplus = getattr(self, '_review_depth_plus_rect', None)
                rrun = getattr(self, '_review_reanalyze_btn_rect', None)
                if rminus is not None and rminus.collidepoint(pos):
                    try:
                        self.review_analysis_depth = max(6, int(self.review_analysis_depth) - 2)
                    except Exception:
                        self.review_analysis_depth = 10
                    return True
                if rplus is not None and rplus.collidepoint(pos):
                    try:
                        self.review_analysis_depth = min(20, int(self.review_analysis_depth) + 2)
                    except Exception:
                        self.review_analysis_depth = 10
                    return True
                if rrun is not None and rrun.collidepoint(pos):
                    try:
                        self._review_reanalyze_current_game()
                    except Exception:
                        pass
                    return True
        except Exception:
            pass
        # Analyze current displayed position.
        try:
            r = getattr(self, '_review_analyze_btn_rect', None)
            top_r = getattr(self, '_review_top_rect', None)
            if r is not None and r.collidepoint(pos) and (top_r is None or top_r.collidepoint(pos)):
                fen = self._review_display_fen()
                nm = ''
                try:
                    nm = str(self.review_name or 'Review')
                except Exception:
                    nm = 'Review'
                self.start_analysis_from_fen(str(fen), name=f"Analysis ({nm})")
                return True
        except Exception:
            pass
        # Delete user variation (small 'x' next to Var:).
        for rect, base_index in getattr(self, '_review_variation_delete_hitboxes', []):
            try:
                if rect.collidepoint(pos):
                    self._review_delete_variation(int(base_index))
                    return True
            except Exception:
                pass
        # PV (top lines) click-to-jump.
        try:
            top_r = getattr(self, '_review_top_rect', None)
            if top_r is None or top_r.collidepoint(pos):
                for rect, pv_item, move_index in getattr(self, '_review_pv_hitboxes', []):
                    if rect.collidepoint(pos):
                        if self._review_append_pv_to_user_variation(pv_item, int(move_index)):
                            return True
                        # Not currently inside a user variation: only create a variation if the
                        # chosen PV deviates from the mainline; otherwise just jump along mainline.
                        try:
                            uci_list = pv_item.get('uci') if isinstance(pv_item.get('uci'), list) else []
                            uci_list = [str(x) for x in uci_list]
                        except Exception:
                            uci_list = []
                        n_keep = max(1, min(int(move_index) + 1, len(uci_list)))
                        seq = list(uci_list[:n_keep])
                        if seq:
                            self._apply_uci_sequence_respecting_mainline('review', int(self.review_index), list(seq), default_label='')
                        return True
        except Exception:
            pass
        # User "Variation" click-to-jump.
        for rect, base_index, cursor_index in getattr(self, '_review_variation_hitboxes', []):
            if rect.collidepoint(pos):
                self._review_set_variation_cursor(int(base_index), int(cursor_index), play_sound=True)
                return True
        for rect, ply_index in getattr(self, '_review_move_hitboxes', []):
            if rect.collidepoint(pos):
                self._review_set_index(int(ply_index), play_sound=True)
                return True
        return False

    def _review_reanalyze_current_game(self) -> None:
        if not self.review_active or not self.review_fens:
            return
        try:
            start_fen = str(self.review_fens[0])
            start_board = chess.Board(start_fen)
        except Exception:
            return

        # Clear current aggregates so the UI shows progress again.
        try:
            self.review_acpl_white = None
            self.review_acpl_black = None
            self.review_accuracy_white = None
            self.review_accuracy_black = None
            self.review_accuracy_overall = None
            self.review_analysis_progress = 0.0
        except Exception:
            pass
        try:
            self.review_move_labels = ['' for _ in (self.review_plies or [])]
        except Exception:
            pass

        # Start analysis at the configured depth.
        self._start_review_analysis_thread(start_board, list(self.review_plies or []), depth=int(getattr(self, 'review_analysis_depth', 10)))

    def _review_delete_variation(self, base_index: int) -> None:
        """Delete the saved user variation for a mainline ply index."""
        base = int(base_index)
        try:
            self._review_variations.pop(base, None)
        except Exception:
            pass
        try:
            self._review_variation_fens.pop(base, None)
        except Exception:
            pass
        try:
            self._review_variation_ucis.pop(base, None)
        except Exception:
            pass
        try:
            self._review_variation_labels.pop(base, None)
        except Exception:
            pass

        # If we were currently viewing that variation, exit back to the mainline position.
        try:
            if bool(getattr(self, '_review_analysis_active', False)) and int(getattr(self, '_review_analysis_base_index', -1)) == base:
                self._review_analysis_active = False
                self._review_analysis_fen = ''
                self._review_analysis_cursor = 0
        except Exception:
            pass

        # Re-sync to current mainline position.
        try:
            if self.review_active and self.review_fens:
                self._load_fen_into_ui(str(self.review_fens[self.review_index]))
                self.request_eval()
                self.request_review_pv()
            elif self.analysis_active and self.analysis_fens:
                self._load_fen_into_ui(str(self.analysis_fens[self.analysis_index]))
                self.request_eval()
                self.request_review_pv()
        except Exception:
            pass

        # Persist deletion.
        try:
            if self.review_active and self.review_pgn_path:
                self._save_review_analysis_cache(str(self.review_pgn_path), self.review_plies or [])
        except Exception:
            pass

    def _handle_review_board_click(self, mouse_pos: tuple[int, int]) -> None:
        if not self.review_active:
            return

        # Respect movement mode.
        if not self.movement_click_enabled:
            return

        target = self._mouse_to_square(mouse_pos)
        if target is None:
            self.selected_square = None
            return

        target_row, target_col = target

        fen = self._review_display_fen()
        if not fen:
            self.selected_square = None
            return

        try:
            board = chess.Board(fen)
        except Exception:
            self.selected_square = None
            return

        # First click: select a piece that belongs to side-to-move.
        if self.selected_square is None:
            try:
                sq = self._chess_square_from_coords(target_row, target_col)
                piece = board.piece_at(sq)
                if piece is None:
                    self.selected_square = None
                    return
                if piece.color != board.turn:
                    self.selected_square = None
                    return
                self.selected_square = (target_row, target_col)
                return
            except Exception:
                self.selected_square = None
                return

        sel_row, sel_col = self.selected_square
        if (sel_row, sel_col) == (target_row, target_col):
            self.selected_square = None
            return

        try:
            from_sq = self._chess_square_from_coords(sel_row, sel_col)
            to_sq = self._chess_square_from_coords(target_row, target_col)
        except Exception:
            self.selected_square = None
            return

        promo = None
        try:
            p = board.piece_at(from_sq)
            if p is not None and p.piece_type == chess.PAWN:
                to_rank = chess.square_rank(to_sq)
                if (p.color == chess.WHITE and to_rank == 7) or (p.color == chess.BLACK and to_rank == 0):
                    promo = chess.QUEEN
        except Exception:
            promo = None

        mv = chess.Move(from_sq, to_sq, promotion=promo)
        if mv not in board.legal_moves:
            self.selected_square = None
            return

        # If we're on a previous mainline position and the move matches the next mainline move,
        # do not create a variation; just move forward on the mainline.
        try:
            if (not bool(getattr(self, '_review_analysis_active', False))) and int(self.review_index) < int(len(self.review_fens) - 1):
                if 0 <= int(self.review_index) < len(self.review_plies) and str(mv.uci()) == str(self.review_plies[int(self.review_index)].uci()):
                    self._review_set_index(int(self.review_index) + 1, play_sound=True)
                    self.selected_square = None
                    return
        except Exception:
            pass

        # Start or continue an analysis branch.
        if not self._review_analysis_active:
            self._review_analysis_active = True
            self._review_analysis_base_index = int(self.review_index)
            self._review_analysis_cursor = 0
            self._review_variations[int(self.review_index)] = []
            self._review_variation_fens[int(self.review_index)] = []
            self._review_variation_ucis[int(self.review_index)] = []
            try:
                self._review_variation_labels[int(self.review_index)] = []
            except Exception:
                pass

        # This is a user-built line; PV browsing mode is not active.
        try:
            self._review_pv_variation_active = False
        except Exception:
            pass
        try:
            san = board.san(mv)
        except Exception:
            san = mv.uci()

        fen_before = fen
        try:
            board.push(mv)
        except Exception:
            self.selected_square = None
            return

        base = int(getattr(self, '_review_analysis_base_index', int(self.review_index)))
        cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)

        # If the user stepped back within the variation and then makes a new move,
        # truncate the tail and continue from the current cursor.
        try:
            if cur < len(self._review_variations.get(base, [])):
                self._review_variations[base] = list(self._review_variations.get(base, [])[:cur])
        except Exception:
            pass
        try:
            if cur < len(self._review_variation_fens.get(base, [])):
                self._review_variation_fens[base] = list(self._review_variation_fens.get(base, [])[:cur])
        except Exception:
            pass
        try:
            if cur < len(self._review_variation_ucis.get(base, [])):
                self._review_variation_ucis[base] = list(self._review_variation_ucis.get(base, [])[:cur])
        except Exception:
            pass
        try:
            if cur < len((getattr(self, '_review_variation_labels', {}) or {}).get(base, []) or []):
                self._review_variation_labels[base] = list((self._review_variation_labels.get(base, []) or [])[:cur])
        except Exception:
            pass

        new_fen = board.fen()
        self._review_analysis_fen = new_fen
        try:
            self._review_variations[int(base)].append(str(san))
        except Exception:
            pass
        try:
            self._review_variation_fens[int(base)].append(str(new_fen))
        except Exception:
            pass
        try:
            self._review_variation_ucis[int(base)].append(str(mv.uci()))
        except Exception:
            pass
        try:
            self._review_variation_labels[int(base)].append('')
        except Exception:
            pass
        try:
            self._review_analysis_cursor = len(self._review_variation_fens.get(int(base), []))
        except Exception:
            pass

        # Update UI to the analysis position.
        try:
            self.last_move = [str(mv.uci())]
        except Exception:
            self.last_move = []
        self._load_fen_into_ui(new_fen)
        self.request_eval()
        self.request_review_pv()

        # Sound feedback for analysis moves.
        try:
            self._play_sound_for_move_from_fen(fen_before, mv)
        except Exception:
            pass

        # Best-move arrow from mainline no longer applies.
        self.review_best_move_uci = ''
        self.review_arrow = None

        self.selected_square = None

        # Persist the variation immediately (labels may fill in asynchronously).
        try:
            if self.review_active and self.review_pgn_path:
                self._save_review_analysis_cache(str(self.review_pgn_path), self.review_plies or [])
        except Exception:
            pass

        # Analyze labels for this variation in the background.
        try:
            self._start_review_variation_analysis_thread(int(base))
        except Exception:
            pass

    def _ensure_layout(self, force: bool = False) -> None:
        """Ensure the board layout matches the current mode.

        In review/analysis mode, shrink and left-align the board so the side panel fits on the right.
        """
        try:
            win_w, win_h = self.screen.get_size()
        except Exception:
            return

        review_like = bool(self.review_active or self.analysis_active)
        moves_visible = bool((not review_like) and bool(getattr(self, 'game_movelist_visible', True)))
        key = (
            bool(review_like),
            bool(moves_visible),
            int(win_w),
            int(win_h),
            str(self.board_style),
            bool(self.show_numbers),
            float(getattr(self, '_board_user_scale', 1.0)),
        )
        if not force and self._layout_cache_key == key:
            return
        self._layout_cache_key = key

        if not review_like:
            if moves_visible:
                # Normal play + move list layout:
                # - Board is centered within the left 60% of the window.
                # - Move list panel occupies the right 40%.
                # - Board labels (1-8 / a-h) must never be clipped when enabled.
                gap = 18
                board_area_w = int(win_w * 0.60)
                right_limit = int(board_area_w - gap)

                try:
                    scale = float(self._board_user_scale)
                except Exception:
                    scale = 0.92
                scale = max(0.45, min(1.0, scale))

                # Reserve space for clocks (above/below) and the action buttons above the board.
                top_reserved = 0
                left_gutter = 60 if self.show_numbers else 20
                bottom_gutter = 10
                base = 1
                new_size = int(self.size)

                for _ in range(3):
                    board_available_w = max(120, int(right_limit - left_gutter))
                    board_available_h = max(120, int(win_h - top_reserved - bottom_gutter - 10))
                    board_px = int(min(board_available_w, board_available_h))
                    base = max(1, int(board_px // 8))
                    new_size = int(max(10, min(base, int(round(base * scale)))))

                    if self.show_numbers:
                        try:
                            font_h = int(self.font.get_linesize())
                        except Exception:
                            font_h = 18
                        left_gutter = max(20, int((new_size / 2) + 12))
                        bottom_gutter = max(10, int((new_size / 2) + font_h + 10))
                    else:
                        left_gutter = 20
                        bottom_gutter = 10

                    # Mirror the sizing logic in _draw_clocks (and _draw_game_action_buttons).
                    margin_clock = max(8, int(new_size * 0.12))
                    box_h = max(26, int(new_size * 0.55))
                    label_h = int(new_size * (0.60 if self.show_numbers else 0.25))
                    btn_h = max(24, int(box_h * 0.92))

                    # Top needs room for: buttons above, then black clock, then board.
                    gap_btn_clock = max(6, int(margin_clock * 0.8))
                    top_reserved = max(10, int(btn_h + gap_btn_clock + box_h + margin_clock + 10))

                    # Bottom needs room for: letters + white clock.
                    bottom_clock = int(label_h + margin_clock + box_h + 10)
                    bottom_gutter = max(int(bottom_gutter), int(bottom_clock))

                self._board_base_size = int(base)

                if int(new_size) != int(self.size):
                    self.size = int(new_size)
                    try:
                        self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
                        self.board_background = pg.transform.smoothscale(self.board_background, (self.size * 8, self.size * 8))
                    except Exception:
                        pass

                board_w = int(self.size * 8)
                board_h = int(self.size * 8)

                x0 = int((right_limit - board_w) / 2)
                x0 = max(int(left_gutter), min(int(x0), int(right_limit - board_w)))

                y_min = max(10, int(top_reserved))
                y_max = int(win_h - bottom_gutter - board_h - 10)
                if y_max < y_min:
                    y0 = int(y_min)
                else:
                    y0 = int((y_min + y_max) / 2)

                self.offset = [float(x0), float(y0)]
                return

            # Normal play layout (reserve space for clocks + labels).
            try:
                new_size, new_offset, show_nums, base = self._compute_normal_layout(int(win_w), int(win_h))
            except Exception:
                return

            self.show_numbers = bool(show_nums)
            self._board_base_size = int(base)

            if int(new_size) != int(self.size):
                self.size = int(new_size)
                try:
                    self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
                    self.board_background = pg.transform.smoothscale(self.board_background, (self.size * 8, self.size * 8))
                except Exception:
                    pass

            self.offset = [float(new_offset[0]), float(new_offset[1])]
            return

        # Review/Analysis layout:
        # - Board is centered within the left 60% of the window.
        # - Stats panel occupies the right 40%.
        # - Board labels (1-8 / a-h) must never be clipped when enabled.
        gap = 18
        board_area_w = int(win_w * 0.60)
        right_limit = int(board_area_w - gap)

        try:
            scale = float(self._board_user_scale)
        except Exception:
            scale = 0.92
        scale = max(0.45, min(1.0, scale))

        # Reserve top space in analysis *and* review so the board sits at the same vertical
        # position in both modes (review matches the height of the analysis Undo row).
        reserve_top_row = bool((self.analysis_active or self.review_active) and (not self.end_popup_active))

        top_reserved = 0
        left_gutter = 60 if self.show_numbers else 20
        bottom_gutter = 10
        base = 1
        new_size = int(self.size)

        # A few passes because gutters depend on the final size.
        for _ in range(3):
            board_available_w = max(120, int(right_limit - left_gutter))
            board_available_h = max(120, int(win_h - top_reserved - bottom_gutter - 10))
            board_px = int(min(board_available_w, board_available_h))
            base = max(1, int(board_px // 8))
            new_size = int(max(10, min(base, int(round(base * scale)))))

            # Ensure labels (numbers/letters) remain visible when enabled.
            if self.show_numbers:
                try:
                    font_h = int(self.font.get_linesize())
                except Exception:
                    font_h = 18
                # Numbers are drawn at x = offset_x - size/2.
                left_gutter = max(20, int((new_size / 2) + 12))
                # Letters are drawn ~0.5*square below the board.
                bottom_gutter = max(10, int((new_size / 2) + font_h + 10))
            else:
                left_gutter = 20
                bottom_gutter = 10

            # Mirror the sizing logic in _draw_game_action_buttons.
            if reserve_top_row:
                margin_btn = max(8, int(new_size * 0.12))
                box_h = max(26, int(new_size * 0.55))
                btn_h = max(24, int(box_h * 0.92))
                top_reserved = int(btn_h + margin_btn + 10)
            else:
                top_reserved = 0

        self._board_base_size = int(base)

        if new_size != int(self.size):
            self.size = int(new_size)
            try:
                self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
                self.board_background = pg.transform.smoothscale(self.board_background, (self.size * 8, self.size * 8))
            except Exception:
                pass

        board_w = int(self.size * 8)
        board_h = int(self.size * 8)

        # Center horizontally within the left 60% region (without colliding with label gutter).
        x0 = int((right_limit - board_w) / 2)
        x0 = max(int(left_gutter), min(int(x0), int(right_limit - board_w)))

        # Center vertically within the remaining space, keeping top buttons and bottom labels visible.
        y_min = max(10, int(top_reserved))
        y_max = int(win_h - bottom_gutter - board_h - 10)
        if y_max < y_min:
            y0 = int(y_min)
        else:
            y0 = int((y_min + y_max) / 2)

        self.offset = [float(x0), float(y0)]

    def _restore_normal_layout(self) -> None:
        """Restore the default (non-review) board layout."""
        self._layout_cache_key = None
        self._ensure_layout(force=True)

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
        if new_idx != prev:
            if play_sound:
                self._play_review_navigation_sound(prev, new_idx)
            self.review_index = new_idx

        # Leaving analysis branch when navigating.
        self._review_analysis_active = False
        self._review_analysis_base_index = int(self.review_index)
        self._review_analysis_cursor = 0
        self._review_analysis_fen = ''
        try:
            self._review_pv_variation_active = False
        except Exception:
            pass
        try:
            with self._review_pv_lock:
                # Keep previous PV visible while new PV is computing.
                self.review_pv_pending = True
        except Exception:
            pass

        # Highlight last move like in the main game.
        try:
            if self.review_index <= 0:
                self.last_move = []
            else:
                self.last_move = [str(self.review_plies[self.review_index - 1].uci())]
        except Exception:
            self.last_move = []

        # Always reload the mainline FEN for this ply (important when exiting a variation
        # while staying on the same mainline ply).
        self._load_fen_into_ui(self.review_fens[self.review_index])
        # Update eval bar for the currently viewed review position.
        self.request_eval()
        # Update PV lines for the currently viewed review position.
        self.request_review_pv()
        if self.review_show_best_move:
            self._request_review_best()
        else:
            self.review_best_move_uci = ''
            self.review_arrow = None

    def _analysis_scroll_moves(self, delta_rows: int) -> None:
        try:
            self.analysis_move_scroll = max(0, int(self.analysis_move_scroll) + int(delta_rows))
        except Exception:
            self.analysis_move_scroll = 0

    def start_analysis_new(self) -> None:
        """Enter analysis mode from the initial position."""
        # Switching modes: ensure review is fully deactivated so overlays don't stack.
        try:
            if getattr(self, 'review_active', False):
                self.exit_review(return_to_start_menu=False)
        except Exception:
            pass
        self.analysis_last_error = ''
        self.analysis_active = True
        self.analysis_path = None
        self.analysis_name = 'Analysis'
        self.analysis_last_saved_path = ''

        start_fen = chess.STARTING_FEN
        self.analysis_fens = [str(start_fen)]
        self.analysis_plies = []
        self.analysis_sans = []
        self.analysis_move_labels = []
        self.analysis_opening_names = []
        self.analysis_index = 0
        self.analysis_move_scroll = 0
        try:
            self.analysis_top_scroll = 0
        except Exception:
            pass
        self.analysis_progress = None

        # Reset any PV/variation overlay state.
        self._review_analysis_active = False
        self._review_analysis_base_index = 0
        self._review_analysis_fen = ''
        self._review_analysis_cursor = 0
        self._review_variations = {}
        self._review_variation_fens = {}
        self._review_variation_ucis = {}

        try:
            with self._review_pv_lock:
                self.review_pv_lines = []
                self.review_pv_pending = False
        except Exception:
            pass
        try:
            self._review_pv_render_cache = []
            self._review_pv_render_pending = False
            self._review_pv_last_requested_fen = ''
        except Exception:
            pass

        # Analysis mode does not support premoves.
        try:
            self._clear_premove()
        except Exception:
            pass

        # Load position.
        self.last_move = []
        self._load_fen_into_ui(start_fen)
        self._ensure_layout(force=True)
        self.request_eval()
        self.request_review_pv()

    def start_analysis_from_fen(self, fen: str, name: str | None = None) -> bool:
        """Enter analysis mode starting from an arbitrary FEN (no moves yet)."""
        if not fen:
            return False

        # Switching modes: ensure review is fully deactivated so overlays don't stack.
        try:
            if getattr(self, 'review_active', False):
                self.exit_review(return_to_start_menu=False)
        except Exception:
            pass

        # Validate FEN.
        try:
            _ = chess.Board(str(fen))
        except Exception:
            try:
                self.analysis_last_error = 'Invalid FEN'
            except Exception:
                pass
            return False

        self.analysis_last_error = ''
        self.analysis_active = True
        self.analysis_path = None
        self.analysis_name = str(name or 'Analysis')
        self.analysis_last_saved_path = ''

        start_fen = str(fen)
        self.analysis_fens = [start_fen]
        self.analysis_plies = []
        self.analysis_sans = []
        self.analysis_move_labels = []
        self.analysis_opening_names = []
        self.analysis_index = 0
        self.analysis_move_scroll = 0
        try:
            self.analysis_top_scroll = 0
        except Exception:
            pass
        self.analysis_progress = None

        # Reset any PV/variation overlay state.
        self._review_analysis_active = False
        self._review_analysis_base_index = 0
        self._review_analysis_fen = ''
        self._review_analysis_cursor = 0
        self._review_variations = {}
        self._review_variation_fens = {}
        self._review_variation_ucis = {}

        try:
            with self._review_pv_lock:
                self.review_pv_lines = []
                self.review_pv_pending = False
        except Exception:
            pass
        try:
            self._review_pv_render_cache = []
            self._review_pv_render_pending = False
            self._review_pv_last_requested_fen = ''
        except Exception:
            pass

        # Analysis mode does not support premoves.
        try:
            self._clear_premove()
        except Exception:
            pass

        self.last_move = []
        self._load_fen_into_ui(start_fen)
        self._ensure_layout(force=True)
        self.request_eval()
        self.request_review_pv()
        return True

    def start_analysis_from_file(self, path: str) -> bool:
        """Load a saved analysis file and enter analysis mode."""
        # Switching modes: ensure review is fully deactivated so overlays don't stack.
        try:
            if getattr(self, 'review_active', False):
                self.exit_review(return_to_start_menu=False)
        except Exception:
            pass
        self.analysis_last_error = ''
        if not path:
            self.analysis_last_error = 'No file provided'
            return False
        try:
            import json
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            self.analysis_last_error = f'Could not read file: {str(e).splitlines()[0]}'
            return False

        if not isinstance(data, dict):
            self.analysis_last_error = 'Invalid analysis file'
            return False

        start_fen = str(data.get('start_fen') or chess.STARTING_FEN)
        moves_uci = data.get('moves_uci') if isinstance(data.get('moves_uci'), list) else []
        move_labels = data.get('move_labels') if isinstance(data.get('move_labels'), list) else []
        opening_names = data.get('opening_names') if isinstance(data.get('opening_names'), list) else []

        # Rebuild fens + SAN from UCI moves.
        try:
            board = chess.Board(start_fen)
        except Exception:
            self.analysis_last_error = 'Invalid start FEN'
            return False

        fens = [board.fen()]
        plies: list[chess.Move] = []
        sans: list[str] = []
        for u in moves_uci:
            try:
                mv = chess.Move.from_uci(str(u))
                san = board.san(mv)
                board.push(mv)
                plies.append(mv)
                sans.append(str(san))
                fens.append(board.fen())
            except Exception:
                # Stop at first invalid move.
                break

        self.analysis_active = True
        self.analysis_path = str(path)
        self.analysis_name = str(data.get('name') or 'Analysis')
        self.analysis_last_saved_path = str(path)
        self.analysis_fens = list(fens)
        self.analysis_plies = list(plies)
        self.analysis_sans = list(sans)
        self.analysis_move_labels = list(move_labels)[: len(self.analysis_sans)]
        self.analysis_opening_names = list(opening_names)[: len(self.analysis_sans)]
        # Default to last position.
        self.analysis_index = len(self.analysis_fens) - 1
        self.analysis_move_scroll = 0
        try:
            self.analysis_top_scroll = 0
        except Exception:
            pass
        self.analysis_progress = None

        # Reset any variation overlay.
        self._review_analysis_active = False
        self._review_analysis_base_index = 0
        self._review_analysis_fen = ''
        self._review_analysis_cursor = 0
        self._review_variations = {}
        self._review_variation_fens = {}
        self._review_variation_ucis = {}

        try:
            with self._review_pv_lock:
                self.review_pv_lines = []
                self.review_pv_pending = False
        except Exception:
            pass
        try:
            self._review_pv_render_cache = []
            self._review_pv_render_pending = False
            self._review_pv_last_requested_fen = ''
        except Exception:
            pass

        # Analysis mode does not support premoves.
        try:
            self._clear_premove()
        except Exception:
            pass

        # Load UI.
        try:
            if self.analysis_index <= 0:
                self.last_move = []
            else:
                self.last_move = [str(self.analysis_plies[self.analysis_index - 1].uci())]
        except Exception:
            self.last_move = []
        self._load_fen_into_ui(self.analysis_fens[self.analysis_index])
        self._ensure_layout(force=True)
        self.request_eval()
        self.request_review_pv()

        # Kick a lightweight re-annotation if missing labels.
        try:
            if len(self.analysis_move_labels) != len(self.analysis_sans):
                self._start_analysis_annotation_thread(chess.Board(start_fen), list(self.analysis_plies))
        except Exception:
            pass
        return True

    def exit_analysis(self, return_to_start_menu: bool = True) -> None:
        try:
            self._analysis_run_id += 1
        except Exception:
            pass
        self.analysis_active = False
        self.analysis_path = None
        self.analysis_name = ''
        self.analysis_fens = []
        self.analysis_plies = []
        self.analysis_sans = []
        self.analysis_move_labels = []
        self.analysis_opening_names = []
        self.analysis_index = 0
        self.analysis_move_scroll = 0
        self.analysis_progress = None
        self.analysis_last_error = ''
        self.analysis_last_saved_path = ''
        self._analysis_move_hitboxes = []
        self._analysis_pv_hitboxes = []
        self._analysis_variation_hitboxes = []
        self._review_analysis_active = False
        self._review_analysis_base_index = 0
        self._review_analysis_fen = ''
        self._review_analysis_cursor = 0
        self._review_variations = {}
        self._review_variation_fens = {}
        self._review_variation_ucis = {}
        try:
            self._review_pv_variation_active = False
            self._review_pv_variation_base_index = 0
            self._review_pv_variation_fens = []
            self._review_pv_variation_ucis = []
            self._review_pv_variation_sans = []
        except Exception:
            pass
        try:
            with self._review_pv_lock:
                self.review_pv_lines = []
                self.review_pv_pending = False
        except Exception:
            pass

        # Return to start menu (ESC behavior). When switching modes (e.g. analysis -> review),
        # callers pass return_to_start_menu=False so we don't reset state mid-transition.
        if return_to_start_menu:
            try:
                self._start_menu_shown = False
            except Exception:
                pass
            self._restore_normal_layout()
            self.reset_game()

    def save_analysis(self) -> bool:
        """Save current analysis line to data/analysis as a JSON file."""
        self.analysis_last_error = ''
        if not self.analysis_active:
            return False
        try:
            os.makedirs('data/analysis', exist_ok=True)
        except Exception:
            pass
        ts = time.strftime('%Y%m%d_%H%M%S')
        path = os.path.join('data', 'analysis', f'analysis_{ts}.json')

        try:
            import json
            data = {
                'version': 1,
                'name': str(self.analysis_name or 'Analysis'),
                'created': ts,
                'start_fen': str(self.analysis_fens[0]) if self.analysis_fens else chess.STARTING_FEN,
                'moves_uci': [str(m.uci()) for m in (self.analysis_plies or [])],
                'move_labels': list(self.analysis_move_labels or []),
                'opening_names': list(self.analysis_opening_names or []),
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            self.analysis_last_saved_path = str(path)
            self.analysis_path = str(path)
            return True
        except Exception as e:
            self.analysis_last_error = f'Failed to save: {str(e).splitlines()[0]}'
            return False

    def analysis_step(self, delta: int) -> None:
        if not self.analysis_active or not self.analysis_fens:
            return
        d = int(delta)
        if d == 0:
            return

        # Step through an overlay variation (PV) first.
        try:
            if self._review_analysis_active:
                base = int(self._review_analysis_base_index)
                cur = int(self._review_analysis_cursor)
                vfens = self._review_variation_fens.get(base, [])
                vuci = self._review_variation_ucis.get(base, [])

                # If we're at the end of a variation and press right, do nothing.
                # (Don't spill over into mainline navigation.)
                if d > 0 and cur >= len(vfens):
                    return

                if d > 0 and cur < len(vfens):
                    new_cur = cur + 1
                    tgt_fen = str(vfens[new_cur - 1])
                    uci = str(vuci[new_cur - 1]) if 0 <= (new_cur - 1) < len(vuci) else ''
                    fen_before = ''
                    if new_cur == 1:
                        fen_before = str(self.analysis_fens[base]) if 0 <= base < len(self.analysis_fens) else ''
                    else:
                        fen_before = str(vfens[new_cur - 2])
                    if uci:
                        try:
                            self.last_move = [uci]
                        except Exception:
                            self.last_move = []
                        try:
                            self._play_sound_for_move_from_fen(str(fen_before), chess.Move.from_uci(str(uci)))
                        except Exception:
                            pass
                    self._review_analysis_cursor = int(new_cur)
                    self._review_analysis_fen = ''
                    self._load_fen_into_ui(tgt_fen)
                    self.request_eval()
                    self.request_review_pv()
                    return

                if d < 0 and cur > 0:
                    new_cur = cur - 1
                    self._review_analysis_cursor = int(new_cur)
                    self._review_analysis_fen = ''
                    if new_cur == 0:
                        # Back to base position in analysis mainline.
                        try:
                            if base <= 0:
                                self.last_move = []
                            else:
                                self.last_move = [str(self.analysis_plies[base - 1].uci())]
                        except Exception:
                            self.last_move = []
                        self._load_fen_into_ui(str(self.analysis_fens[base]))
                    else:
                        tgt_fen = str(vfens[new_cur - 1])
                        uci = str(vuci[new_cur - 1]) if 0 <= (new_cur - 1) < len(vuci) else ''
                        try:
                            self.last_move = [uci] if uci else []
                        except Exception:
                            self.last_move = []
                        self._load_fen_into_ui(tgt_fen)
                    self.request_eval()
                    self.request_review_pv()
                    return
        except Exception:
            pass

        new_idx = self.analysis_index + d
        self._analysis_set_index(new_idx, play_sound=True)

    def _analysis_set_index(self, new_idx: int, play_sound: bool = False) -> None:
        if not self.analysis_active or not self.analysis_fens:
            return
        prev = int(self.analysis_index)
        new_idx = max(0, min(len(self.analysis_fens) - 1, int(new_idx)))
        if new_idx == prev:
            # If we're currently inside a variation overlay, selecting the current mainline
            # index should act as an explicit exit from the overlay.
            if bool(getattr(self, '_review_analysis_active', False)):
                self._review_analysis_active = False
                self._review_analysis_base_index = int(self.analysis_index)
                self._review_analysis_fen = ''
                self._review_analysis_cursor = 0
                try:
                    if self.analysis_index <= 0:
                        self.last_move = []
                    else:
                        self.last_move = [str(self.analysis_plies[self.analysis_index - 1].uci())]
                except Exception:
                    self.last_move = []
                try:
                    self._load_fen_into_ui(self.analysis_fens[self.analysis_index])
                except Exception:
                    pass
                self.request_eval()
                self.request_review_pv()
            return
        if play_sound:
            try:
                # Similar to review: contextual sound on +1, else generic.
                if new_idx == prev + 1 and 0 <= prev < len(self.analysis_plies):
                    fen_before = self.analysis_fens[prev]
                    board = chess.Board(fen_before)
                    mv = self.analysis_plies[prev]
                    self._play_sound_for_move_from_fen(str(fen_before), mv)
                else:
                    pg.mixer.music.load('data/sounds/move.mp3')
                    pg.mixer.music.play(1)
            except Exception:
                pass
        self.analysis_index = int(new_idx)

        # Leaving overlay variation when navigating the mainline.
        self._review_analysis_active = False
        self._review_analysis_base_index = int(self.analysis_index)
        self._review_analysis_fen = ''
        self._review_analysis_cursor = 0

        try:
            if self.analysis_index <= 0:
                self.last_move = []
            else:
                self.last_move = [str(self.analysis_plies[self.analysis_index - 1].uci())]
        except Exception:
            self.last_move = []

        self._load_fen_into_ui(self.analysis_fens[self.analysis_index])
        self.request_eval()
        self.request_review_pv()

    def _analysis_display_fen(self) -> str:
        if self._review_analysis_active:
            try:
                base = int(self._review_analysis_base_index)
                cur = int(self._review_analysis_cursor)
                vfens = self._review_variation_fens.get(base, [])
                if cur > 0 and 0 <= (cur - 1) < len(vfens):
                    return str(vfens[cur - 1])
            except Exception:
                pass
            return self._review_analysis_fen or (self.analysis_fens[self.analysis_index] if self.analysis_fens else '')
        return self.analysis_fens[self.analysis_index] if self.analysis_fens else ''

    def _handle_analysis_click(self, pos: tuple[int, int]) -> bool:
        if not self.analysis_active:
            return False

        # Footer arrows: step backward/forward through positions.
        try:
            prev_r = getattr(self, '_analysis_movelist_prev_rect', None)
            next_r = getattr(self, '_analysis_movelist_next_rect', None)
            if prev_r is not None and prev_r.collidepoint(pos):
                self.analysis_step(-1)
                return True
            if next_r is not None and next_r.collidepoint(pos):
                self.analysis_step(1)
                return True
        except Exception:
            pass
        # Clicking an opening suggestion switches to that opening line.
        try:
            top_r = getattr(self, '_analysis_top_rect', None)
            if top_r is None or top_r.collidepoint(pos):
                more_r = getattr(self, '_analysis_opening_suggest_more_rect', None)
                if more_r is not None and more_r.collidepoint(pos):
                    try:
                        self._analysis_opening_suggest_offset = int(getattr(self, '_analysis_opening_suggest_offset', 0) or 0) + 3
                    except Exception:
                        self._analysis_opening_suggest_offset = 0
                    return True
                for rect, nm, uci_line in getattr(self, '_analysis_opening_suggestion_hitboxes', []):
                    if rect.collidepoint(pos):
                        self._apply_opening_respecting_mainline_from_uci('analysis', str(nm), str(uci_line))
                        return True
        except Exception:
            pass
        # Clicking the Opening line creates a saved, extendable variation (delete to remove).
        try:
            top_r = getattr(self, '_analysis_top_rect', None)
            orect = getattr(self, '_analysis_opening_rect', None)
            if orect is not None and orect.collidepoint(pos) and (top_r is None or top_r.collidepoint(pos)):
                rec = None
                try:
                    rec = self._opening_record_for_fen(str(self._analysis_display_fen()))
                except Exception:
                    rec = None
                nm = ''
                uci_line = ''
                if isinstance(rec, dict):
                    try:
                        nm = str(rec.get('name', '') or '')
                    except Exception:
                        nm = ''
                    try:
                        uci_line = str(rec.get('uci', '') or '')
                    except Exception:
                        uci_line = ''
                if uci_line:
                    self._apply_opening_respecting_mainline_from_uci('analysis', str(nm), str(uci_line))
                return True
        except Exception:
            pass

        # Delete saved variation (small 'x' next to Var:).
        for rect, base_index in getattr(self, '_analysis_variation_delete_hitboxes', []):
            try:
                if rect.collidepoint(pos):
                    self._review_delete_variation(int(base_index))
                    return True
            except Exception:
                pass
        top_r = getattr(self, '_analysis_top_rect', None)
        if top_r is None or top_r.collidepoint(pos):
            for rect, pv_item, move_index in getattr(self, '_analysis_pv_hitboxes', []):
                if rect.collidepoint(pos):
                    self._analysis_start_pv_variation(pv_item, int(move_index))
                    return True
        for rect, base_index, cursor_index in getattr(self, '_analysis_variation_hitboxes', []):
            if rect.collidepoint(pos):
                self._analysis_set_variation_cursor(int(base_index), int(cursor_index), play_sound=True)
                return True
        for rect, ply_index in getattr(self, '_analysis_move_hitboxes', []):
            if rect.collidepoint(pos):
                self._analysis_set_index(int(ply_index), play_sound=True)
                return True
        return False

    def _analysis_set_variation_cursor(self, base_index: int, cursor: int, play_sound: bool = True) -> None:
        """Analysis-mode wrapper around the variation cursor setter."""
        # Reuse the existing implementation, but ensure it uses analysis mainline for base_fen.
        base = int(base_index)
        cur = int(cursor)
        vfens = self._review_variation_fens.get(base, [])
        vucis = self._review_variation_ucis.get(base, [])

        if cur < 0:
            cur = 0
        if cur > len(vfens):
            cur = len(vfens)

        self._review_analysis_active = True
        self._review_analysis_base_index = int(base)
        self._review_analysis_cursor = int(cur)
        self._review_analysis_fen = ''

        if cur == 0:
            tgt_fen = ''
            try:
                tgt_fen = str(self.analysis_fens[base]) if 0 <= base < len(self.analysis_fens) else ''
            except Exception:
                tgt_fen = ''
            if not tgt_fen:
                return
            try:
                self.last_move = [] if base <= 0 else [str(self.analysis_plies[base - 1].uci())]
            except Exception:
                self.last_move = []
            self._load_fen_into_ui(tgt_fen)
            self.request_eval()
            self.request_review_pv()
            return

        tgt_fen = ''
        try:
            tgt_fen = str(vfens[cur - 1])
        except Exception:
            tgt_fen = ''
        if not tgt_fen:
            return

        uci = ''
        try:
            uci = str(vucis[cur - 1]) if 0 <= (cur - 1) < len(vucis) else ''
        except Exception:
            uci = ''

        if uci:
            try:
                self.last_move = [uci]
            except Exception:
                self.last_move = []
            if play_sound:
                try:
                    mv = chess.Move.from_uci(str(uci))
                    if cur == 1:
                        fen_before = str(self.analysis_fens[base]) if 0 <= base < len(self.analysis_fens) else ''
                    else:
                        fen_before = str(vfens[cur - 2])
                    if fen_before:
                        self._play_sound_for_move_from_fen(str(fen_before), mv)
                except Exception:
                    pass
        else:
            try:
                self.last_move = []
            except Exception:
                pass

        self._load_fen_into_ui(tgt_fen)
        self.request_eval()
        self.request_review_pv()

    def _analysis_start_pv_variation(self, pv_item: dict, move_index: int) -> None:
        if not self.analysis_active:
            return
        if not isinstance(pv_item, dict):
            return
        base_fen = self._analysis_display_fen()
        if not base_fen:
            return

        uci_list = pv_item.get('uci') if isinstance(pv_item.get('uci'), list) else []
        san_list = pv_item.get('moves') if isinstance(pv_item.get('moves'), list) else []
        fen_list = pv_item.get('fens') if isinstance(pv_item.get('fens'), list) else []
        try:
            uci_list = [str(x) for x in uci_list]
            san_list = [str(x) for x in san_list]
            fen_list = [str(x) for x in fen_list]
        except Exception:
            uci_list, san_list, fen_list = [], [], []
        if not fen_list:
            return

        n_total = min(len(uci_list), len(san_list), len(fen_list))
        if n_total <= 0:
            return

        # If we're already inside a saved variation, append PV moves from the CURRENT displayed
        # position onto that variation (do not overwrite earlier variation moves).
        try:
            in_variation = bool(getattr(self, '_review_analysis_active', False)) and int(getattr(self, '_review_analysis_cursor', 0) or 0) > 0
        except Exception:
            in_variation = False

        if in_variation:
            base = int(getattr(self, '_review_analysis_base_index', int(self.analysis_index)))
            cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)
            n_keep = max(1, min(int(move_index) + 1, int(n_total)))

            # Truncate to current cursor (if user stepped back), then append PV prefix.
            try:
                self._review_variations[base] = list((self._review_variations.get(base, []) or [])[:cur])
            except Exception:
                self._review_variations[base] = []
            try:
                self._review_variation_fens[base] = list((self._review_variation_fens.get(base, []) or [])[:cur])
            except Exception:
                self._review_variation_fens[base] = []
            try:
                self._review_variation_ucis[base] = list((self._review_variation_ucis.get(base, []) or [])[:cur])
            except Exception:
                self._review_variation_ucis[base] = []
            try:
                self._review_variation_labels[base] = list((getattr(self, '_review_variation_labels', {}) or {}).get(base, []) or [])[:cur]
            except Exception:
                self._review_variation_labels[base] = list((getattr(self, '_review_variation_labels', {}) or {}).get(base, []) or [])

            try:
                self._review_variations[base].extend([str(x) for x in san_list[:n_keep]])
            except Exception:
                pass
            try:
                self._review_variation_fens[base].extend([str(x) for x in fen_list[:n_keep]])
            except Exception:
                pass
            try:
                self._review_variation_ucis[base].extend([str(x) for x in uci_list[:n_keep]])
            except Exception:
                pass
            try:
                self._review_variation_labels[base].extend(['' for _ in range(int(n_keep))])
            except Exception:
                pass

            # Move cursor to new end.
            try:
                self._review_analysis_cursor = len(self._review_variation_fens.get(base, []) or [])
            except Exception:
                self._review_analysis_cursor = cur + n_keep
            try:
                tgt_fen = str(self._review_variation_fens.get(base, [])[-1])
            except Exception:
                tgt_fen = ''
            if not tgt_fen:
                return

            last_uci = ''
            try:
                last_uci = str(self._review_variation_ucis.get(base, [])[-1])
                self.last_move = [last_uci]
            except Exception:
                last_uci = ''
                self.last_move = []

            # Play sound for final appended move.
            if last_uci:
                try:
                    mv = chess.Move.from_uci(str(last_uci))
                    vfens2 = list(self._review_variation_fens.get(base, []) or [])
                    if len(vfens2) == 1:
                        fen_before = str(self.analysis_fens[base]) if 0 <= base < len(self.analysis_fens) else ''
                    else:
                        fen_before = str(vfens2[-2])
                    if fen_before:
                        self._play_sound_for_move_from_fen(str(fen_before), mv)
                except Exception:
                    pass

            self._review_analysis_fen = ''
            self._load_fen_into_ui(tgt_fen)
            self.request_eval()
            self.request_review_pv()
            try:
                self._start_review_variation_analysis_thread(int(base))
            except Exception:
                pass
            return

        # Not inside a variation overlay: only branch if the PV deviates from the mainline.
        base = int(self.analysis_index)
        n_keep = max(1, min(int(move_index) + 1, int(n_total)))
        seq = list(uci_list[:n_keep])
        if not seq:
            return
        try:
            self._apply_uci_sequence_respecting_mainline('analysis', int(base), list(seq), default_label='')
        except Exception:
            return

    def _handle_analysis_board_click(self, mouse_pos: tuple[int, int]) -> None:
        if not self.analysis_active:
            return
        if not self.movement_click_enabled:
            return

        target = self._mouse_to_square(mouse_pos)
        if target is None:
            self.selected_square = None
            return
        target_row, target_col = target

        fen = self._analysis_display_fen()
        if not fen:
            self.selected_square = None
            return

        try:
            board = chess.Board(fen)
        except Exception:
            self.selected_square = None
            return

        # In analysis mode we still follow side-to-move, but click-to-move should feel like
        # the main game: you can switch selection by clicking another own piece.
        try:
            clicked_sq = self._chess_square_from_coords(target_row, target_col)
            clicked_piece = board.piece_at(clicked_sq)
        except Exception:
            clicked_piece = None

        if self.selected_square is None:
            try:
                if clicked_piece is None:
                    self.selected_square = None
                    return
                if clicked_piece.color != board.turn:
                    self.selected_square = None
                    return
                self.selected_square = (target_row, target_col)
                return
            except Exception:
                self.selected_square = None
                return

        sel_row, sel_col = self.selected_square
        if (sel_row, sel_col) == (target_row, target_col):
            self.selected_square = None
            return

        # Clicking another own piece switches selection (like the main game).
        try:
            if clicked_piece is not None and clicked_piece.color == board.turn:
                self.selected_square = (target_row, target_col)
                return
        except Exception:
            pass

        try:
            from_sq = self._chess_square_from_coords(sel_row, sel_col)
            to_sq = self._chess_square_from_coords(target_row, target_col)
        except Exception:
            self.selected_square = None
            return

        promo = None
        try:
            p = board.piece_at(from_sq)
            if p is not None and p.piece_type == chess.PAWN:
                to_rank = chess.square_rank(to_sq)
                if (p.color == chess.WHITE and to_rank == 7) or (p.color == chess.BLACK and to_rank == 0):
                    promo = chess.QUEEN
        except Exception:
            promo = None

        mv = chess.Move(from_sq, to_sq, promotion=promo)
        if mv not in board.legal_moves:
            # Keep selection so the user can try another destination.
            return

        # If we're on a previous mainline position and the move matches the next mainline move,
        # do not create a variation; just move forward on the mainline.
        try:
            if (not bool(getattr(self, '_review_analysis_active', False))) and int(self.analysis_index) < int(len(self.analysis_fens) - 1):
                if 0 <= int(self.analysis_index) < len(self.analysis_plies) and str(mv.uci()) == str(self.analysis_plies[int(self.analysis_index)].uci()):
                    self._analysis_set_index(int(self.analysis_index) + 1, play_sound=True)
                    self.selected_square = None
                    return
        except Exception:
            pass

        fen_before = fen
        try:
            san = board.san(mv)
        except Exception:
            san = mv.uci()
        try:
            board.push(mv)
        except Exception:
            self.selected_square = None
            return
        new_fen = board.fen()

        # If we're not on the latest mainline position, or we're inside a variation,
        # treat the move as a user variation (do not truncate the mainline).
        go_to_variation = False
        try:
            go_to_variation = bool(getattr(self, '_review_analysis_active', False)) or (int(self.analysis_index) < int(len(self.analysis_fens) - 1))
        except Exception:
            go_to_variation = bool(getattr(self, '_review_analysis_active', False))

        if go_to_variation:
            # Start a variation anchored at the current mainline ply if not already in one.
            if not bool(getattr(self, '_review_analysis_active', False)):
                try:
                    base = int(self.analysis_index)
                except Exception:
                    base = 0
                self._review_analysis_active = True
                self._review_analysis_base_index = int(base)
                self._review_analysis_cursor = 0
                self._review_analysis_fen = ''
                try:
                    self._review_variations[int(base)] = list(self._review_variations.get(int(base), []) or [])
                    self._review_variation_fens[int(base)] = list(self._review_variation_fens.get(int(base), []) or [])
                    self._review_variation_ucis[int(base)] = list(self._review_variation_ucis.get(int(base), []) or [])
                except Exception:
                    self._review_variations[int(base)] = []
                    self._review_variation_fens[int(base)] = []
                    self._review_variation_ucis[int(base)] = []
                try:
                    self._review_variation_labels[int(base)] = list((getattr(self, '_review_variation_labels', {}) or {}).get(int(base), []) or [])
                except Exception:
                    pass

            base = int(getattr(self, '_review_analysis_base_index', int(self.analysis_index)))
            cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)

            # If user stepped back within variation then makes a move, truncate tail.
            try:
                if cur < len(self._review_variations.get(base, []) or []):
                    self._review_variations[base] = list((self._review_variations.get(base, []) or [])[:cur])
            except Exception:
                pass
            try:
                if cur < len(self._review_variation_fens.get(base, []) or []):
                    self._review_variation_fens[base] = list((self._review_variation_fens.get(base, []) or [])[:cur])
            except Exception:
                pass
            try:
                if cur < len(self._review_variation_ucis.get(base, []) or []):
                    self._review_variation_ucis[base] = list((self._review_variation_ucis.get(base, []) or [])[:cur])
            except Exception:
                pass
            try:
                if cur < len((getattr(self, '_review_variation_labels', {}) or {}).get(base, []) or []):
                    self._review_variation_labels[base] = list((self._review_variation_labels.get(base, []) or [])[:cur])
            except Exception:
                pass

            # Append move to variation.
            try:
                self._review_variations[int(base)].append(str(san))
                self._review_variation_fens[int(base)].append(str(new_fen))
                self._review_variation_ucis[int(base)].append(str(mv.uci()))
            except Exception:
                pass
            try:
                # Default label blank; background thread may classify.
                self._review_variation_labels[int(base)].append('')
            except Exception:
                pass
            try:
                self._review_analysis_cursor = len(self._review_variation_fens.get(int(base), []) or [])
            except Exception:
                pass
            self._review_analysis_fen = str(new_fen)

            # Background classification for analysis variations.
            try:
                self._start_review_variation_analysis_thread(int(base))
            except Exception:
                pass
        else:
            # Extend the mainline.
            try:
                self.analysis_plies.append(mv)
                self.analysis_sans.append(str(san))
                self.analysis_fens.append(str(new_fen))
                self.analysis_index = len(self.analysis_fens) - 1
            except Exception:
                pass

            # Clear overlay variation when extending the mainline.
            self._review_analysis_active = False
            self._review_analysis_base_index = int(self.analysis_index)
            self._review_analysis_fen = ''
            self._review_analysis_cursor = 0

        # UI update
        try:
            self.last_move = [str(mv.uci())]
        except Exception:
            self.last_move = []
        self._load_fen_into_ui(new_fen)
        self.request_eval()
        self.request_review_pv()
        try:
            self._play_sound_for_move_from_fen(fen_before, mv)
        except Exception:
            pass

        # Start (or restart) async annotations for mainline when it changed.
        try:
            if not go_to_variation:
                self._start_analysis_annotation_thread(chess.Board(str(self.analysis_fens[0])), list(self.analysis_plies))
        except Exception:
            pass

        self.selected_square = None

    def _handle_analysis_drag_release(self, mouse_pos: tuple[int, int]) -> None:
        # Proper drag/drop handler (mirrors main-game semantics).
        try:
            self._un_click_left_analysis()
        except Exception:
            # Fall back to click-to-move if anything goes wrong.
            try:
                self.selected_square = None
            except Exception:
                pass
            self._handle_analysis_board_click(mouse_pos)

    def _un_click_left_analysis(self) -> None:
        """Drag-release handler for analysis mode.

        Mirrors the main-game drag behavior (piece clamps to cursor) and records the move
        into the analysis mainline.
        """
        if not self.analysis_active:
            return

        fen_before = self._analysis_display_fen()
        if not fen_before:
            # Ensure we stop dragging.
            for piece in self.all_pieces:
                try:
                    piece.clicked = False
                except Exception:
                    pass
            return

        # Find the clicked piece (set by update_board once drag threshold is exceeded).
        moved_piece = None
        for piece in self.all_pieces:
            try:
                if piece.clicked:
                    moved_piece = piece
                    break
            except Exception:
                continue
        if moved_piece is None:
            return

        # Attempt to apply move using piece.make_move (same as main game).
        row = int(moved_piece.position[0])
        col = int(moved_piece.position[1])
        ok = False
        try:
            ok = bool(moved_piece.make_move(self.board, self.offset, self.turn, self.flipped, None, None))
        except Exception:
            ok = False

        # Compute destination square from current mouse position (same logic as un_click_left).
        try:
            x = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
            y = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
            if self.flipped:
                x = -x + 7
                y = -y + 7
        except Exception:
            x, y = col, row

        # Always stop dragging visuals.
        try:
            moved_piece.clicked = False
        except Exception:
            pass

        if not ok:
            # Re-sync UI to the original position.
            self._load_fen_into_ui(fen_before)
            self.selected_square = None
            return

        # Build UCI (auto-promote to queen like main game)
        uci = translate_move(row, col, y, x)
        try:
            if moved_piece.piece.lower() == 'p' and (y == 0 or y == 7):
                uci += 'q'
        except Exception:
            pass

        # Validate/apply via python-chess to derive SAN and new FEN.
        try:
            b = chess.Board(fen_before)
        except Exception:
            self._load_fen_into_ui(fen_before)
            self.selected_square = None
            return

        try:
            mv = chess.Move.from_uci(str(uci))
            if mv not in b.legal_moves:
                raise ValueError('illegal')
            san = b.san(mv)
            b.push(mv)
        except Exception:
            self._load_fen_into_ui(fen_before)
            self.selected_square = None
            return

        # If we're on a previous mainline position and the move matches the next mainline move,
        # do not create a variation; just move forward on the mainline.
        try:
            if (not bool(getattr(self, '_review_analysis_active', False))) and int(self.analysis_index) < int(len(self.analysis_fens) - 1):
                if 0 <= int(self.analysis_index) < len(self.analysis_plies) and str(mv.uci()) == str(self.analysis_plies[int(self.analysis_index)].uci()):
                    self._analysis_set_index(int(self.analysis_index) + 1, play_sound=True)
                    self.selected_square = None
                    return
        except Exception:
            pass

        new_fen = b.fen()

        go_to_variation = False
        try:
            go_to_variation = bool(getattr(self, '_review_analysis_active', False)) or (int(self.analysis_index) < int(len(self.analysis_fens) - 1))
        except Exception:
            go_to_variation = bool(getattr(self, '_review_analysis_active', False))

        if go_to_variation:
            # Start a variation anchored at the current mainline ply if not already in one.
            if not bool(getattr(self, '_review_analysis_active', False)):
                try:
                    base = int(self.analysis_index)
                except Exception:
                    base = 0
                self._review_analysis_active = True
                self._review_analysis_base_index = int(base)
                self._review_analysis_cursor = 0
                self._review_analysis_fen = ''
                try:
                    if int(base) not in (getattr(self, '_review_variations', {}) or {}):
                        self._review_variations[int(base)] = []
                    if int(base) not in (getattr(self, '_review_variation_fens', {}) or {}):
                        self._review_variation_fens[int(base)] = []
                    if int(base) not in (getattr(self, '_review_variation_ucis', {}) or {}):
                        self._review_variation_ucis[int(base)] = []
                    if int(base) not in (getattr(self, '_review_variation_labels', {}) or {}):
                        self._review_variation_labels[int(base)] = []
                except Exception:
                    pass

            base = int(getattr(self, '_review_analysis_base_index', int(self.analysis_index)))
            cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)

            # Truncate tail if user stepped back inside the variation.
            try:
                if cur < len(self._review_variations.get(base, []) or []):
                    self._review_variations[base] = list((self._review_variations.get(base, []) or [])[:cur])
            except Exception:
                pass
            try:
                if cur < len(self._review_variation_fens.get(base, []) or []):
                    self._review_variation_fens[base] = list((self._review_variation_fens.get(base, []) or [])[:cur])
            except Exception:
                pass
            try:
                if cur < len(self._review_variation_ucis.get(base, []) or []):
                    self._review_variation_ucis[base] = list((self._review_variation_ucis.get(base, []) or [])[:cur])
            except Exception:
                pass
            try:
                if cur < len((getattr(self, '_review_variation_labels', {}) or {}).get(base, []) or []):
                    self._review_variation_labels[base] = list((self._review_variation_labels.get(base, []) or [])[:cur])
            except Exception:
                pass

            try:
                self._review_variations[int(base)].append(str(san))
                self._review_variation_fens[int(base)].append(str(new_fen))
                self._review_variation_ucis[int(base)].append(str(mv.uci()))
            except Exception:
                pass
            try:
                self._review_variation_labels[int(base)].append('')
            except Exception:
                pass
            try:
                self._review_analysis_cursor = len(self._review_variation_fens.get(int(base), []) or [])
            except Exception:
                pass
            self._review_analysis_fen = str(new_fen)

            try:
                self._start_review_variation_analysis_thread(int(base))
            except Exception:
                pass
        else:
            try:
                self.analysis_plies.append(mv)
                self.analysis_sans.append(str(san))
                self.analysis_fens.append(str(new_fen))
                self.analysis_index = len(self.analysis_fens) - 1
            except Exception:
                pass

            # Clear PV/variation overlay when extending the mainline.
            self._review_analysis_active = False
            self._review_analysis_base_index = int(self.analysis_index)
            self._review_analysis_fen = ''
            self._review_analysis_cursor = 0

        # UI update
        try:
            self.last_move = [str(mv.uci())]
        except Exception:
            self.last_move = []
        self._load_fen_into_ui(new_fen)
        self.request_eval()
        self.request_review_pv()
        try:
            self._play_sound_for_move_from_fen(fen_before, mv)
        except Exception:
            pass

        # Start (or restart) async annotations for mainline only when it changed.
        try:
            if not go_to_variation:
                self._start_analysis_annotation_thread(chess.Board(str(self.analysis_fens[0])), list(self.analysis_plies))
        except Exception:
            pass

        self.selected_square = None

    def _start_analysis_annotation_thread(self, start_board: chess.Board, moves: list[chess.Move]) -> None:
        if not self._ensure_review_analysis_engine() or self.stockfish_review_analysis is None:
            self.analysis_progress = None
            return

        try:
            self._analysis_run_id += 1
            run_id = int(self._analysis_run_id)
        except Exception:
            run_id = 0

        def eval_to_cp(e) -> int:
            if not isinstance(e, dict):
                return 0
            if e.get('type') == 'cp':
                try:
                    return int(e.get('value', 0))
                except Exception:
                    return 0
            try:
                mv = int(e.get('value', 0))
            except Exception:
                mv = 0
            return 10000 if mv > 0 else -10000

        def loss_unclamped(cp: int, cap: int = 2000) -> int:
            try:
                return max(0, min(int(cp), int(cap)))
            except Exception:
                return 0

        def classify_move(side_to_move_white: bool, before_eval: int, played_eval: int, best_eval: int, played_uci: str, best_uci: str | None) -> str:
            if side_to_move_white:
                loss = loss_unclamped(best_eval - played_eval)
                gain = played_eval - before_eval
                abs_before = abs(before_eval)
            else:
                loss = loss_unclamped(played_eval - best_eval)
                gain = before_eval - played_eval
                abs_before = abs(before_eval)

            is_best = bool(best_uci and str(played_uci) == str(best_uci))
            if loss <= 15 and gain >= 140 and abs_before <= 150:
                return 'Amazing'
            if loss <= 35 and gain >= 80 and abs_before <= 220:
                return 'Great'
            if is_best:
                return 'Best'
            if loss <= 15:
                return 'Good'
            if loss >= 250:
                return 'Blunder'
            if loss >= 120:
                return 'Mistake'
            return ''

        def worker() -> None:
            try:
                with self._review_analysis_engine_lock:
                    self.stockfish_review_analysis.update_engine_parameters({"UCI_LimitStrength": "false"})
                    self.stockfish_review_analysis.set_skill_level(20)
                    self.stockfish_review_analysis.set_depth(10)
            except Exception:
                pass

            board = start_board.copy(stack=False)
            labels: list[str] = []
            total = max(1, len(moves))

            # Analysis: prefer book detection by move-sequence prefix against the openings database.
            # This catches "book" moves even when the exact EPD/FEN isn't present for the position.
            try:
                opening_names = self._compute_analysis_openings_by_prefix(moves)
            except Exception:
                opening_names = ['' for _ in moves]
            # Fallback: if prefix matching yields nothing (e.g. minimal openings DB), use EPD/FEN matching.
            try:
                if not any(bool(x) for x in (opening_names or [])):
                    opening_names = self._compute_review_openings(start_board, moves)
            except Exception:
                pass

            for i, mv in enumerate(moves):
                try:
                    if (not self.analysis_active) or int(self._analysis_run_id) != int(run_id):
                        return
                except Exception:
                    return

                fen_before = board.fen()
                try:
                    with self._review_analysis_engine_lock:
                        self.stockfish_review_analysis.set_fen_position(fen_before)
                        before_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                        best_uci = self.stockfish_review_analysis.get_best_move_time(80)
                except Exception:
                    before_eval = 0
                    best_uci = None

                played_board = chess.Board(fen_before)
                try:
                    played_board.push(mv)
                except Exception:
                    pass
                try:
                    with self._review_analysis_engine_lock:
                        self.stockfish_review_analysis.set_fen_position(played_board.fen())
                        played_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                except Exception:
                    played_eval = 0

                best_eval = played_eval
                if best_uci:
                    best_board = chess.Board(fen_before)
                    try:
                        best_board.push_uci(str(best_uci))
                    except Exception:
                        pass
                    try:
                        with self._review_analysis_engine_lock:
                            self.stockfish_review_analysis.set_fen_position(best_board.fen())
                            best_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                    except Exception:
                        best_eval = played_eval

                if board.turn == chess.WHITE:
                    labels.append(classify_move(True, before_eval, played_eval, best_eval, str(mv.uci()), str(best_uci) if best_uci else None))
                else:
                    labels.append(classify_move(False, before_eval, played_eval, best_eval, str(mv.uci()), str(best_uci) if best_uci else None))

                try:
                    board.push(mv)
                except Exception:
                    pass
                try:
                    self.analysis_progress = min(1.0, (i + 1) / total)
                except Exception:
                    pass

            # Book moves only when openings DB has an entry (and don't overwrite blunders/mistakes).
            try:
                for i, nm in enumerate(opening_names):
                    if not nm:
                        continue
                    if 0 <= i < len(labels) and labels[i] not in ('Blunder', 'Mistake'):
                        labels[i] = 'Book'
            except Exception:
                pass

            try:
                if int(self._analysis_run_id) == int(run_id) and self.analysis_active:
                    self.analysis_move_labels = list(labels)
                    self.analysis_opening_names = list(opening_names)
                    self.analysis_progress = None
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _draw_analysis_overlay(self) -> None:
        win_w, win_h = self.screen.get_size()

        margin = 14
        x = int(win_w * 0.60) + margin
        y = int(margin)
        w = max(180, int(win_w * 0.40) - (margin * 2))
        h = int(win_h - 2 * margin)
        if w < 220:
            x = margin
            y = margin
            w = max(220, win_w - 2 * margin)
            h = max(140, int(win_h - 2 * margin))

        panel = pg.Rect(int(x), int(y), int(w), int(h))
        bg = pg.Surface((panel.w, panel.h), pg.SRCALPHA)
        bg.fill((0, 0, 0, 160))
        self.screen.blit(bg, (panel.x, panel.y))
        pg.draw.rect(self.screen, (140, 140, 140), panel, width=1, border_radius=6)

        # Split panel into top stats/PV area and bottom move list area.
        # Default top area is ~30% of the screen height, but user can drag the divider.
        try:
            if getattr(self, 'analysis_top_h_px', None) is not None:
                top_h_target = int(self.analysis_top_h_px)
            else:
                top_h_target = int(win_h * 0.35)
        except Exception:
            top_h_target = int(panel.h * 0.35)
        min_top_h = 120
        min_bottom_h = 140
        top_h = max(int(min_top_h), min(int(top_h_target), int(panel.h - min_bottom_h)))
        split_y = int(panel.y + top_h)
        top_rect = pg.Rect(int(panel.x), int(panel.y), int(panel.w), int(top_h))
        bottom_rect = pg.Rect(int(panel.x), int(split_y), int(panel.w), int(panel.bottom - split_y))
        self._analysis_top_rect = top_rect
        try:
            self._analysis_splitter_rect = pg.Rect(int(panel.x), int(split_y - 5), int(panel.w), 10)
        except Exception:
            self._analysis_splitter_rect = None
        try:
            pg.draw.line(self.screen, (140, 140, 140), (panel.x, split_y), (panel.right, split_y), width=1)
        except Exception:
            pass

        # More-visible draggable splitter handle (center grip).
        try:
            grip_w = 44
            grip_h = 14
            gx = int(panel.centerx - grip_w // 2)
            gy = int(split_y - grip_h // 2)
            grip_rect = pg.Rect(gx, gy, grip_w, grip_h)
            pg.draw.rect(self.screen, (25, 25, 25), grip_rect, border_radius=7)
            pg.draw.rect(self.screen, (110, 110, 110), grip_rect, width=1, border_radius=7)
            cx = int(grip_rect.centerx)
            for k in (-3, 0, 3):
                yk = int(grip_rect.centery + k)
                pg.draw.line(self.screen, (200, 200, 200), (cx - 12, yk), (cx + 12, yk), width=2)
        except Exception:
            pass

        # Clip top section so stats/PV never render over the move list.
        prev_clip = None
        try:
            prev_clip = self.screen.get_clip()
            self.screen.set_clip(top_rect)
        except Exception:
            prev_clip = None

        try:
            top_scroll = max(0, int(getattr(self, 'analysis_top_scroll', 0)))
        except Exception:
            top_scroll = 0

        pad = 10
        line_h = self.eval_font.get_linesize() + 2
        ply = max(0, int(self.analysis_index))
        total = max(0, len(self.analysis_fens) - 1)
        lines: list[str] = [
            f"Analysis: {self.analysis_name or 'Analysis'}",
            f"Ply: {ply}/{total} (Left/Right, Ctrl+S saves, ESC exits)",
        ]

        # Show opening name when the current mainline move is a book move.
        # Keep it visible while the temporary opening variation is active so it can be clicked again to close.
        try:
            nm = ''
            if bool(getattr(self, '_opening_variation_active', False)):
                nm = str(getattr(self, '_opening_variation_name', '') or '')
            elif bool(getattr(self, '_review_analysis_active', False)):
                try:
                    base = int(getattr(self, '_review_analysis_base_index', -1))
                    cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)
                    vucis = list((getattr(self, '_review_variation_ucis', {}) or {}).get(base, []) or [])
                    main_prefix = [str(m.uci()) for m in (self.analysis_plies[: max(0, base)] if self.analysis_plies else [])]
                    combined = list(main_prefix) + [str(x) for x in vucis[: max(0, cur)]]
                    nm = str(self._opening_name_for_uci_prefix(combined) or '')
                except Exception:
                    nm = ''
            elif (not self._review_analysis_active) and int(self.analysis_index) > 0:
                mi = int(self.analysis_index) - 1
                if 0 <= mi < len(self.analysis_move_labels) and str(self.analysis_move_labels[mi]) == 'Book':
                    try:
                        nm = str(self.analysis_opening_names[mi]) if 0 <= mi < len(self.analysis_opening_names) else ''
                    except Exception:
                        nm = ''
            if nm:
                lines.append(f"Opening: {nm}")
        except Exception:
            pass

        if self.analysis_last_saved_path:
            try:
                lines.append(f"Saved: {os.path.basename(self.analysis_last_saved_path)}")
            except Exception:
                pass
        if self.analysis_progress is not None:
            try:
                pct = int(float(self.analysis_progress) * 100)
                lines.append(f"Annotating... {pct}%")
            except Exception:
                pass
        if self.analysis_last_error:
            lines.append(str(self.analysis_last_error))

        # Render top section (stats + PV) inside top_rect.
        self._analysis_opening_rect = None
        self._analysis_opening_suggestion_hitboxes = []
        self._analysis_opening_suggest_more_rect = None
        xx = top_rect.x + pad
        yy = top_rect.y + pad - int(top_scroll)
        for t in lines:
            if str(t).startswith('Opening:'):
                # Draw as a button so it's obviously clickable.
                try:
                    ts = str(t)
                    surf = self.eval_font.render(ts, False, (220, 220, 220))
                    bx_pad = 10
                    by_pad = 5
                    bw = int(surf.get_width() + 2 * bx_pad)
                    bh = int(surf.get_height() + 2 * by_pad)
                    btn = pg.Rect(int(xx), int(yy), int(bw), int(bh))
                    self._analysis_opening_rect = btn
                    pg.draw.rect(self.screen, (25, 25, 25), btn, border_radius=10)
                    border_col = (200, 200, 200) if bool(getattr(self, '_opening_variation_active', False)) else (110, 110, 110)
                    pg.draw.rect(self.screen, border_col, btn, width=1, border_radius=10)
                    self.screen.blit(surf, (int(btn.x + bx_pad), int(btn.y + by_pad)))
                    yy += int(max(line_h, btn.h))
                except Exception:
                    surf = self.eval_font.render(str(t), False, (255, 255, 255))
                    self.screen.blit(surf, (xx, yy))
                    yy += line_h

                # Suggestions: other openings sharing the current move prefix with longer lines.
                try:
                    opening_nm = ''
                    try:
                        opening_nm = str(str(t).split(':', 1)[1]).strip()
                    except Exception:
                        opening_nm = ''
                    prefix_ucis: list[str] = []
                    try:
                        if bool(getattr(self, '_review_analysis_active', False)):
                            base = int(getattr(self, '_review_analysis_base_index', -1))
                            cur = int(getattr(self, '_review_analysis_cursor', 0) or 0)
                            vucis = list((getattr(self, '_review_variation_ucis', {}) or {}).get(base, []) or [])
                            main_prefix = [str(m.uci()) for m in (self.analysis_plies[: max(0, base)] if self.analysis_plies else [])]
                            prefix_ucis = list(main_prefix) + [str(x) for x in vucis[: max(0, cur)]]
                        else:
                            prefix_ucis = [str(m.uci()) for m in (self.analysis_plies[: int(self.analysis_index)] if self.analysis_plies else [])]
                    except Exception:
                        prefix_ucis = []

                    all_suggestions = self._opening_suggestions_for_prefix(prefix_ucis, limit=25, exclude_name=opening_nm)
                    n_sug = len(all_suggestions)
                    if n_sug > 3:
                        try:
                            key = (tuple(prefix_ucis), str(opening_nm or '').strip().lower())
                        except Exception:
                            key = (tuple(), '')
                        try:
                            if key != (getattr(self, '_analysis_opening_suggest_key', (tuple(), '')) or (tuple(), '')):
                                self._analysis_opening_suggest_key = key
                                self._analysis_opening_suggest_offset = 0
                        except Exception:
                            self._analysis_opening_suggest_key = key
                            self._analysis_opening_suggest_offset = 0

                        try:
                            lab = self.eval_font.render('See more', False, (200, 200, 200))
                            sbx = 8
                            sby = 4
                            orect = getattr(self, '_analysis_opening_rect', None)
                            if orect is None:
                                bx = int(xx + 12)
                                by = int(yy + 2)
                            else:
                                bx = int(orect.right + 8)
                                by = int(orect.y)
                            brect = pg.Rect(int(bx), int(by), int(lab.get_width() + 2 * sbx), int(lab.get_height() + 2 * sby))

                            # Clamp into the top panel; if it would overlap, fall back below.
                            try:
                                max_x = int(top_rect.right - pad - brect.w)
                                brect.x = int(min(int(brect.x), int(max_x)))
                            except Exception:
                                pass
                            if orect is not None and brect.x < int(orect.right + 4):
                                brect.x = int(xx + 12)
                                brect.y = int(yy + 2)

                            self._analysis_opening_suggest_more_rect = brect
                            pg.draw.rect(self.screen, (25, 25, 25), brect, border_radius=9)
                            pg.draw.rect(self.screen, (110, 110, 110), brect, width=1, border_radius=9)
                            self.screen.blit(lab, (int(brect.x + sbx), int(brect.y + sby)))
                            if orect is None or brect.y >= int(yy):
                                yy = int(brect.bottom + 2)
                        except Exception:
                            self._analysis_opening_suggest_more_rect = None

                        try:
                            off = int(getattr(self, '_analysis_opening_suggest_offset', 0) or 0)
                        except Exception:
                            off = 0
                        off = int(off) % int(n_sug)
                        suggestions = [all_suggestions[(off + i) % n_sug] for i in range(3)]
                    else:
                        suggestions = list(all_suggestions)

                    for sidx, (snm, suci_line, _slen) in enumerate(suggestions):
                        preview = self._opening_next_moves_preview(str(suci_line), prefix_len=len(prefix_ucis), max_moves=2)
                        try:
                            if n_sug > 0 and n_sug > 3:
                                num = int(((off + sidx) % n_sug) + 1)
                            else:
                                num = int(sidx + 1)
                        except Exception:
                            num = int(sidx + 1)
                        label = f"{num}. {snm}"
                        if preview:
                            label = f"{label}  (next: {preview})"

                        ss = self.eval_font.render(str(label), False, (200, 200, 200))
                        sx = int(xx + 12)
                        sy = int(yy + 2)
                        sbx = 8
                        sby = 4
                        srect = pg.Rect(int(sx), int(sy), int(ss.get_width() + 2 * sbx), int(ss.get_height() + 2 * sby))
                        pg.draw.rect(self.screen, (25, 25, 25), srect, border_radius=9)
                        pg.draw.rect(self.screen, (110, 110, 110), srect, width=1, border_radius=9)
                        self.screen.blit(ss, (int(srect.x + sbx), int(srect.y + sby)))
                        self._analysis_opening_suggestion_hitboxes.append((srect, str(snm), str(suci_line)))
                        yy = int(srect.bottom + 2)
                except Exception:
                    pass
                continue

            surf = self.eval_font.render(str(t), False, (255, 255, 255))
            self.screen.blit(surf, (xx, yy))
            yy += line_h

        # PV lines (reuse the same published PV data; analysis and review are mutually exclusive).
        yy += 6
        pv_title = self.eval_font.render('Top lines:', False, (255, 255, 255))
        self.screen.blit(pv_title, (xx, yy))
        # Depth indicator (last completed or in-flight if pending).
        try:
            fen_for_depth = str(self._analysis_display_fen())
        except Exception:
            fen_for_depth = ''
        try:
            d_done = int((getattr(self, '_pv_depth_by_fen', {}) or {}).get(fen_for_depth, 0) or 0)
        except Exception:
            d_done = 0
        try:
            d_inflight = int((getattr(self, '_pv_inflight_depth_by_fen', {}) or {}).get(fen_for_depth, 0) or 0)
        except Exception:
            d_inflight = 0
        try:
            depth_to_show = int(d_inflight if (d_inflight > 0 and bool(getattr(self, 'review_pv_pending', False))) else d_done)
        except Exception:
            depth_to_show = int(d_done)
        if int(depth_to_show) > 0:
            try:
                ds = self.eval_font.render(f"d{int(depth_to_show)}", False, (160, 180, 200))
                self.screen.blit(ds, (int(xx + pv_title.get_width() + 10), int(yy)))
            except Exception:
                pass
        yy += line_h

        self._analysis_pv_hitboxes = []
        pv_lines: list[dict] = []
        pv_pending = False
        try:
            if self._review_pv_lock.acquire(False):
                try:
                    pv_lines = list(self.review_pv_lines or [])
                    pv_pending = bool(self.review_pv_pending)
                    self._review_pv_render_cache = list(pv_lines)
                    self._review_pv_render_pending = bool(pv_pending)
                finally:
                    self._review_pv_lock.release()
            else:
                pv_lines = list(getattr(self, '_review_pv_render_cache', []) or [])
                pv_pending = bool(getattr(self, '_review_pv_render_pending', False))
        except Exception:
            pv_lines = list(getattr(self, '_review_pv_render_cache', []) or [])
            pv_pending = bool(getattr(self, '_review_pv_render_pending', False))

        # If PV hasn't started yet (common at ply 0), kick it once.
        try:
            if (not pv_lines) and (not pv_pending):
                self.request_review_pv()
                pv_pending = True
        except Exception:
            pass

        if not pv_lines and pv_pending:
            pv_lines = [{"rank": 0, "eval": {"type": "cp", "value": 0}, "moves": ["Analyzing..."], "fens": ['']}]
        if not pv_lines:
            pv_lines = [{"rank": 0, "eval": {"type": "cp", "value": 0}, "moves": ["(click board to analyze)"], "fens": ['']}]

        max_x = top_rect.right - pad
        token_gap = 10
        token_gap_small = 6

        def draw_tokens_row(x0: int, y0: int, tokens: list[tuple[str, tuple[int, int, int], tuple[dict, int] | None]], max_x_: int) -> int:
            x = int(x0)
            y2 = int(y0)
            row_h2 = self.eval_font.get_linesize() + 4
            for txt, col, meta in tokens:
                try:
                    surf2 = self.eval_font.render(str(txt), False, col)
                except Exception:
                    continue
                if x + surf2.get_width() > max_x_ and x != int(x0):
                    x = int(x0)
                    y2 += row_h2
                rect = pg.Rect(x - 2, y2 - 1, surf2.get_width() + 4, row_h2)
                self.screen.blit(surf2, (x, y2))
                if meta:
                    try:
                        pv_item, mv_i = meta
                        self._analysis_pv_hitboxes.append((rect, pv_item, int(mv_i)))
                    except Exception:
                        pass
                x += surf2.get_width() + token_gap
            return y2 + row_h2

        for item in pv_lines[:3]:
            if not isinstance(item, dict):
                continue
            rank = int(item.get('rank', 0) or 0)
            eval_d = item.get('eval') if isinstance(item.get('eval'), dict) else {"type": "cp", "value": 0}
            moves = item.get('moves') if isinstance(item.get('moves'), list) else []
            fens = item.get('fens') if isinstance(item.get('fens'), list) else []

            x0 = int(xx)
            y_line = int(yy)
            if rank > 0:
                pfx = self.eval_font.render(f"{rank}.", False, (200, 220, 255))
                self.screen.blit(pfx, (x0, y_line))
                x0 += pfx.get_width() + token_gap_small

            score_txt = self._format_eval_short(eval_d)
            side = self._eval_side_for_rect(eval_d)
            if side == 'w':
                box_bg, box_fg, box_bd = (230, 230, 230), (0, 0, 0), (180, 180, 180)
            elif side == 'b':
                box_bg, box_fg, box_bd = (25, 25, 25), (245, 245, 245), (110, 110, 110)
            else:
                box_bg, box_fg, box_bd = (140, 140, 140), (0, 0, 0), (180, 180, 180)

            score_surf = self.eval_font.render(str(score_txt), False, box_fg)
            box_pad_x = 8
            box_pad_y = 4
            box_rect = pg.Rect(x0, y_line - 1, score_surf.get_width() + box_pad_x * 2, score_surf.get_height() + box_pad_y)
            try:
                pg.draw.rect(self.screen, box_bg, box_rect, border_radius=6)
                pg.draw.rect(self.screen, box_bd, box_rect, width=1, border_radius=6)
            except Exception:
                pass
            self.screen.blit(score_surf, (box_rect.x + box_pad_x, box_rect.y + (box_rect.h - score_surf.get_height()) // 2))
            x0 = box_rect.right + token_gap

            tokens: list[tuple[str, tuple[int, int, int], tuple[dict, int] | None]] = []
            for j, san in enumerate(moves):
                fen_to = ''
                try:
                    fen_to = str(fens[j]) if 0 <= j < len(fens) else ''
                except Exception:
                    fen_to = ''
                tokens.append((str(san), (220, 220, 220), (item, int(j)) if fen_to else None))

            if tokens:
                yy = int(draw_tokens_row(x0, y_line, tokens, int(max_x)))
            else:
                yy += line_h
            yy += 2

        if pv_pending:
            try:
                status = self.eval_font.render('(analyzing...)', False, (160, 180, 200))
                self.screen.blit(status, (xx, yy))
                yy += line_h
            except Exception:
                pass

        # Clamp top scroll based on total content height.
        try:
            content_h = int((yy + int(top_scroll)) - int(top_rect.y) + int(pad))
            max_scroll = max(0, int(content_h - top_rect.h))
            try:
                self.analysis_top_scroll = max(0, min(int(top_scroll), int(max_scroll)))
            except Exception:
                self.analysis_top_scroll = 0
        except Exception:
            pass

        # Restore clip before drawing the move list section.
        try:
            if prev_clip is not None:
                self.screen.set_clip(prev_clip)
            else:
                self.screen.set_clip(None)
        except Exception:
            pass

        # Render move list inside bottom_rect (separate from PV/stats).
        xx = bottom_rect.x + pad
        yy = bottom_rect.y + pad
        title = self.eval_font.render('Moves:', False, (255, 255, 255))
        self.screen.blit(title, (xx, yy))
        yy += line_h

        list_top = yy
        # Reserve footer space for prev/next arrows.
        footer_h = max(28, int(self.eval_font.get_linesize() + 6))
        footer_y = int(bottom_rect.bottom - pad - footer_h)
        list_bottom = int(footer_y - 6)
        row_h = self.eval_font.get_linesize() + 6

        # Footer nav buttons hitboxes (used by click handler).
        try:
            self._analysis_movelist_prev_rect = None
            self._analysis_movelist_next_rect = None
        except Exception:
            pass

        sans = self.analysis_sans or []
        labels = self.analysis_move_labels or []

        def decorate_san(i: int, san: str) -> tuple[str, tuple[int, int, int]]:
            tag = labels[i] if 0 <= i < len(labels) else ''
            if tag == 'Best':
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
        visible_rows = max(1, int((list_bottom - list_top) // row_h))
        max_scroll = max(0, fullmove_count - visible_rows)
        self.analysis_move_scroll = max(0, min(self.analysis_move_scroll, max_scroll))

        col2 = bottom_rect.x + (bottom_rect.w // 2)
        move_idx_at_pos = self.analysis_index - 1
        self._analysis_move_hitboxes = []
        self._analysis_variation_hitboxes = []

        self._analysis_variation_delete_hitboxes = []

        var_base = -1
        var_cursor_active = 0
        var_sans: list[str] = []
        var_fens: list[str] = []
        var_labels: list[str] = []
        try:
            if bool(getattr(self, '_review_analysis_active', False)):
                var_base = int(self._review_analysis_base_index)
                var_cursor_active = int(getattr(self, '_review_analysis_cursor', 0) or 0)
            else:
                var_base = int(self.analysis_index)
                var_cursor_active = 0
            var_sans = list((getattr(self, '_review_variations', {}) or {}).get(var_base, []) or [])
            var_fens = list((getattr(self, '_review_variation_fens', {}) or {}).get(var_base, []) or [])
            var_labels = list((getattr(self, '_review_variation_labels', {}) or {}).get(var_base, []) or [])
        except Exception:
            var_base = -1
            var_cursor_active = 0
            var_sans = []
            var_fens = []
            var_labels = []

        def decorate_var_san(j: int, san: str) -> tuple[str, tuple[int, int, int]]:
            tag = var_labels[j] if 0 <= j < len(var_labels) else ''
            if tag == 'Best':
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
            return san, (220, 220, 220)

        try:
            anchor_ply = 1 if var_base <= 0 else int(var_base)
        except Exception:
            anchor_ply = 1

        y_cursor = int(list_top)
        fm = 1 + int(self.analysis_move_scroll)
        while fm <= fullmove_count and (y_cursor + row_h) <= int(list_bottom):
            w_i = 2 * (fm - 1)
            b_i = w_i + 1
            w_san = sans[w_i] if w_i < len(sans) else ''
            b_san = sans[b_i] if b_i < len(sans) else ''

            y_row = int(y_cursor)
            num = self.eval_font.render(f"{fm}.", False, (200, 200, 200))
            self.screen.blit(num, (xx, y_row))

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
                self._analysis_move_hitboxes.append((w_rect, w_i + 1))

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
                self._analysis_move_hitboxes.append((b_rect, b_i + 1))

            y_cursor += int(row_h)

            try:
                if var_base >= 0 and var_sans and (int(anchor_ply) in (w_i + 1, b_i + 1)) and (y_cursor < int(list_bottom)):
                    prefix = self.eval_font.render('Var:', False, (180, 180, 180))
                    x0 = int(xx + 14)
                    y0 = int(y_cursor)
                    self.screen.blit(prefix, (x0, y0))
                    x2 = int(x0 + prefix.get_width() + 10)
                    y2 = int(y0)
                    max_x2 = int(bottom_rect.right - pad)
                    row_h2 = self.eval_font.get_linesize() + 4

                    # Small delete button next to the variation.
                    try:
                        del_surf = self.eval_font.render('x', False, (255, 140, 140))
                        del_rect = pg.Rect(int(x2), int(y2 - 1), int(del_surf.get_width() + 10), int(row_h2))
                        pg.draw.rect(self.screen, (25, 25, 25), del_rect, border_radius=6)
                        pg.draw.rect(self.screen, (110, 110, 110), del_rect, width=1, border_radius=6)
                        self.screen.blit(del_surf, (del_rect.x + 5, del_rect.y + (del_rect.h - del_surf.get_height()) // 2))
                        self._analysis_variation_delete_hitboxes.append((del_rect, int(var_base)))
                        x2 = int(del_rect.right + 10)
                    except Exception:
                        x2 = int(x0 + prefix.get_width() + 10)

                    for j, san in enumerate(var_sans):
                        try:
                            txt2, col2b = decorate_var_san(int(j), str(san))
                            surf2 = self.eval_font.render(str(txt2), False, col2b)
                        except Exception:
                            continue
                        if x2 + surf2.get_width() > max_x2 and x2 != int(x0):
                            x2 = int(x0)
                            y2 += int(row_h2)
                            if y2 + int(row_h2) > int(list_bottom):
                                break
                        rect2 = pg.Rect(x2 - 2, y2 - 1, surf2.get_width() + 4, row_h2)

                        # Highlight current variation cursor when actively viewing it.
                        try:
                            if bool(getattr(self, '_review_analysis_active', False)) and int(var_cursor_active) == int(j + 1):
                                hi = pg.Surface((rect2.w, rect2.h), pg.SRCALPHA)
                                hi.fill((120, 200, 255, 55))
                                self.screen.blit(hi, (rect2.x, rect2.y))
                        except Exception:
                            pass

                        self.screen.blit(surf2, (x2, y2))
                        try:
                            fen_to = str(var_fens[j]) if 0 <= j < len(var_fens) else ''
                        except Exception:
                            fen_to = ''
                        if fen_to:
                            self._analysis_variation_hitboxes.append((rect2, int(var_base), int(j + 1)))
                        x2 += surf2.get_width() + 10
                    y_cursor = int(y2 + row_h2)
            except Exception:
                pass

            fm += 1

        # Footer nav buttons (◀ / ▶) at bottom of move list.
        try:
            btn_w = max(40, int(footer_h * 1.35))
            btn_h = int(footer_h)
            gap_btn = 12
            total_w = int(btn_w * 2 + gap_btn)
            bx = int(bottom_rect.centerx - total_w // 2)
            by = int(footer_y)
            prev_rect = pg.Rect(int(bx), int(by), int(btn_w), int(btn_h))
            next_rect = pg.Rect(int(bx + btn_w + gap_btn), int(by), int(btn_w), int(btn_h))
            self._analysis_movelist_prev_rect = prev_rect
            self._analysis_movelist_next_rect = next_rect

            try:
                pg.draw.line(self.screen, (140, 140, 140), (bottom_rect.x + pad, footer_y - 3), (bottom_rect.right - pad, footer_y - 3), width=1)
            except Exception:
                pass

            def draw_arrow_btn(r: pg.Rect, direction: str) -> None:
                pg.draw.rect(self.screen, (25, 25, 25), r, border_radius=8)
                pg.draw.rect(self.screen, (110, 110, 110), r, width=1, border_radius=8)
                cx, cy = r.centerx, r.centery
                sx = max(8, int(r.w * 0.18))
                sy = max(8, int(r.h * 0.28))
                if direction == 'left':
                    pts = [(cx + sx, cy - sy), (cx + sx, cy + sy), (cx - sx, cy)]
                else:
                    pts = [(cx - sx, cy - sy), (cx - sx, cy + sy), (cx + sx, cy)]
                pg.draw.polygon(self.screen, (220, 220, 220), pts)

            draw_arrow_btn(prev_rect, 'left')
            draw_arrow_btn(next_rect, 'right')
        except Exception:
            self._analysis_movelist_prev_rect = None
            self._analysis_movelist_next_rect = None

        self._analysis_panel_rect = panel

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
                # Prefer the bracketed continuation.
                cls._pgn_collect_san_prefer_variations(item, out)
                # CRITICAL: do not also keep consuming tokens after the variation at this
                # nesting level. Mixing both lines often makes later SAN illegal (e.g. Nfxd4
                # being applied to the wrong position).
                return

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

        # Switching modes: ensure analysis is fully deactivated so overlays don't stack.
        try:
            if getattr(self, 'analysis_active', False):
                self.exit_analysis(return_to_start_menu=False)
        except Exception:
            pass

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
        # Pre-fill labels so book moves can appear immediately.
        self.review_move_labels = ['' for _ in moves]
        try:
            self.review_opening_names = self._compute_analysis_openings_by_prefix(moves)
            if not any(bool(x) for x in (self.review_opening_names or [])):
                self.review_opening_names = self._compute_review_openings(start_board, moves)
        except Exception:
            self.review_opening_names = ['' for _ in moves]
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

        # Reset per-review depth selector to the configured default.
        try:
            self.review_analysis_depth = int(getattr(self, 'review_analysis_depth_default', getattr(self, 'review_analysis_depth', 10)))
        except Exception:
            self.review_analysis_depth = 10

        # Reset interactive analysis state.
        self._review_analysis_active = False
        self._review_analysis_base_index = 0
        self._review_analysis_fen = ''
        self._review_variations = {}
        self._review_variation_fens = {}
        self._review_variation_ucis = {}
        self._review_analysis_cursor = 0
        try:
            with self._review_pv_lock:
                self.review_pv_lines = []
                self.review_pv_pending = False
        except Exception:
            pass

        self.last_move = []
        self._ensure_layout(force=True)

        self._load_fen_into_ui(self.review_fens[self.review_index])
        if self.review_show_best_move:
            self._request_review_best()
        self.request_review_pv()

        # Load cached analysis if present; otherwise run analysis.
        if not self._load_review_analysis_cache(pgn_path, moves):
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

    def exit_review(self, return_to_start_menu: bool = True) -> None:
        # Cancel any in-flight background review analysis.
        try:
            self._review_analysis_run_id += 1
        except Exception:
            pass
        self.review_active = False
        self.review_pgn_path = None
        self.review_name = ''
        self.review_fens = []
        self.review_plies = []
        self.review_sans = []
        self.review_move_labels = []
        self.review_opening_names = []
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
        self._review_analysis_active = False
        self._review_analysis_base_index = 0
        self._review_analysis_fen = ''
        self._review_analysis_cursor = 0
        self._review_variations = {}
        self._review_variation_fens = {}
        self._review_variation_ucis = {}
        try:
            with self._review_pv_lock:
                self.review_pv_lines = []
                self.review_pv_pending = False
        except Exception:
            pass
        if return_to_start_menu:
            self._restore_normal_layout()
            self.reset_game()

    def review_step(self, delta: int) -> None:
        if not self.review_active or not self.review_fens:
            return
        d = int(delta)
        if d == 0:
            return

        # If we're inside an analysis line (PV/variation), step through it first.
        try:
            if self._review_analysis_active:
                base = int(self._review_analysis_base_index)
                cur = int(self._review_analysis_cursor)
                if bool(getattr(self, '_review_pv_variation_active', False)) and int(getattr(self, '_review_pv_variation_base_index', -9999)) == base:
                    vfens = list(getattr(self, '_review_pv_variation_fens', []) or [])
                    vuci = list(getattr(self, '_review_pv_variation_ucis', []) or [])
                else:
                    vfens = self._review_variation_fens.get(base, [])
                    vuci = self._review_variation_ucis.get(base, [])

                # When we're back at the base position (cursor==0), treat arrow keys as mainline navigation.
                if cur <= 0:
                    self._review_analysis_active = False
                    self._review_analysis_fen = ''
                    try:
                        self._review_pv_variation_active = False
                    except Exception:
                        pass
                    # Fall through to mainline navigation.
                else:
                    # At the last move of the variation, Right should do nothing.
                    if d > 0 and cur >= len(vfens):
                        return

                # Forward within variation.
                if d > 0 and cur < len(vfens):
                    new_cur = cur + 1
                    tgt_fen = str(vfens[new_cur - 1])
                    uci = str(vuci[new_cur - 1]) if 0 <= (new_cur - 1) < len(vuci) else ''
                    fen_before = ''
                    if new_cur == 1:
                        fen_before = str(self.review_fens[base]) if 0 <= base < len(self.review_fens) else self._review_display_fen()
                    else:
                        fen_before = str(vfens[new_cur - 2])

                    if uci:
                        try:
                            self.last_move = [uci]
                        except Exception:
                            self.last_move = []
                        try:
                            self._play_sound_for_move_from_fen(str(fen_before), chess.Move.from_uci(str(uci)))
                        except Exception:
                            pass

                    self._review_analysis_cursor = int(new_cur)
                    self._review_analysis_fen = ''
                    self._load_fen_into_ui(tgt_fen)
                    self.request_eval()
                    self.request_review_pv()
                    self.review_best_move_uci = ''
                    self.review_arrow = None
                    return

                # Backward within variation.
                if d < 0 and cur > 0:
                    new_cur = cur - 1
                    self._review_analysis_cursor = int(new_cur)
                    self._review_analysis_fen = ''

                    if new_cur == 0:
                        # Back to base mainline position.
                        try:
                            self.last_move = [] if base <= 0 else [str(self.review_plies[base - 1].uci())]
                        except Exception:
                            self.last_move = []
                        try:
                            self._play_review_navigation_sound(cur, cur - 1)
                        except Exception:
                            pass
                        self._load_fen_into_ui(str(self.review_fens[base]))
                        # Exit variation mode at base (keep the saved line in the move list).
                        self._review_analysis_active = False
                        try:
                            self._review_pv_variation_active = False
                        except Exception:
                            pass
                    else:
                        tgt_fen = str(vfens[new_cur - 1])
                        uci = str(vuci[new_cur - 1]) if 0 <= (new_cur - 1) < len(vuci) else ''
                        try:
                            self.last_move = [uci] if uci else []
                        except Exception:
                            self.last_move = []
                        try:
                            # Back navigation uses the generic move sound.
                            self._play_review_navigation_sound(cur, cur - 1)
                        except Exception:
                            pass
                        self._load_fen_into_ui(tgt_fen)

                    self.request_eval()
                    self.request_review_pv()
                    self.review_best_move_uci = ''
                    self.review_arrow = None
                    return
        except Exception:
            pass

        new_idx = self.review_index + d
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
        if not self.review_active or not self.review_fens or not self.review_show_best_move:
            return
        if not self._ensure_review_engine():
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

    def _start_review_analysis_thread(self, start_board: chess.Board, moves: list[chess.Move], depth: int | None = None) -> None:
        # Fire-and-forget analysis thread to compute ACPL for both sides.
        if not self._ensure_review_analysis_engine() or self.stockfish_review_analysis is None:
            self.review_analysis_progress = None
            return

        # Cancel any previous analysis.
        try:
            self._review_analysis_run_id += 1
            run_id = int(self._review_analysis_run_id)
        except Exception:
            run_id = 0

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

            is_best = bool(best_uci and str(played_uci) == str(best_uci))

            # Great / Amazing: close to best and creates a notable swing from a roughly-equal position.
            if loss <= 15 and gain >= 140 and abs_before <= 150:
                return 'Amazing'
            if loss <= 35 and gain >= 80 and abs_before <= 220:
                return 'Great'

            # Best move: played move matches engine best move from this position.
            if is_best:
                return 'Best'

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
                with self._review_analysis_engine_lock:
                    self.stockfish_review_analysis.update_engine_parameters({"UCI_LimitStrength": "false"})
            except Exception:
                pass
            try:
                with self._review_analysis_engine_lock:
                    self.stockfish_review_analysis.set_skill_level(20)
                    # Default is lightweight so review stays responsive; can be increased via UI.
                    try:
                        d = int(depth) if depth is not None else int(getattr(self, 'review_analysis_depth', 10))
                    except Exception:
                        d = 10
                    d = max(6, min(20, int(d)))
                    self.stockfish_review_analysis.set_depth(int(d))
            except Exception:
                pass

            board = start_board.copy(stack=False)
            white_losses: list[int] = []
            black_losses: list[int] = []
            move_labels: list[str] = []
            total = max(1, len(moves))

            # Compute opening names per ply once (fast) so we can mark real book moves.
            # Prefer move-sequence prefix matching to catch book moves even without exact EPD/FEN matches.
            try:
                opening_names = self._compute_analysis_openings_by_prefix(moves)
                if not any(bool(x) for x in (opening_names or [])):
                    opening_names = self._compute_review_openings(start_board, moves)
            except Exception:
                opening_names = ['' for _ in moves]

            for i, mv in enumerate(moves):
                # Stop if review mode ended or a newer analysis started.
                try:
                    if (not self.review_active) or int(self._review_analysis_run_id) != int(run_id):
                        return
                except Exception:
                    if not self.review_active:
                        return
                    return
                fen_before = board.fen()
                try:
                    with self._review_analysis_engine_lock:
                        self.stockfish_review_analysis.set_fen_position(fen_before)
                        try:
                            before_eval = eval_to_cp(self.stockfish_review_analysis.get_evaluation())
                        except Exception:
                            before_eval = 0
                        best_uci = self.stockfish_review_analysis.get_best_move_time(80)
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
                    with self._review_analysis_engine_lock:
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
                        with self._review_analysis_engine_lock:
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

            # Mark Book moves only when the openings database has an entry.
            try:
                for i, nm in enumerate(opening_names):
                    if not nm:
                        continue
                    if 0 <= i < len(move_labels):
                        if move_labels[i] not in ('Blunder', 'Mistake'):
                            move_labels[i] = 'Book'
            except Exception:
                pass

            # Publish opening names so the UI can show the opening title.
            try:
                self.review_opening_names = list(opening_names)
            except Exception:
                pass

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

            # Persist for next time.
            try:
                if self.review_active and self.review_pgn_path:
                    # Only save if we're still the latest run.
                    if int(self._review_analysis_run_id) == int(run_id):
                        self._save_review_analysis_cache(str(self.review_pgn_path), moves)
            except Exception:
                pass

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def draw_pieces(self, piece_selected: Piece = None):
        """
        Draws all the pieces and the selected piece last so that it appears on top.
        Also draws the arrows.
        :param piece_selected:
        :return:
        """
        # Build a virtual occupancy map after applying the queued premoves.
        # This lets us (a) draw premoved pieces at their final virtual squares and
        # (b) temporarily hide any piece that a premove lands on (including own pieces).
        premove_virtual: dict[Piece, tuple[int, int]] = {}
        hidden_by_premove: set[Piece] = set()
        try:
            pos_by_piece: dict[Piece, tuple[int, int]] = {}
            sq_to_piece: dict[tuple[int, int], Piece] = {}
            queued_pieces: set[Piece] = set()

            def castle_rook_squares(frm: tuple[int, int], to: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]] | None:
                try:
                    fr, fc = int(frm[0]), int(frm[1])
                    tr, tc = int(to[0]), int(to[1])
                except Exception:
                    return None
                if fr != tr:
                    return None
                if fc != 4:
                    return None
                if abs(tc - fc) != 2:
                    return None
                if tc == 6:
                    return (fr, 7), (fr, 5)
                if tc == 2:
                    return (fr, 0), (fr, 3)
                return None
            for p in getattr(self, 'all_pieces', []) or []:
                try:
                    if p is None or getattr(p, 'dead', False):
                        continue
                    pr, pc = int(p.position[0]), int(p.position[1])
                    pos_by_piece[p] = (pr, pc)
                    sq_to_piece[(pr, pc)] = p
                except Exception:
                    continue

            for _uci, _frm, to, p in getattr(self, '_premove_queue', []) or []:
                if p is None:
                    continue
                queued_pieces.add(p)
                if p in hidden_by_premove:
                    # A previous premove already "covered" this piece.
                    continue
                try:
                    to_sq = (int(to[0]), int(to[1]))
                except Exception:
                    continue

                old_sq = pos_by_piece.get(p)
                if old_sq is not None and sq_to_piece.get(old_sq) == p:
                    sq_to_piece.pop(old_sq, None)

                # If something is (virtually) on the destination square, hide it.
                victim = sq_to_piece.get(to_sq)
                if victim is not None and victim != p:
                    hidden_by_premove.add(victim)
                    pos_by_piece.pop(victim, None)
                    sq_to_piece.pop(to_sq, None)

                pos_by_piece[p] = to_sq
                sq_to_piece[to_sq] = p

                # Castling premove: also move rook (visual only).
                try:
                    if str(getattr(p, 'piece', '')).lower() == 'k':
                        frm_sq = tuple(pos_by_piece.get(p, to_sq))
                        # Use the provided from-square if available in the queue tuple.
                        try:
                            frm_sq = (int(_frm[0]), int(_frm[1]))
                        except Exception:
                            pass
                        rook_sqs = castle_rook_squares(frm_sq, to_sq)
                        if rook_sqs is not None:
                            rook_from, rook_to = rook_sqs
                            rook_piece = sq_to_piece.get(rook_from)
                            if (
                                rook_piece is not None
                                and str(getattr(rook_piece, 'piece', '')).lower() == 'r'
                                and getattr(rook_piece, 'colour', '')[:1] == getattr(p, 'colour', '')[:1]
                            ):
                                queued_pieces.add(rook_piece)
                                sq_to_piece.pop(rook_from, None)
                                victim2 = sq_to_piece.get(rook_to)
                                if victim2 is not None and victim2 != rook_piece:
                                    hidden_by_premove.add(victim2)
                                    pos_by_piece.pop(victim2, None)
                                    sq_to_piece.pop(rook_to, None)
                                pos_by_piece[rook_piece] = rook_to
                                sq_to_piece[rook_to] = rook_piece
                except Exception:
                    pass

            premove_virtual = {p: pos for p, pos in pos_by_piece.items() if p in queued_pieces}
        except Exception:
            premove_virtual = {}
            hidden_by_premove = set()

        for piece in self.all_pieces:
            if piece == piece_selected:
                continue
            if piece in hidden_by_premove:
                continue
            # When a premove is queued, visually "snap" the premoved piece to its destination
            # by skipping its normal draw at the original square.
            if piece in premove_virtual:
                continue
            piece.draw(self.offset, self.screen, self.size, self.flipped)

        # Draw the piece last, if it is being clicked/dragged
        if piece_selected is not None:
            piece_selected.draw(self.offset, self.screen, self.size, False)

        # Draw premoved pieces at their virtual destination (visual only).
        if premove_virtual:
            for premove_piece, premove_to in premove_virtual.items():
                if premove_piece == piece_selected:
                    continue
                try:
                    if premove_piece not in self.all_pieces or getattr(premove_piece, 'dead', False):
                        continue
                    if premove_piece in hidden_by_premove:
                        continue

                    # Ensure sprite image matches current size.
                    try:
                        premove_piece.size = int(self.size)
                        if premove_piece.picture is not None and premove_piece.picture.get_size() != (self.size, self.size):
                            premove_piece.picture = pg.image.load(
                                "data/img/pieces/" + premove_piece.piece_set + "/" + premove_piece.colour[0] + premove_piece.piece.lower() + ".png"
                            ).convert_alpha()
                            premove_piece.picture = pg.transform.smoothscale(premove_piece.picture, (self.size, self.size))
                    except Exception:
                        pass

                    r, c = int(premove_to[0]), int(premove_to[1])
                    if not self.flipped:
                        x = self.offset[0] + self.size * c
                        y = self.offset[1] + self.size * r
                    else:
                        x = self.offset[0] + self.size * (-c + 7)
                        y = self.offset[1] + self.size * (-r + 7)
                    self.screen.blit(premove_piece.picture, (x, y))
                except Exception:
                    continue

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

    def _draw_puzzle_rush_overlay(self) -> None:
        """Puzzle Rush HUD: big score + per-puzzle tick/cross with rating (Chess.com style)."""
        try:
            win_w, win_h = self.screen.get_size()
        except Exception:
            return

        margin = 14
        x = int(win_w * 0.60) + margin
        y = int(margin)
        w = max(180, int(win_w * 0.40) - (margin * 2))
        h = int(win_h - 2 * margin)
        if w < 220:
            x = margin
            y = margin
            w = max(220, win_w - 2 * margin)
            h = max(140, int(win_h - 2 * margin))

        panel = pg.Rect(int(x), int(y), int(w), int(h))
        try:
            bg = pg.Surface((panel.w, panel.h), pg.SRCALPHA)
            bg.fill((0, 0, 0, 160))
            self.screen.blit(bg, (panel.x, panel.y))
            pg.draw.rect(self.screen, (140, 140, 140), panel, width=1, border_radius=6)
        except Exception:
            pass

        # Big score
        score = 0
        try:
            score = int(getattr(self, '_puzzle_rush_solved', 0) or 0)
        except Exception:
            score = 0

        try:
            big_font = getattr(self, '_puzzle_rush_big_font', None)
            big_size = max(44, min(120, int(panel.h * 0.18)))
            if big_font is None or int(getattr(self, '_puzzle_rush_big_font_size', 0) or 0) != int(big_size):
                try:
                    big_font = pg.font.SysFont('arial', int(big_size), bold=True)
                except Exception:
                    big_font = self.font
                self._puzzle_rush_big_font = big_font
                self._puzzle_rush_big_font_size = int(big_size)
        except Exception:
            big_font = self.font

        try:
            score_surf = big_font.render(str(score), True, (255, 255, 255))
            self.screen.blit(score_surf, (panel.centerx - score_surf.get_width() // 2, panel.y + int(panel.h * 0.16)))
        except Exception:
            pass

        # Highscore line
        try:
            hs = int(getattr(self, '_puzzle_rush_highscore', 0) or 0)
        except Exception:
            hs = 0
        try:
            hs_font = self.eval_font if hasattr(self, 'eval_font') else self.font
            hs_surf = hs_font.render(f'High: {hs}', True, (200, 200, 200))
            self.screen.blit(hs_surf, (panel.centerx - hs_surf.get_width() // 2, panel.y + int(panel.h * 0.16) + score_surf.get_height() + 6))
        except Exception:
            pass

        # Attempt history (tick/cross + rating)
        try:
            results = list(getattr(self, '_puzzle_rush_results', []) or [])
        except Exception:
            results = []

        # Layout grid below the big score.
        top_y = panel.y + int(panel.h * 0.38)
        inner_left = panel.x + 14
        inner_right = panel.right - 14
        avail_w = max(1, inner_right - inner_left)

        icon_box = 18
        cell_w = 56
        cell_h = 44
        gap_x = 10
        gap_y = 10
        per_row = max(1, int((avail_w + gap_x) // (cell_w + gap_x)))
        per_row = min(9, per_row)

        # Center the grid within the available width.
        grid_w = int(per_row * cell_w + max(0, (per_row - 1)) * gap_x)
        left_x = int(panel.x + (panel.w - grid_w) // 2)
        left_x = max(int(inner_left), min(int(left_x), int(inner_right - grid_w)))

        # Show the earliest results first, but clamp to what fits.
        max_rows = max(1, int((panel.bottom - 16 - top_y + gap_y) // (cell_h + gap_y)))
        max_cells = max(1, per_row * max_rows)
        if len(results) > max_cells:
            results = results[-max_cells:]

        try:
            small_font = getattr(self, '_puzzle_rush_small_font', None)
            if small_font is None:
                self._puzzle_rush_small_font = pg.font.SysFont('arial', 16, bold=True)
            small_font = self._puzzle_rush_small_font
        except Exception:
            small_font = self.eval_font if hasattr(self, 'eval_font') else self.font

        # Prefer a symbol-capable font for ✓/✗.
        try:
            icon_font = getattr(self, '_puzzle_rush_icon_font', None)
            if icon_font is None:
                self._puzzle_rush_icon_font = pg.font.SysFont('Segoe UI Symbol', 18, bold=True)
            icon_font = self._puzzle_rush_icon_font
        except Exception:
            icon_font = small_font

        for i, (rating, ok) in enumerate(results):
            row = i // per_row
            col = i % per_row
            cx = left_x + col * (cell_w + gap_x)
            cy = top_y + row * (cell_h + gap_y)
            if cy + cell_h > panel.bottom - 10:
                break

            color = (40, 200, 40) if ok else (220, 60, 60)
            # Icon box
            rect = pg.Rect(int(cx + (cell_w - icon_box) // 2), int(cy), int(icon_box), int(icon_box))
            try:
                pg.draw.rect(self.screen, (20, 20, 20), rect, border_radius=4)
                pg.draw.rect(self.screen, color, rect, width=2, border_radius=4)
            except Exception:
                pass

            glyph = '✓' if ok else '✗'
            try:
                gsurf = icon_font.render(glyph, True, color)
                self.screen.blit(gsurf, (rect.centerx - gsurf.get_width() // 2, rect.centery - gsurf.get_height() // 2))
            except Exception:
                try:
                    gsurf = small_font.render('OK' if ok else 'X', True, color)
                    self.screen.blit(gsurf, (rect.centerx - gsurf.get_width() // 2, rect.centery - gsurf.get_height() // 2))
                except Exception:
                    pass

            # Rating below
            try:
                r = int(rating)
            except Exception:
                r = 0
            try:
                rsurf = small_font.render(str(r), True, color)
                self.screen.blit(rsurf, (cx + (cell_w - rsurf.get_width()) // 2, cy + icon_box + 6))
            except Exception:
                pass

    # -----------------
    # Puzzle Rush mode
    # -----------------

    @staticmethod
    def _normalize_fen_6_fields(fen: str) -> str:
        s = str(fen or '').strip()
        parts = s.split()
        if len(parts) == 4:
            return s + ' 0 1'
        if len(parts) == 5:
            return s + ' 1'
        return s

    @staticmethod
    def _puzzle_rush_user_side_from_entry(entry: dict) -> str:
        v = str(entry.get('colorOfUser', '') or '').strip().lower()
        # data uses "white"/"black"
        return 'b' if v.startswith('b') else 'w'

    @staticmethod
    def _puzzle_rush_parse_pgn_headers(pgn_text: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        for line in str(pgn_text or '').splitlines():
            line = line.strip()
            if not line.startswith('[') or '"' not in line:
                continue
            m = re.match(r'^\[(\w+)\s+"(.*)"\]$', line)
            if not m:
                continue
            headers[m.group(1)] = m.group(2)
        return headers

    @staticmethod
    def _puzzle_rush_san_tokens_from_full(full: str) -> list[str]:
        s = str(full or '').strip()
        if not s:
            return []

        # Remove move numbers like "9." and "1..."
        s = re.sub(r'\b\d+\.(\.\.)?\b', ' ', s)
        s = s.replace('...', ' ')
        toks = [t.strip() for t in s.split() if t.strip()]
        # Drop result tokens if present
        toks = [t for t in toks if t not in ('1-0', '0-1', '*')]
        return toks

    def _puzzle_rush_load_highscore(self) -> int:
        try:
            path = str(getattr(self, '_puzzle_rush_highscore_path', '') or '')
        except Exception:
            path = ''
        if not path:
            return 0
        try:
            if not os.path.exists(path):
                return 0
        except Exception:
            return 0
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                raw = f.read().strip()
            return max(0, int(raw or '0'))
        except Exception:
            return 0

    def _puzzle_rush_save_highscore(self, value: int) -> None:
        try:
            path = str(getattr(self, '_puzzle_rush_highscore_path', '') or '')
        except Exception:
            path = ''
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(str(int(max(0, int(value)))))
        except Exception:
            pass

    def _puzzle_rush_update_highscore(self) -> None:
        try:
            score = int(getattr(self, '_puzzle_rush_solved', 0) or 0)
        except Exception:
            score = 0
        try:
            hs = int(getattr(self, '_puzzle_rush_highscore', 0) or 0)
        except Exception:
            hs = 0
        if score > hs:
            self._puzzle_rush_highscore = int(score)
            try:
                self._puzzle_rush_save_highscore(int(score))
            except Exception:
                pass

    def _puzzle_rush_play_result_sound(self, ok: bool) -> None:
        if not self.sound_enabled:
            return
        try:
            path = 'data/sounds/result-good.mp3' if bool(ok) else 'data/sounds/incorrect.mp3'
            pg.mixer.music.load(path)
            pg.mixer.music.play(1)
        except Exception:
            pass

    def _puzzle_rush_expected_uci_from_entry(self, entry: dict) -> tuple[str, list[str]]:
        """Return (start_fen, uci_line) for the puzzle, best-effort."""
        pgn_text = str(entry.get('pgn', '') or '')
        headers = self._puzzle_rush_parse_pgn_headers(pgn_text)

        fen = str(entry.get('initialFen') or '').strip() or str(headers.get('FEN', '') or '').strip()
        fen = self._normalize_fen_6_fields(fen)

        # Preferred: parse the PGN mainline directly (handles messy movetext/variations).
        # We patch the PGN's FEN header to a 6-field FEN so python-chess won't reject it.
        uci_line: list[str] = []
        try:
            if pgn_text.strip():
                patched = pgn_text
                if '[FEN ' in patched:
                    patched = re.sub(r'^\[FEN\s+".*"\]$', f'[FEN "{fen}"]', patched, flags=re.MULTILINE)
                else:
                    # Inject minimal setup headers if missing.
                    injected = [
                        '[SetUp "1"]',
                        f'[FEN "{fen}"]',
                        '',
                    ]
                    patched = '\n'.join(injected) + patched

                game = chess.pgn.read_game(io.StringIO(patched))
                if game is not None:
                    for mv in game.mainline_moves():
                        try:
                            uci_line.append(mv.uci())
                        except Exception:
                            pass
        except Exception:
            uci_line = []

        # Fallback: use header-provided SAN line if PGN parsing didn't yield moves.
        if not uci_line:
            san_line: list[str] = []
            if 'Tactic_line' in headers and str(headers.get('Tactic_line') or '').strip():
                san_line = [t for t in str(headers['Tactic_line']).split() if t.strip()]
            elif 'FULL' in headers and str(headers.get('FULL') or '').strip():
                san_line = self._puzzle_rush_san_tokens_from_full(str(headers['FULL']))

            try:
                b = chess.Board(str(fen))
                for san in san_line:
                    try:
                        mv = b.parse_san(san)
                    except Exception:
                        # Skip any odd tokens rather than failing the entire puzzle.
                        continue
                    uci_line.append(mv.uci())
                    b.push(mv)
            except Exception:
                uci_line = []

        return str(fen), uci_line

    def _puzzle_rush_last_pack_state_path(self) -> str:
        return os.path.join('data', 'settings', 'puzzle_rush_last_pack.txt')

    def _puzzle_rush_load_last_pack_index(self) -> int | None:
        try:
            path = self._puzzle_rush_last_pack_state_path()
            if not os.path.isfile(path):
                return None
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                raw = str(f.read() or '').strip()
            idx = int(raw)
            if 0 <= idx <= 500:
                return idx
            return None
        except Exception:
            return None

    def _puzzle_rush_save_last_pack_index(self, idx: int) -> None:
        try:
            idx = int(idx)
        except Exception:
            return
        if idx < 0 or idx > 500:
            return
        try:
            path = self._puzzle_rush_last_pack_state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(str(idx))
        except Exception:
            pass

    def _puzzle_rush_pick_next_pack(self) -> str | None:
        """Pick the next pack in sequence: 0.json -> 1.json -> ... -> 500.json -> 0.json.

        If some indices are missing, it scans forward (wrapping) to the next existing file.
        """
        try:
            base = os.path.join('data', 'puzzle-rush')
            if not os.path.isdir(base):
                return None

            max_idx = 499
            last = self._puzzle_rush_load_last_pack_index()
            start = 0 if last is None else (int(last) + 1) % (max_idx + 1)

            for offset in range(max_idx + 1):
                idx = (start + offset) % (max_idx + 1)
                candidate = os.path.join(base, f'{idx}.json')
                if os.path.isfile(candidate):
                    self._puzzle_rush_save_last_pack_index(idx)
                    return candidate

            # Fallback: pick any JSON if naming doesn't match expected scheme.
            files = [f for f in os.listdir(base) if f.lower().endswith('.json')]
            if not files:
                return None
            files_sorted = sorted(files, key=lambda s: str(s).lower())
            chosen = files_sorted[0]
            m = re.match(r'^(\d+)\.json$', str(chosen).strip(), flags=re.IGNORECASE)
            if m:
                try:
                    idx = int(m.group(1))
                    self._puzzle_rush_save_last_pack_index(idx)
                except Exception:
                    pass
            return os.path.join(base, chosen)
        except Exception:
            return None

    def _puzzle_rush_start_new_run(self) -> None:
        # Clear end popup if reset was pressed.
        self.end_popup_active = False
        self.end_popup_text = ''
        self.end_popup_pgn_path = None
        self.game_just_ended = False

        self._puzzle_rush_strikes = 0
        self._puzzle_rush_solved = 0
        self._puzzle_rush_index = 0
        self._puzzle_rush_expected_uci = []
        self._puzzle_rush_expected_i = 0
        self._puzzle_rush_results = []
        self._puzzle_rush_current_rating = 0
        self._puzzle_rush_pending_uci = ''
        self._puzzle_rush_pending_due_ts = None
        self._puzzle_rush_pending_advance = 0

        # Ensure no hint visuals leak into Puzzle Rush.
        self.best_move = ''
        self.hint_arrow = None

        pack = self._puzzle_rush_pick_next_pack()
        self._puzzle_rush_pack_path = pack
        if not pack:
            self.end_popup_active = True
            self.end_popup_text = 'Puzzle Rush: no packs found in data/puzzle-rush'
            return

        try:
            with open(pack, 'r', encoding='utf-8', errors='ignore') as f:
                puzzles = json.load(f)
        except Exception:
            puzzles = []

        if not isinstance(puzzles, list) or not puzzles:
            self.end_popup_active = True
            self.end_popup_text = 'Puzzle Rush: failed to load puzzle pack'
            return

        # Pack is ordered easiest -> hardest already.
        self._puzzle_rush_puzzles = puzzles

        # Load first puzzle.
        self._puzzle_rush_load_puzzle(self._puzzle_rush_index)

    def _puzzle_rush_load_puzzle(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._puzzle_rush_puzzles or []):
            # End of pack.
            try:
                self._puzzle_rush_update_highscore()
            except Exception:
                pass
            self.end_popup_active = True
            self.end_popup_text = f'Puzzle Rush complete. Solved: {self._puzzle_rush_solved}  Strikes: {self._puzzle_rush_strikes}/3'
            return

        entry = self._puzzle_rush_puzzles[idx]
        if not isinstance(entry, dict):
            # Skip invalid entry.
            self._puzzle_rush_index = idx + 1
            self._puzzle_rush_load_puzzle(self._puzzle_rush_index)
            return

        self._puzzle_rush_user_side = self._puzzle_rush_user_side_from_entry(entry)
        try:
            self._puzzle_rush_current_rating = int(entry.get('rating') or 0)
        except Exception:
            self._puzzle_rush_current_rating = 0
        self.flipped = bool(self._puzzle_rush_user_side == 'b')

        start_fen, uci_line = self._puzzle_rush_expected_uci_from_entry(entry)
        start_fen = self._normalize_fen_6_fields(start_fen)
        self._puzzle_rush_expected_uci = list(uci_line or [])
        self._puzzle_rush_expected_i = 0
        self._puzzle_rush_pending_uci = ''
        self._puzzle_rush_pending_due_ts = None
        self._puzzle_rush_pending_advance = 0

        # Some pack entries can parse down to a single ply (only the forced starting computer move).
        # That would auto-complete immediately after the computer move and feel like a bug.
        # Skip such malformed entries without awarding a point or a strike.
        try:
            if len(self._puzzle_rush_expected_uci or []) < 2:
                self._puzzle_rush_index = idx + 1
                self._puzzle_rush_load_puzzle(self._puzzle_rush_index)
                return
        except Exception:
            pass

        # Ensure no hint visuals leak into Puzzle Rush.
        self.best_move = ''
        self.hint_arrow = None

        # Reset UI/game state to this puzzle position.
        self._load_fen_into_ui(start_fen)
        self.game_fens = [str(start_fen)]
        self.last_move = []
        self._clear_premove()

        # Fresh PGN container (we don't rely on it heavily, but node.board() is used elsewhere).
        self.game = chess.pgn.Game()
        try:
            self.game.headers['Event'] = 'Puzzle Rush'
            self.game.headers['PuzzlePack'] = os.path.basename(str(self._puzzle_rush_pack_path or ''))
            self.game.headers['PuzzleIndex'] = str(int(idx))
            if 'id' in entry:
                self.game.headers['PuzzleId'] = str(entry.get('id'))
        except Exception:
            pass
        self.node = self.game

        # Apply the starting computer move (puzzle always starts with computer).
        self._puzzle_rush_apply_computer_start_move()

    def _puzzle_rush_schedule_computer_move(self, uci: str, advance: int) -> None:
        """Schedule a computer move to be applied later without blocking the UI."""
        try:
            delay = float(getattr(self, '_puzzle_rush_reply_delay_s', 0.5) or 0.5)
        except Exception:
            delay = 0.5
        try:
            due = float(time.time()) + max(0.0, float(delay))
        except Exception:
            due = None
        self._puzzle_rush_pending_uci = str(uci or '')
        self._puzzle_rush_pending_due_ts = due
        self._puzzle_rush_pending_advance = int(advance)

    def _puzzle_rush_pump_pending(self) -> None:
        """Apply a scheduled computer move if due (called each frame from run())."""
        if self.end_popup_active or self.review_active or getattr(self, 'analysis_active', False):
            return
        uci = str(getattr(self, '_puzzle_rush_pending_uci', '') or '')
        if not uci:
            return
        due = getattr(self, '_puzzle_rush_pending_due_ts', None)
        if due is not None:
            try:
                if float(time.time()) < float(due):
                    return
            except Exception:
                return

        # Clear pending BEFORE applying, so re-entrancy can't replay it.
        adv = int(getattr(self, '_puzzle_rush_pending_advance', 0) or 0)
        self._puzzle_rush_pending_uci = ''
        self._puzzle_rush_pending_due_ts = None
        self._puzzle_rush_pending_advance = 0

        self._puzzle_rush_autoplay = True
        try:
            self.last_move.append(uci)
            self.node = self.node.add_variation(chess.Move.from_uci(uci))
            self.engine_make_move(uci)
        finally:
            self._puzzle_rush_autoplay = False

        # Advance expected index once the computer move is actually applied.
        if adv:
            try:
                self._puzzle_rush_expected_i = int(self._puzzle_rush_expected_i) + int(adv)
            except Exception:
                self._puzzle_rush_expected_i = 0

        # If a premove is queued, apply it immediately (no extra delay).
        try:
            idx_before = int(getattr(self, '_puzzle_rush_index', 0) or 0)
        except Exception:
            idx_before = 0
        try:
            self._apply_premove_if_legal()
        except Exception:
            pass
        # If the premove advanced/ended the puzzle, don't double-advance below.
        try:
            if int(getattr(self, '_puzzle_rush_index', 0) or 0) != int(idx_before):
                return
        except Exception:
            pass

        # If that ended the sequence, advance immediately.
        try:
            if self._puzzle_rush_expected_i >= len(self._puzzle_rush_expected_uci or []):
                try:
                    self._puzzle_rush_play_result_sound(True)
                except Exception:
                    pass
                self._puzzle_rush_results.append((int(getattr(self, '_puzzle_rush_current_rating', 0) or 0), True))
                self._puzzle_rush_solved += 1
                self._puzzle_rush_index += 1
                self._puzzle_rush_load_puzzle(self._puzzle_rush_index)
        except Exception:
            pass

    def _puzzle_rush_apply_computer_start_move(self) -> None:
        if not self._puzzle_rush_expected_uci:
            # No parseable solution line: do NOT skip forward (this caused score jumps).
            # Stop with an explicit error so the pack can be improved.
            self.end_popup_active = True
            self.end_popup_text = 'Puzzle Rush: this puzzle has no parseable solution line'
            return

        user = self._puzzle_rush_user_side
        computer = 'b' if user == 'w' else 'w'
        try:
            to_move = str(self.turn)
        except Exception:
            to_move = 'w'

        if to_move != computer:
            # Data is expected to be computer-to-move first; if it isn't, do not force an illegal move.
            self._puzzle_rush_expected_i = 0
            return

        first = str(self._puzzle_rush_expected_uci[0])
        # Schedule the move via the main loop to avoid freezing the UI.
        self._puzzle_rush_schedule_computer_move(first, advance=1)

    def _puzzle_rush_on_move(self, mover: str, uci: str) -> None:
        """Called from moved() after any move while puzzle rush is active."""
        if self._puzzle_rush_autoplay:
            return

        # Only validate the user's moves.
        if mover != self._puzzle_rush_user_side:
            return

        # If we don't have an expected line, we can't validate.
        if not self._puzzle_rush_expected_uci:
            self.end_popup_active = True
            self.end_popup_text = 'Puzzle Rush: this puzzle has no parseable solution line'
            return

        i = int(self._puzzle_rush_expected_i)
        if i < 0 or i >= len(self._puzzle_rush_expected_uci):
            # Already at end.
            try:
                self._puzzle_rush_results.append((int(getattr(self, '_puzzle_rush_current_rating', 0) or 0), True))
            except Exception:
                pass
            self._puzzle_rush_solved += 1
            self._puzzle_rush_index += 1
            self._puzzle_rush_load_puzzle(self._puzzle_rush_index)
            return

        expected = str(self._puzzle_rush_expected_uci[i])
        played = str(uci or '')

        if played != expected:
            self._puzzle_rush_strikes += 1
            try:
                self._puzzle_rush_play_result_sound(False)
            except Exception:
                pass
            try:
                self._puzzle_rush_results.append((int(getattr(self, '_puzzle_rush_current_rating', 0) or 0), False))
            except Exception:
                pass
            if self._puzzle_rush_strikes >= 3:
                try:
                    self._puzzle_rush_update_highscore()
                except Exception:
                    pass
                self.end_popup_active = True
                self.end_popup_text = f'Puzzle Rush over. Solved: {self._puzzle_rush_solved}  Strikes: 3/3'
                return
            # Wrong move: advance to next puzzle.
            self._puzzle_rush_index += 1
            self._puzzle_rush_load_puzzle(self._puzzle_rush_index)
            return

        # Correct user move.
        self._puzzle_rush_expected_i = i + 1

        # If that finished the line, count as solved and advance.
        if self._puzzle_rush_expected_i >= len(self._puzzle_rush_expected_uci):
            try:
                self._puzzle_rush_play_result_sound(True)
            except Exception:
                pass
            try:
                self._puzzle_rush_results.append((int(getattr(self, '_puzzle_rush_current_rating', 0) or 0), True))
            except Exception:
                pass
            self._puzzle_rush_solved += 1
            self._puzzle_rush_index += 1
            self._puzzle_rush_load_puzzle(self._puzzle_rush_index)
            return

        # Schedule the computer reply if present (non-blocking).
        comp_move = str(self._puzzle_rush_expected_uci[self._puzzle_rush_expected_i])
        self._puzzle_rush_schedule_computer_move(comp_move, advance=1)
