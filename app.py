import os
import subprocess
import sys

REPO_URL = "https://github.com/Piper-Pepper/hutbot.git"
CLONE_DIR = "hutbot"

def run_command(command, error_message, cwd=None):
    print(f"[CMD] {' '.join(command)}")
    result = subprocess.run(command, cwd=cwd)
    if result.returncode != 0:
        print(f"[FEHLER] {error_message}")
        exit(1)

# 1. Clone or pull repository
if not os.path.exists(CLONE_DIR):
    print(f"[INFO] Klone Repository von {REPO_URL} ...")
    run_command(["git", "clone", REPO_URL, CLONE_DIR], "Git-Clone fehlgeschlagen!")
else:
    print("[INFO] Repository bereits vorhanden. Führe git pull aus ...")
    run_command(["git", "-C", CLONE_DIR, "pull"], "Git-Pull fehlgeschlagen!")

# 2. Optional: Pip upgraden (nur wenn erlaubt)
print("[INFO] Versuche pip zu aktualisieren ...")
subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "--quiet", "--user"])

# 3. Optional: requirements.txt installieren (wenn vorhanden)
req_path = os.path.join(CLONE_DIR, "requirements.txt")
if os.path.exists(req_path):
    print("[INFO] Installiere Abhängigkeiten aus requirements.txt ...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet", "--user"], cwd=CLONE_DIR)
else:
    print("[WARNUNG] Keine requirements.txt gefunden.")

# 4. Starte Bot
os.chdir(CLONE_DIR)
print("[INFO] Starte hutbot.py ...")

if os.getenv("RUN_RIDDLE_IMPORT") == "1":
    print("[INFO] Running one-time riddle import...")
    subprocess.run([sys.executable, "import_riddle.py"], cwd=CLONE_DIR, check=True)
    
subprocess.run([sys.executable, "hutbot.py"])
