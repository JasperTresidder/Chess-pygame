import re
import sys
from pathlib import Path

import chess


def strip_comments(text: str) -> str:
    text = re.sub(r"\{[^}]*\}", " ", text, flags=re.DOTALL)
    text = re.sub(r";[^\n]*", " ", text)
    return text


def tokenize_movetext(text: str) -> list[str]:
    return re.findall(r"\(|\)|\S+", text)


def parse_nested(tokens: list[str]) -> list:
    root: list = []
    stack: list[list] = [root]
    for tok in tokens:
        if tok == "(":
            new_list: list = []
            stack[-1].append(new_list)
            stack.append(new_list)
        elif tok == ")":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(tok)
    return root


def is_result(tok: str) -> bool:
    return tok in ("1-0", "0-1", "1/2-1/2", "*")


def is_move_number(tok: str) -> bool:
    return bool(re.match(r"^\d+\.(?:\.\.)?$", tok)) or (tok.endswith("...") and tok[:-3].isdigit())


def collect_san_prefer_variations(node, out: list[str]) -> None:
    for item in node:
        if isinstance(item, list):
            if out:
                out.pop()
            collect_san_prefer_variations(item, out)
            return

        tok = str(item)
        if is_result(tok):
            return
        if tok.startswith("$"):
            continue
        if is_move_number(tok):
            continue
        if tok in ("!", "?", "!!", "??", "!?", "?!"):
            continue
        out.append(tok)


def headers_and_movetext(raw: str) -> tuple[dict[str, str], str]:
    headers: dict[str, str] = {}
    movelines: list[str] = []
    in_headers = True
    for line in raw.splitlines():
        s = line.strip()
        if in_headers and s.startswith("[") and s.endswith("]"):
            m = re.match(r"^\[(\w+)\s+\"(.*)\"\]$", s)
            if m:
                headers[m.group(1)] = m.group(2)
            continue
        if s == "" and in_headers:
            in_headers = False
            continue
        if not in_headers:
            movelines.append(line)
    return headers, "\n".join(movelines)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tools/repro_review_parse.py <path-to-pgn>")
        return 2

    pgn_path = Path(sys.argv[1])
    raw = pgn_path.read_text(encoding="utf-8", errors="ignore")

    _, movetext = headers_and_movetext(raw)
    movetext = strip_comments(movetext)
    tokens = tokenize_movetext(movetext)
    nested = parse_nested(tokens)

    san_moves: list[str] = []
    collect_san_prefer_variations(nested, san_moves)

    print("SAN moves collected:", len(san_moves))
    print("First 50 SAN:")
    for i, san in enumerate(san_moves[:50], start=1):
        print(f"{i:>3}: {san}")

    board = chess.Board()
    for ply, san in enumerate(san_moves, start=1):
        try:
            mv = board.parse_san(san)
        except Exception as e:
            print("\nFAIL")
            print(" ply:", ply)
            print(" side:", "white" if board.turn else "black")
            print(" san:", san)
            print(" fen:", board.fen())
            print(" err:", repr(e))
            return 1
        board.push(mv)

    print("\nParsed OK, plies:", len(san_moves))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
