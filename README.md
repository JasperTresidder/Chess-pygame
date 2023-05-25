<style>
.box {
    background: rgba(253,54,54,0.27);
    border-radius: 15px;
    padding: 10px;
}
body {
    background: darkslategrey;
}
</style>
<body>

<h3>This is a pygame implementation of chess. Following all rules, playing against stockfish AI or PvP.
First upload. Needs refinement. </h3>

### Tips:
- click middle mouse button to undo move 
- crtl + f to print the FEN position


## Setup
<h4>Clone repo and change directory into the project.</h4>
```
git clone https://github.com/JasperTresidder/Chess-pygame.git
cd Chess-pygame
```
<h4>Create a virtual environment</h4>
```
python3 -m venv env
```
<h4>Activate the virtual environment 
<br><br>
MacOS/Unix:</h4>
```
source env/bin/activate
```
<h4>Windows:</h4>
```
.\env\Scripts\activate
```
<h4>Install packages from the requirements list into the venv. <br> Then run the program</h4>
```
python -m pip install -r requirements.txt
python main.py
```

## Important
<div class="box">
- If you are <b>NOT</b> on Windows you will have to <a href="https://stockfishchess.org/download/">install</a> your own version of Stockfish.<br>
- Place the Stockfish application in the lit/stockfish_15.1_win_x64_avx2/ dictionary<br>
- Then change the reference to this path in the __init__ of engine.py located in the src dictionary. 
</div>
</body>

![image](https://github.com/JasperTresidder/Chess-pygame/assets/51917264/2665b390-faa4-41a9-aff3-b8b0884b3623)
![image](https://github.com/JasperTresidder/Chess-pygame/assets/51917264/f5e4a61b-5c11-4e92-93ff-e2bcb222ed1c)
