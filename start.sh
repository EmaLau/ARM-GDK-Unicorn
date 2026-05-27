#!/bin/bash

# Forza lo script a posizionarsi nella cartella corretta del progetto
cd "$(dirname "$0")"
PROJECT_DIR=$(pwd)

echo "[START] Configurazione ed esecuzione dell'architettura modulare..."

# --- GESTIONE VIRTUAL ENVIRONMENT & REQUISITI ---
if [ ! -d "env" ]; then
    echo "[INIT] Creazione del virtual environment con Python 3.12..."
    python3.12 -m venv env
fi

echo "[SETUP] Controllo e installazione dei requisiti..."
source env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "[INFO] Ambiente pronto. Lancio i moduli in finestre separate..."
echo "------------------------------------------------------------------"

# --- LANCIO MODULI COORIDNATI ---

# 1. Avvia l'Hub Backend FastAPI (Porta 8001)
osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_DIR' && source env/bin/activate && DYLD_LIBRARY_PATH=/opt/homebrew/opt/keystone/lib PYTHONPATH=. python3 app/backend/main.py\""

# Aspetta che il backend sia totalmente pronto sulla porta 8001
sleep 2

# 2. Avvia l'Interfaccia Grafica NiceGUI (Porta 8080)
osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_DIR' && source env/bin/activate && python3 app/frontend/run.py\""

sleep 1

# 3. Avvia l'Emulatore Unicorn Worker (Client WebSocket)
osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_DIR' && source env/bin/activate && python3 app/emulator/unicorn_worker.py\""

echo "[SUCCESS] Tutti e 3 i moduli sono stati avviati correttamente nelle loro finestre."