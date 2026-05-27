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

echo "[INFO] Ambiente pronto. Rilevamento del Sistema Operativo..."
echo "------------------------------------------------------------------"

# Rileva il sistema operativo
OS_TYPE="$(uname)"

# Funzione per lanciare i comandi in base all'OS
run_module() {
    local title=$1
    local cmd=$2
    local log_file=$3

    if [ "$OS_TYPE" = "Darwin" ]; then
        # macOS: Apri in una nuova finestra del Terminale
        osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_DIR' && source env/bin/activate && $cmd\""
    else
        # Linux / Altri: Esegui in background con nohup e salva i log
        echo "[Linux] Avvio di '$title' in background. Log in: $log_file"
        cd "$PROJECT_DIR" && source env/bin/activate && nohup sh -c "$cmd" > "$log_file" 2>&1 &
    fi
}

# Creazione cartella log per Linux (se non esiste)
if [ "$OS_TYPE" != "Darwin" ]; then
    mkdir -p logs
fi

# --- LANCIO MODULI COORDINATI ---

# 1. Avvia l'Hub Backend FastAPI (Porta 8001)
CMD_BACKEND="DYLD_LIBRARY_PATH=/opt/homebrew/opt/keystone/lib PYTHONPATH=. python3 app/backend/main.py"
run_module "Backend FastAPI" "$CMD_BACKEND" "logs/backend.log"

# Aspetta che il backend sia totalmente pronto sulla porta 8001
sleep 2

# 2. Avvia l'Interfaccia Grafica NiceGUI (Porta 8080)
CMD_FRONTEND="python3 app/frontend/run.py"
run_module "Frontend NiceGUI" "$CMD_FRONTEND" "logs/frontend.log"

sleep 1

# 3. Avvia l'Emulatore Unicorn Worker (Client WebSocket)
CMD_EMULATOR="python3 app/emulator/unicorn_worker.py"
run_module "Emulator" "$CMD_EMULATOR" "logs/emulator.log"

echo "------------------------------------------------------------------"
if [ "$OS_TYPE" = "Darwin" ]; then
    echo "[SUCCESS] Tutti e 3 i moduli sono stati avviati in finestre separate."
else
    echo "[SUCCESS] Tutti e 3 i moduli sono avviati in background."
    echo "[INFO] Puoi monitorare i log con: tail -f logs/*.log"
fi