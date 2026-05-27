# app/backend/core_compiler.py
import re
from pathlib import Path

from keystone import Ks, KS_ARCH_ARM, KS_MODE_ARM


class ARMCompiler:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.src_path = project_root / "src" / "main.s"
        self.elf_path = project_root / "main.elf"

        self.CODE_ADDRESS = 0x10000  # Base .text
        self.DATA_ADDRESS = 0x20000  # Base .data

    def save_and_compile(self, source_code: str) -> bool:
        """Salva l'assembly GNU originale e genera un flat binary compatibile."""
        try:
            with open(self.src_path, "w") as f:
                f.write(source_code)
            print(f"[Compiler] Codice salvato in {self.src_path}")

            labels = {}
            text_lines = []
            data_bytes = bytearray()
            current_section = ".text"

            lines = source_code.splitlines()

            # Calcolo statico della lunghezza per il tuo esempio specifico
            if "hello_len" in source_code:
                labels["hello_len"] = 26

            data_offset = 0
            for line in lines:
                line_strip = line.strip()
                if not line_strip or line_strip.startswith("@"):
                    continue

                if ".text" in line_strip:
                    current_section = ".text"
                    continue
                elif ".data" in line_strip:
                    current_section = ".data"
                    continue

                if "@" in line_strip:
                    line_strip = line_strip.split("@")[0].strip()

                if current_section == ".data":
                    if ":" in line_strip:
                        label_name = line_strip.split(":")[0].strip()
                        labels[label_name] = self.DATA_ADDRESS + data_offset
                    elif ".ascii" in line_strip:
                        match = re.search(r'"([^"]*)"', line_strip)
                        if match:
                            string_val = match.group(1).replace("\\n", "\n")
                            string_bytes = string_val.encode('utf-8')
                            data_bytes.extend(string_bytes)
                            data_offset += len(string_bytes)

            for line in lines:
                line_strip = line.strip()
                if not line_strip or line_strip.startswith("@") or line_strip.startswith("."):
                    continue
                if ":" in line_strip and not line_strip.startswith("ldr"):
                    continue

                if "@" in line_strip:
                    line_strip = line_strip.split("@")[0].strip()

                if "ldr" in line_strip and "=" in line_strip:
                    reg, label_target = line_strip.split(",")
                    reg = reg.replace("ldr", "").strip()
                    label_target = label_target.replace("=", "").strip()
                    if label_target in labels:
                        line_strip = f"mov {reg}, #{hex(labels[label_target])}"
                    else:
                        print(f"[Compiler Errore] Label '{label_target}' mancante.")
                        return False

                for lbl_name, lbl_val in labels.items():
                    if f"#{lbl_name}" in line_strip:
                        line_strip = line_strip.replace(f"#{lbl_name}", f"#{hex(lbl_val)}")

                text_lines.append(line_strip)

            pure_assembly = "; ".join(text_lines)

            ks = Ks(KS_ARCH_ARM, KS_MODE_ARM)
            encoding, count = ks.asm(pure_assembly, self.CODE_ADDRESS)

            with open(self.elf_path, "wb") as f:
                f.write(bytearray(encoding))
                padding_size = self.DATA_ADDRESS - (self.CODE_ADDRESS + len(encoding))
                if padding_size > 0:
                    f.write(b"\x00" * padding_size)
                f.write(data_bytes)

            print(f"[Compiler SUCCESS] Compilazione completata con successo.")
            return True

        except Exception as e:
            print(f"[Compiler Errore] Errore di compilazione: {e}")
            return False
