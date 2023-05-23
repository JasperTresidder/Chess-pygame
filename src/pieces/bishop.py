from .base import Piece
import pygame as pg

class Bishop(Piece):
    def __init__(self, position, colour):
        super().__init__()
        self.legal_directions = [(1, 1), (-1, -1), (-1, 1), (1, -1)]
        self.colour = colour
        self.position = position
        if colour == 'black':
            self.piece = 'b'
            self.picture = pg.image.load("C:/Users/jaspe/PycharmProjects/Chess/data/img/black_b.png").convert_alpha()
        else:
            self.piece = 'B'
            self.picture = pg.image.load("C:/Users/jaspe/PycharmProjects/Chess/data/img/white_b.png").convert_alpha()

    def update_legal_moves(self, board, eps, captures):
        self.legal_positions = []
        x = self.position[1]
        y = self.position[0]
        for direction in self.legal_directions:
            for i in range(1, 9):
                try:
                    if -1 < y + direction[0]*i < 8 and -1 < x + direction[1]*i < 8:
                        if board[y + direction[0] * i][x + direction[1] * i] == ' ':
                            self.legal_positions.append((direction[1] * i, direction[0] * i))
                        elif board[y + direction[0] * i][x + direction[1] * i].colour != self.colour and not captures:
                            self.legal_positions.append((direction[1] * i, direction[0] * i))
                            break
                        elif board[y + direction[0] * i][x + direction[1] * i].colour != self.colour and \
                                board[y + direction[0] * i][x + direction[1] * i].piece.lower() == 'k' and captures:
                            self.legal_positions.append((direction[1] * i, direction[0] * i))
                        elif board[y + direction[0] * i][x + direction[1] * i].colour != self.colour and captures:
                            self.legal_positions.append((direction[1] * i, direction[0] * i))
                            break
                        elif board[y + direction[0] * i][x + direction[1] * i].colour == self.colour and captures:
                            self.legal_positions.append((direction[1] * i, direction[0] * i))
                            break
                        else:
                            break
                except:
                    continue
