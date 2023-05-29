<h3>This is a pygame implementation of chess. Following all rules, playing against stockfish AI, Player vs Player or AI vs AI.
First upload. Needs refinement. </h3>

<h5>Requires Python >=3.10 <br>
Size: 166Mb</h5>

### Tips:
- Click middle mouse button or press 'u' to <b>undo</b> move 
- Crtl + F to print the FEN position
- Games are <b>automatically saved</b> in the <b>/data/games</b> folder as a pgn file
- You can forfeit with Ctrl + S
- You can resize the window how you like 


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
<code>python main.py</code>

## Important
<div class="box">
- If you are <b>NOT</b> on Windows you will have to <a href="https://stockfishchess.org/download/">install</a> your own version of Stockfish.<br>
- Place the Stockfish application in the lit/stockfish_15.1_win_x64_avx2/ dictionary<br>
- Then change the reference to this path in the __init__ of engine.py (line 42 & 48) located in the src dictionary. 
</div>

### TODO
- Async evaluation of the position. 
- Ability to play vs AI as black
- Load games
- Click through moves using arrow keys.

![image](https://github.com/JasperTresidder/Chess-pygame/assets/51917264/2665b390-faa4-41a9-aff3-b8b0884b3623)
![image](https://github.com/JasperTresidder/Chess-pygame/assets/51917264/f5e4a61b-5c11-4e92-93ff-e2bcb222ed1c)
