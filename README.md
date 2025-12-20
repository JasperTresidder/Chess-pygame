<h3>This is a pygame implementation of chess. It follows chess rules, supports Player vs Player / Player vs AI / AI vs AI, and includes an in-game Game Review system for saved PGNs.</h3>

<h5>Requires Python >=3.10 <br>
Size: 166Mb</h5>

### Tips:
- You may have to <a href="https://stockfishchess.org/download/">install</a> your own version of Stockfish.<br>
- Games are <b>automatically saved</b> in the <b>/data/games</b> folder as a pgn file
- You can resize the window how you like 

## Features
- <b>Rules + legality</b>: move legality is generated via <code>python-chess</code>.
- <b>Move input modes</b>: Click-to-move, Drag-to-move, or Click+Drag (configurable in Settings).
- <b>Hint</b>: <code>Ctrl+H</code> draws a blue hint arrow.
- <b>Eval bar</b>: asynchronous evaluation bar with show/hide toggle; shows the number on hover.
- <b>AI strength</b>: Elo-based strength limiting (more beginner-friendly at low Elo).
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
![image](https://github.com/JasperTresidder/Chess-pygame/assets/51917264/b01feef1-62ac-49de-9bff-b3eea429fd1f)
![image](https://github.com/JasperTresidder/Chess-pygame/assets/51917264/348d3928-b7d3-4dab-9e20-09cacebfde73)
![image](https://github.com/JasperTresidder/Chess-pygame/assets/51917264/8e7ea6f8-6f18-4259-afa7-c802e682975b)
![image](https://github.com/JasperTresidder/Chess-pygame/assets/51917264/0259b5f8-c75a-4eda-8815-89f93b4d6c47)


