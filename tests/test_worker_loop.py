# tests/test_worker_loop.py
# Prova il ciclo principale del worker (main) senza rete: il modulo websocket
# viene sostituito da uno finto che consegna comandi preparati in anticipo e
# raccoglie gli aggiornamenti inviati. Nessuna porta viene aperta, quindi il test
# non interferisce con un debugger già in esecuzione.

import json
import time
import types
import unittest

from support import CompilerFixture

import app.emulator.unicorn_worker as worker
from app.toolchain import find_toolchain

PROG = """
.global _start
_start:
    ldr r0, =30000
loop:
    subs r0, r0, #1
    bne loop
    mov r7, #1
    mov r0, #7
    svc 0
"""


class Timeout(Exception):
    pass


class FakeWS:
    """Consegna i comandi uno alla volta (anche con un ritardo) e registra gli invii."""

    def __init__(self, script):
        self.script = list(script)   # (secondi di attesa dal comando precedente, comando o None per chiudere)
        self.sent = []
        self.timeout = 0.05
        self.next_at = time.monotonic()

    def settimeout(self, t):
        self.timeout = t

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        if not self.script:
            raise ConnectionError("fine dello script")
        delay, cmd = self.script[0]
        now = time.monotonic()
        if now < self.next_at + delay:
            time.sleep(min(self.timeout, self.next_at + delay - now))
            raise Timeout()
        self.script.pop(0)
        self.next_at = time.monotonic()
        if cmd is None:
            raise ConnectionError("chiusura")
        cmd.setdefault("type", "COMMAND")
        return json.dumps(cmd)


@unittest.skipUnless(find_toolchain(), "toolchain GNU per ARM non installata")
class WorkerLoopTest(CompilerFixture, unittest.TestCase):

    def run_loop(self, script):
        elf = self.build(PROG)
        ws = FakeWS(script)
        fake = types.SimpleNamespace(create_connection=lambda url: ws, WebSocketTimeoutException=Timeout)
        saved = (worker.websocket, worker.BUILD_ELF)
        worker.websocket, worker.BUILD_ELF = fake, elf
        try:
            worker.main()
        finally:
            worker.websocket, worker.BUILD_ELF = saved
        logs = []
        for payload in ws.sent:
            out = payload["console_output"]
            logs += out if isinstance(out, list) else ([out] if out else [])
        return ws.sent, logs

    def test_continue_alla_massima_velocita_termina_e_poi_non_esegue_altro(self):
        sent, logs = self.run_loop([
            (0, {"action": "continue", "speed": 0}),
            (2, {"action": "step"}),
            (0.2, {"action": "continue"}),
            (0.5, None),
        ])
        final = [p for p in sent if p["status"] == "terminated"]
        self.assertTrue(final, logs)
        self.assertEqual(sum("exit(7)" in m for m in logs), 1, logs)
        self.assertEqual(len({p["pc"] for p in final}), 1)          # dopo exit il PC non cambia
        self.assertEqual(sum("premi Restart" in m for m in logs), 2, logs)
        self.assertLess(len(sent), 60)                             # un aggiornamento per tick, non per istruzione

    def test_velocita_lenta_e_pausa(self):
        sent, logs = self.run_loop([
            (0, {"action": "continue", "speed": 5}),
            (1.0, {"action": "pause"}),
            (0.3, None),
        ])
        executed = [p for p in sent if p["console_output"] is None]
        # ~5 istruzioni in un secondo (una subito, poi una ogni 0,2 s)
        self.assertTrue(4 <= len(executed) <= 7, len(executed))
        self.assertIn("[Sistema] Esecuzione in pausa.", logs)
        self.assertEqual(sent[-1]["status"], "running")

    def test_breakpoint_e_restart(self):
        sent, logs = self.run_loop([
            (0, {"action": "continue", "speed": 0, "breakpoints": ["0x10004"]}),
            (0.5, {"action": "continue", "speed": 0, "breakpoints": ["0x10004"]}),
            (0.5, {"action": "restart", "breakpoints": []}),
            (0.3, None),
        ])
        self.assertEqual(sum("[Breakpoint] Raggiunto indirizzo 0x10004" in m for m in logs), 2, logs)
        self.assertIn("[Restart] Emulatore resettato.", logs)
        self.assertEqual(sent[-1]["pc"], "0x10000")


if __name__ == "__main__":
    unittest.main()
