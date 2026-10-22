"""
Rilevamento di suoni noti (applausi, risate, musica...) in una registrazione con YAMNet.

YAMNet è un classificatore di Google addestrato su AudioSet (521 categorie). Qui si
usa la versione TFLite, che pesa pochi MB e gira sulla CPU: la registrazione viene
decodificata da ffmpeg a 16 kHz mono e analizzata a finestre di 0,975 s con passo
di circa 0,49 s. Le finestre consecutive sopra soglia sono unite in eventi.

Uscite (accanto all'ingresso, con lo stesso nome):
  <nome>_suoni.srt  sottotitoli: importabili in Shotcut/Kdenlive come segnaposto
  <nome>_suoni.tsv  stesso formato di `separa-parlato` (etichetta = nome della classe),
                    riusabile da `casoweb pulisci-audio --escludi`

Le soglie non sono probabilità: i punteggi di YAMNet non sono calibrati tra le classi.
Un applauso sommesso (es. quello finale, in sala) può restare sotto una soglia che per
uno scrosciante a scena aperta va benissimo: per questo il predefinito è basso (0,2) e
le classi vicine "Clapping" e "Cheering" sono cercate insieme.

Runtime: ai-edge-litert (consigliato, ~20 MB), tflite-runtime o tensorflow, il primo
che si trova. Servono anche numpy e ffmpeg.

Interfaccia a riga di comando: `casoweb rileva-suoni` (vedi casoweb_tools.cmds.rileva_suoni).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from casoweb_tools.ambiente import Extra, manca_extra, manca_programma

logger = logging.getLogger(__name__)

SR = 16000
FINESTRA = 15600  # campioni: 0,975 s, la dimensione fissa che il modello TFLite richiede
PASSO = 7800      # mezza finestra
URL_MODELLO = "https://tfhub.dev/google/lite-model/yamnet/classification/tflite/1?lite-format=tflite"
CLASSI_PREDEFINITE = ("Applause", "Clapping", "Cheering")


# --------------------------------------------------------------------------
# Utilità pure
# --------------------------------------------------------------------------
def inizio_finestra(i: int) -> float:
    return i * PASSO / SR


def unisci_eventi(punteggi: list[float], soglia: float, *, pausa_max: float = 1.0,
                  durata_min: float = 0.0) -> list[tuple[float, float, float]]:
    """Da un punteggio per finestra a eventi (inizio, fine, punteggio massimo).

    Le finestre sopra `soglia` consecutive, o separate da meno di `pausa_max` secondi,
    formano un evento; quelli più brevi di `durata_min` secondi sono scartati.
    """
    durata = FINESTRA / SR
    eventi: list[list[float]] = []
    for i, p in enumerate(punteggi):
        if p < soglia:
            continue
        a, b = inizio_finestra(i), inizio_finestra(i) + durata
        if eventi and a - eventi[-1][1] <= pausa_max:
            eventi[-1][1] = b
            eventi[-1][2] = max(eventi[-1][2], p)
        else:
            eventi.append([a, b, p])
    return [(a, b, p) for a, b, p in eventi if b - a >= durata_min]


def fmt_srt(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def scrivi_srt(path: Path, eventi: list[tuple[str, float, float, float]]) -> None:
    """eventi: (classe, inizio, fine, punteggio), in ordine di tempo."""
    with open(path, "w", encoding="utf-8") as f:
        for n, (classe, a, b, p) in enumerate(eventi, 1):
            f.write(f"{n}\n{fmt_srt(a)} --> {fmt_srt(b)}\n{classe} ({p * 100:.0f}%)\n\n")


def scrivi_tsv(path: Path, eventi: list[tuple[str, float, float, float]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("inizio_s\tfine_s\tetichetta\tinizio_mm:ss\tdurata_s\n")
        for classe, a, b, _p in eventi:
            f.write(f"{a:.3f}\t{b:.3f}\t{classe}\t{int(a) // 60:02d}:{int(a) % 60:02d}\t{b - a:.1f}\n")


# --------------------------------------------------------------------------
# Modello e audio
# --------------------------------------------------------------------------
def cartella_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "casoweb"


def etichette_da_modello(modello: Path) -> list[str]:
    """Le 521 etichette, impacchettate nel file .tflite (che è anche un archivio zip)."""
    try:
        with zipfile.ZipFile(modello) as z:
            for nome in z.namelist():
                if "label" in nome.lower():
                    righe = z.read(nome).decode("utf-8").splitlines()
                    return [r.strip() for r in righe if r.strip()]
    except zipfile.BadZipFile:
        pass
    raise SystemExit(f"Il modello {modello.name} non contiene l'elenco delle classi: "
                     "scaricalo di nuovo con --scarica-modello.")


def _e_tflite(dati: bytes) -> bool:
    return dati[4:8] == b"TFL3"


def scarica_modello(destinazione: Path) -> None:
    logger.info(f"Scarico il modello YAMNet (pochi MB) in {destinazione}...")
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    richiesta = urllib.request.Request(URL_MODELLO, headers={"User-Agent": "casoweb-tools"})
    with tempfile.TemporaryDirectory() as tmp:
        grezzo = Path(tmp) / "scaricato"
        try:
            with urllib.request.urlopen(richiesta, timeout=60) as r, open(grezzo, "wb") as f:
                shutil.copyfileobj(r, f)
        except OSError as e:
            raise SystemExit(f"Download del modello non riuscito ({e}).\n"
                             f"Scaricalo a mano da {URL_MODELLO} e indicalo con --modello.") from e
        if tarfile.is_tarfile(grezzo):  # alcuni mirror lo consegnano in un tar.gz
            with tarfile.open(grezzo) as t:
                for m in t.getmembers():
                    if m.name.endswith(".tflite"):
                        (Path(tmp) / "estratto.tflite").write_bytes(t.extractfile(m).read())
                        grezzo = Path(tmp) / "estratto.tflite"
                        break
        if not _e_tflite(grezzo.read_bytes()[:8]):
            raise SystemExit("Il file scaricato non è un modello TFLite.\n"
                             f"Scaricalo a mano da {URL_MODELLO} e indicalo con --modello.")
        shutil.move(str(grezzo), destinazione)


def trova_modello(percorso: Path | None, scarica: bool) -> Path:
    if percorso is not None:
        return percorso
    p = cartella_cache() / "yamnet.tflite"
    if not p.is_file() or scarica:
        scarica_modello(p)
    return p


def carica_interprete(modello: Path):
    """Interprete TFLite dal primo runtime disponibile; None se nessuno è installato."""
    for modulo in ("ai_edge_litert.interpreter", "tflite_runtime.interpreter"):
        try:
            m = __import__(modulo, fromlist=["Interpreter"])
            logger.debug(f"Runtime: {modulo}")
            return m.Interpreter(model_path=str(modello))
        except ImportError:
            continue
    try:
        import tensorflow as tf  # noqa: PLC0415  (pesante: solo come ultima scelta)
        logger.debug("Runtime: tensorflow.lite")
        return tf.lite.Interpreter(model_path=str(modello))
    except ImportError:
        return None


def manca_runtime(mancanti: list[str]) -> str:
    return manca_extra(Extra("rileva-suoni"), mancanti) + (
        "\n(In alternativa va bene un tensorflow già installato.)")


def decodifica(path: Path):
    """Audio a 16 kHz mono float32 in [-1, 1], decodificato da ffmpeg."""
    import numpy as np  # noqa: PLC0415

    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    logger.debug("Avvio: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg non riesce a leggere {path.name}: {r.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def punteggi_per_finestra(interprete, audio, indici: list[int]):
    """Per ogni finestra, i punteggi delle classi `indici`. Restituisce una lista di liste."""
    import numpy as np  # noqa: PLC0415

    interprete.allocate_tensors()
    ingresso = interprete.get_input_details()[0]
    uscita = interprete.get_output_details()[0]
    n = max(1, 1 + (max(len(audio) - FINESTRA, 0) + PASSO - 1) // PASSO)
    risultato = [[] for _ in indici]
    for i in range(n):
        pezzo = audio[i * PASSO:i * PASSO + FINESTRA]
        if len(pezzo) < FINESTRA:
            pezzo = np.pad(pezzo, (0, FINESTRA - len(pezzo)))
        interprete.set_tensor(ingresso["index"], pezzo.astype(np.float32).reshape(ingresso["shape"]))
        interprete.invoke()
        s = interprete.get_tensor(uscita["index"]).reshape(-1)
        for k, idx in enumerate(indici):
            risultato[k].append(float(s[idx]))
        if i % 200 == 0:
            from casoweb_tools import log  # noqa: PLC0415
            log.progresso(f"  analisi: {100 * i // n}%")
    from casoweb_tools import log  # noqa: PLC0415
    log.fine_progresso()
    return risultato


# --------------------------------------------------------------------------
# Programma
# --------------------------------------------------------------------------
def elenca_classi(modello: Path | None) -> None:
    for c in etichette_da_modello(trova_modello(modello, False)):
        print(c)


def run(ingresso: Path, *, classi: tuple[str, ...], soglia: float, pausa_max: float, durata_min: float,
        modello: Path | None, scarica: bool, uscita_dir: Path | None) -> list[tuple[str, float, float, float]]:
    if shutil.which("ffmpeg") is None:
        raise SystemExit(manca_programma("ffmpeg"))
    try:
        import numpy  # noqa: F401, PLC0415
    except ImportError:
        raise SystemExit(manca_runtime(["numpy"])) from None

    modello = trova_modello(modello, scarica)
    nomi = etichette_da_modello(modello)
    minuscole = {n.lower(): i for i, n in enumerate(nomi)}
    indici = []
    for c in classi:
        if c.lower() not in minuscole:
            raise SystemExit(f"Classe sconosciuta: {c!r}  (vedi --elenca-classi)")
        indici.append(minuscole[c.lower()])

    interprete = carica_interprete(modello)
    if interprete is None:
        raise SystemExit(manca_runtime(["ai-edge-litert"]))

    logger.info("Decodifico l'audio...")
    audio = decodifica(ingresso)
    logger.info(f"Durata: {len(audio) / SR / 60:.1f} min  |  classi: {', '.join(nomi[i] for i in indici)}  |  soglia: {soglia}")
    per_classe = punteggi_per_finestra(interprete, audio, indici)

    eventi: list[tuple[str, float, float, float]] = []
    for idx, punteggi in zip(indici, per_classe):
        eventi += [(nomi[idx], a, b, p) for a, b, p in
                   unisci_eventi(punteggi, soglia, pausa_max=pausa_max, durata_min=durata_min)]
    eventi.sort(key=lambda e: e[1])

    base = (uscita_dir or ingresso.parent) / f"{ingresso.stem}_suoni"
    base.parent.mkdir(parents=True, exist_ok=True)
    scrivi_srt(base.with_suffix(".srt"), eventi)
    scrivi_tsv(base.with_suffix(".tsv"), eventi)
    logger.info(f"Eventi trovati: {len(eventi)}  ->  {base.with_suffix('.srt').name}, {base.with_suffix('.tsv').name}")
    if not eventi:
        logger.warning("Nessun evento: prova ad abbassare --soglia.")
    return eventi
