# tests/test_launcher.py
# Prova app/launcher.py dal vivo: avvio dei tre moduli e chiusura di tutti insieme.
#
# Gira su una copia temporanea del progetto e viene saltato se le porte 8001 o 8080
# sono già occupate: non tocca mai un debugger aperto dall'utente.

import importlib.util
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTS = (8001, 8080)


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ports_free():
    return not any(port_in_use(p) for p in PORTS)


@unittest.skipUnless(importlib.util.find_spec("nicegui"), "nicegui non installato")
class LauncherTest(unittest.TestCase):

    def setUp(self):
        if not ports_free():
            self.skipTest("porte 8001/8080 occupate: probabilmente il debugger è aperto")
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "app", self.tmp / "app", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "examples", self.tmp / "examples")
        self.output = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def launch(self):
        env = dict(os.environ, ARM_GDK_NO_BROWSER="1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
        proc = subprocess.Popen([sys.executable, "-m", "app.launcher"], cwd=self.tmp, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", **extra)
        def read():
            with proc.stdout:   # chiude la pipe quando il lanciatore termina
                self.output.extend(proc.stdout)
        threading.Thread(target=read, daemon=True).start()
        return proc

    def wait_for(self, text, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if any(text in line for line in self.output):
                return True
            time.sleep(0.2)
        return False

    def test_avvio_e_chiusura_di_tutti_i_moduli(self):
        proc = self.launch()
        try:
            self.assertTrue(self.wait_for("[pronto]", 90), "".join(self.output))
            deadline = time.monotonic() + 60   # l'interfaccia può metterci un po' ad aprire la porta
            while not all(port_in_use(p) for p in PORTS) and time.monotonic() < deadline:
                time.sleep(0.3)
            self.assertTrue(all(port_in_use(p) for p in PORTS), "".join(self.output))

            # Come Ctrl+C (o Ctrl+Interr su Windows)
            proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertTrue(self.wait_for("Debugger chiuso.", 5), "".join(self.output))
        time.sleep(1)
        self.assertTrue(ports_free(), "una porta è rimasta occupata:\n" + "".join(self.output))

    def test_rifiuta_di_partire_se_la_porta_e_occupata(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            busy.bind(("127.0.0.1", 8001))
            busy.listen()
            proc = self.launch()
            self.assertEqual(proc.wait(timeout=30), 1)
        self.assertTrue(self.wait_for("già in uso", 5), "".join(self.output))


if __name__ == "__main__":
    unittest.main()
