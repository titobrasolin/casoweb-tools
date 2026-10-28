"""casoweb pulisci-audio: rumore, rimbombo e volume di una registrazione in presa diretta."""

from __future__ import annotations

from pathlib import Path

import click

from casoweb_tools import pulisci_audio as core


def _elenca(ctx: click.Context, _param, value: bool) -> None:
    if value and not ctx.resilient_parsing:
        core.elenca_profili()
        ctx.exit()


@click.command(
    "pulisci-audio",
    epilog="""\b
Esempi:
  casoweb pulisci-audio serata.mp4
  casoweb pulisci-audio serata.mp4 --segmenti serata_output/serata_segmenti.tsv --motore deepfilternet
  casoweb pulisci-audio serata.wav --profilo forte --volume-finale
  casoweb pulisci-audio --elenca-profili""",
)
@click.argument("ingresso", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--uscita", "-o", type=click.Path(dir_okay=False, path_type=Path),
              help="File di uscita [default: <nome>_pulito.<estensione> accanto all'ingresso]. "
                   "Se l'ingresso ha un video, viene copiato senza ricodificarlo.")
@click.option("--profilo", type=click.Choice(list(core.PROFILI)), default=core.PROFILO_PREDEFINITO,
              show_default=True, help="Intensità del trattamento (vedi --elenca-profili).")
@click.option("--elenca-profili", is_flag=True, expose_value=False, is_eager=True, callback=_elenca,
              help="Mostra i profili disponibili ed esce.")
@click.option("--motore", type=click.Choice(core.MOTORI), default="ffmpeg", show_default=True,
              help="ffmpeg = filtri, niente da installare; deepfilternet = rete neurale, molto più efficace sul rumore.")
@click.option("--segmenti", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="TSV di `casoweb separa-parlato`: il trattamento forte va solo ai tratti 'parlato', "
                   "la musica riceve solo un ritocco leggero [default: tutto trattato allo stesso modo].")
@click.option("--dissolvenza", type=float, default=0.4, show_default=True,
              help="Secondi di dissolvenza incrociata tra parlato e musica (con --segmenti).")
@click.option("--post-filtro/--senza-post-filtro", default=False,
              help="Con deepfilternet: post-filtro (--pf), più incisivo sul rumore ma più artificiale.")
@click.option("--volume-finale", is_flag=True, help="Porta il volume a -16 LUFS (loudnorm) alla fine.")
@click.option("--tieni-temporanei", is_flag=True, help="Non cancella i file intermedi (per capire cosa è successo).")
def pulisci_audio(ingresso: Path, uscita: Path | None, profilo: str, motore: str, segmenti: Path | None,
                  dissolvenza: float, post_filtro: bool, volume_finale: bool, tieni_temporanei: bool) -> None:
    """Ripulisce l'audio di una registrazione in presa diretta (anche dentro un video).

    Riduce il rumore di fondo e le code di rimbombo e uniforma il volume della
    voce; con --segmenti lascia la musica quasi intatta. Il parlato esce in
    mono (copiato su due canali). Richiede ffmpeg, e per --motore deepfilternet
    il programma DeepFilterNet.
    """
    core.run(ingresso, uscita=uscita, profilo=profilo, motore=motore, segmenti=segmenti,
             dissolvenza=dissolvenza, post_filtro=post_filtro, volume_finale=volume_finale,
             tieni_temporanei=tieni_temporanei)
