"""casoweb video-da-audio: da un audio con sottotitoli a uno o più video."""

from __future__ import annotations

from pathlib import Path

import click

from casoweb_tools import video_da_audio as core


def _elenca(ctx: click.Context, _param, value: bool) -> None:
    if value and not ctx.resilient_parsing:
        core.elenca_profili()
        ctx.exit()


@click.command(
    "video-da-audio",
    epilog="""\b
Esempi:
  casoweb video-da-audio intervista_parlato.mp3
  casoweb video-da-audio intervista_parlato.mp3 --immagine locandina.jpg
  casoweb video-da-audio intervista_parlato.mp3 --srt corretto.srt --profili youtube_1080p,verticale_1080x1920
  casoweb video-da-audio --elenca-profili""",
)
@click.argument("audio", type=click.Path(path_type=Path))
@click.option("--srt", help="Sottotitoli .srt [default: stesso nome dell'audio, estensione .srt].")
@click.option("--immagine", help="Immagine di sfondo fissa [default: sfondo nero].")
@click.option("--profili", help=f"Profili separati da virgola [default: {core.DEFAULT_PROFILES}].")
@click.option("--elenca-profili", is_flag=True, expose_value=False, is_eager=True, callback=_elenca,
              help="Mostra i profili disponibili ed esce.")
@click.option("--adattamento", type=click.Choice(["contain", "cover"]),
              help="contain = immagine intera con barre; cover = riempie e ritaglia [default: contain].")
@click.option("--colore-barre", help="Colore delle barre con 'contain' (nome ffmpeg o 0xRRGGBB) [default: black].")
@click.option("--font", help="Font dei sottotitoli [default: DejaVu Sans Mono].")
@click.option("--dimensione-font", type=float,
              help="Dimensione dei sottotitoli, in % della larghezza del video [default: 2.8].")
@click.option("--fps", type=int,
              help="Fotogrammi al secondo per tutti i profili [default: del profilo, 5 o 10]; "
                   "precisione dei sottotitoli = 1/fps.")
@click.option("--respiro", type=float, help="Secondi di nero/silenzio a inizio e fine, 0 per nessuno [default: 0.5].")
@click.option("--dissolvenza", type=float, help="Secondi di dissolvenza video, 0 per nessuna [default: 0.5].")
@click.option("--dissolvenza-audio", type=float, help="Secondi di dissolvenza audio [default: 0.2].")
@click.option("--soft/--incorporati", default=None,
              help="Sottotitoli come traccia attivabile dal player, oppure incorporati nelle immagini [default].")
@click.option("--titolo", help="Titolo nei metadati del video.")
@click.option("--output-dir", help="Cartella di uscita [default: <nome>_video accanto all'audio].")
@click.option("--anteprima", is_flag=True, help="A fine lavoro apre l'ultimo video con mpv.")
@click.option("--load-params", metavar="FILE",
              help="Riusa i parametri salvati in <nome>_params.json; i flag espliciti hanno la precedenza.")
def video_da_audio(audio: Path, anteprima: bool, load_params: str | None, **cli) -> None:
    """Trasforma un audio sottotitolato in video con sfondo fisso, per più piattaforme.

    Per ogni profilo produce <nome>_<profilo>.mp4 (YouTube, web, WhatsApp
    sotto i 16 MB per circa 30 minuti, quadrato, verticale), con "respiro"
    di nero e silenzio a inizio e fine e dissolvenze. Richiede solo ffmpeg.
    """
    core.run(audio, cli, load_params=load_params, anteprima=anteprima)
