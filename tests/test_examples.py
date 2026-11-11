# tests/test_examples.py
# Ogni programma in examples/ deve compilare e terminare con il risultato atteso.

import unittest

from support import ROOT, CompilerFixture

from app.emulator.unicorn_worker import Emulator
from app.toolchain import find_toolchain
from unicorn.arm_const import UC_ARM_REG_CPSR

# esempio -> (codice di uscita, output atteso o None)
EXPECTED = {
    "hello.s": (0, "[Output Emulatore] Hello, World!!!!!!!!!!!!!ciaoneeeeee"),
    "somma.s": (55, None),
    "massimo.s": (42, None),
    "fattoriale.s": (120, None),
    "lunghezza.s": (29, "[Output Emulatore] Architettura dei calcolatori"),
    "flag.s": (0, None),
}


def flags(emu):
    cpsr = emu.mu.reg_read(UC_ARM_REG_CPSR)
    return "".join(n for n, bit in (("N", 31), ("Z", 30), ("C", 29), ("V", 28)) if cpsr >> bit & 1)


@unittest.skipUnless(find_toolchain(), "toolchain GNU per ARM non installata")
class ExamplesTest(CompilerFixture, unittest.TestCase):

    def test_tutti_gli_esempi_sono_provati(self):
        self.assertEqual(sorted(p.name for p in (ROOT / "examples").glob("*.s")), sorted(EXPECTED))

    def test_esempi(self):
        for name, (code, output) in EXPECTED.items():
            with self.subTest(esempio=name):
                emu = Emulator(self.build((ROOT / "examples" / name).read_text(encoding="utf-8")))
                self.assertEqual(emu.run(100_000), "exit")
                self.assertEqual(emu.exit_code, code)
                if output:
                    self.assertIn(output, emu.take_messages())

    def test_flag_spiegati_in_flag_s(self):
        emu = Emulator(self.build((ROOT / "examples" / "flag.s").read_text(encoding="utf-8")))
        seen = []
        for _ in range(8):          # le prime 8 istruzioni, una alla volta
            emu.run(1)
            seen.append(flags(emu))
        # mov, subs 5-5, subs 5-6, mvn, adds, ldr, adds overflow, cmp 5-3
        self.assertEqual(seen[1], "ZC")
        self.assertEqual(seen[2], "N")
        self.assertEqual(seen[4], "ZC")
        self.assertEqual(seen[6], "NV")
        self.assertEqual(seen[7], "C")


if __name__ == "__main__":
    unittest.main()
