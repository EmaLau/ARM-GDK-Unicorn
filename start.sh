#!/bin/bash

# Forza lo script a posizionarsi nella cartella corretta del progetto
cd "$(dirname "$0")"
PROJECT_DIR=$(pwd)

echo "[START] Lancio dell'architettura in tre tab/finestre separate..."

# 1. Avvia il Backend FastAPI caricando l'ambiente virtuale e passando la variabile per le librerie dinamiche
osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_DIR' && source env/bin/activate && DYLD_LIBRARY_PATH=/opt/homebrew/opt/keystone/lib PYTHONPATH=. python3 app/backend/main.py\""

# Aspetta che il backend si sia inizializzato sulla porta 8001
sleep 2

# 2. Avvia l'Interfaccia Grafica NiceGUI
osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_DIR' && source env/bin/activate && python3 run.py\""

sleep 1

# 3. Avvia l'Emulatore Unicorn
osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_DIR' && source env/bin/activate && python3 py_qemu_alt.py\""

echo "[SUCCESS] I tre moduli sono stati inoltrati a finestre dedicate."
echo "Controlla le finestre che si sono appena aperte per vedere l'output!"