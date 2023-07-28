import pygame_menu as pm


class SettingsMenu(pm.menu.Menu):
    def __init__(self, surface, parent, piece_type, strength, style,  *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen = surface
        self.o_size = self.screen.get_size()
        self.parent = parent
        # try:
        #     self.parent.change_pieces(piece_type)
        # except:
        #     pass
        self.add.button('Back', self.exit_menu, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(255, 0, 0), cursor=11, font_color=(0,0,0))
        self.pieces = [
            ('Alpha', 'alpha'),
            ('Cardinal', 'cardinal'),
            ('Chessicons', 'chessicons'),
            ('Horsey', 'horsey'),
            ('Gioco', 'gioco'),
            ('Maya', 'maya'),
            ('Kosal', 'kosal'),
            ('Riohacha', 'riohacha'),
            ('Chessmonk', 'chessmonk'),
        ]

        # Resize event creates new settings instance
        index = 0
        for i, j in enumerate(self.pieces):
            if j[0] == piece_type.capitalize():
                index = i

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
        style_i = 0
        for i, j in enumerate(self.board_background):
            if j[1] == style:
                style_i = i

        self.modes = [
            ('Player vs AI', 'pvai'),
            ('Player vs Player', 'pvp'),
            ('AI vs AI', 'aivai'),
        ]
        self.label1 = self.add.label('Game Mode')
        self.mode = self.add.dropselect('', self.modes, 0, selection_box_width=350,
                                            selection_option_font_size=None, placeholder='Select Mode',
                                            selection_box_height=6)

        self.label2 = self.add.label('Pieces:')
        self.piece = self.add.dropselect('', self.pieces, index, selection_box_width=350, selection_option_font_size=None, placeholder='Select Piece Type', selection_box_height=6)

        self.label3 = self.add.label('Board Style')
        self.board = self.add.dropselect('', self.board_background, style_i, selection_box_width=350,
                                            selection_option_font_size=None, placeholder='Select Board Style',
                                            selection_box_height=6)

        self.label4 = self.add.label('AI strength')
        self.strength = self.add.dropselect('', self.ai_strength, strength, selection_box_width=350, selection_option_font_size=None, placeholder='Select Strength', selection_box_height=6)

        self.confirms = self.add.button('Confirm', self.confirm, accept_kwargs=True, font_shadow=True,
                        font_shadow_color=(100, 100, 100), font_background_color=(0, 200, 0), cursor=11, font_color=(0,0,0))
        self.resized = False

    def run(self):
        self.enable()
        self.mainloop(self.screen, self.update_height)

    def update_height(self):
        if self.screen.get_size() != self.o_size:
            self.resized = True
            self.resize(self.screen.get_width(), self.screen.get_height())
            self.render()

    def confirm(self):
        chosen = self.piece.get_value()[0][1]
        self.parent.change_pieces(chosen)
        self.parent.change_mode(self.mode.get_value()[0][1])
        self.parent.change_board(self.board.get_value()[0][1])
        self.parent.change_ai_strength(self.strength.get_value()[0][1])
        self.exit_menu()

    def exit_menu(self):
        self.disable()
        if self.resized:
            self.parent.check_resize()