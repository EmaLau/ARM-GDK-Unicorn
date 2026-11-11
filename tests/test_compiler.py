# tests/test_compiler.py
# Prova il compilatore (toolchain GNU) e il caricamento nell'emulatore con i
# programmi dell'audit del 2026-11-11: ognuno era sbagliato col vecchio compilatore.
#
# Uso:  python -m unittest discover -s tests -v   (dalla cartella del progetto)
# Se la toolchain GNU per ARM non è installata i test vengono saltati.

import types
import unittest

from support import ROOT, CompilerFixture, core_compiler

from app.emulator import unicorn_worker as worker  # noqa: E402
from app.toolchain import find_toolchain  # noqa: E402
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_INTR  # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,  # noqa: E402
                               UC_ARM_REG_R2, UC_ARM_REG_R7, UC_ARM_REG_SP)

MAX_STEPS = 10_000


@unittest.skipUnless(find_toolchain(), "toolchain GNU per ARM non installata")
class CompilerTest(CompilerFixture, unittest.TestCase):

    def run_program(self, source):
        """Compila ed esegue fino a exit; restituisce (r0 all'uscita, output di write, registri)."""
        result = self.compiler.save_and_compile(source)
        self.assertTrue(result.ok, "\n".join(result.messages))

        mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        entry, loaded = worker.load_program(mu, self.compiler.elf_path)
        self.assertTrue(loaded)
        mu.mem_map(0x70000000, 0x10000)
        mu.reg_write(UC_ARM_REG_SP, 0x7000FFFC)
        mu.reg_write(UC_ARM_REG_PC, entry)

        state = {"exit": None, "out": b""}

        def on_svc(uc, intno, _):
            r7 = uc.reg_read(UC_ARM_REG_R7)
            if r7 == 4:
                state["out"] += bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)))
            elif r7 == 1:
                state["exit"] = uc.reg_read(UC_ARM_REG_R0)
                uc.emu_stop()

        mu.hook_add(UC_HOOK_INTR, on_svc)
        for _ in range(MAX_STEPS):
            if state["exit"] is not None:
                break
            worker.execute_single_step(mu)
        self.assertIsNotNone(state["exit"], "il programma non ha chiamato exit")
        return state["exit"], state["out"], mu

    # --- I programmi dell'audit ---------------------------------------------

    def test_esempio_stampa_la_stringa_intera(self):
        _, out, _ = self.run_program((ROOT / "examples" / "hello.s").read_text())
        self.assertEqual(out, b"Hello, World!!!!!!!!!!!!!ciaoneeeeee\n")

    def test_ciclo_con_etichetta(self):
        code, _, _ = self.run_program("""
.global _start
_start:
    mov r0, #0
    mov r1, #5
loop:
    add r0, r0, r1
    subs r1, r1, #1
    bne loop
    mov r7, #1
    svc 0
""")
        self.assertEqual(code, 15)

    def test_funzione_con_bl_push_pop(self):
        code, _, _ = self.run_program("""
.global _start
_start:
    mov r0, #3
    bl double
    mov r7, #1
    svc 0
double:
    push {lr}
    add r0, r0, r0
    pop {pc}
""")
        self.assertEqual(code, 6)

    def test_etichetta_e_istruzione_sulla_stessa_riga(self):
        code, _, _ = self.run_program(".global _start\n_start: mov r0, #7\n    mov r7, #1\n    svc 0\n")
        self.assertEqual(code, 7)

    def test_etichetta_e_ascii_sulla_stessa_riga(self):
        _, out, _ = self.run_program("""
.global _start
_start:
    mov r7, #4
    mov r0, #1
    ldr r1, =msg
    mov r2, #3
    svc 0
    mov r7, #1
    svc 0
.data
msg: .ascii "abc"
""")
        self.assertEqual(out, b"abc")

    def test_seconda_stringa(self):
        _, out, _ = self.run_program("""
.global _start
_start:
    mov r7, #4
    mov r0, #1
    ldr r1, =b
    mov r2, #2
    svc 0
    mov r7, #1
    svc 0
.data
a:
    .ascii "xyz"
b:
    .ascii "ok"
""")
        self.assertEqual(out, b"ok")

    def test_word_e_asciz(self):
        code, _, _ = self.run_program("""
.global _start
_start:
    ldr r1, =val
    ldr r0, [r1]
    mov r7, #1
    svc 0
.data
s:
    .asciz "hi"
.align 2
val:
    .word 42
""")
        self.assertEqual(code, 42)

    def test_chiocciola_e_escape_nelle_stringhe(self):
        _, out, _ = self.run_program("""
.global _start
_start:
    mov r7, #4
    mov r0, #1
    ldr r1, =m
    mov r2, #len
    svc 0
    mov r7, #1
    svc 0
.data
m:
    .ascii "a@b\\t\\"c\\"\\n"     @ commento dopo la stringa
.equ len, . - m
""")
        self.assertEqual(out, b'a@b\t"c"\n')

    def test_costante_calcolata(self):
        _, out, _ = self.run_program("""
.global _start
_start:
    mov r7, #4
    mov r0, #1
    ldr r1, =hello
    mov r2, #hello_len
    svc 0
    mov r7, #1
    svc 0
.data
hello:
    .ascii "Hi\\n"
hello_len = (. - hello)
""")
        self.assertEqual(out, b"Hi\n")

    # --- Messaggi e corrispondenza righe/indirizzi ----------------------------

    def test_errore_con_numero_di_riga(self):
        result = self.compiler.save_and_compile(".global _start\n_start:\n    mov r0, #1\n    bogus r1\n")
        self.assertFalse(result.ok)
        self.assertTrue(any("Riga 4" in m and "bogus" in m for m in result.messages), result.messages)

    def test_etichetta_mancante_con_numero_di_riga(self):
        result = self.compiler.save_and_compile(".global _start\n_start:\n    b nowhere\n")
        self.assertFalse(result.ok)
        self.assertTrue(any("Riga 3" in m and "nowhere" in m for m in result.messages), result.messages)

    def test_compilazione_fallita_non_lascia_il_programma_vecchio(self):
        self.assertTrue(self.compiler.save_and_compile(".global _start\n_start: b _start\n").ok)
        self.assertFalse(self.compiler.save_and_compile("bogus\n").ok)
        self.assertFalse(self.compiler.elf_path.exists())

    def test_righe_mappate_sugli_indirizzi_reali(self):
        import json
        self.compiler.save_and_compile(".global _start\n_start: mov r0, #1\n\nloop:\n    b loop\n")
        lines = json.loads((self.tmp / "build" / "lines.json").read_text())
        self.assertEqual(lines, {"2": "0x10000", "5": "0x10004"})


class MissingToolchainTest(unittest.TestCase):

    def test_messaggio_chiaro_se_manca_la_toolchain(self):
        saved = core_compiler.find_toolchain
        core_compiler.find_toolchain = lambda: None
        try:
            result = core_compiler.ARMCompiler.compile(types.SimpleNamespace())
        finally:
            core_compiler.find_toolchain = saved
        self.assertFalse(result.ok)
        self.assertIn("non trovata", result.messages[0])
        self.assertGreater(len(result.messages), 1)  # seguono le istruzioni di installazione


if __name__ == "__main__":
    unittest.main()
