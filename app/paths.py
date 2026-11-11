# app/paths.py
# Percorsi condivisi dai tre moduli, assoluti rispetto alla cartella del progetto:
# funzionano qualunque sia la cartella da cui vengono avviati.
#
#   examples/hello.s   -- programma di esempio, versionato
#   workspace/main.s   -- copia di lavoro modificata dall'editor (ignorata da git)
#   build/main.o       -- file oggetto prodotto dall'assembler GNU (ignorato da git)
#   build/main.elf     -- programma collegato dal linker GNU, caricato dall'emulatore
#   build/lines.json   -- corrispondenza riga del sorgente -> indirizzo, per l'interfaccia

import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_SRC = PROJECT_ROOT / "examples" / "hello.s"
WORKSPACE_SRC = PROJECT_ROOT / "workspace" / "main.s"
BUILD_DIR = PROJECT_ROOT / "build"
BUILD_OBJ = BUILD_DIR / "main.o"
BUILD_ELF = BUILD_DIR / "main.elf"
BUILD_LINES = BUILD_DIR / "lines.json"


def ensure_workspace() -> Path:
    """Crea la copia di lavoro dall'esempio se non esiste ancora e ne restituisce il percorso."""
    if not WORKSPACE_SRC.exists():
        WORKSPACE_SRC.parent.mkdir(parents=True, exist_ok=True)
        if EXAMPLE_SRC.exists():
            shutil.copyfile(EXAMPLE_SRC, WORKSPACE_SRC)
        else:
            WORKSPACE_SRC.touch()
    return WORKSPACE_SRC
