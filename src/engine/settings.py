import os
import re
import tkinter as tk

import pygame_menu as pm
from pygame_menu.controls import Controller


class SettingsMenu(pm.menu.Menu):
    def __init__(self, surface, parent,  *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen = surface
        self.o_size = self.screen.get_size()
        self.parent = parent
        custom_controller = Controller()
        custom_controller.apply = self.btn_apply
        self.back = self.add.button('Back', self.exit_menu, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(255, 0, 0), cursor=11, font_color=(0, 0, 0))
        self.back.set_controller(custom_controller)
        view = self.add.button('View Controls', self.view_controls, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(100, 100, 100), cursor=11,
                        font_color=(0, 0, 0))
        view.set_controller(custom_controller)

        review = self.add.button('Review Games', self.view_games, accept_kwargs=True, font_shadow=True,
                font_shadow_color=(100, 100, 100), font_background_color=(100, 100, 100), cursor=11,
                font_color=(0, 0, 0))
        review.set_controller(custom_controller)
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

        # 21 entries to stay compatible with older saved indices (0..20)
        self.ai_elo = [(str(600 + i * 120), 600 + i * 120) for i in range(21)]
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
        self.label1 = self.add.label('Game Mode:')
        self.mode = self.add.dropselect('', self.modes, int(lines[0].replace('\n', '')), selection_box_width=350,
                                            selection_option_font_size=None, placeholder='Select Mode',
                                            selection_box_height=6, cursor=11)
        self.mode.set_controller(custom_controller)

        self.label3 = self.add.label('Flip Board:')
        self.flip = self.add.toggle_switch('', int(lines[4]), cursor=11)
        self.flip.set_controller(custom_controller)

        self.label3 = self.add.label('Sounds:')
        self.sounds = self.add.toggle_switch('', int(lines[5]), cursor=11)
        self.sounds.set_controller(custom_controller)

        self.label3 = self.add.label('Eval Bar:')
        self.eval_bar = self.add.toggle_switch('', int(lines[7]), cursor=11)
        self.eval_bar.set_controller(custom_controller)

        self.label3 = self.add.label('Movement:')
        self.movement = self.add.dropselect('', self.movement_modes, int(lines[6].replace('\n', '')),
                            selection_box_width=350, selection_option_font_size=None,
                            placeholder='Select Movement', selection_box_height=6, cursor=11)
        self.movement.set_controller(custom_controller)


        self.label3 = self.add.label('Pieces:')
        self.piece = self.add.dropselect('', self.pieces, int(lines[1].replace('\n', '')), selection_box_width=350, selection_option_font_size=None, placeholder='Select Piece Type', selection_box_height=6, cursor=11)
        self.piece.set_controller(custom_controller)

        self.label4 = self.add.label('Board Style:')
        self.board = self.add.dropselect('', self.board_background, int(lines[2].replace('\n', '')), selection_box_width=350,
                                            selection_option_font_size=None, placeholder='Select Board Style',
                                            selection_box_height=6, cursor=11)
        self.board.set_controller(custom_controller)

        self.label5 = self.add.label('AI Elo:')
        self.strength = self.add.dropselect('', self.ai_elo, int(lines[3].replace('\n', '')), selection_box_width=350, selection_option_font_size=None, placeholder='Select Elo', selection_box_height=6, cursor=11)
        self.strength.set_controller(custom_controller)

        self.confirms = self.add.button('Confirm', self.confirm, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(0, 200, 0), cursor=11, font_color=(0,0,0))
        self.confirms.set_controller(custom_controller)

        self.resized = False

    def run(self):
        self.enable()
        self.mainloop(self.screen, self.resize_event, fps_limit=120)

    def resize_event(self):
        if self.screen.get_size() != self.o_size:
            self.resized = True
            self.resize(self.screen.get_width(), self.screen.get_height())
            self.render()
            self.o_size = self.screen.get_size()

    def confirm(self):
        chosen = self.piece.get_value()[0][1]
        self.parent.change_pieces(chosen)
        self.parent.change_mode(self.mode.get_value()[0][1])
        self.parent.change_board(self.board.get_value()[0][1])
        self.parent.change_ai_elo(self.strength.get_value()[0][1])
        self.parent.flip_enable(int(self.flip.get_value()))
        self.parent.sounds_enable(int(self.sounds.get_value()))
        self.parent.set_movement_mode(self.movement.get_value()[0][1])
        self.parent.set_eval_bar_enabled(bool(int(self.eval_bar.get_value())))
        with open('data/settings/settings.txt', 'w') as file:
            file.writelines(str(self.mode.get_index())+'\n')
            file.writelines(str(self.piece.get_index())+'\n')
            file.writelines(str(self.board.get_index())+'\n')
            file.writelines(str(self.strength.get_index())+'\n')
            file.writelines(str(int(self.flip.get_value()))+'\n')
            file.writelines(str(int(self.sounds.get_value()))+'\n')
            file.writelines(str(self.movement.get_index())+'\n')
            file.writelines(str(int(self.eval_bar.get_value()))+'\n')
        self.mode.get_index()
        self.exit_menu()

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
        super().__init__(*args, **kwargs)
        self.screen = surface
        self.o_size = self.screen.get_size()
        self.parent = parent
        custom_controller = Controller()
        custom_controller.apply = self.btn_apply
        self.button = self.add.button('Back', self.exit_menu, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(255, 0, 0), cursor=11,
                        font_color=(0, 0, 0))
        self.button.set_controller(custom_controller)
        self.text = self.add.label('Undo - U\nSave and reset - Ctrl + S\nPrint game FEN position - Ctrl + F\nGet current evaluation - Crtl + E\nReverse board - Ctrl + R\nHint - Crtl + H', font_size=20, border_color=(150,150,150), border_width=3, label_id='123')
        self.resized = False

    def run(self):
        self.enable()
        self.mainloop(self.screen, self.resize_event, fps_limit=120)

    def resize_event(self):
        if self.screen.get_size() != self.o_size:
            self.resized = True
            self.resize(self.screen.get_width(), self.screen.get_height())
            self.render()
            self.force_surface_cache_update()
            self.o_size = self.screen.get_size()

    def btn_apply(self, event, ob):
        applied = event.key == 27
        if applied:
            self.exit_menu()

    def exit_menu(self):
        self.disable()
        self.parent.enable()


class GameReviewMenu(pm.menu.Menu):
    def __init__(self, surface, parent, engine, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen = surface
        self.o_size = self.screen.get_size()
        self.parent = parent
        self.engine = engine
        custom_controller = Controller()
        custom_controller.apply = self.btn_apply

        self.back = self.add.button('Back', self.exit_menu, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(255, 0, 0), cursor=11,
                        font_color=(0, 0, 0))
        self.back.set_controller(custom_controller)

        self.status = self.add.label('', font_size=18)

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
        if self.screen.get_size() != self.o_size:
            self.resized = True
            self.resize(self.screen.get_width(), self.screen.get_height())
            self.render()
            self.force_surface_cache_update()
            self.o_size = self.screen.get_size()

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
        if self.screen.get_size() != self.o_size:
            self.resized = True
            self.resize(self.screen.get_width(), self.screen.get_height())
            self.render()
            self.force_surface_cache_update()
            self.o_size = self.screen.get_size()

    def btn_apply(self, event, ob):
        applied = event.key == 27
        if applied:
            self.exit_menu()

    def exit_menu(self):
        self.disable()
        if self.resized:
            self.parent.check_resize()



