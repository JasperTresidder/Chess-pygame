<h3>This is a pygame implementation of chess. It follows chess rules, supports Player vs Player / Player vs AI / AI vs AI, and includes an in-game Game Review system for saved PGNs.</h3>
<img width="1768" height="992" alt="Screenshot 2025-12-22 201737" src="https://github.com/user-attachments/assets/3f39849f-2853-4ed7-83cc-388e4d29ebbc" />
<h5>Requires Python >=3.10 <br>
Size (Chess.zip): 137Mb</h5>

### Tips:
- You may have to <a href="https://stockfishchess.org/download/">install</a> your own version of Stockfish.<br>
- Games are <b>automatically saved</b> in the <b>/data/games</b> folder as a pgn file
- You can resize the window how you like 

## Features
- <b>Rules + legality</b>: move legality is generated via <code>python-chess</code>.
- <b>Move input modes</b>: Click-to-move, Drag-to-move, or Click+Drag (configurable in Settings).
- <b>Premove</b>: queue premoves while waiting for the opponent/engine; supports multiple queued premoves.
- <b>Hint</b>: <code>Ctrl+H</code> draws a blue hint arrow.
- <b>Eval bar</b>: asynchronous evaluation bar with show/hide toggle; shows the number on hover.
- <b>AI strength</b>: Elo-based strength limiting (more beginner-friendly at low Elo).
- <b>Puzzle Rush</b>:
	- Loads puzzle packs from <code>data/puzzle-rush/*.json</code> and plays puzzles easiest → hardest.
	- Always starts with the computer move (dataset is expected to be “engine-to-move first”).
	- Board flips automatically if you are playing as Black for that puzzle.
	- Run ends after <b>3 strikes</b>; score and highscore are tracked.
	- Uses non-blocking delayed computer replies (smooth UI), with fast replies when you premove.
	- <b>Hint/Eval</b> are disabled in Puzzle Rush.
	- Attempt history is clickable: click a ✓/✗ to open a read-only solution review.
	- Solution review shows a move list on the right; click moves (or use Left/Right) to jump through positions.
- <b>Game Review</b>:
	- Browse saved PGNs from Settings (dropdown shows timestamp + AI Elo).
	- Step through moves with Left/Right, jump by clicking moves, scroll with mouse wheel.
	- Shows ACPL + Accuracy, and per-move quality tags (Book / Good / Best / Great / Amazing / Mistake / Blunder).
	- Copies PGN to clipboard from the Review Games menu.
	- On-board move-quality marker is drawn on the destination square in review.

## Controls
- <b>ESC</b>: open Settings (or exit Game Review when reviewing)
- <b>Left/Right</b>: step through moves in Game Review
- <b>Ctrl+H</b>: hint arrow

<b>Puzzle Rush</b>
- Click a ✓/✗ result marker: open solution review for that puzzle
- <b>Back</b> (button) or <b>ESC</b>: return to the live Puzzle Rush run
- Click moves in the right-side move list: jump to that position (with move sounds)
- <b>Left/Right</b>: step through the solution line


## Setup
<h4>Clone repo and change directory into the project.</h4>
<code>git clone https://github.com/JasperTresidder/Chess-pygame.git </code><br>
<code>cd Chess-pygame</code>
<h4>Create a virtual environment</h4>
<code>python -m venv env</code>
<h4>Activate the virtual environment 
<br><br>
MacOS/Unix:</h4>
<code>source env/bin/activate</code>
<h4>Windows:</h4>
<code>.\env\Scripts\activate</code>
<h4>Install packages from the requirements list into the venv. <br> Then run the program</h4>
<code>python -m pip install -r requirements.txt </code><br>
<code>python Chess.py</code>

## Important
<div class="box">
- You may have to <a href="https://stockfishchess.org/download/">install</a> your own version of Stockfish.<br>
- Place the Stockfish application in the lit/stockfish/{your_platform}/ dictionary. and rename the application to 'stockfish'<br>
</div>

### Notes
- Saved games are written to <code>data/games/*.pgn</code>.
- Stockfish is loaded from <code>lit/stockfish/&lt;platform&gt;/</code>.
- Puzzle Rush packs live in <code>data/puzzle-rush/</code>.
- Puzzle Rush pack progression is persisted in <code>data/settings/puzzle_rush_last_pack.txt</code>.
- Puzzle Rush highscore is persisted in <code>data/settings/puzzle_rush_highscore.txt</code>.

<img width="1771" height="989" alt="Screenshot 2025-12-22 201820" src="https://github.com/user-attachments/assets/f5b0b3fb-8f5f-416a-a902-d19fc5437345" />
<img width="1767" height="990" alt="Screenshot 2025-12-22 201857" src="https://github.com/user-attachments/assets/f46a0c1b-b38b-45c3-99f1-69f813ca0405" />
<img width="1917" height="1011" alt="image" src="https://github.com/user-attachments/assets/72017aa9-527f-43e8-b4d9-927f3da3d20e" />


