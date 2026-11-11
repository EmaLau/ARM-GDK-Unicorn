# tests/support.py
# Preparazione comune ai test: compilatore che lavora in una cartella temporanea,
# così workspace/ e build/ del progetto restano intatti.

import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Il worker importa websocket-client: per i test basta un modulo finto
sys.modules.setdefault("websocket", types.ModuleType("websocket"))

import app.backend.core_compiler as core_compiler  # noqa: E402
import app.paths as paths  # noqa: E402


class CompilerFixture:
    """Da usare insieme a unittest.TestCase: fornisce self.compiler e self.build(source)."""

    def setUp(self):
        # Ogni test lavora in una cartella temporanea: workspace/ e build/ del progetto restano intatti
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "workspace").mkdir()
        self.patches = {
            (paths, "WORKSPACE_SRC"): self.tmp / "workspace" / "main.s",
            (core_compiler, "BUILD_DIR"): self.tmp / "build",
            (core_compiler, "BUILD_OBJ"): self.tmp / "build" / "main.o",
            (core_compiler, "BUILD_ELF"): self.tmp / "build" / "main.elf",
            (core_compiler, "BUILD_LINES"): self.tmp / "build" / "lines.json",
        }
        self.saved = {k: getattr(*k) for k in self.patches}
        for (mod, name), value in self.patches.items():
            setattr(mod, name, value)
        (self.tmp / "workspace" / "main.s").touch()
        self.compiler = core_compiler.ARMCompiler(self.tmp)
        self.compiler.src_path = self.tmp / "workspace" / "main.s"
        self.compiler.elf_path = self.tmp / "build" / "main.elf"

    def tearDown(self):
        for (mod, name), value in self.saved.items():
            setattr(mod, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, source):
        """Compila il sorgente e restituisce il percorso dell'ELF (fallisce il test se non compila)."""
        result = self.compiler.save_and_compile(source)
        self.assertTrue(result.ok, "\n".join(result.messages))
        return self.compiler.elf_path
