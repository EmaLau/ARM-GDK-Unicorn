# app/backend/core_compiler.py
# Compilatore ARMv7 basato sulla toolchain GNU (arm-none-eabi-as e arm-none-eabi-ld).
# Assembla la copia di lavoro del sorgente, la collega in un ELF caricabile
# dall'emulatore Unicorn e ricava dalle informazioni di debug (DWARF) la
# corrispondenza esatta tra le righe del sorgente e gli indirizzi delle istruzioni.

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from elftools.elf.elffile import ELFFile

from app.paths import BUILD_DIR, BUILD_ELF, BUILD_LINES, BUILD_OBJ, PROJECT_ROOT, ensure_workspace
from app.toolchain import find_toolchain, install_help

# Indirizzo di caricamento della sezione .text; la sezione .data segue subito dopo.
CODE_ADDRESS = 0x10000

# Messaggi dell'assembler: "workspace/main.s:12: Error: bad instruction `foo'"
_AS_MESSAGE = re.compile(r"^.*?:(\d+): (Error|Warning): (.*)$")
# Messaggi del linker: ".../main.s:3:(.text+0x0): undefined reference to `foo'"
_LD_MESSAGE = re.compile(r"^.*?:(\d+):\(.*?\): (.*)$")


@dataclass
class CompileResult:
    ok: bool
    messages: list = field(default_factory=list)  # righe pronte per la console dell'interfaccia


class ARMCompiler:
    """
    Compila sorgenti ARMv7 GNU Assembly con la toolchain GNU vera:

      1. arm-none-eabi-as  -> build/main.o   (con informazioni di debug -g)
      2. arm-none-eabi-ld  -> build/main.elf (.text a CODE_ADDRESS, entry point _start)
      3. lettura del DWARF -> build/lines.json (riga del sorgente -> indirizzo)

    La sintassi è quella del corso: etichette, direttive (.data, .word, .ascii, .equ…),
    pseudo-istruzioni come ldr rX, =label e commenti @ sono gestiti dall'assembler.
    """

    def __init__(self, project_root: Path = PROJECT_ROOT):
        self.project_root = project_root
        # Copia di lavoro del sorgente Assembly (workspace/main.s, ignorata da git)
        self.src_path = ensure_workspace()
        self.elf_path = BUILD_ELF

    def save_and_compile(self, source_code: str) -> CompileResult:
        """Salva il sorgente nella copia di lavoro e lo compila."""
        self.src_path.write_text(source_code, encoding="utf-8")
        print(f"[Compiler] Codice salvato in {self.src_path}")
        return self.compile()

    def compile(self) -> CompileResult:
        tools = find_toolchain()
        if not tools:
            return CompileResult(False, [
                "[Compiler Errore] Toolchain GNU per ARM non trovata (servono arm-none-eabi-as e arm-none-eabi-ld).",
                *("[Compiler Errore] " + line for line in install_help().splitlines()),
            ])

        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        # Un binario vecchio non deve restare caricabile se la nuova compilazione fallisce
        for stale in (BUILD_OBJ, BUILD_ELF, BUILD_LINES):
            stale.unlink(missing_ok=True)

        # Percorso relativo: i messaggi d'errore mostrano "workspace/main.s:<riga>"
        src_rel = self.src_path.relative_to(self.project_root)

        messages = []
        ok, out = self._run([tools["as"], "-march=armv7-a", "-g", "-o", str(BUILD_OBJ), str(src_rel)])
        messages += self._format(out, _AS_MESSAGE, assembler=True)
        if not ok:
            return CompileResult(False, messages or ["[Compiler Errore] Assemblaggio fallito."])

        ld_args = [tools["ld"], "-N", "-Ttext=" + hex(CODE_ADDRESS), "-e", "_start",
                   "-o", str(BUILD_ELF), str(BUILD_OBJ)]
        ok, out = self._run(ld_args[:2] + ["--no-warn-rwx-segments"] + ld_args[2:])
        if not ok and "no-warn-rwx-segments" in out:
            # Le binutils precedenti alla 2.39 non conoscono l'opzione: si riprova senza
            ok, out = self._run(ld_args)
        messages += self._format(out, _LD_MESSAGE, assembler=False)
        if not ok:
            return CompileResult(False, messages or ["[Compiler Errore] Collegamento fallito."])

        lines = self._line_map()
        BUILD_LINES.write_text(json.dumps(lines), encoding="utf-8")

        messages.append("[Compiler SUCCESS] Compilazione completata con successo.")
        return CompileResult(True, messages)

    def _run(self, args):
        """Esegue un programma della toolchain e restituisce (successo, output combinato)."""
        try:
            proc = subprocess.run(args, cwd=self.project_root, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()

    @staticmethod
    def _format(output: str, pattern, assembler: bool):
        """Trasforma l'output di as/ld in messaggi per la console, con il numero di riga."""
        result = []
        for raw in output.splitlines():
            raw = raw.strip()
            if not raw or raw.endswith("Assembler messages:") or ": in function `" in raw:
                continue
            raw = re.sub(r"^.*?arm-none-eabi-ld(\.exe)?: ", "", raw)
            m = pattern.match(raw)
            if m and assembler:
                line, kind, text = m.groups()
                tag = "Errore" if kind == "Error" else "Avviso"
                result.append(f"[Compiler {tag}] Riga {line}: {text}")
            elif m:
                line, text = m.groups()
                result.append(f"[Compiler Errore] Riga {line}: {text}")
            elif raw.startswith("warning: "):
                result.append("[Compiler Avviso] " + raw[len("warning: "):])
            else:
                result.append("[Compiler Errore] " + raw)
        return result

    def _line_map(self) -> dict:
        """Riga del sorgente (stringa, 1-based) -> indirizzo della prima istruzione su quella riga."""
        mapping = {}
        with open(self.elf_path, "rb") as f:
            elf = ELFFile(f)
            if not elf.has_dwarf_info():
                return mapping
            dwarf = elf.get_dwarf_info()
            for cu in dwarf.iter_CUs():
                program = dwarf.line_program_for_CU(cu)
                if program is None:
                    continue
                for entry in program.get_entries():
                    state = entry.state
                    if state is None or state.end_sequence:
                        continue
                    key = str(state.line)
                    if key not in mapping or state.address < int(mapping[key], 16):
                        mapping[key] = hex(state.address)
        return mapping
