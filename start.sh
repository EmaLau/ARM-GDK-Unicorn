#!/bin/bash
# start.sh
# Script di avvio dell'architettura modulare ARM GDK Unicorn.
# Esegue in sequenza:
#   1. Creazione del virtual environment Python (se assente)
#   2. Installazione delle dipendenze da requirements.txt
#   3. Avvio del Backend FastAPI (porta 8001)
#   4. Avvio del Frontend NiceGUI (porta 8080)
#   5. Avvio dell'Emulatore Unicorn Worker (client WebSocket)
#
# Su macOS ogni modulo viene aperto in una nuova finestra del Terminale.
# Su Linux ogni modulo viene avviato in background con nohup e i log
# vengono salvati nella cartella logs/.

# Posiziona la shell nella directory del progetto (indipendentemente da dove
# viene invocato lo script)
cd "$(dirname "$0")"
PROJECT_DIR=$(pwd)

echo "[START] Configurazione ed esecuzione dell'architettura modulare..."

# --- Gestione del virtual environment ---
# Crea l'ambiente virtuale con Python 3.12 solo se non esiste già
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

# Rileva il sistema operativo corrente (Darwin = macOS, altro = Linux)
OS_TYPE="$(uname)"

# Funzione helper per lanciare i moduli in modo compatibile con macOS e Linux.
# Parametri:
#   $1 title    -- nome del modulo (usato come titolo nella finestra / nel log)
#   $2 cmd      -- comando da eseguire all'interno dell'ambiente virtuale
#   $3 log_file -- percorso del file di log (solo Linux)
run_module() {
    local title=$1
    local cmd=$2
    local log_file=$3

    if [ "$OS_TYPE" = "Darwin" ]; then
        # macOS: apre ogni modulo in una nuova finestra del Terminale
        osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_DIR' && source env/bin/activate && $cmd\""
    else
        # Linux: avvia in background con nohup, redirige stdout+stderr nel file di log
        echo "[Linux] Avvio di '$title' in background. Log in: $log_file"
        cd "$PROJECT_DIR" && source env/bin/activate && nohup sh -c "$cmd" > "$log_file" 2>&1 &
    fi
}

# Crea la cartella dei log su Linux (non necessaria su macOS)
if [ "$OS_TYPE" != "Darwin" ]; then
    mkdir -p logs
fi

# --- Avvio dei moduli in ordine di dipendenza ---

# 1. Backend FastAPI (porta 8001)
#    DYLD_LIBRARY_PATH serve su macOS per trovare la libreria Keystone installata via Homebrew.
#    PYTHONPATH=. permette import assoluti dalla root del progetto.
CMD_BACKEND="DYLD_LIBRARY_PATH=/opt/homebrew/opt/keystone/lib PYTHONPATH=. python3 app/backend/main.py"
run_module "Backend FastAPI" "$CMD_BACKEND" "logs/backend.log"

# Attende 2 secondi per garantire che il backend sia in ascolto prima di avviare i client
sleep 2

# 2. Frontend NiceGUI (porta 8080)
CMD_FRONTEND="python3 app/frontend/run.py"
run_module "Frontend NiceGUI" "$CMD_FRONTEND" "logs/frontend.log"

sleep 1

# 3. Emulatore Unicorn Worker (si connette al backend via WebSocket)
CMD_EMULATOR="python3 app/emulator/unicorn_worker.py"
run_module "Emulator" "$CMD_EMULATOR" "logs/emulator.log"

echo "------------------------------------------------------------------"
if [ "$OS_TYPE" = "Darwin" ]; then
    echo "[SUCCESS] Tutti e 3 i moduli sono stati avviati in finestre separate."
else
    echo "[SUCCESS] Tutti e 3 i moduli sono avviati in background."
    echo "[INFO] Puoi monitorare i log con: tail -f logs/*.log"
fi
