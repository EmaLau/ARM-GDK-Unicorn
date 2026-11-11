# app/toolchain.py
# Ricerca della toolchain GNU per ARM (arm-none-eabi-as e arm-none-eabi-ld).
#
# Ordine di ricerca:
#   1. la cartella indicata dalla variabile d'ambiente ARM_TOOLCHAIN_DIR;
#   2. il PATH del sistema;
#   3. le cartelle di installazione abituali di Windows, macOS e Linux.
#
# Eseguito come script (python -m app.toolchain) controlla che la toolchain ci sia
# e, se manca, stampa le istruzioni di installazione per il sistema in uso.

import glob
import os
import platform
import shutil
import sys
from pathlib import Path

PREFIX = "arm-none-eabi-"

# Cartelle in cui gli installer ufficiali mettono i programmi (i nomi delle versioni cambiano,
# per questo si usano i caratteri jolly e si preferisce la versione più recente).
_COMMON_DIRS = {
    "Windows": [
        r"C:\Program Files*\Arm GNU Toolchain arm-none-eabi\*\bin",
        r"C:\Program Files*\GNU Arm Embedded Toolchain\*\bin",
    ],
    "Darwin": [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Applications/ArmGNUToolchain/*/arm-none-eabi/bin",
    ],
    "Linux": [
        "/usr/bin",
        "/usr/local/bin",
        "/opt/arm-gnu-toolchain-*/bin",
    ],
}

INSTALL_HELP = {
    "Windows": (
        "Scarica e installa 'Arm GNU Toolchain' (versione arm-none-eabi, host Windows) da\n"
        "  https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads\n"
        "Durante l'installazione spunta 'Add path to environment variable', oppure imposta\n"
        "ARM_TOOLCHAIN_DIR con la cartella 'bin' della toolchain."
    ),
    "Darwin": (
        "Installa le binutils per ARM con Homebrew:\n"
        "  brew install arm-none-eabi-binutils"
    ),
    "Linux": (
        "Installa le binutils per ARM con il gestore di pacchetti, ad esempio:\n"
        "  sudo apt install binutils-arm-none-eabi      (Debian, Ubuntu)\n"
        "  sudo dnf install arm-none-eabi-binutils-cs   (Fedora)"
    ),
}


def _candidate_dirs():
    env_dir = os.environ.get("ARM_TOOLCHAIN_DIR")
    if env_dir:
        yield Path(env_dir)
    for pattern in _COMMON_DIRS.get(platform.system(), []):
        # sorted(..., reverse=True): a parità di cartella base vince la versione più recente
        for match in sorted(glob.glob(pattern), reverse=True):
            yield Path(match)


def find_tool(name: str):
    """Restituisce il percorso completo di arm-none-eabi-<name>, oppure None."""
    exe = PREFIX + name
    env_dir = os.environ.get("ARM_TOOLCHAIN_DIR")
    if env_dir:
        found = shutil.which(exe, path=env_dir)
        if found:
            return found
    found = shutil.which(exe)  # gestisce anche l'estensione .exe su Windows
    if found:
        return found
    for directory in _candidate_dirs():
        found = shutil.which(exe, path=str(directory))
        if found:
            return found
    return None


def find_toolchain():
    """Restituisce {'as': percorso, 'ld': percorso} oppure None se manca uno dei due."""
    tools = {name: find_tool(name) for name in ("as", "ld")}
    return tools if all(tools.values()) else None


def install_help() -> str:
    return INSTALL_HELP.get(platform.system(), INSTALL_HELP["Linux"])


def main() -> int:
    tools = find_toolchain()
    if tools:
        print(f"[Toolchain] Trovata: {tools['as']}")
        return 0
    print("[Toolchain] Toolchain GNU per ARM non trovata (servono arm-none-eabi-as e arm-none-eabi-ld).")
    print(install_help())
    return 1


if __name__ == "__main__":
    sys.exit(main())
