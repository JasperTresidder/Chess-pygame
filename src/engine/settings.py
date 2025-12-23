import os
import re
import threading
import queue
import tkinter as tk

import pygame as pg
import pygame_menu as pm
from pygame_menu.controls import Controller

from src.engine.perft import perft_nodes_from_fen_with_progress


def _menu_window_size_fallback(surface) -> tuple[int, int]:
    try:
        return tuple(int(x) for x in pg.display.get_window_size())
    except Exception:
        try:
            return surface.get_size()
        except Exception:
            return 800, 600


def _apply_menu_resize(menu_obj, width: int | None = None, height: int | None = None) -> None:
    """Resize the pygame display surface while a pygame-menu Menu is active.

    pygame-menu won't call pg.display.set_mode for us; without that, widgets can disappear
    after resizing the OS window.
    """
    try:
        if width is None or height is None:
            w, h = _menu_window_size_fallback(getattr(menu_obj, 'screen', None))
        else:
            w, h = int(width), int(height)
    except Exception:
        return

    try:
        old = getattr(menu_obj, 'o_size', None)
        if old is not None and tuple(old) == (int(w), int(h)):
            return
    except Exception:
        pass

    try:
        new_surface = pg.display.set_mode((int(w), int(h)), pg.RESIZABLE, vsync=1)
    except Exception:
        try:
            new_surface = pg.display.set_mode((int(w), int(h)), pg.RESIZABLE)
        except Exception:
            return

    try:
        menu_obj.screen = new_surface
    except Exception:
        pass
    try:
        menu_obj._surface = new_surface
    except Exception:
        pass

    def _refresh_engine_like(obj) -> None:
        """Best-effort: keep the main game visuals/layout in sync with the resized display.

        Important: do NOT call obj.check_resize() here; that calls settings.resize_event()
        and can recurse while we're already handling a menu resize.
        """
        try:
            if obj is None:
                return
        except Exception:
            return

        try:
            if hasattr(obj, 'background'):
                try:
                    obj.background = pg.image.load('data/img/background_dark.png').convert()
                    obj.background = pg.transform.smoothscale(obj.background, (int(w), int(h)))
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if hasattr(obj, '_ensure_layout'):
                obj._ensure_layout(force=True)
        except Exception:
            pass

    # Propagate to parent/engine so the game doesn't come back in a broken layout.
    try:
        p = getattr(menu_obj, 'parent', None)
        if p is not None and hasattr(p, 'screen'):
            p.screen = new_surface
            _refresh_engine_like(p)
    except Exception:
        pass
    try:
        eng = getattr(menu_obj, 'engine', None)
        if eng is not None and hasattr(eng, 'screen'):
            eng.screen = new_surface
            _refresh_engine_like(eng)
    except Exception:
        pass

    try:
        menu_obj.resize(int(w), int(h))
    except Exception:
        pass
    try:
        menu_obj.render()
    except Exception:
        pass
    try:
        if hasattr(menu_obj, 'force_surface_cache_update'):
            menu_obj.force_surface_cache_update()
    except Exception:
        pass
    try:
        menu_obj.o_size = (int(w), int(h))
    except Exception:
        pass


def _set_theme_attr(theme: pm.themes.Theme, name: str, value) -> None:
    try:
        if hasattr(theme, name):
            setattr(theme, name, value)
    except Exception:
        pass


def _make_settings_theme(base: pm.themes.Theme | None = None) -> pm.themes.Theme:
    """Create a nicer-looking theme while staying compatible with older pygame-menu versions."""
    try:
        theme = (base or pm.themes.THEME_DARK).copy()
    except Exception:
        theme = pm.themes.THEME_DARK

    # Typography & spacing (compact, so everything fits without scrolling).
    # Use a common font for readability.
    try:
        font_arial = getattr(getattr(pm, 'font', None), 'FONT_ARIAL', None)
    except Exception:
        font_arial = None
    _set_theme_attr(theme, 'title_font', font_arial or 'arial')
    _set_theme_attr(theme, 'widget_font', font_arial or 'arial')
    _set_theme_attr(theme, 'title_font_size', 54)
    _set_theme_attr(theme, 'widget_font_size', 22)
    _set_theme_attr(theme, 'widget_margin', (0, 6))
    _set_theme_attr(theme, 'widget_padding', 8)
    _set_theme_attr(theme, 'title_offset', (0, 14))

    # Title bar styling differs across pygame-menu versions.
    # Some versions assert that title_bar_style must be a valid MENUBAR_STYLE_* constant.
    try:
        menubar_none = getattr(pm.widgets, 'MENUBAR_STYLE_NONE', None)
        if menubar_none is not None:
            _set_theme_attr(theme, 'title_bar_style', menubar_none)
    except Exception:
        # Fall back to the theme's default.
        pass

    # Controls.
    _set_theme_attr(theme, 'widget_selection_effect', pm.widgets.NoneSelection())
    _set_theme_attr(theme, 'scrollbar_color', (140, 140, 140))
    _set_theme_attr(theme, 'scrollbar_slider_color', (220, 220, 220))

    # Keep overall palette consistent with the existing app.
    _set_theme_attr(theme, 'background_color', (18, 18, 18))
    return theme


def _btn_text_color(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    # Simple contrast heuristic.
    try:
        lum = 0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]
        return (0, 0, 0) if lum > 140 else (255, 255, 255)
    except Exception:
        return (0, 0, 0)


class SettingsMenu(pm.menu.Menu):
    def __init__(self, surface, parent,  *args, **kwargs):
        # Apply a nicer theme by default (caller can still pass a theme).
        if 'theme' not in kwargs or kwargs.get('theme') is None:
            kwargs['theme'] = _make_settings_theme(pm.themes.THEME_DARK)
        else:
            kwargs['theme'] = _make_settings_theme(kwargs.get('theme'))
        super().__init__(*args, **kwargs)
        self.screen = surface
        self.o_size = self.screen.get_size()
        self.parent = parent

        # Autosave: persist settings.txt whenever a widget changes.
        # Debounced to avoid writing on every keystroke.
        self._autosave_lock = threading.Lock()
        self._autosave_timer: threading.Timer | None = None
        self._autosave_enabled = False
        custom_controller = Controller()
        custom_controller.apply = self.btn_apply

        # Two-column layout using Frames (no scrolling).
        win_w, win_h = self.screen.get_size()
        content_w = max(640, int(win_w * 0.92))
        content_h = max(360, int(win_h * 0.78))
        col_gap = 22
        col_w = int((content_w - col_gap) / 2)
        # pygame-menu DropSelect width includes extra internal margins/arrow padding;
        # be conservative so DropSelect never exceeds the Frame width.
        select_w = max(200, col_w - 120)

        def add_action(label: str, fn, bg: tuple[int, int, int]):
            btn = self.add.button(
                label,
                fn,
                accept_kwargs=True,
                font_shadow=True,
                font_shadow_color=(70, 70, 70),
                font_background_color=bg,
                cursor=11,
                font_color=_btn_text_color(bg),
            )
            btn.set_controller(custom_controller)
            return btn

        def add_label(text: str, size: int = 22):
            return self.add.label(text, font_size=size)

        # Top row actions
        actions = self.add.frame_h(content_w, 64, background_color=None, border_width=0, padding=0)
        try:
            actions._pack_margin_warning = False
        except Exception:
            pass
        # Back should also persist changes (user expectation: leaving settings saves).
        self.back = add_action('Back', self.confirm, (200, 0, 0))
        view = add_action('View Controls', self.view_controls, (100, 100, 100))
        new_game = add_action('New Game', self.new_game, (100, 100, 100))
        review = add_action('Review Games', self.view_games, (100, 100, 100))
        analysis = add_action('Analysis Mode', self.enter_analysis_mode, (100, 100, 100))
        actions.pack(self.back)
        actions.pack(view, margin=(14, 0))
        actions.pack(new_game, margin=(14, 0))
        actions.pack(review, margin=(14, 0))
        actions.pack(analysis, margin=(14, 0))
        self.pieces = [
            ('Alila', 'alila'),
            ('Alpha', 'alpha'),
            ('Cardinal', 'cardinal'),
            ('Chessicons', 'chessicons'),
            ('Chessmonk', 'chessmonk'),
            ('Dubrovny', 'dubrovny'),
            ('Gioco', 'gioco'),
            ('Horsey', 'horsey'),
            ('Kosal', 'kosal'),
            ('Maya', 'maya'),
            ('Metaltops', 'metaltops'),
            ('Pirouetti', 'pirouetti'),
            ('Regular', 'regular'),
            ('Riohacha', 'riohacha'),
            ('Staunty', 'staunty'),
            ('Tatiana', 'tatiana'),
        ]

        # Keep AI Elo presets identical to the Start menu.
        # NOTE: We load old saved settings that stored an *index* into a legacy list.
        self.ai_elo = [(str(e), e) for e in (600, 800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400, 2600, 2800, 3000)]
        self.board_background = [
            ('Cherry', 'cherry_800x.jpg'),
            ('Coffee', 'coffee-beans.jpg'),
            ('Maple', 'maple.jpg'),
            ('Marble', 'marble.png'),
            ('Sand', 'sand.jpg'),
        ]

        self.modes = [
            ('Player vs AI', 'pvai'),
            ('Player vs Player', 'pvp'),
            ('AI vs AI', 'aivai'),
        ]

        self.movement_modes = [
            ('Click', 'click'),
            ('Drag', 'drag'),
            ('Click + Drag', 'click+drag'),
        ]

        file = open('data/settings/settings.txt', 'r')
        lines = file.readlines()
        if len(lines) < 7:
            lines = lines + ['2\n']
        if len(lines) < 8:
            lines = lines + ['1\n']
        if len(lines) < 9:
            # Default time control: minutes|incrementSeconds
            lines = lines + ['5|0\n']
        if len(lines) < 10:
            # Default review analysis depth (used for Game Review re-analysis)
            lines = lines + ['10\n']

        # Backward-compatible Elo load:
        # - Old format stored a dropselect index 0..20 for values 600 + 120*i.
        # - New format stores the actual Elo value.
        saved_elo = 800
        try:
            raw = str(lines[3]).strip()
            v = int(raw)
            if 0 <= v <= 20:
                saved_elo = 600 + v * 120
            else:
                saved_elo = v
        except Exception:
            saved_elo = 800
        # Map to the closest preset.
        try:
            elo_values = [int(x[1]) for x in self.ai_elo]
            default_elo_index = min(range(len(elo_values)), key=lambda i: abs(elo_values[i] - int(saved_elo)))
        except Exception:
            default_elo_index = 1

        # Columns
        columns = self.add.frame_h(content_w, content_h, background_color=None, border_width=0, padding=0)
        try:
            columns._pack_margin_warning = False
        except Exception:
            pass
        left = self.add.frame_v(col_w, content_h, background_color=None, border_width=0, padding=0)
        right = self.add.frame_v(col_w, content_h, background_color=None, border_width=0, padding=0)
        # pygame-menu warns about margins when packing; also, packed widgets can slightly exceed
        # the frame width due to internal calculations. Keep layout stable and quiet.
        try:
            left._pack_margin_warning = False
            right._pack_margin_warning = False
        except Exception:
            pass
        columns.pack(left)
        columns.pack(right)

        # Gameplay column
        left.pack(add_label('Gameplay', 26), margin=(0, 6))

        self.label1 = add_label('Game Mode', 22)
        self.mode = self.add.dropselect('', self.modes, int(lines[0].replace('\n', '')),
                        selection_box_width=select_w,
                        selection_box_margin=(0, 0),
                        selection_option_font_size=20,
                        placeholder='Select Mode',
                        selection_box_height=6,
                        cursor=11,
                                        )
        self.mode.set_controller(custom_controller)
        left.pack(self.label1)
        left.pack(self.mode)

        self.label_movement = add_label('Movement', 22)
        self.movement = self.add.dropselect('', self.movement_modes, int(lines[6].replace('\n', '')),
                            selection_box_width=select_w,
                            selection_box_margin=(0, 0),
                            selection_option_font_size=20,
                            placeholder='Select Movement',
                            selection_box_height=6,
                            cursor=11,
                                            )
        self.movement.set_controller(custom_controller)
        left.pack(self.label_movement)
        left.pack(self.movement)

        self.label_flip = add_label('Flip Board', 22)
        self.flip = self.add.toggle_switch('', int(lines[4]), cursor=11)
        self.flip.set_controller(custom_controller)
        left.pack(self.label_flip)
        left.pack(self.flip)

        self.label_sounds = add_label('Sounds', 22)
        self.sounds = self.add.toggle_switch('', int(lines[5]), cursor=11)
        self.sounds.set_controller(custom_controller)
        left.pack(self.label_sounds)
        left.pack(self.sounds)

        self.label_eval = add_label('Eval Bar', 22)
        self.eval_bar = self.add.toggle_switch('', int(lines[7]), cursor=11)
        self.eval_bar.set_controller(custom_controller)
        left.pack(self.label_eval)
        left.pack(self.eval_bar)

        self.label_tc = add_label('Time Control (min|inc)', 22)
        self.time_control = self.add.text_input(
            '',
            default=str(lines[8]).replace('\n', ''),
            maxchar=12,
            copy_paste_enable=False,
            valid_chars=list('0123456789| '),
            cursor=11,
        )
        self.time_control.set_controller(custom_controller)
        left.pack(self.label_tc)
        left.pack(self.time_control)

        # Default Game Review analysis depth (Stockfish set_depth) for ACPL/move labels.
        try:
            raw_rd = str(lines[9]).strip().lower()
            if raw_rd in ('w', 'b'):
                # Old files stored player colour here.
                saved_rd = int(getattr(self.parent, 'review_analysis_depth', 10))
            else:
                saved_rd = int(raw_rd)
        except Exception:
            saved_rd = int(getattr(self.parent, 'review_analysis_depth', 10))
        saved_rd = max(6, min(20, int(saved_rd)))
        review_depth_values = [(f"d{d}", d) for d in range(6, 22, 2)]
        try:
            rd_list = [int(x[1]) for x in review_depth_values]
            default_rd_index = min(range(len(rd_list)), key=lambda i: abs(rd_list[i] - int(saved_rd)))
        except Exception:
            default_rd_index = 2
        self.label_review_depth = add_label('Review Analysis Depth', 22)
        self.review_depth = self.add.dropselect(
            '',
            review_depth_values,
            int(default_rd_index),
            selection_box_width=select_w,
            selection_box_margin=(0, 0),
            selection_option_font_size=20,
            placeholder='Select depth',
            selection_box_height=6,
            cursor=11,
        )
        self.review_depth.set_controller(custom_controller)
        left.pack(self.label_review_depth)
        left.pack(self.review_depth)

        # Appearance + Engine column
        right.pack(add_label('Appearance', 26), margin=(0, 6))
        self.label_pieces = add_label('Pieces', 22)
        self.piece = self.add.dropselect('', self.pieces, int(lines[1].replace('\n', '')),
                         selection_box_width=select_w,
                         selection_box_margin=(0, 0),
                         selection_option_font_size=20,
                         placeholder='Select Piece Type',
                         selection_box_height=6,
                         cursor=11,
                                         )
        self.piece.set_controller(custom_controller)
        right.pack(self.label_pieces)
        right.pack(self.piece)

        self.label_board = add_label('Board Style', 22)
        self.board = self.add.dropselect('', self.board_background, int(lines[2].replace('\n', '')),
                         selection_box_width=select_w,
                         selection_box_margin=(0, 0),
                         selection_option_font_size=20,
                         placeholder='Select Board Style',
                         selection_box_height=6,
                         cursor=11,
                                         )
        self.board.set_controller(custom_controller)
        right.pack(self.label_board)
        right.pack(self.board)

        right.pack(add_label('Engine', 26))
        self.label_elo = add_label('AI Elo', 22)
        self.strength = self.add.dropselect('', self.ai_elo, int(default_elo_index),
                            selection_box_width=select_w,
                            selection_box_margin=(0, 0),
                            selection_option_font_size=20,
                            placeholder='Select Elo',
                            selection_box_height=6,
                            cursor=11,
                                            )
        self.strength.set_controller(custom_controller)
        right.pack(self.label_elo)
        right.pack(self.strength)

        self.perft_btn = add_action('Perft Test', self.view_perft, (100, 100, 100))
        right.pack(self.perft_btn)


        # Enable autosave once widgets are fully constructed.
        self._autosave_enabled = True

        # Hook autosave callbacks for all settings widgets.
        for w in (
            self.mode,
            self.movement,
            self.flip,
            self.sounds,
            self.eval_bar,
            self.time_control,
            self.review_depth,
            self.piece,
            self.board,
            self.strength,
        ):
            self._hook_widget_autosave(w)


        self.resized = False

    def _hook_widget_autosave(self, widget) -> None:
        """Best-effort hook for pygame-menu widgets across versions."""
        try:
            widget.set_onchange(lambda *_a, **_k: self._request_autosave())
            return
        except Exception:
            pass
        try:
            widget.onchange = lambda *_a, **_k: self._request_autosave()
        except Exception:
            pass

    def _cancel_autosave_timer(self) -> None:
        with self._autosave_lock:
            t = self._autosave_timer
            self._autosave_timer = None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    def _request_autosave(self) -> None:
        if not bool(getattr(self, '_autosave_enabled', False)):
            return

        # Debounce writes: schedule one save shortly after the last change.
        def _do_save():
            try:
                self._persist_settings_to_file()
            except Exception:
                pass

        with self._autosave_lock:
            if self._autosave_timer is not None:
                try:
                    self._autosave_timer.cancel()
                except Exception:
                    pass
            t = threading.Timer(0.25, _do_save)
            t.daemon = True
            self._autosave_timer = t
            t.start()

    def _settings_file_lines(self) -> list[str]:
        # Match the same format used by confirm().
        lines: list[str] = []
        try:
            lines.append(str(self.mode.get_index()) + '\n')
        except Exception:
            lines.append('0\n')
        try:
            lines.append(str(self.piece.get_index()) + '\n')
        except Exception:
            lines.append('0\n')
        try:
            lines.append(str(self.board.get_index()) + '\n')
        except Exception:
            lines.append('0\n')

        # Elo: persist the value (not index).
        try:
            elo_val = int(self.strength.get_value()[0][1])
        except Exception:
            elo_val = 800
        lines.append(str(int(elo_val)) + '\n')

        try:
            lines.append(str(int(self.flip.get_value())) + '\n')
        except Exception:
            lines.append('0\n')
        try:
            lines.append(str(int(self.sounds.get_value())) + '\n')
        except Exception:
            lines.append('1\n')
        try:
            lines.append(str(self.movement.get_index()) + '\n')
        except Exception:
            lines.append('0\n')
        try:
            lines.append(str(int(self.eval_bar.get_value())) + '\n')
        except Exception:
            lines.append('1\n')

        try:
            tc = str(self.time_control.get_value()).strip()
            lines.append((tc if tc else '5|0') + '\n')
        except Exception:
            lines.append('5|0\n')

        try:
            rd = int(self.review_depth.get_value()[0][1])
        except Exception:
            rd = 10
        rd = max(6, min(20, int(rd)))
        lines.append(str(int(rd)) + '\n')

        # Preserve player colour (set in Start menu for PvAI).
        try:
            pc = str(getattr(self.parent, 'player_colour', 'w') or 'w').strip().lower()
        except Exception:
            pc = 'w'
        lines.append(('b' if pc.startswith('b') else 'w') + '\n')
        return lines

    def _persist_settings_to_file(self) -> None:
        try:
            os.makedirs('data/settings', exist_ok=True)
        except Exception:
            pass
        with open('data/settings/settings.txt', 'w') as file:
            file.writelines(self._settings_file_lines())

    def run(self):
        self.enable()
        self.mainloop(self.screen, self.resize_event, fps_limit=120)

    def resize_event(self):
        try:
            w, h = _menu_window_size_fallback(self.screen)
            if (int(w), int(h)) != tuple(self.o_size):
                self.resized = True
                _apply_menu_resize(self, int(w), int(h))
        except Exception:
            pass

    def confirm(self):
        # Flush any pending autosave so the latest keystrokes are persisted.
        self._cancel_autosave_timer()

        chosen = self.piece.get_value()[0][1]
        self.parent.change_pieces(chosen)
        self.parent.change_mode(self.mode.get_value()[0][1])
        self.parent.change_board(self.board.get_value()[0][1])
        self.parent.change_ai_elo(self.strength.get_value()[0][1])
        self.parent.flip_enable(int(self.flip.get_value()))
        self.parent.sounds_enable(int(self.sounds.get_value()))
        self.parent.set_movement_mode(self.movement.get_value()[0][1])
        self.parent.set_eval_bar_enabled(bool(int(self.eval_bar.get_value())))
        try:
            self.parent.set_time_control(str(self.time_control.get_value()))
        except Exception:
            pass
        try:
            rd_val = int(self.review_depth.get_value()[0][1])
            rd_val = max(6, min(20, int(rd_val)))
            self.parent.review_analysis_depth_default = int(rd_val)
            self.parent.review_analysis_depth = int(rd_val)
        except Exception:
            try:
                self.parent.review_analysis_depth = int(getattr(self.parent, 'review_analysis_depth', 10))
            except Exception:
                self.parent.review_analysis_depth = 10
        try:
            self._persist_settings_to_file()
        except Exception:
            pass
        self.mode.get_index()
        self.exit_menu()

    def enter_analysis_mode(self, **kwargs):
        """Apply settings and enter analysis mode (new analysis)."""
        was_active = False
        try:
            was_active = bool(getattr(self.parent, 'analysis_active', False))
        except Exception:
            was_active = False

        # Save/apply settings and close the menu.
        try:
            self.confirm()
        except Exception:
            try:
                self.exit_menu()
            except Exception:
                pass

        # If we're already in analysis mode, don't restart the line.
        if was_active:
            return
        try:
            self.parent.start_analysis_new()
        except Exception:
            pass

    def new_game(self, **kwargs):
        """Apply settings and return to the Start menu to begin a new game."""
        # Save/apply settings and close the menu.
        try:
            self.confirm()
        except Exception:
            try:
                self.exit_menu()
            except Exception:
                pass

        # Ensure we leave review/analysis cleanly (without triggering their own menu flows).
        try:
            if bool(getattr(self.parent, 'review_active', False)):
                self.parent.exit_review(return_to_start_menu=False)
        except Exception:
            pass
        try:
            if bool(getattr(self.parent, 'analysis_active', False)):
                self.parent.exit_analysis(return_to_start_menu=False)
        except Exception:
            pass

        # Flip the start-menu flag and reset so the run-loop shows StartMenu next frame.
        try:
            self.parent._start_menu_shown = False
        except Exception:
            pass
        try:
            self.parent._restore_normal_layout()
        except Exception:
            pass
        try:
            self.parent.reset_game()
        except Exception:
            pass

    def view_controls(self, **kwargs):
        self.disable()
        control_menu = Controls(title='Controls', width=self.screen.get_width(), height=self.screen.get_height(),surface=self.screen, parent=self, theme=pm.themes.THEME_DARK)
        control_menu.run()

    def view_games(self, **kwargs):
        self.disable()
        try:
            review_menu = GameReviewMenu(title='Game Review', width=self.screen.get_width(), height=self.screen.get_height(), surface=self.screen, parent=self, engine=self.parent, theme=pm.themes.THEME_DARK)
            review_menu.run()
        except Exception:
            # Never crash the app from the settings menu.
            self.enable()

    def view_perft(self, **kwargs):
        self.disable()
        try:
            perft_menu = PerftMenu(
                title='Perft Test',
                width=self.screen.get_width(),
                height=self.screen.get_height(),
                surface=self.screen,
                parent=self,
                theme=pm.themes.THEME_DARK,
            )
            perft_menu.run()
        except Exception:
            self.enable()

    def btn_apply(self, event, ob):
        applied = event.key == 27
        if applied:
            self.exit_menu()


    def exit_menu(self):
        self.disable()
        if self.resized:
            self.parent.check_resize()


class Controls(pm.menu.Menu):
    def __init__(self, surface, parent,  *args, **kwargs):
        if 'theme' not in kwargs or kwargs.get('theme') is None:
            kwargs['theme'] = _make_settings_theme(pm.themes.THEME_DARK)
        else:
            kwargs['theme'] = _make_settings_theme(kwargs.get('theme'))
        super().__init__(*args, **kwargs)
        self.screen = surface
        self.o_size = self.screen.get_size()
        self.parent = parent
        custom_controller = Controller()
        custom_controller.apply = self.btn_apply
        self.add.vertical_margin(10)
        self.button = self.add.button('Back', self.exit_menu, accept_kwargs=True, font_shadow=True,
                                      font_shadow_color=(80, 80, 80), font_background_color=(200, 0, 0), cursor=11,
                                      font_color=_btn_text_color((200, 0, 0)))
        self.button.set_controller(custom_controller)
        self.add.vertical_margin(14)
        self.text = self.add.label(
            'Undo - U\n'
            'Save and reset - Ctrl + S\n'
            'Print game FEN position - Ctrl + F\n'
            'Get current evaluation - Crtl + E\n'
            'Reverse board - Ctrl + R\n'
            'Hint - Crtl + H',
            font_size=24,
            border_color=(150, 150, 150),
            border_width=2,
            label_id='controls_text',
        )
        self.resized = False

    def run(self):
        self.enable()
        self.mainloop(self.screen, self.resize_event, fps_limit=120)

    def resize_event(self):
        try:
            w, h = _menu_window_size_fallback(self.screen)
            if (int(w), int(h)) != tuple(self.o_size):
                self.resized = True
                _apply_menu_resize(self, int(w), int(h))
        except Exception:
            pass

    def btn_apply(self, event, ob):
        applied = event.key == 27
        if applied:
            self.exit_menu()

    def exit_menu(self):
        self.disable()
        self.parent.enable()


class PerftMenu(pm.menu.Menu):
    def __init__(self, surface, parent, *args, **kwargs):
        if 'theme' not in kwargs or kwargs.get('theme') is None:
            kwargs['theme'] = _make_settings_theme(pm.themes.THEME_DARK)
        else:
            kwargs['theme'] = _make_settings_theme(kwargs.get('theme'))
        super().__init__(*args, **kwargs)

        self.screen = surface
        self.o_size = self.screen.get_size()
        self.parent = parent

        self._perft_queue: 'queue.Queue[tuple]' = queue.Queue()
        self._perft_thread: threading.Thread | None = None
        self._perft_stop: threading.Event | None = None
        self._perft_running = False

        custom_controller = Controller()
        custom_controller.apply = self.btn_apply

        self.add.vertical_margin(10)
        self.back = self.add.button(
            'Back',
            self.exit_menu,
            accept_kwargs=True,
            font_shadow=True,
            font_shadow_color=(80, 80, 80),
            font_background_color=(200, 0, 0),
            cursor=11,
            font_color=_btn_text_color((200, 0, 0)),
        )
        self.back.set_controller(custom_controller)

        self.add.vertical_margin(12)
        self.info = self.add.label(
            'Enter a FEN and a depth.\nDepth 1 = number of legal moves.',
            font_size=22,
        )

        self.add.vertical_margin(10)
        fen_chars = list('pnbrqkPNBRQK/12345678 wbkqWKQ-abcdefgh0123456789')
        self.fen = self.add.text_input(
            'FEN: ',
            default='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
            maxchar=140,
            copy_paste_enable=False,
            valid_chars=fen_chars,
            cursor=11,
        )
        self.fen.set_controller(custom_controller)

        self.paste_btn = self.add.button(
            'Paste FEN',
            self.paste_fen,
            accept_kwargs=True,
            font_shadow=True,
            font_shadow_color=(100, 100, 100),
            font_background_color=(100, 100, 100),
            cursor=11,
            font_color=(0, 0, 0),
        )
        self.paste_btn.set_controller(custom_controller)

        self.depth = self.add.text_input(
            'Depth: ',
            default='1',
            maxchar=3,
            input_type=pm.locals.INPUT_INT,
            copy_paste_enable=False,
            valid_chars=list('0123456789'),
            cursor=11,
        )
        self.depth.set_controller(custom_controller)

        self.run_btn = self.add.button(
            'Run',
            self.run_perft,
            accept_kwargs=True,
            font_shadow=True,
            font_shadow_color=(100, 100, 100),
            font_background_color=(0, 200, 0),
            cursor=11,
            font_color=(0, 0, 0),
        )
        self.run_btn.set_controller(custom_controller)

        self.result = self.add.label('', font_size=22)
        self.resized = False

    def run(self):
        self.enable()
        self.mainloop(self.screen, self.resize_event, fps_limit=120)

    def resize_event(self):
        self._poll_perft_updates()
        try:
            w, h = _menu_window_size_fallback(self.screen)
            if (int(w), int(h)) != tuple(self.o_size):
                self.resized = True
                _apply_menu_resize(self, int(w), int(h))
        except Exception:
            pass

    def _set_running_state(self, running: bool) -> None:
        self._perft_running = running
        try:
            self.run_btn.readonly = running
            self.fen.readonly = running
            self.depth.readonly = running
            self.paste_btn.readonly = running
        except Exception:
            pass

    def _poll_perft_updates(self) -> None:
        updated = False
        while True:
            try:
                msg = self._perft_queue.get_nowait()
            except Exception:
                break

            updated = True
            kind = msg[0]
            if kind == 'progress':
                done, total, nodes = msg[1], msg[2], msg[3]
                try:
                    self.result.set_title(f'Running: {done}/{total} root moves  |  Nodes so far: {nodes}')
                except Exception:
                    pass
            elif kind == 'done':
                nodes = msg[1]
                try:
                    self.result.set_title(f'Nodes: {nodes}')
                except Exception:
                    pass
                self._set_running_state(False)
            elif kind == 'error':
                err = msg[1]
                try:
                    self.result.set_title(str(err))
                except Exception:
                    pass
                self._set_running_state(False)

        if updated:
            try:
                self.force_surface_cache_update()
            except Exception:
                pass

    def paste_fen(self, **kwargs):
        if self._perft_running:
            return
        text = ''
        try:
            root = tk.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.update()
            root.destroy()
        except Exception as e:
            try:
                self.result.set_title('Clipboard paste failed: ' + str(e).splitlines()[0])
            except Exception:
                pass
            return

        if not isinstance(text, str):
            text = str(text)

        # Allow pasting whole JSON snippets like {"fen":"...","depth":1}
        m = re.search(r'"fen"\s*:\s*"([^"]+)"', text)
        if m:
            text = m.group(1)

        text = text.strip()
        try:
            self.fen.set_value(text)
            self.result.set_title('')
        except Exception:
            pass

    def run_perft(self, **kwargs):
        if self._perft_running:
            return

        fen = ''
        try:
            fen = str(self.fen.get_value()).strip()
            depth_s = str(self.depth.get_value()).strip()
            d = int(depth_s)
            if d < 0:
                raise ValueError('Depth must be >= 0')
        except Exception as e:
            try:
                self.result.set_title('Invalid depth: ' + str(e).splitlines()[0])
            except Exception:
                pass
            return

        self._perft_stop = threading.Event()
        self._set_running_state(True)
        try:
            self.result.set_title('Running...')
        except Exception:
            pass

        def progress_cb(done: int, total: int, nodes: int) -> None:
            try:
                self._perft_queue.put(('progress', done, total, nodes))
            except Exception:
                pass

        def worker() -> None:
            try:
                nodes = perft_nodes_from_fen_with_progress(
                    fen,
                    d,
                    progress_cb=progress_cb,
                    stop_event=self._perft_stop,
                )
                self._perft_queue.put(('done', nodes))
            except Exception as e:
                self._perft_queue.put(('error', 'Perft error: ' + str(e).splitlines()[0]))

        self._perft_thread = threading.Thread(target=worker, daemon=True)
        self._perft_thread.start()

    def btn_apply(self, event, ob):
        if event.key == 27:
            self.exit_menu()

    def exit_menu(self, **kwargs):
        try:
            if self._perft_stop is not None:
                self._perft_stop.set()
        except Exception:
            pass
        self.disable()
        self.parent.enable()


class GameReviewMenu(pm.menu.Menu):
    def __init__(self, surface, parent, engine, *args, **kwargs):
        if 'theme' not in kwargs or kwargs.get('theme') is None:
            kwargs['theme'] = _make_settings_theme(pm.themes.THEME_DARK)
        else:
            kwargs['theme'] = _make_settings_theme(kwargs.get('theme'))
        super().__init__(*args, **kwargs)
        self.screen = surface
        self.o_size = self.screen.get_size()
        self.parent = parent
        self.engine = engine
        custom_controller = Controller()
        custom_controller.apply = self.btn_apply

        self.add.vertical_margin(10)
        self.back = self.add.button('Back', self.exit_menu, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(80, 80, 80), font_background_color=(200, 0, 0), cursor=11,
                        font_color=_btn_text_color((200, 0, 0)))
        self.back.set_controller(custom_controller)

        self.status = self.add.label('', font_size=22)

        def parse_header_value(path: str, key: str) -> str | None:
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    for _ in range(80):
                        line = f.readline()
                        if not line:
                            break
                        s = line.strip()
                        if not (s.startswith('[') and s.endswith(']')):
                            # headers end at blank line; stop scanning once movetext starts
                            if s == '':
                                break
                            continue
                        m = re.match(r'^\[' + re.escape(key) + r'\s+"(.*)"\]$', s)
                        if m:
                            return m.group(1)
            except Exception:
                return None
            return None

        def parse_timestamp_from_filename(filename: str) -> str | None:
            # Expected: YYYYMMDD_HHMMSS_micro.pgn
            m = re.match(r'^(\d{8})_(\d{6})_\d+\.pgn$', filename)
            if not m:
                return None
            d = m.group(1)
            t = m.group(2)
            return f"{d[0:4]}-{d[4:6]}-{d[6:8]} {t[0:2]}:{t[2:4]}:{t[4:6]}"

        def make_game_label(filename: str, path: str) -> str:
            ts = parse_timestamp_from_filename(filename)
            if ts is None:
                # fall back to PGN Date header
                date = parse_header_value(path, 'Date')
                ts = date or filename

            ai_elo = (
                parse_header_value(path, 'AIElo')
                or parse_header_value(path, 'UCI_Elo')
                or parse_header_value(path, 'BlackElo')
                or ''
            )
            ai_elo = str(ai_elo).strip()
            if ai_elo and ai_elo != '?':
                return f"{ts}  —  AI {ai_elo}"
            return str(ts)

        games = []
        try:
            files = [f for f in os.listdir('data/games') if f.lower().endswith('.pgn')]
            files.sort(reverse=True)
            for f in files:
                path = os.path.join('data/games', f)
                label = make_game_label(f, path)
                games.append((label, path))
        except Exception:
            games = []

        if not games:
            self.add.label('No saved games found in data/games', font_size=22)
            self.selector = None
        else:
            self.add.label('Select a game:', font_size=24)
            self.selector = self.add.dropselect('', games, 0, selection_box_width=520,
                                                selection_option_font_size=18,
                                                placeholder='Select PGN', selection_box_height=12, cursor=11)
            self.selector.set_controller(custom_controller)

            self.load_btn = self.add.button('Load', self.load_game, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(0, 200, 0), cursor=11,
                        font_color=(0, 0, 0))
            self.load_btn.set_controller(custom_controller)

            self.copy_btn = self.add.button('Copy PGN', self.copy_pgn, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(100, 100, 100), cursor=11,
                        font_color=(0, 0, 0))
            self.copy_btn.set_controller(custom_controller)

        self.resized = False

    def run(self):
        self.enable()
        self.mainloop(self.screen, self.resize_event, fps_limit=120)

    def resize_event(self):
        try:
            w, h = _menu_window_size_fallback(self.screen)
            if (int(w), int(h)) != tuple(self.o_size):
                self.resized = True
                _apply_menu_resize(self, int(w), int(h))
        except Exception:
            pass

    def load_game(self, **kwargs):
        if self.selector is None:
            return
        try:
            path = self.selector.get_value()[0][1]
        except Exception:
            return

        ok = False
        try:
            ok = bool(self.engine.start_review(path))
        except Exception:
            ok = False

        if not ok:
            msg = 'Failed to load PGN.'
            try:
                err = getattr(self.engine, 'review_last_error', '')
                if err:
                    msg = 'Failed to load PGN: ' + str(err).splitlines()[0]
            except Exception:
                pass
            try:
                self.status.set_title(msg)
            except Exception:
                pass
            return

        self.disable()
        self.parent.disable()

    def copy_pgn(self, **kwargs):
        if self.selector is None:
            return
        try:
            path = self.selector.get_value()[0][1]
        except Exception:
            return
        try:
            text = open(path, 'r', encoding='utf-8', errors='ignore').read()
        except Exception as e:
            try:
                self.status.set_title('Failed to read PGN: ' + str(e).splitlines()[0])
            except Exception:
                pass
            return

        try:
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()  # keep clipboard after window closes
            root.destroy()
            try:
                self.status.set_title('PGN copied to clipboard')
            except Exception:
                pass
        except Exception as e:
            try:
                self.status.set_title('Clipboard copy failed: ' + str(e).splitlines()[0])
            except Exception:
                pass
            return

    def btn_apply(self, event, ob):
        applied = event.key == 27
        if applied:
            self.exit_menu()

    def exit_menu(self, **kwargs):
        self.disable()
        self.parent.enable()



class EndGameMenu(pm.menu.Menu):
    def __init__(self, surface, parent,  *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen = surface
        self.o_size = self.screen.get_size()
        self.parent = parent
        custom_controller = Controller()
        custom_controller.apply = self.btn_apply
        self.button = self.add.button('View PGN file', self.view_file, accept_kwargs=True, font_shadow=True,
                                      font_shadow_color=(100, 100, 100), font_background_color=(20, 20, 200), cursor=11,
                                      font_color=(0, 0, 0))
        self.button = self.add.button('Reset', self.exit_menu, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(255, 0, 0), cursor=11,
                        font_color=(0, 0, 0))
        self.button.set_controller(custom_controller)
        self.resized = False
        self.file_path = None

    def run(self):
        self.enable()
        self.mainloop(self.screen, self.resize_event, fps_limit=120)

    def set_file_path_and_text(self, path, text):
        self.add.label(text, max_char=1000)
        self.file_path = path

    def view_file(self):
        os.system('notepad ' + self.file_path)

    def resize_event(self):
        try:
            w, h = _menu_window_size_fallback(self.screen)
            if (int(w), int(h)) != tuple(self.o_size):
                self.resized = True
                _apply_menu_resize(self, int(w), int(h))
        except Exception:
            pass

    def btn_apply(self, event, ob):
        applied = event.key == 27
        if applied:
            self.exit_menu()

    def exit_menu(self):
        self.disable()
        if self.resized:
            self.parent.check_resize()



