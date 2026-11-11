# tests/test_emulator.py
# Prova la classe Emulator del worker: fine del programma, chiamate di sistema,
# breakpoint ed esecuzione a blocchi (fase 2 della roadmap).
#
# Uso:  python -m unittest discover -s tests -v   (dalla cartella del progetto)

import time
import unittest

from support import CompilerFixture

from app.emulator.unicorn_worker import Emulator
from app.toolchain import find_toolchain
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R4

HELLO = """
.global _start
_start:
    mov r7, #4
    mov r0, #1
    ldr r1, =msg
    mov r2, #len
    svc 0
    mov r4, r0          @ valore restituito da write
    mov r7, #1
    mov r0, #0
    svc 0
    mov r5, #99         @ non deve essere mai eseguita
.data
msg: .ascii "  due spazi\\tun tab\\n"
.equ len, . - msg
"""


@unittest.skipUnless(find_toolchain(), "toolchain GNU per ARM non installata")
class EmulatorTest(CompilerFixture, unittest.TestCase):

    def emulator(self, source):
        return Emulator(self.build(source))

    def test_exit_ferma_il_programma(self):
        emu = self.emulator(HELLO)
        self.assertEqual(emu.run(1000), "exit")
        self.assertTrue(emu.terminated)
        self.assertEqual(emu.exit_code, 0)
        pc_finale = emu.pc
        # Dopo exit non si esegue più nulla, né a passi né a blocchi
        self.assertEqual(emu.run(1), "exit")
        self.assertEqual(emu.run(1000), "exit")
        self.assertEqual(emu.pc, pc_finale)
        messages = emu.take_messages()
        self.assertEqual(sum("exit(0)" in m for m in messages), 1, messages)

    def test_write_conserva_spazi_e_restituisce_i_byte(self):
        emu = self.emulator(HELLO)
        emu.run(1000)
        messages = emu.take_messages()
        self.assertIn("[Output Emulatore]   due spazi\tun tab", messages)
        self.assertEqual(emu.mu.reg_read(UC_ARM_REG_R4), len("  due spazi\tun tab\n"))

    def test_write_su_stderr_e_fd_non_valido(self):
        emu = self.emulator("""
.global _start
_start:
    mov r7, #4
    mov r0, #2
    ldr r1, =msg
    mov r2, #3
    svc 0
    mov r0, #5
    svc 0
    mov r4, r0
    mov r7, #1
    svc 0
.data
msg: .ascii "err"
""")
        emu.run(1000)
        messages = emu.take_messages()
        self.assertIn("[Output Emulatore stderr] err", messages)
        self.assertTrue(any("file descriptor 5" in m for m in messages), messages)
        self.assertEqual(emu.mu.reg_read(UC_ARM_REG_R4), (-9) & 0xFFFFFFFF)   # -EBADF

    def test_syscall_non_supportata(self):
        emu = self.emulator("""
.global _start
_start:
    mov r7, #20         @ getpid: non supportata
    svc 0
    mov r4, r0
    mov r7, #1
    svc 0
""")
        self.assertEqual(emu.run(1000), "exit")
        self.assertTrue(any("Chiamata di sistema 20 non supportata" in m for m in emu.take_messages()))
        self.assertEqual(emu.mu.reg_read(UC_ARM_REG_R4), (-38) & 0xFFFFFFFF)   # -ENOSYS

    def test_breakpoint_ferma_prima_dell_istruzione_e_si_riparte(self):
        emu = self.emulator("""
.global _start
_start:
    mov r0, #0
loop:
    add r0, r0, #1
    cmp r0, #3
    bne loop
    mov r7, #1
    svc 0
""")
        emu.set_breakpoints(["0x10004"])      # add r0, r0, #1
        valori = []
        for _ in range(3):
            self.assertEqual(emu.run(1000), "breakpoint")
            self.assertEqual(emu.pc, 0x10004)
            valori.append(emu.mu.reg_read(UC_ARM_REG_R0))
        self.assertEqual(valori, [0, 1, 2])   # fermo prima di eseguire l'add
        self.assertEqual(emu.run(1000), "exit")
        self.assertEqual(emu.exit_code, 3)

    def test_step_singolo_anche_su_un_breakpoint(self):
        emu = self.emulator(".global _start\n_start:\n    mov r0, #1\n    mov r0, #2\n    mov r7, #1\n    svc 0\n")
        emu.set_breakpoints([0x10000, 0x10004])
        self.assertEqual(emu.run(1), "count")
        self.assertEqual(emu.pc, 0x10004)

    def test_eccezione_della_cpu(self):
        emu = self.emulator(".global _start\n_start:\n    mov r1, #0\n    ldr r0, [r1]\n")
        self.assertEqual(emu.run(10), "error")
        self.assertTrue(emu.terminated)
        self.assertTrue(any("Eccezione della CPU a 0x10004" in m for m in emu.take_messages()))

    def test_memoria_data_con_etichette(self):
        emu = self.emulator("""
.global _start
_start:
    ldr r1, =val
    mov r0, #7
    str r0, [r1]
    mov r7, #1
    svc 0
.data
msg: .ascii "Ciao"
val: .word 42
.bss
buf: .space 8
""")
        rows = emu.data_view()
        self.assertEqual([r["label"] for r in rows], ["msg", "val", "buf", ""])
        self.assertEqual(rows[0]["ascii"], "Ciao")
        self.assertEqual(rows[1]["value"], "0x0000002A")
        self.assertEqual([r["section"] for r in rows], [".data", ".data", ".bss", ".bss"])
        emu.run(1000)
        self.assertEqual(emu.data_view()[1]["value"], "0x00000007")   # la str si vede subito

    def test_programma_senza_dati(self):
        emu = self.emulator(".global _start\n_start:\n    mov r7, #1\n    svc 0\n")
        self.assertEqual(emu.data_view(), [])

    def test_esecuzione_a_blocchi_veloce(self):
        emu = self.emulator("""
.global _start
_start:
    ldr r0, =100000
loop:
    subs r0, r0, #1
    bne loop
    mov r7, #1
    svc 0
""")
        start = time.monotonic()
        steps = 0
        while emu.run(20_000) == "count":
            steps += 1
        self.assertTrue(emu.terminated)
        self.assertGreater(steps, 5)                    # davvero a blocchi
        self.assertLess(time.monotonic() - start, 10)   # ~200.000 istruzioni in pochi secondi


if __name__ == "__main__":
    unittest.main()
