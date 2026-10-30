"""casoweb separa-parlato: dal parlato alternato a musica ai sottotitoli, senza la musica."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import click

from casoweb_tools import ambiente

FORMATI = ("mp3", "m4a", "opus", "flac", "wav")  # le chiavi di CODEC nel modulo core
DIPENDENZE = ("numpy", "inaSpeechSegmenter", "faster_whisper")
EXTRA = ambiente.Extra("separa-parlato", max_python=(3, 12), variante_cpu=True)

logger = logging.getLogger(__name__)
SEGMENTAZIONE = "TensorFlow (inaSpeechSegmenter, per la segmentazione)"


def percorso_tsv(input: Path, outdir: Path | None) -> Path:
    """Dove separa_parlato.run cerca e scrive la segmentazione (stessa regola)."""
    return (outdir or input.parent / f"{input.stem}_output") / f"{input.stem}_segmenti.tsv"


def requisiti_cpu(segmenta: bool = True, trascrive: bool = True) -> dict[str, set[str]]:
    """Istruzioni del processore che le librerie precompilate richiedono, per i passi da fare.

    TensorFlow si carica solo per segmentare (senza TSV o con --risegmenta), CTranslate2 solo
    per trascrivere. Fonti: TensorFlow usa AVX dalla 1.6; CTranslate2 richiede almeno SSE4.1
    (pagina "Hardware support"); NumPy dalla 2.4 richiede il livello x86-64-v2.
    """
    req = {}
    if segmenta:
        req[SEGMENTAZIONE] = {"avx"}
    if trascrive:
        req["CTranslate2 (faster-whisper)"] = {"sse4_1"}
    numpy = ambiente.versione_installata("numpy")
    if numpy is None or numpy >= (2, 4):  # se manca, si installerà la versione più recente
        req["NumPy 2.4 o successivo"] = {"ssse3", "sse4_1", "sse4_2", "popcnt"}
    return req


@click.command(
    "separa-parlato",
    epilog="""\b
Esempi:
  casoweb separa-parlato registrazione.webm
  casoweb separa-parlato registrazione.webm --nomi "Mario Rossi, Anna Bianchi"

\b
Revisione "umano nel mezzo": correggere a mano <nome>_segmenti.tsv
(parlato <-> musica) e rilanciare lo stesso comando; i sottotitoli si
rigenerano dalla trascrizione in cache e Whisper lavora solo sulle parti
diventate "parlato".

\b
Articolo: https://www.casoweb.eu/?view=article&id=307&catid=90""",
)
@click.argument("input", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--outdir", type=click.Path(file_okay=False, path_type=Path),
              help="Cartella di uscita [default: <nome>_output accanto al file].")
@click.option("--formato", type=click.Choice(FORMATI), default="mp3", show_default=True,
              help="Formato audio in uscita.")
@click.option("--bitrate", help="Bitrate audio, es. 128k [default: del formato].")
@click.option("--modo", type=click.Choice(["taglia", "silenzia", "entrambi"]), default="entrambi",
              show_default=True, help="Quali audio produrre: solo parlato, musica silenziata o entrambi.")
@click.option("--modello", default="large-v3-turbo", show_default=True,
              help="Modello Whisper (small, medium, large-v3-turbo...).")
@click.option("--device", type=click.Choice(["auto", "cuda", "cpu"]), default="auto", show_default=True,
              help="Dove eseguire Whisper: auto usa la GPU NVIDIA se disponibile.")
@click.option("--compute-type", default="auto", show_default=True,
              help="Precisione CTranslate2 (int8, int8_float16, float16...); "
                   "auto = la migliore supportata dalla GPU, int8 su CPU.")
@click.option("--lingua", default="it", show_default=True)
@click.option("--nomi", help='Nomi propri da suggerire a Whisper, es. "Mario Rossi, Anna Bianchi".')
@click.option("--prompt", help="Testo iniziale libero per Whisper (sostituisce --nomi).")
@click.option("--min-parlato", type=float, default=3.0, show_default=True,
              help="Secondi minimi per un tratto di parlato.")
@click.option("--min-musica", type=float, default=2.0, show_default=True,
              help="Secondi minimi per un tratto di musica.")
@click.option("--padding", type=float, default=0.3, show_default=True,
              help="Secondi di margine attorno a ogni tratto di parlato.")
@click.option("--dissolvenza", type=float, default=0.05, show_default=True,
              help="Secondi di dissolvenza a ogni taglio.")
@click.option("--max-car", type=int, default=80, show_default=True, help="Caratteri massimi per sottotitolo.")
@click.option("--max-riga", type=int, default=42, show_default=True, help="Caratteri per riga (al massimo due righe).")
@click.option("--max-durata", type=float, default=6.0, show_default=True,
              help="Durata massima di un sottotitolo, in secondi.")
@click.option("--risegmenta", is_flag=True, help="Ignora il TSV esistente e rifà la segmentazione.")
@click.option("--blocco-segmentazione", type=float, default=300.0, show_default=True,
              help="Secondi analizzati alla volta nella segmentazione: tiene bassa la memoria "
                   "sulle registrazioni lunghe (0 = tutto il file insieme).")
@click.option("--ritrascrivi", is_flag=True, help="Ignora la trascrizione in cache.")
@click.option("--solo-segmenti", is_flag=True,
              help="Genera solo il TSV della segmentazione, da controllare prima di proseguire.")
@click.option("--no-audio", is_flag=True, help="Non rigenera i file audio.")
@click.option("--no-trascrizione", is_flag=True, help="Solo segmentazione e audio.")
def separa_parlato(input: Path, **opzioni) -> None:
    """Separa il parlato dalla musica in una registrazione e lo sottotitola.

    \b
    Dato un file audio/video in cui parlato e musica si alternano
    (trasmissione radio, podcast, conferenza con stacchi musicali...):
      1. riconosce dove si parla e dove c'è musica (inaSpeechSegmenter);
      2. produce <nome>_parlato (musica tagliata) e <nome>_silenziato
         (durata originale, musica a volume zero);
      3. trascrive il parlato con faster-whisper e genera due SRT,
         ciascuno sincronizzato con il proprio audio.

    Richiede ffmpeg di sistema e l'extra "separa-parlato" (vedi README).
    """
    istruzioni = ambiente.istruzioni_cpu()
    logger.debug(f"Istruzioni del processore rilevanti: "
                 f"{sorted(istruzioni & {'ssse3', 'sse4_1', 'sse4_2', 'popcnt', 'avx', 'avx2'}) if istruzioni else '?'}")
    tsv = percorso_tsv(input, opzioni["outdir"])
    segmenta = opzioni["risegmenta"] or not tsv.exists()
    trascrive = not (opzioni["no_trascrizione"] or opzioni["solo_segmenti"])
    req = requisiti_cpu(segmenta, trascrive)
    suggerimento = None
    if istruzioni is not None and segmenta:
        resto = set().union(*(v for k, v in req.items() if k != SEGMENTAZIONE))
        if not resto - istruzioni:  # manca solo ciò che serve a segmentare: il resto gira qui
            suggerimento = ("Su questo PC funziona tutto il resto: crea la segmentazione su un PC più recente\n"
                            f"  casoweb separa-parlato {input.name} --solo-segmenti\n"
                            f"e copia il TSV in\n  {tsv}\n"
                            "(oppure scrivilo a mano), poi rilancia qui lo stesso comando.")
    if (msg := ambiente.manca_cpu(req, istruzioni, suggerimento)) is not None:
        raise click.ClickException(msg)
    mancanti = [m for m in DIPENDENZE if importlib.util.find_spec(m) is None]
    if mancanti:
        raise click.ClickException(ambiente.manca_extra(EXTRA, mancanti))
    logger.debug("Carico NumPy...")
    from casoweb_tools import separa_parlato as core

    logger.debug(f"NumPy {core.np.__version__} caricato")

    core.run(input, core.Opzioni(**opzioni))
