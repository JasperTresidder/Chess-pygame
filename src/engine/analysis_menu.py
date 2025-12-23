import os
import re
import pygame_menu as pm
from pygame_menu.controls import Controller

from src.engine.settings import _make_settings_theme, _btn_text_color, _apply_menu_resize, _menu_window_size_fallback


class SavedAnalysisMenu(pm.menu.Menu):
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

        self.status = self.add.label('', font_size=22)

        analyses = []
        try:
            os.makedirs('data/analysis', exist_ok=True)
        except Exception:
            pass

        def label_for_file(filename: str, path: str) -> str:
            # Try timestamped name: analysis_YYYYMMDD_HHMMSS.json
            m = re.match(r'^analysis_(\d{8})_(\d{6})\.json$', filename)
            if m:
                d = m.group(1)
                t = m.group(2)
                return f"{d[0:4]}-{d[4:6]}-{d[6:8]} {t[0:2]}:{t[2:4]}:{t[4:6]}"
            return filename

        try:
            files = [f for f in os.listdir('data/analysis') if f.lower().endswith('.json')]
            files.sort(reverse=True)
            for f in files:
                path = os.path.join('data/analysis', f)
                analyses.append((label_for_file(f, path), path))
        except Exception:
            analyses = []

        if not analyses:
            self.add.label('No saved analyses found in data/analysis', font_size=22)
            self.selector = None
        else:
            self.add.label('Select an analysis:', font_size=24)
            self.selector = self.add.dropselect(
                '',
                analyses,
                0,
                selection_box_width=520,
                selection_option_font_size=18,
                placeholder='Select analysis',
                selection_box_height=12,
                cursor=11,
            )
            self.selector.set_controller(custom_controller)

            self.load_btn = self.add.button(
                'Load',
                self.load_analysis,
                accept_kwargs=True,
                font_shadow=True,
                font_shadow_color=(100, 100, 100),
                font_background_color=(0, 200, 0),
                cursor=11,
                font_color=(0, 0, 0),
            )
            self.load_btn.set_controller(custom_controller)

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

    def load_analysis(self, **kwargs):
        if self.selector is None:
            return
        try:
            path = self.selector.get_value()[0][1]
        except Exception:
            return

        ok = False
        try:
            ok = bool(self.engine.start_analysis_from_file(path))
        except Exception:
            ok = False

        if not ok:
            try:
                msg = 'Failed to load analysis.'
                err = getattr(self.engine, 'analysis_last_error', '')
                if err:
                    msg = 'Failed to load analysis: ' + str(err).splitlines()[0]
                self.status.set_title(msg)
            except Exception:
                pass
            return

        # Exit menus back to engine loop.
        try:
            self.disable()
        except Exception:
            pass
        try:
            self.parent.disable()
        except Exception:
            pass

    def btn_apply(self, event, ob):
        if event.key == 27:
            self.exit_menu()

    def exit_menu(self, **kwargs):
        self.disable()
        try:
            self.parent.enable()
        except Exception:
            pass
