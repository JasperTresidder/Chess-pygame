from pprint import pprint
from src.functions.fen import parse_FEN, FEN_to_board
from src.board.board import Board


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    board = Board(True) # SET FALSE FOR PLAYER VS PLAYER !!

    while(1):
        board.run()


# See PyCharm help at https://www.jetbrains.com/help/pycharm/
