import pygame as pg
import sys
import pygame_menu as pm
class Settings:
    def __init__(self, screen, size):
        self.screen = screen
        self.size = size
        self.on = True

    def run(self):
        while self.on:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    pg.quit()
                    sys.exit()
                elif event.type == pg.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        print('click')

            self.draw()
            pg.display.flip()

    def draw(self):
        pg.draw.rect(self.screen,(128,128,128),(0,0,self.size[0], self.size[1]))


class SettingsMenu(pm.menu.Menu):
    def __init__(self, surface, parent,  *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen = surface
        self.parent = parent
        self.add.button('Back', self.exit_menu, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(255, 0, 0), cursor=11, font_color=(0,0,0))
        self.pieces = [
            ('Alpha', 'alpha'),
            ('Cardinal', 'cardinal'),
            ('Chess7', 'chess7'),
            ('Chessicons', 'chessicons'),
            ('Horsey', 'horsey'),
            ('Chessmonk', 'chessmonk'),
        ]
        self.ai_strength = [
            ('0', 0),
            ('1', 1),
            ('2', 2),
            ('3', 3),
            ('4', 4),
            ('5', 5),
            ('6', 6),
            ('7', 7),
            ('8', 8),
            ('9', 9),
            ('10', 10),
        ]
        self.board_background = [
            ('Maple', 'maple.jpg'),
            ('Cherry', 'cherry_800x.jpg'),
            ('Sand', 'sand.jpg'),
            ('Coffee', 'coffee-beans.jpg'),
            ('Marble', 'marble.png'),
        ]
        self.modes = [
            ('Player vs AI', 'pvai'),
            ('Player vs Player', 'pvp'),
            ('AI vs AI', 'aivai'),
        ]
        self.label1 = self.add.label('Game Mode')
        self.mode = self.add.dropselect('', self.modes, 0, selection_box_width=350,
                                            selection_option_font_size=None, placeholder='Select Mode',
                                            selection_box_height=6)
        self.label = self.add.label('Pieces:')
        self.piece = self.add.dropselect('', self.pieces, 5, selection_box_width=350, selection_option_font_size=None, placeholder='Select Piece Type', selection_box_height=6)
        self.label1 = self.add.label('Board Style')
        self.board = self.add.dropselect('', self.board_background, 4, selection_box_width=350,
                                            selection_option_font_size=None, placeholder='Select Board Style',
                                            selection_box_height=6)
        self.label1 = self.add.label('AI strength')
        self.strength = self.add.dropselect('', self.ai_strength, 0, selection_box_width=350, selection_option_font_size=None, placeholder='Select Strength', selection_box_height=6)

        self.confirms = self.add.button('Confirm', self.confirm, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(0, 200, 0), cursor=11, font_color=(0,0,0))

    def run(self):
        self.enable()
        self.mainloop(self.screen)

    def confirm(self):
        chosen = self.piece.get_value()[0][1]
        self.parent.change_pieces(chosen)
        self.parent.change_mode(self.mode.get_value()[0][1])
        self.parent.change_board(self.board.get_value()[0][1])
        self.parent.change_ai_strength(self.strength.get_value()[0][1])
        self.exit_menu()

    def exit_menu(self):
        self.disable()