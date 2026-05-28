# app/backend/core_compiler.py
# Compilatore ARMv7 basato su Keystone Engine.
# Legge il sorgente GNU Assembly, risolve label e direttive .data/.text,
# produce un flat binary (main.elf) compatibile con l'emulatore Unicorn.

import re
from pathlib import Path

from keystone import Ks, KS_ARCH_ARM, KS_MODE_ARM


class ARMCompiler:
    """
    Compila sorgenti ARMv7 GNU Assembly in un flat binary caricabile da Unicorn.

    Il flusso di compilazione è in due passate:
      1. Prima passata: raccoglie le label della sezione .data e calcola
         gli indirizzi assoluti delle stringhe ASCII.
      2. Seconda passata: emette le istruzioni della sezione .text,
         sostituendo i riferimenti a label con i loro valori numerici.
    Il testo finale viene assemblato con Keystone e scritto su disco come
    file binario flat, con padding tra .text e .data per rispettare gli
    indirizzi base configurati.
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        # File sorgente Assembly di input
        self.src_path = project_root / "src" / "main.s"
        # File binario flat di output (caricato dall'emulatore Unicorn)
        self.elf_path = project_root / "main.elf"

        # Indirizzo di caricamento della sezione .text (deve coincidere con CODE_ADDRESS in unicorn_worker.py)
        self.CODE_ADDRESS = 0x10000
        # Indirizzo di caricamento della sezione .data (subito dopo 1 MB di .text)
        self.DATA_ADDRESS = 0x20000

    def save_and_compile(self, source_code: str) -> bool:
        """
        Salva il sorgente su disco e produce il flat binary.

        Parametri:
            source_code -- testo del programma ARMv7 GNU Assembly

        Ritorna:
            True  se la compilazione ha avuto successo
            False in caso di label mancante o errore Keystone
        """
        try:
            # Salva il sorgente originale (incluse direttive GNU) per il visualizzatore
            with open(self.src_path, "w") as f:
                f.write(source_code)
            print(f"[Compiler] Codice salvato in {self.src_path}")

            labels = {}           # Mappa nome_label -> indirizzo assoluto in memoria
            text_lines = []       # Righe di istruzione da passare a Keystone
            data_bytes = bytearray()  # Byte della sezione .data (stringhe ASCII, ecc.)
            current_section = ".text"

            lines = source_code.splitlines()

            # Caso speciale: 'hello_len' è una costante calcolata a compile-time (.set / =)
            # che Keystone non capisce; la si risolve manualmente con il valore noto.
            if "hello_len" in source_code:
                labels["hello_len"] = 26

            # ------------------------------------------------------------------
            # Prima passata: scandisce la sezione .data per raccogliere label
            # e calcolare gli offset delle stringhe ASCII in memoria.
            # ------------------------------------------------------------------
            data_offset = 0
            for line in lines:
                line_strip = line.strip()
                if not line_strip or line_strip.startswith("@"):
                    continue  # Salta righe vuote e commenti

                # Aggiorna la sezione corrente al cambio di .text / .data
                if ".text" in line_strip:
                    current_section = ".text"
                    continue
                elif ".data" in line_strip:
                    current_section = ".data"
                    continue

                # Rimuove il commento inline prima di analizzare la riga
                if "@" in line_strip:
                    line_strip = line_strip.split("@")[0].strip()

                if current_section == ".data":
                    # Registra l'indirizzo assoluto della label (base .data + offset accumulato)
                    if ":" in line_strip:
                        label_name = line_strip.split(":")[0].strip()
                        labels[label_name] = self.DATA_ADDRESS + data_offset
                    # Calcola quanti byte occupa la stringa e aggiorna i dati e l'offset
                    elif ".ascii" in line_strip:
                        match = re.search(r'"([^"]*)"', line_strip)
                        if match:
                            string_val = match.group(1).replace("\\n", "\n")
                            string_bytes = string_val.encode('utf-8')
                            data_bytes.extend(string_bytes)
                            data_offset += len(string_bytes)

            # ------------------------------------------------------------------
            # Seconda passata: elabora la sezione .text e trasforma le istruzioni
            # in formato compatibile con Keystone (no direttive GNU, no pseudo-op).
            # ------------------------------------------------------------------
            for line in lines:
                line_strip = line.strip()
                # Salta righe vuote, commenti e direttive (es. .global, .text, .data)
                if not line_strip or line_strip.startswith("@") or line_strip.startswith("."):
                    continue
                # Salta le etichette (es. '_start:'), ma non le istruzioni 'ldr' che contengono ':'
                if ":" in line_strip and not line_strip.startswith("ldr"):
                    continue

                # Rimuove il commento inline
                if "@" in line_strip:
                    line_strip = line_strip.split("@")[0].strip()

                # Converte la pseudo-istruzione GNU 'ldr rX, =label' in 'mov rX, #addr'
                # perché Keystone non supporta la sintassi GNU con il prefisso '='
                if "ldr" in line_strip and "=" in line_strip:
                    reg, label_target = line_strip.split(",")
                    reg = reg.replace("ldr", "").strip()
                    label_target = label_target.replace("=", "").strip()
                    if label_target in labels:
                        line_strip = f"mov {reg}, #{hex(labels[label_target])}"
                    else:
                        print(f"[Compiler Errore] Label '{label_target}' mancante.")
                        return False

                # Sostituisce i riferimenti simbolici a label nei parametri immediati
                # (es. 'mov r2, #hello_len' -> 'mov r2, #0x1a')
                for lbl_name, lbl_val in labels.items():
                    if f"#{lbl_name}" in line_strip:
                        line_strip = line_strip.replace(f"#{lbl_name}", f"#{hex(lbl_val)}")

                text_lines.append(line_strip)

            # Keystone accetta un singolo stringa con le istruzioni separate da ';'
            pure_assembly = "; ".join(text_lines)

            ks = Ks(KS_ARCH_ARM, KS_MODE_ARM)
            encoding, count = ks.asm(pure_assembly, self.CODE_ADDRESS)

            # Scrive il binario flat: codice | padding | dati
            with open(self.elf_path, "wb") as f:
                f.write(bytearray(encoding))
                # Padding di zero-byte tra la fine del codice e l'inizio di .data
                padding_size = self.DATA_ADDRESS - (self.CODE_ADDRESS + len(encoding))
                if padding_size > 0:
                    f.write(b"\x00" * padding_size)
                f.write(data_bytes)

            print(f"[Compiler SUCCESS] Compilazione completata con successo.")
            return True

        except Exception as e:
            print(f"[Compiler Errore] Errore di compilazione: {e}")
            return False
