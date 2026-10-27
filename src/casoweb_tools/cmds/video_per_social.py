"""casoweb video-per-social: da una ripresa amatoriale a una clip in bianco e nero per il sito e i social."""

from __future__ import annotations

from pathlib import Path

import click

from casoweb_tools import video_per_social as core

_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)


def _tempo(_ctx, _param, value: str | None) -> float | None:
    try:
        return None if value is None else core.parse_tempo(value)
    except ValueError as e:
        raise click.BadParameter(str(e)) from None


def _ritaglio(_ctx, _param, value: str | None):
    try:
        return None if value is None else core.parse_ritaglio(value)
    except ValueError as e:
        raise click.BadParameter(str(e)) from None


def _risoluzione(_ctx, _param, value: str | None):
    if value is None:
        return None
    try:
        w, h = (int(v) for v in value.lower().split("x"))
    except ValueError:
        raise click.BadParameter(f"{value!r}: atteso LARGHEZZAxALTEZZA, es. 720x1280") from None
    if w % 2 or h % 2:
        raise click.BadParameter("larghezza e altezza devono essere pari")
    return w, h


@click.command(
    "video-per-social",
    epilog="""\b
Esempi:
  casoweb video-per-social serata.mp4 --inizio 4.5 --fine 33 --ritaglio 378:672:571:20 --srt serata.srt
  casoweb video-per-social serata.mp4 --ritaglio 378:672:571:20 --titolo "PROVING GROUNDS" \\
      --titolo "finissage, Caffè Pedrocchi" --nomi nomi.tsv --audio serata_pulito.mp4
  casoweb video-per-social serata.mp4 --ass serata_social_9x16.ass --grana fotocopia

\b
I tempi di --srt e --nomi sono quelli della ripresa intera: vengono
spostati da soli di --inizio. Il file --nomi ha una riga per nome:
  12.4<TAB>Mario Rossi, chitarra
L'audio ripulito (--audio) deve avere gli stessi tempi della ripresa,
come quello prodotto da `casoweb pulisci-audio`.""",
)
@click.argument("ingresso", type=_FILE)
@click.option("--formato", type=click.Choice(list(core.FORMATI)), default="9:16", show_default=True,
              help="Formato d'uscita: 9:16 storie e reel, 4:5 feed, 16:9 sito.")
@click.option("--ritaglio", callback=_ritaglio, metavar="L:A:X:Y",
              help="Ritaglio fisso in pixel della ripresa, come il filtro crop di ffmpeg "
                   "[default: il più grande del formato, al centro].")
@click.option("--risoluzione", callback=_risoluzione, metavar="LxA",
              help=f"Dimensioni d'uscita [default: 720 sul lato corto se l'altezza ritagliata è sotto "
                   f"{core.SOGLIA_720} pixel, altrimenti 1080].")
@click.option("--inizio", callback=_tempo, default="0", help="Da dove tagliare (33.5, 0:33, 00:00:33,500).")
@click.option("--fine", callback=_tempo, help="Dove finire [default: fine della ripresa].")
@click.option("--audio", type=_FILE, help="Audio da usare al posto di quello della ripresa (es. ripulito).")
@click.option("--srt", type=_FILE, help="Sottotitoli da battere a macchina.")
@click.option("--ass", type=_FILE, help="ASS già pronto (es. quello generato e corretto a mano): "
                                         "sostituisce --srt, --titolo e --nomi.")
@click.option("--titolo", multiple=True, help="Riga del titolo d'apertura, in alto; ripetibile per più righe.")
@click.option("--durata-titolo", type=float, default=4.0, show_default=True, help="Secondi del titolo.")
@click.option("--nomi", type=_FILE, help="Nomi da scrivere quando vengono pronunciati: righe tempo<TAB>testo.")
@click.option("--durata-nome", type=float, default=4.0, show_default=True,
              help="Secondi massimi di ciascun nome (finisce prima se arriva il successivo).")
@click.option("--look", type=click.Choice(core.LOOK), default="bn", show_default=True,
              help="bn = bianco e nero ad alto contrasto; colore = solo ritaglio e testi.")
@click.option("--contrasto", type=click.FloatRange(0, 1), default=0.75, show_default=True,
              help="Durezza della curva a S, da 0 (lineare) a 1.")
@click.option("--grana", type=click.Choice(core.GRANA), default="pellicola", show_default=True,
              help="pellicola = grana fine; fotocopia = grigi chiusi, bordi duri, disturbo più ruvido.")
@click.option("--intensita-grana", type=click.FloatRange(0, 100), default=14.0, show_default=True,
              help="Quanta grana, da 0 a 100.")
@click.option("--vignettatura", type=click.FloatRange(0, 1), default=0.5, show_default=True,
              help="Scurimento dei bordi, da 0 (nessuno) a 1.")
@click.option("--font", default="Liberation Mono", show_default=True,
              help="Carattere dei testi (es. \"Courier Prime\", se installato).")
@click.option("--dimensione-font", type=float, default=5.0, show_default=True,
              help="Dimensione dei sottotitoli, in % della larghezza del video.")
@click.option("--stile-testo", type=click.Choice(core.STILI_TESTO), default="carta", show_default=True,
              help="carta = testo scuro su una striscia chiara; contorno = testo chiaro con bordo nero.")
@click.option("--velocita", type=click.FloatRange(min=1), default=18.0, show_default=True,
              help="Caratteri battuti al secondo (al massimo nel 60% della durata di ogni sottotitolo).")
@click.option("--dissolvenza", type=float, default=0.5, show_default=True,
              help="Secondi di dissolvenza dal nero e verso il nero, 0 per nessuna.")
@click.option("--dissolvenza-audio", type=float, default=0.3, show_default=True,
              help="Secondi di dissolvenza dell'audio all'inizio e alla fine.")
@click.option("--crf", type=click.IntRange(0, 51), default=23, show_default=True,
              help=f"Qualità x264 (più alto = più leggero), con un tetto di {core.MAX_BITRATE}bit/s.")
@click.option("--uscita", "-o", type=click.Path(dir_okay=False, path_type=Path),
              help="File di uscita [default: <nome>_social_<formato>.mp4 accanto alla ripresa].")
def video_per_social(ingresso: Path, titolo: tuple[str, ...], **opzioni) -> None:
    """Ritaglia una ripresa e le dà un look in bianco e nero, con testi battuti a macchina.

    Taglio e ritaglio fissi, bianco e nero ad alto contrasto con grana e
    vignettatura, sottotitoli dall'SRT che compaiono una lettera alla volta
    (alzati dal bordo per i comandi delle storie), titolo d'apertura e nomi
    nel momento in cui vengono pronunciati. Richiede ffmpeg con libass.
    """
    core.converti(ingresso, core.Opzioni(titolo=list(titolo), **opzioni))
