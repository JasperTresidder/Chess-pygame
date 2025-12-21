'''
Common class for all pieces.
'''

import pygame as pg
from pygame import sprite

class Piece(sprite.Sprite):
    """Base class for all pieces"""
    def __init__(self):
        """Initialize the piece"""
        super().__init__()
        self.piece_set = 'chessmonk'
        self.dead = False
        self.position = None
        self.colour = None
        self.piece = None
        self.legal_directions = None
        self.checks = []
        self.legal_positions = []
        # Populated by Engine.update_legal_moves() from python-chess.
        # Contains (dx, dy) moves that are captures (including en-passant which can land on an empty square).
        self.legal_captures = set()
        self.pin_lines = set()
        self.legal_directions = None
        self.clicked = False
        self.size = 100
        self.is_alive = True
        self.has_moved = False
        self.picture = None

    def click(self):
        """Piece is currently being clicked"""
        self.clicked = True

    def show_legal_moves(self, screen, offset, turn, flipped, board):
        """If piece is clicked show legal moves"""

        # Chess.com-style move dots (smaller + grayer + semi-transparent).
        # Use RGBA on an SRCALPHA surface so alpha blending works reliably.
        dot_dark = (80, 80, 80, 140)
        dot_light = (160, 160, 160, 140)
        dot_radius = max(3, int(self.size * 0.14))
        ring_radius = max(dot_radius + 6, int(self.size * 0.42))
        ring_thickness = max(3, int(self.size * 0.09))

        for dx, dy in self.legal_positions:
            if turn != self.colour[0]:
                continue

            r = self.position[0] + dy
            c = self.position[1] + dx
            if not (-1 < r < 8 and -1 < c < 8):
                continue

            # Capture detection comes only from python-chess (needed for en-passant).
            is_capture = (dx, dy) in self.legal_captures

            if not flipped:
                draw_r, draw_c = r, c
            else:
                draw_r, draw_c = -r + 7, -c + 7

            if not is_capture:
                dot_color = dot_dark if ((draw_r + draw_c + 1) % 2 == 0) else dot_light
                dot_surf = pg.Surface((dot_radius * 2 + 2, dot_radius * 2 + 2), pg.SRCALPHA)
                pg.draw.circle(dot_surf, dot_color, (dot_radius + 1, dot_radius + 1), dot_radius)
                screen.blit(
                    dot_surf,
                    (
                        draw_c * self.size + offset[0] + self.size / 2 - (dot_radius + 1),
                        draw_r * self.size + offset[1] + self.size / 2 - (dot_radius + 1),
                    ),
                )
            else:
                ring_color = dot_dark if ((draw_r + draw_c + 1) % 2 == 0) else dot_light
                ring_size = ring_radius * 2 + ring_thickness * 2 + 2
                ring_surf = pg.Surface((ring_size, ring_size), pg.SRCALPHA)
                center = (ring_size // 2, ring_size // 2)
                pg.draw.circle(ring_surf, ring_color, center, ring_radius, width=ring_thickness)
                screen.blit(
                    ring_surf,
                    (
                        draw_c * self.size + offset[0] + self.size / 2 - ring_size / 2,
                        draw_r * self.size + offset[1] + self.size / 2 - ring_size / 2,
                    ),
                )

    def update_legal_moves(self, board, eps=None, captures=False):
        """Refresh legal moves"""
        pass

    def check(self, board):
        """Can piece capture the opponents king"""
        self.checks = []
        x = self.position[1]
        y = self.position[0]
        for direction in self.legal_directions:
            temp_check = []
            for i in range(1, 9):
                try:
                    piece = board[y + direction[0] * i][x + direction[1] * i]
                    if -1 < y + direction[0] * i < 8 and -1 < x + direction[1] * i < 8:
                        if piece == ' ':
                            temp_check.append((y + direction[0] * i, x + direction[1] * i))
                        elif piece.colour != self.colour and piece.piece.lower() == 'k':
                            temp_check.append((y + direction[0] * i, x + direction[1] * i))
                            temp_check.append(self.position)
                            for i in temp_check:
                                self.checks.append(i)
                            return True
                        else:
                            break
                    else:
                        break
                except:
                    continue
        return False

    def trim_checks(self, board, turn, map=None, in_check=False):
        """Trim legal moves based on Checks"""
        updated_moves = []
        for i, row in enumerate(board):
            for j, piece in enumerate(row):
                if piece != ' ':
                    if len(piece.checks) > 0:
                        if piece.colour[0] != turn:
                            if piece.piece.lower() != 'k':
                                for move in self.legal_positions:
                                    # print('Piece, position moves: ')
                                    # print(self.piece, self.position)
                                    # print((self.position[0] + move[1], self.position[1] + move[0]))
                                    if (self.position[0] + move[1], self.position[1] + move[0]) in piece.checks:
                                        updated_moves.append(move)
                                self.legal_positions = updated_moves

    def pin_line_update(self, board):
        """Calculate pieces in pinned positions"""
        self.pin_lines.clear()
        x = self.position[1]
        y = self.position[0]
        self.pin_lines.add((y, x))
        for direction in self.legal_directions:
            count = 0
            temp_pins = []
            piece_pinned = ''
            for i in range(1, 9):
                try:
                    piece = board[y + direction[0] * i][x + direction[1] * i]
                    if -1 < y + direction[0] * i < 8 and -1 < x + direction[1] * i < 8:
                        if piece == ' ':
                            temp_pins.append((y + direction[0] * i, x + direction[1] * i))
                        elif piece.colour != self.colour and count < 1:
                            temp_pins.append((y + direction[0] * i, x + direction[1] * i))
                            piece_pinned = piece.piece + str(piece.position)
                            count += 1
                        elif piece.colour != self.colour and piece.piece.lower() == 'k':
                            for i in temp_pins:
                                self.pin_lines.add(i)
                            break
                        elif piece.colour == self.colour:
                            break
                        else:
                            break
                    else:
                        break
                except:
                    continue
        # if len(self.pin_lines) > 1:
        #     print(self.piece, self.position, self.pin_lines)

    def trim_pin_moves(self, board):
        """Trim legal moves based on Pins"""
        x = self.position[1]
        y = self.position[0]
        for pin in self.pin_lines:
            try:
                if board[pin[0]][pin[1]].colour != self.colour:
                    piece = board[pin[0]][pin[1]]
                    updated_moves = []
                    for legal_pos in piece.legal_positions:
                        if (piece.position[0] + legal_pos[1], piece.position[1] + legal_pos[0]) in list(self.pin_lines):
                            updated_moves.append(legal_pos)
                    piece.legal_positions = updated_moves
            except:
                continue

    def make_move(self, board, offset, turn, flipped, i=None, j=None):
        """Move the pieces position on the board if legal"""
        # ai move's using i amd j
        if i == None:
            x = int((pg.mouse.get_pos()[0] - offset[0]) // self.size)
            y = int((pg.mouse.get_pos()[1] - offset[1]) // self.size)
        else:
            x = i
            y = j
        if flipped and i is None:
            x = -x+7
            y = -y+7
        # if the piece moved is in a legal position then make the move
        if (x - self.position[1], y - self.position[0]) in self.legal_positions and self.colour[0] == turn:
            self.position = (y, x)
            self.has_moved = True
            return True
        else:
            return False

    def draw(self, offset, screen, size, flipped):
        """Draw the current piece"""
        self.size = size
        if self.picture.get_size() != (self.size, self.size):
            self.picture = pg.image.load(
                "data/img/pieces/" + self.piece_set + "/" + self.colour[0] + self.piece.lower() + ".png").convert_alpha()
            self.picture = pg.transform.smoothscale(self.picture, (self.size, self.size))
        if self.clicked:
            screen.blit(self.picture,
                        (pg.mouse.get_pos()[0] - self.size / 2 ,
                         pg.mouse.get_pos()[1] - self.size / 2)
                        )
        else:
            if not flipped:
                screen.blit(self.picture,
                        (offset[0] + self.size * self.position[1], offset[1] + self.size * self.position[0]))
            else:
                screen.blit(self.picture,
                            (offset[0] + self.size * (-self.position[1]+7), offset[1] + self.size * (-self.position[0]+7)))

    def change_piece_set(self, piece_type):
        """Change piece set"""
        self.piece_set = piece_type
        self.picture = pg.image.load(
            "data/img/pieces/" + self.piece_set + "/" + self.colour[0] + self.piece.lower() + ".png").convert_alpha()
        self.picture = pg.transform.smoothscale(self.picture, (self.size, self.size))

    def __del__(self):
        """Delete the piece"""
        self.dead = True
        self.clicked = False
        self.kill()
        # print('a', self.colour, self.piece.upper(), 'has died')

