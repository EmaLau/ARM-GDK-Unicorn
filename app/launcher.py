# app/launcher.py
# Avvia i tre moduli (backend, emulatore, interfaccia) come processi figli nella
# stessa finestra e li chiude tutti insieme.
#
# Si chiude tutto quando:
#   - si preme Ctrl+C o si chiude la finestra del terminale;
#   - si preme "Chiudi" nell'interfaccia (il frontend termina e il lanciatore se ne accorge);
#   - uno qualsiasi dei moduli si ferma per un errore.
#
# Uso (dalla cartella del progetto, con il virtual environment attivo):
#   python -m app.launcher

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BACKEND_PORT = 8001
FRONTEND_PORT = 8080

MODULES = [
    ("backend", "app/backend/main.py"),
    ("emulatore", "app/emulator/unicorn_worker.py"),
    ("interfaccia", "app/frontend/run.py"),
]

_stopping = threading.Event()


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, proc: subprocess.Popen, seconds: float) -> bool:
    """Aspetta che la porta risponda; False se il processo muore o scade il tempo."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if port_in_use(port):
            return True
        time.sleep(0.2)
    return False


def _pipe_output(name: str, proc: subprocess.Popen):
    """Ripete l'output di un modulo con il suo nome davanti, per distinguerli nella stessa finestra."""
    for line in proc.stdout:
        sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()


def start(name: str, script: str) -> subprocess.Popen:
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    # Gruppo di processi separato: Ctrl+C arriva solo al lanciatore, che poi chiude
    # i moduli in ordine (senza una cascata di errori KeyboardInterrupt nei figli).
    # La chiusura della finestra li raggiunge comunque tramite il lanciatore.
    if os.name == "nt":
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group = {"start_new_session": True}
    proc = subprocess.Popen(
        [sys.executable, script], cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, **group,
    )
    threading.Thread(target=_pipe_output, args=(name, proc), daemon=True).start()
    print(f"[avvio] {name} avviato (pid {proc.pid})")
    return proc


def stop_all(procs):
    """Chiede a ogni modulo di chiudersi; dopo 5 secondi chiude d'ufficio quelli rimasti."""
    for _, proc in procs:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.monotonic() + 5
    for name, proc in procs:
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print(f"[chiusura] {name}: chiuso")


def main() -> int:
    for port in (BACKEND_PORT, FRONTEND_PORT):
        if port_in_use(port):
            print(f"[errore] La porta {port} è già in uso: il debugger è forse già aperto in un'altra finestra.")
            return 1

    def on_signal(signum, frame):
        _stopping.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    if hasattr(signal, "SIGHUP"):     # chiusura della finestra del terminale (macOS, Linux)
        signal.signal(signal.SIGHUP, on_signal)
    if hasattr(signal, "SIGBREAK"):   # Ctrl+Interr e chiusura della console (Windows)
        signal.signal(signal.SIGBREAK, on_signal)

    procs = []
    try:
        backend = start(*MODULES[0])
        procs.append((MODULES[0][0], backend))
        if not wait_for_port(BACKEND_PORT, backend, 30):
            print("[errore] Il backend non è partito: controlla i messaggi qui sopra.")
            return 1
        for name, script in MODULES[1:]:
            procs.append((name, start(name, script)))

        print(f"[pronto] Debugger aperto su http://127.0.0.1:{FRONTEND_PORT}  "
              "(Ctrl+C, la chiusura di questa finestra o il pulsante 'Chiudi' chiudono tutto)")

        while not _stopping.is_set():
            for name, proc in procs:
                if proc.poll() is not None:
                    reason = "chiusa con il pulsante 'Chiudi'" if name == "interfaccia" and proc.returncode == 0 \
                        else f"si è fermato (codice {proc.returncode})"
                    print(f"[chiusura] {name} {reason}: chiudo anche gli altri moduli.")
                    _stopping.set()
                    break
            _stopping.wait(0.3)
    finally:
        stop_all(procs)
    print("[chiusura] Debugger chiuso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
