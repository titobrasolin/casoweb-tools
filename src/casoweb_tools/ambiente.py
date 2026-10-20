"""Riconosce l'ambiente d'esecuzione, per messaggi d'errore con il comando giusto da lanciare.

Distingue come è stato installato casoweb (uv tool, pipx, copia di sviluppo,
altro), se c'è una GPU NVIDIA, la versione di Python e il gestore di
pacchetti del sistema. Solo libreria standard: deve funzionare proprio
quando mancano le dipendenze.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

REPO = "https://github.com/titobrasolin/casoweb-tools"
GIT = f"git+{REPO}.git"
README = f"{REPO}#readme"
OVERRIDES_CPU = "https://raw.githubusercontent.com/titobrasolin/casoweb-tools/main/scripts/overrides-cpu.txt"

UV_TOOL, PIPX, SVILUPPO, ALTRO = "uv-tool", "pipx", "sviluppo", "altro"


@dataclass
class Extra:
    """Un extra di pyproject.toml, con i suoi vincoli."""

    nome: str
    max_python: tuple[int, int] | None = None  # versione massima supportata, es. (3, 12)
    variante_cpu: bool = False  # esiste la variante senza librerie NVIDIA (extra "cpu" + overrides)


# --------------------------------------------------------------------------
# Rilevamento
# --------------------------------------------------------------------------
def installazione(prefix: Path | None = None) -> str:
    prefix = Path(sys.prefix if prefix is None else prefix)
    if (prefix / "uv-receipt.toml").is_file():
        return UV_TOOL
    if (prefix / "pipx_metadata.json").is_file():
        return PIPX
    if sorgente_editabile() is not None:
        return SVILUPPO
    return ALTRO


def sorgente_editabile() -> Path | None:
    """Cartella del sorgente se casoweb-tools è installato in modalità editabile."""
    try:
        raw = metadata.distribution("casoweb-tools").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    info = json.loads(raw)
    if not info.get("dir_info", {}).get("editable"):
        return None
    url = info.get("url", "")
    return Path(url.removeprefix("file://")) if url.startswith("file://") else None


def versione_installata(distribuzione: str) -> tuple[int, ...] | None:
    """Versione (solo i numeri iniziali) di un pacchetto installato, senza importarlo."""
    try:
        v = metadata.version(distribuzione)
    except metadata.PackageNotFoundError:
        return None
    m = re.match(r"\d+(?:\.\d+)*", v)  # "2.5.4" -> (2, 5, 4); "2.4.0rc1" -> (2, 4, 0)
    return tuple(int(n) for n in m.group().split(".")) if m else None


def istruzioni_cpu(cpuinfo: str | None = None) -> set[str] | None:
    """Estensioni del processore (avx, sse4_1...) lette da /proc/cpuinfo; None se non si possono sapere."""
    if cpuinfo is None:
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    for riga in cpuinfo.splitlines():
        if riga.startswith("flags"):
            return set(riga.partition(":")[2].split())
    return None


def ha_gpu_nvidia() -> bool:
    return Path("/proc/driver/nvidia").exists() or shutil.which("nvidia-smi") is not None


def gestore_pacchetti() -> str | None:
    if sys.platform == "darwin":
        return "brew" if shutil.which("brew") else None
    if sys.platform == "win32":
        return "winget" if shutil.which("winget") else None
    for g in ("apt", "dnf", "pacman", "zypper"):
        if shutil.which(g):
            return g
    return None


# --------------------------------------------------------------------------
# Suggerimenti
# --------------------------------------------------------------------------
_PACCHETTI = {  # nome del pacchetto quando differisce da quello del programma
    ("dnf", "ffmpeg"): "ffmpeg-free",
    ("winget", "ffmpeg"): "Gyan.FFmpeg",
    ("winget", "mpv"): "mpv.net",
}
_INSTALLA = {
    "apt": "sudo apt install {}",
    "dnf": "sudo dnf install {}",
    "pacman": "sudo pacman -S {}",
    "zypper": "sudo zypper install {}",
    "brew": "brew install {}",
    "winget": "winget install {}",
}


def manca_programma(nome: str, gestore: str | None = None) -> str:
    """Messaggio per un programma di sistema mancante (ffmpeg, mpv...)."""
    gestore = gestore if gestore is not None else gestore_pacchetti()
    if gestore in _INSTALLA:
        pacchetto = _PACCHETTI.get((gestore, nome), nome)
        return f"Manca {nome}: {_INSTALLA[gestore].format(pacchetto)}"
    return f"Manca {nome}: installalo con il gestore di pacchetti del sistema."


_NOMI_ISTRUZIONI = {"sse4_1": "SSE4.1", "sse4_2": "SSE4.2"}


def manca_cpu(requisiti: dict[str, set[str]], istruzioni: set[str] | None = None,
              suggerimento: str | None = None) -> str | None:
    """Messaggio se il processore non ha le istruzioni che le librerie precompilate richiedono.

    `requisiti`: libreria -> estensioni necessarie (nomi come in /proc/cpuinfo). Senza questo
    controllo il programma muore con "Illegal instruction" appena carica la libreria.
    `suggerimento`, se c'è, prende il posto del consiglio generico di cambiare processore.
    """
    istruzioni = istruzioni_cpu() if istruzioni is None else istruzioni
    if istruzioni is None or os.environ.get("CASOWEB_IGNORA_CPU"):
        return None
    mancano = {lib: sorted(req - istruzioni) for lib, req in requisiti.items() if req - istruzioni}
    if not mancano:
        return None
    righe = ["Il processore di questo PC non ha le istruzioni richieste dalle librerie precompilate:"]
    for lib, m in mancano.items():
        righe.append(f"  - {lib}: {', '.join(_NOMI_ISTRUZIONI.get(i, i.upper()) for i in m)}")
    righe.append("Il comando si interromperebbe con \"Illegal instruction\".")
    righe.append(suggerimento or "Serve un processore più recente (con AVX: all'incirca dal 2011 in poi),\n"
                                 "oppure librerie compilate apposta per questo.")
    righe += ["Se il controllo sbaglia, CASOWEB_IGNORA_CPU=1 lo salta.", f"Dettagli: {README}"]
    return "\n".join(righe)


def manca_extra(extra: Extra, mancanti: list[str], *, tipo: str | None = None, gpu: bool | None = None,
                versione: tuple[int, int] | None = None, sorgente: Path | None = None) -> str:
    """Messaggio per le dipendenze Python mancanti di un extra, con il comando adatto all'ambiente.

    I parametri con parola chiave servono ai test; di norma si rilevano da soli.
    """
    tipo = tipo or installazione()
    gpu = ha_gpu_nvidia() if gpu is None else gpu
    versione = versione or sys.version_info[:2]
    if tipo == SVILUPPO and sorgente is None:
        sorgente = sorgente_editabile()

    cpu = extra.variante_cpu and not gpu
    extras = extra.nome + (",cpu" if cpu else "")
    py = f"{extra.max_python[0]}.{extra.max_python[1]}" if extra.max_python else None
    troppo_nuovo = extra.max_python is not None and versione > extra.max_python
    overrides = f" --overrides {OVERRIDES_CPU}" if cpu else ""

    righe = [f"Dipendenze mancanti ({', '.join(mancanti)}): serve l'extra '{extra.nome}'."]
    if troppo_nuovo:
        righe.append(f"casoweb gira su Python {versione[0]}.{versione[1]}, ma l'extra richiede "
                     f"Python {py} o precedente: va reinstallato con un Python più vecchio.")
    if cpu:
        righe.append("Nessuna GPU NVIDIA rilevata: ecco la variante leggera, senza librerie CUDA.")

    uv_tool = (f"uv tool install --force{f' --python {py}' if py else ''}{overrides} "
               f"'casoweb-tools[{extras}] @ {GIT}'")
    if tipo == UV_TOOL:
        righe += ["Reinstalla con:", f"  {uv_tool}"]
    elif tipo == PIPX:
        if cpu or troppo_nuovo:
            motivo = ("pipx non sa escludere le librerie NVIDIA (circa 5 GB inutili)" if cpu
                      else f"uv procura da solo Python {py}")
            righe += [f"Conviene passare a uv ({motivo}):", "  pipx uninstall casoweb-tools", f"  {uv_tool}"]
        else:
            righe += ["Reinstalla con:", f"  pipx install --force 'casoweb-tools[{extras}] @ {GIT}'"]
    elif tipo == SVILUPPO:
        dove = sorgente or Path(".")
        ov = f" --overrides {dove / 'scripts' / 'overrides-cpu.txt'}" if cpu else ""
        if troppo_nuovo:
            righe += [f"Ricrea l'ambiente di sviluppo con Python {py} (dalla cartella del sorgente):",
                      f"  uv venv -p {py}", f"  uv pip install{ov} -e '{dove}[dev,{extras}]'"]
        else:
            righe += ["Installa l'extra nell'ambiente di sviluppo:",
                      f"  uv pip install --python {sys.executable}{ov} -e '{dove}[dev,{extras}]'"]
    else:
        righe += ["Installa casoweb con uv:", f"  {uv_tool}"]
    righe.append(f"Dettagli: {README}")
    return "\n".join(righe)
