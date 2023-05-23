# games are saved as .pgn files in the data/games folder

from pprint import pprint
from src.functions.fen import parse_FEN, FEN_to_board
from src.engine.engine import Engine

# SET FALSE FOR PLAYER VS PLAYER !!
PLAYING_AGAINST_AI = True

if __name__ == '__main__':
    engine = Engine(PLAYING_AGAINST_AI)
    while(1):
        engine.run()

