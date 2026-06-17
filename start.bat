@echo off
REM start.bat
REM Script di avvio per Windows dell'architettura ARM GDK Unicorn.
REM Esegue in sequenza:
REM   1. Creazione del virtual environment Python (se assente)
REM   2. Installazione delle dipendenze da requirements.txt
REM   3. Avvio del Backend FastAPI in una nuova finestra cmd (porta 8001)
REM   4. Attesa ATTIVA finché la porta 8001 non risponde (max 30 secondi)
REM   5. Avvio dell'Emulatore Unicorn Worker in una nuova finestra cmd
REM   6. Avvio del Frontend NiceGUI in una nuova finestra cmd (porta 8080)

REM Posiziona la shell nella directory del progetto
cd /d "%~dp0"
set "PROJECT_DIR=%cd%"

echo [START] Configurazione ed esecuzione dell'architettura modulare...

REM -----------------------------------------------------------------------
REM 1. Virtual environment
REM -----------------------------------------------------------------------
if not exist "env" (
    echo [INIT] Creazione del virtual environment con Python 3.12...
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
call env\Scripts\deactivate.bat

echo [INFO] Ambiente pronto. Lancio i moduli in finestre separate...
echo ------------------------------------------------------------------

REM -----------------------------------------------------------------------
REM 3. Backend FastAPI (porta 8001) — deve partire per primo
REM -----------------------------------------------------------------------
echo [INFO] Avvio Backend FastAPI (porta 8001)...
start "Backend FastAPI" cmd /k "cd /d "%PROJECT_DIR%" && call env\Scripts\activate.bat && set PYTHONPATH=. && python app/backend/main.py"

REM -----------------------------------------------------------------------
REM 4. Attesa ATTIVA: verifica che la porta 8001 sia in ascolto
REM    Usa PowerShell per tentare una connessione TCP ogni secondo.
REM    Timeout massimo: 30 secondi (30 tentativi).
REM -----------------------------------------------------------------------
echo [WAIT] Attendo che il Backend sia pronto sulla porta 8001...
set BACKEND_READY=0
for /L %%i in (1,1,30) do (
    if "!BACKEND_READY!"=="1" goto :backend_ok
    powershell -NoProfile -Command ^
        "try { $t = New-Object Net.Sockets.TcpClient; $t.Connect('127.0.0.1',8001); $t.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 (
        set BACKEND_READY=1
    ) else (
        timeout /t 1 /nobreak >nul
    )
)

REM Abilita delayed expansion per leggere la variabile aggiornata nel loop
setlocal enabledelayedexpansion

if "!BACKEND_READY!"=="0" (
    echo [ERRORE] Il Backend non ha risposto entro 30 secondi. Controlla la finestra "Backend FastAPI".
    pause
    exit /b 1
)

:backend_ok
echo [OK] Backend in ascolto. Avvio Unicorn Worker e Frontend...

REM -----------------------------------------------------------------------
REM 5. Unicorn Worker — si connette al backend via WebSocket
REM    Parte DOPO che il backend è confermato pronto.
REM -----------------------------------------------------------------------
echo [INFO] Avvio Emulatore Unicorn Worker...
start "Unicorn Worker" cmd /k "cd /d "%PROJECT_DIR%" && call env\Scripts\activate.bat && python app/emulator/unicorn_worker.py"

REM Piccolo ritardo per dare al worker il tempo di registrarsi prima che la UI si connetta
timeout /t 1 /nobreak >nul

REM -----------------------------------------------------------------------
REM 6. Frontend NiceGUI (porta 8080) — parte per ultima
REM -----------------------------------------------------------------------
echo [INFO] Avvio Interfaccia Grafica NiceGUI...
start "Frontend NiceGUI" cmd /k "cd /d "%PROJECT_DIR%" && call env\Scripts\activate.bat && python app/frontend/run.py"

echo.
echo [SUCCESS] Tutti i moduli sono stati avviati:
echo   - Backend FastAPI  ^>  http://127.0.0.1:8001
echo   - Unicorn Worker   ^>  ws://127.0.0.1:8001/telemetry
echo   - Frontend NiceGUI ^>  http://127.0.0.1:8080
echo.
pause