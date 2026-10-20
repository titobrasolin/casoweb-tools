"""casoweb video-per-canon: un video qualsiasi, riproducibile sul display di una Canon PowerShot."""

from __future__ import annotations

from pathlib import Path

import click

from casoweb_tools import video_per_canon as core

_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)


@click.command(
    "video-per-canon",
    epilog="""\b
Esempi:
  casoweb video-per-canon video.mp4 --numero 1266 --thm-modello MVI_1259.THM
  casoweb video-per-canon video.mp4 --numero 1266 --thm-modello MVI_1259.THM --durata 30 --cartella /media/sd/DCIM/101CANON
  casoweb video-per-canon --verifica MVI_1259.AVI

\b
Il numero (MVI_NNNN) non deve essere usato da nessun altro file della
cartella, foto comprese: foto e video condividono la numerazione.
Copiare sempre AVI e THM insieme.""",
)
@click.argument("ingresso", required=False, type=_FILE)
@click.option("--numero", type=click.IntRange(1, 9999), help="Numero del file (MVI_NNNN), da 1 a 9999.")
@click.option("--thm-modello", type=_FILE,
              help="Un .THM prodotto dalla fotocamera, di qualsiasi modalità: fornisce il blocco Exif da aggiornare.")
@click.option("--cartella", type=click.Path(file_okay=False, path_type=Path), default=Path("."), show_default=True,
              help="Dove scrivere AVI e THM (es. la cartella DCIM/101CANON della scheda).")
@click.option("--cartella-fotocamera", type=click.IntRange(100, 999), default=101, show_default=True,
              help="Numero della cartella sulla scheda (101 per 101CANON), scritto nel THM.")
@click.option("--profilo", type=click.Choice(list(core.PROFILI)), default="compatto", show_default=True,
              help="compatto = 160x120 a 15 fps; 320x240 e 640x480 a 30 fps.")
@click.option("--qualita", type=click.IntRange(1, 95),
              help="Qualità JPEG di partenza, da 1 a 95 (alto = migliore) [default: del profilo].")
@click.option("--max-fotogramma", type=click.IntRange(min=0),
              help="Dimensione massima di un fotogramma in byte, 0 = nessun limite [default: del profilo].")
@click.option("--durata", type=float, help="Taglia a N secondi.")
@click.option("--verifica", "verifica_avi", type=_FILE, metavar="AVI_CANON",
              help="Non converte: rigenera un AVI della fotocamera e controlla che risulti identico byte per byte.")
def video_per_canon(ingresso: Path | None, verifica_avi: Path | None, numero: int | None,
                    thm_modello: Path | None, **opzioni) -> None:
    """Converte un video in una coppia MVI_NNNN.AVI + .THM per Canon PowerShot.

    Le PowerShot mostrano un video copiato sulla scheda solo se è costruito
    esattamente come i loro (AVI Motion JPEG "CanonMVI06", audio 8 bit mono
    a 11024 Hz) e se accanto c'è un .THM coerente con l'AVI. Provato sulla
    A2000 IS nei profili 160x120 a 15 fps e 640x480 a 30 fps. Richiede ffmpeg.
    """
    if verifica_avi is not None:
        if not core.verifica(verifica_avi):
            raise SystemExit(1)
        return
    mancanti = [n for n, v in (("INGRESSO", ingresso), ("--numero", numero), ("--thm-modello", thm_modello))
                if v is None]
    if mancanti:
        raise click.UsageError(f"Per convertire servono {', '.join(mancanti)} (oppure --verifica AVI_CANON).")
    core.converti(ingresso, core.Opzioni(numero=numero, thm_modello=thm_modello, **opzioni))
