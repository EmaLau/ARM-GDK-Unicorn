@echo off
REM start.bat
REM Script di avvio di ARM GDK Unicorn per Windows.
REM Esegue in sequenza:
REM   1. Creazione del virtual environment Python (se assente)
REM   2. Installazione delle dipendenze da requirements.txt e controllo della toolchain GNU per ARM
REM   3. Avvio dei tre moduli (backend, emulatore, interfaccia) con app\launcher.py,
REM      in questa finestra: chiudendola, o con Ctrl+C, si chiude tutto.

REM Posiziona la shell nella directory del progetto
cd /d "%~dp0"
set "PROJECT_DIR=%cd%"

echo [START] Configurazione ed esecuzione dell'architettura modulare...

REM -----------------------------------------------------------------------
REM 1. Virtual environment
REM -----------------------------------------------------------------------
if not exist "env" (
    python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
    if errorlevel 1 (
        echo [ERRORE] Serve Python 3.11 o successivo, installato e nel PATH.
        pause
        exit /b 1
    )
    echo [INIT] Creazione del virtual environment...
    python -m venv env
    if errorlevel 1 (
        echo [ERRORE] Impossibile creare il virtual environment. Verifica che Python sia installato.
        pause
        exit /b 1
    )
)

REM -----------------------------------------------------------------------
REM 2. Installazione dipendenze
REM -----------------------------------------------------------------------
echo [SETUP] Controllo e installazione dei requisiti...
call env\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERRORE] Installazione dipendenze fallita. Controlla requirements.txt e la connessione.
    pause
    exit /b 1
)

REM Il compilatore usa la toolchain GNU per ARM: se manca, spiega come installarla e si ferma
python -m app.toolchain
if errorlevel 1 (
    call env\Scripts\deactivate.bat
    pause
    exit /b 1
)

REM -----------------------------------------------------------------------
REM 3. Avvio: backend, emulatore e interfaccia in questa stessa finestra.
REM    Il lanciatore li chiude tutti insieme: con Ctrl+C, chiudendo la finestra,
REM    premendo "Chiudi" nell'interfaccia o se uno dei moduli si ferma.
REM -----------------------------------------------------------------------
echo [INFO] Ambiente pronto. Avvio del debugger...
echo ------------------------------------------------------------------
python -m app.launcher
call env\Scripts\deactivate.bat
pause
