import datetime
import random
import sys
from src.engine.settings import SettingsMenu
from src.functions.fen import *
import pygame as pg
from src.functions.timer import *
from src.pieces.queen import Queen
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
        return 'Evaluation = ' + str(round(evaluation["value"]/100, 2))
    else:
        if evaluation["value"] < 0:
            return 'Mate in '+ str(-evaluation["value"])
        else:
            return 'Mate in ' + str(evaluation["value"])



class Engine:
    def __init__(self, player_vs_ai: bool, ai_vs_ai: bool):
        self.player_vs_ai = player_vs_ai
        self.ai_vs_ai = ai_vs_ai
        self.evaluation = ''
        self.best_move = ''
        self.game_just_ended = False
        self.engine = 'stockfish'
        pg.init()
        pg.display.set_caption('Chess', 'chess')
        pg.font.init()
        self.last_move = []
        self.highlighted = []
        self.arrows = []
        self.platform = None
        if 'Windows' in platform.platform():
            self.platform = 'Windows/' + self.engine + '.exe'
        if 'macOS' in platform.platform():
            self.platform = 'macOS/stockfish'
        print("lit/" + self.engine + "/" + self.platform)
        if self.ai_vs_ai:
            self.stockfish = Stockfish("lit/" + self.engine + "/" + self.platform,
                                       depth=99,
                                       parameters={"Threads": 6, "Minimum Thinking Time": 100, "Hash": 64,
                                                   "Skill Level": 20,
                                                   "UCI_Elo": 3000})
        else:
            self.stockfish = Stockfish("lit/" + self.engine + "/" + self.platform,
                                       depth=1,
                                       parameters={"Threads": 1, "Minimum Thinking Time": 1, "Hash": 2,
                                                   "Skill Level": 0.001,
                                                   "UCI_LimitStrength": "true",
                                                   "UCI_Elo": 0})
        self.stockfish.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        self.ai_strength = 0
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

        self.screen = pg.display.set_mode((pg.display.get_desktop_sizes()[0][1] - 70, pg.display.get_desktop_sizes()[0][1] - 70), pg.RESIZABLE, vsync=1)
        self.settings = SettingsMenu(title='Settings', width=self.screen.get_width(), height=self.screen.get_height(),surface=self.screen, parent=self, theme=pm.themes.THEME_DARK)
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
        self.background = pg.image.load('data/img/background_dark.png').convert()
        self.background = pg.transform.smoothscale(self.background,
                                                   (pg.display.get_window_size()[0], pg.display.get_window_size()[1]))
        self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
        self.board_background = pg.transform.smoothscale(self.board_background,
                                                         (self.size * 8, self.size * 8))
        self.offset = [pg.display.get_window_size()[0] / 2 - 4 * self.size,
                       pg.display.get_window_size()[1] / 2 - 4 * self.size]
        self.update_board()
        self.update_legal_moves()
        self.prev_board = self.board
        self.debug = False
        self.node = self.game
        self.show_numbers = True
        if EVAL_ON:
            self.get_eval()
        self.clock = pg.time.Clock()
        self.settings.confirm()
    def run(self):
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
        for event in pg.event.get():
            if event.type == pg.QUIT:
                pg.quit()
                sys.exit()
            elif event.type == pg.MOUSEBUTTONDOWN:
                self.game_just_ended = False
                if event.button == 1 and not self.game_just_ended:
                    self.left = True
                    self.click()
                elif event.button == 3:
                    self.click_right()
            elif event.type == pg.MOUSEBUTTONUP:

                if event.button == 1 and self.updates:
                    self.left = False
                    self.un_click()
                elif event.button == 1:
                    self.left = False
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
            elif event.type == pg.KEYDOWN:
                if event.key == pg.K_s and pg.key.get_mods() & pg.KMOD_CTRL:
                    self.end_game()
                if event.key == pg.K_f and pg.key.get_mods() & pg.KMOD_CTRL:
                    print(self.game_fens[-1])
                if event.key == pg.K_e and pg.key.get_mods() & pg.KMOD_CTRL:
                    self.evaluation = self.get_eval()
                if event.key == pg.K_h and pg.key.get_mods() & pg.KMOD_CTRL:
                    self.stockfish.set_skill_level(20)
                    self.best_move = str(self.stockfish.get_best_move_time(200))
                    self.stockfish.set_skill_level(self.ai_strength)
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
                                                           (pg.display.get_window_size()[0], pg.display.get_window_size()[1]))
                self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
                if self.default_size >= pg.display.get_window_size()[1] or self.default_size >= pg.display.get_window_size()[0]:
                    self.show_numbers = False
                    if pg.display.get_window_size()[0] < pg.display.get_window_size()[1]:
                        self.size = int((pg.display.get_window_size()[0])/8)
                    else:
                        self.size = int((pg.display.get_window_size()[1])/8)
                elif (self.default_size < pg.display.get_window_size()[1] < self.default_size + 200) or (self.default_size < pg.display.get_window_size()[0] < self.default_size + 1000):
                    self.show_numbers = True
                    if pg.display.get_window_size()[0] < pg.display.get_window_size()[1]:
                        self.size = int((pg.display.get_window_size()[0]-200)/8)
                    else:
                        self.size = int((pg.display.get_window_size()[1]-200)/8)
                else:
                    self.show_numbers = True
                if self.size <= 1:
                    self.size = 1
                self.board_background = pg.transform.smoothscale(self.board_background,
                                                                 (self.size * 8, self.size * 8))
                self.offset = [pg.display.get_window_size()[0] / 2 - 4 * self.size,
                               pg.display.get_window_size()[1] / 2 - 4 * self.size]

        if self.ai_vs_ai:
            self.un_click()
        pg.display.flip()
        self.clock.tick(1000)

    def get_eval(self):
        self.stockfish.set_depth(20)
        eve = print_eval(self.stockfish.get_evaluation())
        self.stockfish.set_depth(99)
        return eve

    # @timeit
    def un_click(self):
        self.highlighted.clear()
        self.arrows.clear()
        if self.ai_vs_ai:
            self.ai_make_move(0, 0, 0)
            if EVAL_ON:
                self.get_eval()
        else:
            for row in range(8):
                for col in range(8):
                    if self.board[row][col] != ' ':
                        if self.board[row][col].clicked:
                            # Make move if legal
                            if self.board[row][col].make_move(self.board, self.offset, self.turn, None, None):
                                x = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
                                y = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
                                if self.turn == 'w':
                                    self.turn = 'b'
                                    move = translate_move(row, col, y, x)
                                    if self.board[row][col] != ' ':
                                        if self.board[row][col].piece == 'P':
                                            if y == 0:
                                                move += 'q'
                                    self.last_move.append(move)
                                    self.node = self.node.add_variation(chess.Move.from_uci(move))
                                else:
                                    self.fullmove_number += 1
                                    self.turn = 'w'
                                    if not self.player_vs_ai:
                                        move = translate_move(row, col, y, x)
                                        if self.board[row][col] != ' ':
                                            if self.board[row][col].piece == 'p':
                                                if y == 7:
                                                    move += 'q'

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

    def change_pieces(self, piece_type):
        self.piece_type = piece_type
        for piece in self.all_pieces:
            piece.change_type(piece_type)

    def change_board(self, board_type):
        self.board_style = board_type
        self.board_background = pg.image.load('data/img/boards/' + self.board_style).convert()
        self.board_background = pg.transform.smoothscale(self.board_background,
                                                         (self.size * 8, self.size * 8))

    def check_resize(self):
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

    def change_mode(self, mode):
        if mode == 'pvp':
            self.ai_vs_ai = False
            self.player_vs_ai = False
        elif mode == 'aivai':
            self.ai_vs_ai = True
            self.player_vs_ai = False
        elif mode == 'pvai':
            self.ai_vs_ai = False
            self.player_vs_ai = True

    def ai_make_move(self, y, row, col):
        # Engine Moves
        self.draw_board()
        self.draw_pieces()
        pg.display.flip()
        time.sleep(0.15)
        move = self.move_strength(self.ai_strength)
        if move is not None:
            self.last_move.append(move)
            if self.board[row][col] != ' ':
                if self.board[row][col].piece == 'p':  # auto promote queen
                    if y == 7:
                        move += 'q'
            self.node = self.node.add_variation(chess.Move.from_uci(move))
            self.engine_make_move(move) # Making the move

    def move_strength(self, strength):
        # return moves[0]["Move"]
        if self.ai_vs_ai:
            if self.turn == 'w':
                # self.stockfish.set_skill_level(20)
                a = 15*(strength+1)
            else:
                # self.stockfish.set_skill_level(1)
                a = 15*(strength+1)
        else:
            a = random.randint(2, 5)
        move = self.stockfish.get_best_move_time(a)
        return move

    def change_ai_strength(self, num):
        self.ai_strength = num
        self.stockfish.set_skill_level(num)

    def un_click_right(self, right_click):
        txr = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
        tyr = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
        if right_click:
            if self.txr == txr and self.tyr == tyr:
                if (tyr, txr) in self.highlighted:
                    self.highlighted.remove((tyr, txr))
                else:
                    self.highlighted.append((tyr, txr))
            else:
                if ((self.tyr, self.txr), (tyr, txr)) in self.arrows:
                    self.arrows.remove(((self.tyr, self.txr), (tyr, txr)))
                else:
                    try:
                        if -1 < self.txr < 8 and -1 < self.tyr < 8 and -1 < txr < 8 and -1 < tyr < 8:
                            self.arrows.append(((self.tyr, self.txr), (tyr, txr)))
                    except:
                        pass
        for pieces in self.all_pieces:
            pieces.clicked = False

    def updates_kill(self):
        self.updates = False
        for pieces in self.all_pieces:
            pieces.clicked = False
        self.left = False

    # @timeit
    def moved(self):
        self.prev_board = self.board
        eps_moved_made = False
        for i, row in enumerate(self.board):
            for j, piece in enumerate(row):
                if piece != ' ':
                    if piece.position != (i, j):
                        # piece no longer on the square of the board
                        self.board[i][j] = ' '

                        # has a pawn moved 2 squares. en-passant check
                        if piece.piece.lower() == 'p' and piece.position[0] - i == 2 * piece.direction:
                            self.en_passant_square = str(
                                (piece.position[0] + int((piece.position[0] - i) / 2), piece.position[1]))
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

                        if castle or promote:
                            pg.mixer.music.load('data/sounds/castle.mp3')
                            pg.mixer.music.play(1)
                        elif piece_sound == ' ' and not eps_moved_made:
                            pg.mixer.music.load('data/sounds/move.mp3')
                            pg.mixer.music.play(1)
                        else:
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
        if self.update_legal_moves():
            pg.mixer.music.load('data/sounds/check.aiff')
            pg.mixer.music.play(1)

        legal_moves = self.count_legal_moves()
        # print('Number of legal moves', legal_moves)
        # print FEN notation of position
        self.game_fens.append(
            create_FEN(self.board, self.turn, self.castle_rights, self.en_passant_square, self.fullmove_number))
        self.stockfish.set_fen_position(self.game_fens[-1])
        # print(self.game_fens[-1])
        if self.node.board().is_repetition():
            print("DRAW BY REPETITION")
            pg.mixer.music.load('data/sounds/mate.wav')
            pg.mixer.music.play(1)
            time.sleep(0.15)
            pg.mixer.music.play(1)
            self.end_game()
        if self.node.board().is_stalemate():
            print("STALEMATE")
            pg.mixer.music.load('data/sounds/mate.wav')
            pg.mixer.music.play(1)
            time.sleep(0.15)
            pg.mixer.music.play(1)
            self.end_game()
        if self.node.board().is_insufficient_material():
            print("INSUFFICIENT MATERIAL")
            pg.mixer.music.load('data/sounds/mate.wav')
            pg.mixer.music.play(1)
            time.sleep(0.15)
            pg.mixer.music.play(1)
            self.end_game()
        if self.node.board().is_checkmate() or legal_moves == 0:
            if self.node.board().outcome().winner:
                print("CHECKMATE WHITE WINS !!")
            else:
                print("CHECKMATE BLACK WINS !!")
            pg.mixer.music.load('data/sounds/mate.wav')
            pg.mixer.music.play(1)
            time.sleep(0.15)
            pg.mixer.music.play(1)
            self.end_game()
        # pprint(self.board, indent=3)

    def end_game(self):
        self.game_just_ended = True
        dt = datetime.datetime.now()
        dt = dt.strftime("%Y%m%d_%H%M%S_%f")
        print(self.game, file=open("data/games/" + dt + ".pgn", "w"), end="\n\n")
        self.reset_game()
        # file = open(str(dt) + ".pgn" + "w")
        # file.writelines(self.game)
        # file.close()
        # print(self.game, file=open("../../data/games/" + dt + ".pgn" + "w+"), end="\n\n")

    def reset_game(self):
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
            piece.change_type(self.piece_type)
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

        self.node = self.game
        self.update_board()
        self.update_legal_moves()

    def undo_move(self, one):
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
                piece.change_type(self.piece_type)
            self.last_move.pop()
            self.node = self.node.parent  # allows for undoes to show in analysis on https://chess.com/analysis

            self.update_board()
            self.update_legal_moves()

    # @timeit
    def update_legal_moves(self):
        castle = []
        in_check = False
        for piece in self.all_pieces:
            if piece.piece.lower() == 'k':
                if not piece.has_moved:
                    castle.append(piece.colour)
            if piece.colour[0] == self.turn:
                piece.update_legal_moves(self.board, self.en_passant_square, captures=False)
            else:
                if piece.piece.lower() in ['b', 'r', 'q', 'n', 'p']:
                    if piece.check(self.board):
                        in_check = True
                if piece.piece.lower() in ['b', 'r', 'q']:
                    piece.pin_line_update(self.board)

        self.handle_fen_castle(castle)

        if self.turn == 'w':
            self.map = self.create_map(self.black_pieces)
        else:
            self.map = self.create_map(self.white_pieces)

        # if in_check:
        if self.turn == 'w':
            for piece in self.white_pieces:
                piece.trim_checks(self.board, self.turn, self.map, in_check)
        else:
            for piece in self.black_pieces:
                piece.trim_checks(self.board, self.turn, self.map, in_check)

        if self.turn == 'w':
            for piece in self.black_pieces:
                if piece.piece.lower() in ['b', 'r', 'q']:
                    piece.trim_pin_moves(self.board)
        else:
            for piece in self.white_pieces:
                if piece.piece.lower() in ['b', 'r', 'q']:
                    piece.trim_pin_moves(self.board)
        return in_check

    def make_move_board(self, move, piece):
        if self.board[piece.position[0]][piece.position[1]].make_move(self.board, self.offset, self.turn,
                                                                      piece.position[1] + move[0],
                                                                      piece.position[0] + move[1]):
            if self.turn == 'w':
                self.turn = 'b'
            else:
                self.fullmove_number += 1
                self.turn = 'w'
            self.moved()
            self.board[piece.position[0]][piece.position[1]].clicked = False

    def engine_make_move(self, move):
        try:
            square1 = square_on(move[0:2])
            square2 = square_on(move[2:4])
            the_move = (square2[0] - square1[0], square2[1] - square1[1])
            piece = self.board[square1[0]][square1[1]]
            if piece.make_move(self.board, self.offset, self.turn, piece.position[1] + the_move[1],
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

    def create_map(self, pieces):
        map = set()
        for piece in pieces:
            piece.update_legal_moves(self.board, '-', captures=True)
            for move in piece.legal_positions:
                map.add((piece.position[0] + move[1], piece.position[1] + move[0]))
        return list(map)

    def count_legal_moves(self):
        count = 0
        for i, row in enumerate(self.board):
            for j, piece in enumerate(row):
                if piece != ' ':
                    if piece.colour[0] == self.turn:
                        count += len(piece.legal_positions)
        return count

    def promotion(self, piece):
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

    def handle_fen_castle(self, castle):
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

    def click_right(self):
        self.txr = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
        self.tyr = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)

    def click(self):
        self.tx = int((pg.mouse.get_pos()[0] - self.offset[0]) // self.size)
        self.ty = int((pg.mouse.get_pos()[1] - self.offset[1]) // self.size)
        self.updates = True

    def update_board(self):  # is currently clicking a piece?
        try:
            if -1 < self.tx < 8 and -1 < self.ty < 8:
                if self.board[self.ty][self.tx] != ' ':
                    self.board[self.ty][self.tx].update(self.screen, self.offset, self.turn)
        except:
            pass

    def draw_board(self):
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
                surface = pg.Surface((self.size, self.size))
                surface.set_alpha(200)
                if self.debug and (row, col) in self.map:
                    surface.fill(self.colours2[count % 2])
                    self.screen.blit(surface, (self.offset[0] + self.size * col, self.offset[1] + self.size * row))
                else:
                    if (row, col) in self.highlighted:
                        surface.fill(self.colours4[count % 2])
                        self.screen.blit(surface, (self.offset[0] + self.size * col, self.offset[1] + self.size * row))
                    else:
                        if len(self.last_move) != 0:
                            if (row, col) in [square1, square2]:
                                surface.fill(self.colours3[count % 2])
                                self.screen.blit(surface,
                                                 (self.offset[0] + self.size * col, self.offset[1] + self.size * row))
                            else:
                                surface.fill(self.colours[count % 2])
                                self.screen.blit(surface,
                                                 (self.offset[0] + self.size * col, self.offset[1] + self.size * row))
                        else:
                            surface.fill(self.colours[count % 2])
                            self.screen.blit(surface,
                                             (self.offset[0] + self.size * col, self.offset[1] + self.size * row))
                count += 1
            count += 1

        # draw letters + numbers
        if self.show_numbers:
            for i in range(8):
                number = 8 - i
                surface = self.font.render(str(number), False, (255, 255, 255))
                self.screen.blit(surface, (self.offset[0] - self.size / 2,
                                           self.offset[1] + self.size / 2 + self.size * i - 13))  # draw numbers
            for i in range(8):
                letter = board_letters[i]
                surface = self.font.render(str(letter), False, (255, 255, 255))
                self.screen.blit(surface, (self.offset[0] + self.size/2 - 8 + self.size * i,
                                           self.offset[
                                               1] + 17 * self.size / 2  - 25))  # draw letters
            surface = self.font.render('Settings = ESC', False, (255, 255, 255))
            self.screen.blit(surface, (20, 20))
            if self.evaluation != '':
                surface = self.font.render(self.evaluation, False, (255, 255, 255))
                self.screen.blit(surface, (self.screen.get_width()/2 - surface.get_width()/2, 20))

            if self.best_move != '':
                surface = self.font.render('Hint: ' + self.best_move, False, (255, 255, 255))
                self.screen.blit(surface, (self.screen.get_width() - surface.get_width() - 10, 20))

    def draw_pieces(self, piece_selected=None):
        for piece in self.all_pieces:
            if piece != piece_selected:
                piece.draw(self.offset, self.screen, self.size)

        # Draw the piece last, if it is being clicked/dragged
        if piece_selected != None:
            piece_selected.draw(self.offset, self.screen, self.size)

        self.draw_arrows()

    def draw_arrows(self):
        off = (self.offset[0] + self.size / 2, self.offset[1] + self.size / 2)
        for start, end in self.arrows:
            surface = pg.Surface((pg.display.get_window_size()[0], pg.display.get_window_size()[1]), pg.SRCALPHA)
            surface.set_alpha(200)
            pg.draw.line(surface, self.arrow_colour, (off[0] + self.size * start[1], off[1] + self.size * start[0]),
                         (off[0] + self.size * end[1], off[1] + self.size * end[0]), 10)
            self.screen.blit(surface, (0, 0))

