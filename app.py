import subprocess
import sys

REPO_URL = "https://github.com/Piper-Pepper/hutbot.git"
CLONE_DIR = "hutbot"

if not os.path.exists(CLONE_DIR):
    print(f"[INFO] Klone Repository von {REPO_URL} ...")
    result = subprocess.run(["git", "clone", REPO_URL, CLONE_DIR])
    if result.returncode != 0:
        print("[FEHLER] Git-Clone fehlgeschlagen!")
        exit(1)
else:
    print("[INFO] Repository bereits vorhanden. Führe git pull aus ...")
    result = subprocess.run(["git", "-C", CLONE_DIR, "pull"])
    if result.returncode != 0:
        print("[FEHLER] Git-Pull fehlgeschlagen!")
        exit(1)

# Starte hutbot.py
os.chdir(CLONE_DIR)
print("[INFO] Starte hutbot.py ...")
subprocess.run([sys.executable, "hutbot.py"])
