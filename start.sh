#!/bin/bash
# start.sh
# Script di avvio di ARM GDK Unicorn per macOS e Linux.
# Esegue in sequenza:
#   1. Creazione del virtual environment Python (se assente)
#   2. Installazione delle dipendenze da requirements.txt e controllo della toolchain GNU per ARM
#   3. Avvio dei tre moduli (backend, emulatore, interfaccia) con app/launcher.py,
#      in questa finestra: chiudendola, o con Ctrl+C, si chiude tutto.

# Posiziona la shell nella directory del progetto (indipendentemente da dove
# viene invocato lo script)
cd "$(dirname "$0")"
PROJECT_DIR=$(pwd)

echo "[START] Configurazione ed esecuzione dell'architettura modulare..."

# --- Gestione del virtual environment ---
# Crea l'ambiente virtuale solo se non esiste già, con il Python più recente tra
# quelli installati (serve almeno la 3.11)
if [ ! -d "env" ]; then
    PYTHON_BIN="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)"
    if ! "$PYTHON_BIN" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        echo "[ERRORE] Serve Python 3.11 o successivo (trovato: $("$PYTHON_BIN" --version 2>&1))."
        exit 1
    fi
    echo "[INIT] Creazione del virtual environment con $PYTHON_BIN..."
    "$PYTHON_BIN" -m venv env || { echo "[ERRORE] Impossibile creare il virtual environment."; exit 1; }
fi

echo "[SETUP] Controllo e installazione dei requisiti..."
source env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Il compilatore usa la toolchain GNU per ARM: se manca, spiega come installarla e si ferma
if ! python -m app.toolchain; then
    deactivate
    exit 1
fi

# --- Avvio ---
# Il lanciatore apre backend, emulatore e interfaccia in questa stessa finestra e li
# chiude tutti insieme: con Ctrl+C, chiudendo la finestra, premendo "Chiudi"
# nell'interfaccia o se uno dei moduli si ferma.
echo "[INFO] Ambiente pronto. Avvio del debugger..."
echo "------------------------------------------------------------------"
exec python -m app.launcher
