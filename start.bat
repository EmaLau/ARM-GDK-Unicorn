@echo off
REM start.bat
REM Script di avvio per Windows dell'architettura ARM GDK Unicorn.
REM Esegue in sequenza:
REM   1. Creazione del virtual environment Python (se assente)
REM   2. Installazione delle dipendenze da requirements.txt
REM   3. Avvio del Backend FastAPI in una nuova finestra cmd (porta 8001)
REM   4. Avvio del Frontend NiceGUI in una nuova finestra cmd (porta 8080)
REM   5. Avvio dell'Emulatore Unicorn Worker in una nuova finestra cmd

REM Posiziona la shell nella directory del progetto
cd /d "%~dp0"
set "PROJECT_DIR=%cd%"

echo [START] Configurazione ed esecuzione dell'architettura modulare...

REM Crea il virtual environment con Python se non esiste
if not exist "env" (
    echo [INIT] Creazione del virtual environment con Python 3.12...
    python -m venv env
)

echo [SETUP] Controllo e installazione dei requisiti...
call env\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
call env\Scripts\deactivate.bat

echo [INFO] Ambiente pronto. Lancio i moduli in finestre separate...
echo ------------------------------------------------------------------

REM --- Avvio dei moduli in ordine di dipendenza ---

REM 1. Backend FastAPI (porta 8001)
REM    PYTHONPATH=. permette import assoluti dalla root del progetto.
REM    /k mantiene la finestra aperta anche in caso di errore per leggere i log.
echo [INFO] Avvio Backend FastAPI...
start "Backend FastAPI" cmd /k "cd /d "%PROJECT_DIR%" && call env\Scripts\activate.bat && set PYTHONPATH=. && python app/backend/main.py"

REM Attende 2 secondi per garantire che il backend sia in ascolto
timeout /t 2 /nobreak >nul

REM 2. Frontend NiceGUI (porta 8080)
echo [INFO] Avvio Interfaccia Grafica NiceGUI...
start "Frontend NiceGUI" cmd /k "cd /d "%PROJECT_DIR%" && call env\Scripts\activate.bat && python app/frontend/run.py"

timeout /t 1 /nobreak >nul

REM 3. Emulatore Unicorn Worker (si connette al backend via WebSocket)
echo [INFO] Avvio Emulatore Unicorn Worker...
start "Unicorn Worker" cmd /k "cd /d "%PROJECT_DIR%" && call env\Scripts\activate.bat && python app/emulator/unicorn_worker.py"

echo [SUCCESS] Tutti e 3 i moduli sono stati avviati correttamente nelle loro finestre.
pause
