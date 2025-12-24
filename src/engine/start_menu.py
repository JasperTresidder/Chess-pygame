import pygame_menu as pm
from pygame_menu.controls import Controller

from src.engine.settings import _make_settings_theme, _apply_menu_resize, _menu_window_size_fallback


class StartMenu(pm.menu.Menu):
    @staticmethod
    def _parse_tc(value: str) -> tuple[int, int]:
        s = str(value or '').strip().replace(' ', '')
        if not s:
            return 5, 0
        if '|' in s:
            a, b = s.split('|', 1)
        else:
            a, b = s, '0'
        try:
            mins = int(a)
        except Exception:
            mins = 5
        try:
            inc = int(b) if b != '' else 0
        except Exception:
            inc = 0
        return max(0, mins), max(0, inc)

    def __init__(self, surface, parent, *args, **kwargs):
        if 'theme' not in kwargs or kwargs.get('theme') is None:
            kwargs['theme'] = _make_settings_theme(pm.themes.THEME_DARK)
        else:
            kwargs['theme'] = _make_settings_theme(kwargs.get('theme'))

        super().__init__(*args, **kwargs)
        self.screen = surface
        self.parent = parent
        try:
            self.o_size = self.screen.get_size()
        except Exception:
            self.o_size = (0, 0)
        self.resized = False

        custom_controller = Controller()
        custom_controller.apply = self._btn_apply

        self.modes = [
            ('Player vs AI', 'pvai'),
            ('Player vs Player', 'pvp'),
            ('AI vs AI', 'aivai'),
        ]
        self.colours = [
            ('White', 'w'),
            ('Black', 'b'),
        ]
        self.time_presets = [
            ('1 min', '1'),
            ('5 min', '5'),
            ('10 min', '10'),
            ('15 min', '15'),
            ('30 min', '30'),
        ]
        # AI strength presets (Elo). Applies to PvAI and AI vs AI.
        self.elo_presets = [
            ('600', '600'),
            ('800', '800'),
            ('1000', '1000'),
            ('1200', '1200'),
            ('1400', '1400'),
            ('1600', '1600'),
            ('1800', '1800'),
            ('2000', '2000'),
            ('2200', '2200'),
            ('2400', '2400'),
            ('2600', '2600'),
            ('2800', '2800'),
            ('3000', '3000'),
        ]
        self.increment_presets = [
            ('0 sec', '0'),
            ('1 sec', '1'),
            ('2 sec', '2'),
            ('3 sec', '3'),
            ('5 sec', '5'),
            ('10 sec', '10'),
            ('15 sec', '15'),
            ('30 sec', '30'),
        ]

        try:
            default_mode = 'pvai' if getattr(parent, 'player_vs_ai', False) else ('aivai' if getattr(parent, 'ai_vs_ai', False) else 'pvp')
        except Exception:
            default_mode = 'pvai'
        try:
            default_colour = getattr(parent, 'player_colour', 'w')
        except Exception:
            default_colour = 'w'
        try:
            default_tc = str(getattr(parent, 'time_control', '5|0'))
        except Exception:
            default_tc = '5|0'
        try:
            default_elo = int(getattr(parent, 'ai_elo', 800))
        except Exception:
            default_elo = 800

        default_mins, default_inc = self._parse_tc(default_tc)
        default_tc = f"{default_mins}|{default_inc}"

        self.add.vertical_margin(18)
        self.add.label('Game Mode', font_size=26)
        self.mode = self.add.dropselect(
            '',
            self.modes,
            default=next((i for i, v in enumerate([m[1] for m in self.modes]) if v == default_mode), 0),
            selection_box_width=max(240, int(self.screen.get_width() * 0.40)),
            selection_box_height=6,
            cursor=11,
        )
        self.mode.set_controller(custom_controller)

        self.add.vertical_margin(12)
        self.add.label('Your Color (PvAI)', font_size=26)
        self.colour = self.add.dropselect(
            '',
            self.colours,
            default=0 if default_colour != 'b' else 1,
            selection_box_width=max(240, int(self.screen.get_width() * 0.40)),
            selection_box_height=4,
            cursor=11,
        )
        self.colour.set_controller(custom_controller)

        self.add.vertical_margin(12)
        self.add.label('AI Strength (Elo)', font_size=26)
        self.ai_elo = self.add.dropselect(
            '',
            self.elo_presets,
            default=next((i for i, v in enumerate([e[1] for e in self.elo_presets]) if int(v) == int(default_elo)), 1),
            selection_box_width=max(240, int(self.screen.get_width() * 0.40)),
            selection_box_height=7,
            cursor=11,
        )
        self.ai_elo.set_controller(custom_controller)

        self.add.vertical_margin(12)
        self.add.label('Time Control', font_size=26)
        self.preset = self.add.dropselect(
            '',
            self.time_presets,
            default=next((i for i, v in enumerate([t[1] for t in self.time_presets]) if v == str(default_mins)), 1),
            onchange=self._on_preset_change,
            selection_box_width=max(240, int(self.screen.get_width() * 0.40)),
            selection_box_height=6,
            cursor=11,
        )
        self.preset.set_controller(custom_controller)

        self.add.vertical_margin(10)
        self.add.label('Increment (sec)', font_size=26)
        self.increment = self.add.dropselect(
            '',
            self.increment_presets,
            default=next((i for i, v in enumerate([t[1] for t in self.increment_presets]) if v == str(default_inc)), 0),
            onchange=self._on_increment_change,
            selection_box_width=max(240, int(self.screen.get_width() * 0.40)),
            selection_box_height=6,
            cursor=11,
        )
        self.increment.set_controller(custom_controller)

        self.time_control = self.add.text_input(
            'min|inc: ',
            default=default_tc,
            maxchar=12,
            copy_paste_enable=False,
            valid_chars=list('0123456789| '),
            cursor=11,
        )
        self.time_control.set_controller(custom_controller)

        self.add.vertical_margin(14)
        self.start_btn = self.add.button('Start', self._start, font_background_color=(0, 200, 0), font_color=(0, 0, 0), cursor=11)
        self.start_btn.set_controller(custom_controller)

        # Extra entry points
        self.add.vertical_margin(8)
        self.review_btn = self.add.button(
            'Game Review',
            self._open_game_review,
            font_background_color=(100, 100, 100),
            font_color=(0, 0, 0),
            cursor=11,
        )
        self.review_btn.set_controller(custom_controller)

        self.puzzle_rush_btn = self.add.button(
            'Puzzle Rush',
            self._start_puzzle_rush,
            font_background_color=(100, 100, 100),
            font_color=(0, 0, 0),
            cursor=11,
        )
        self.puzzle_rush_btn.set_controller(custom_controller)

        self.analysis_btn = self.add.button(
            'Analysis Mode',
            self._start_analysis_mode,
            font_background_color=(100, 100, 100),
            font_color=(0, 0, 0),
            cursor=11,
        )
        self.analysis_btn.set_controller(custom_controller)

    def _on_preset_change(self, selected, value) -> None:
        """Keep the min|inc input synced when the user picks minutes."""
        mins_s = ''
        try:
            mins_s = str(value or '').strip()
        except Exception:
            mins_s = ''

        # Some pygame-menu versions pass `selected` as list/tuple containing (label, value).
        if not mins_s:
            try:
                if isinstance(selected, (list, tuple)) and selected:
                    maybe = selected[0]
                    if isinstance(maybe, (list, tuple)) and len(maybe) >= 2:
                        mins_s = str(maybe[1]).strip()
            except Exception:
                mins_s = ''

        if not mins_s:
            return

        inc_s = '0'
        try:
            inc_s = str(self.increment.get_value()[0][1]).strip()
        except Exception:
            inc_s = '0'

        try:
            self.time_control.set_value(f"{mins_s}|{inc_s}")
        except Exception:
            pass

    def _on_increment_change(self, selected, value) -> None:
        """Keep the min|inc input synced when the user picks increment."""
        inc_s = ''
        try:
            inc_s = str(value or '').strip()
        except Exception:
            inc_s = ''

        if not inc_s:
            try:
                if isinstance(selected, (list, tuple)) and selected:
                    maybe = selected[0]
                    if isinstance(maybe, (list, tuple)) and len(maybe) >= 2:
                        inc_s = str(maybe[1]).strip()
            except Exception:
                inc_s = ''

        if not inc_s:
            return

        mins_s = '5'
        try:
            mins_s = str(self.preset.get_value()[0][1]).strip()
        except Exception:
            mins_s = '5'

        try:
            self.time_control.set_value(f"{mins_s}|{inc_s}")
        except Exception:
            pass

    def run(self):
        self.enable()
        self.mainloop(self.screen, self.resize_event, fps_limit=120)

    def resize_event(self):
        try:
            w, h = _menu_window_size_fallback(self.screen)
            if (int(w), int(h)) != tuple(getattr(self, 'o_size', (0, 0))):
                self.resized = True
                _apply_menu_resize(self, int(w), int(h))
        except Exception:
            pass

    def _open_game_review(self, **kwargs):
        # Open the existing review-games menu.
        try:
            from src.engine.settings import GameReviewMenu
            review_menu = GameReviewMenu(
                title='Game Review',
                width=self.screen.get_width(),
                height=self.screen.get_height(),
                surface=self.screen,
                parent=self,
                engine=self.parent,
                theme=pm.themes.THEME_DARK,
            )
            self.disable()
            review_menu.run()
        except Exception:
            # Never crash the start menu.
            try:
                self.enable()
            except Exception:
                pass

    def _start_analysis_mode(self, **kwargs):
        try:
            if hasattr(self.parent, 'start_analysis_new'):
                self.parent.start_analysis_new()
                self.disable()
        except Exception:
            pass

    def _start_puzzle_rush(self, **kwargs):
        try:
            if hasattr(self.parent, 'start_puzzle_rush_new'):
                self.parent.start_puzzle_rush_new()
                self.disable()
        except Exception:
            pass

    @staticmethod
    def _mode_to_settings_index(mode: str) -> int:
        m = str(mode or '').strip().lower()
        if m == 'pvp':
            return 1
        if m == 'aivai':
            return 2
        return 0  # pvai

    @staticmethod
    def _normalize_elo(elo: int | None) -> int:
        try:
            e = int(elo) if elo is not None else 800
        except Exception:
            e = 800
        return max(600, min(3000, e))

    def _persist_start_configuration(self, mode: str, colour: str, tc: str, elo: int | None) -> None:
        """Save chosen start options as new defaults in data/settings/settings.txt.

        Keeps the existing settings file schema (indices for dropdowns) so SettingsMenu
        continues to load without changes.
        """
        try:
            with open('data/settings/settings.txt', 'r') as f:
                lines = f.readlines()
        except Exception:
            lines = []

        # Ensure minimum expected lines exist (0..8 used by SettingsMenu).
        while len(lines) < 9:
            lines.append('0\n')

        # Mode index (0 pvai, 1 pvp, 2 aivai)
        lines[0] = str(self._mode_to_settings_index(mode)) + '\n'

        # AI strength as Elo value (not an index), matches SettingsMenu + avoids indexing bugs.
        lines[3] = str(self._normalize_elo(elo)) + '\n'

        # Time control string stored directly
        mins, inc = self._parse_tc(tc)
        lines[8] = f"{mins}|{inc}\n"

        # Player colour (new optional line; ignored by older readers)
        pc = 'w'
        try:
            pc = str(colour or 'w').strip().lower()
        except Exception:
            pc = 'w'
        pc = 'b' if pc.startswith('b') else 'w'

        # Backward compatibility: old files stored player colour at index 9.
        # New format stores review analysis depth at index 9 and player colour at index 10.
        try:
            if len(lines) >= 10 and str(lines[9]).strip().lower() in ('w', 'b'):
                # Migrate: keep the old colour, insert default depth.
                old_pc = str(lines[9]).strip().lower()
                lines[9] = '10\n'
                if len(lines) >= 11:
                    lines[10] = old_pc + '\n'
                else:
                    lines.append(old_pc + '\n')
        except Exception:
            pass

        # Ensure review-depth line exists.
        while len(lines) < 10:
            lines.append('10\n')

        # Write player colour as the final line.
        if len(lines) >= 11:
            lines[10] = pc + '\n'
        else:
            lines.append(pc + '\n')

        try:
            with open('data/settings/settings.txt', 'w') as f:
                f.writelines(lines)
        except Exception:
            pass

    def _btn_apply(self, event, ob):
        # Esc closes (same as start) to avoid trapping.
        if event.key == 27:
            self._start()

    def _start(self, **kwargs):
        try:
            mode = self.mode.get_value()[0][1]
        except Exception:
            mode = 'pvai'
        try:
            colour = self.colour.get_value()[0][1]
        except Exception:
            colour = 'w'

        try:
            elo = int(self.ai_elo.get_value()[0][1])
        except Exception:
            elo = None

        # Prefer explicit text input; fallback to preset.
        tc = ''
        try:
            tc = str(self.time_control.get_value()).strip()
        except Exception:
            tc = ''
        if not tc:
            try:
                tc = str(self.preset.get_value()[0][1]).strip()
            except Exception:
                tc = '5|0'

        try:
            self.parent.change_mode(mode)
        except Exception:
            pass
        try:
            self.parent.set_player_colour(colour)
        except Exception:
            pass
        try:
            self.parent.set_time_control(tc)
        except Exception:
            pass
        try:
            if elo is not None:
                self.parent.change_ai_elo(int(elo))
        except Exception:
            pass

        # Persist the chosen configuration as defaults.
        try:
            self._persist_start_configuration(mode=mode, colour=colour, tc=tc, elo=elo)
        except Exception:
            pass

        try:
            self.parent.reset_game()
        except Exception:
            pass

        self.disable()
