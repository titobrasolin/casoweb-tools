"""casoweb rileva-suoni: applausi e altri suoni noti in una registrazione (YAMNet)."""

from __future__ import annotations

from pathlib import Path

import click

from casoweb_tools import rileva_suoni as core


def _elenca(ctx: click.Context, param, value) -> None:
    if value and not ctx.resilient_parsing:
        core.elenca_classi(None)
        ctx.exit()


@click.command(
    "rileva-suoni",
    epilog="""\b
Esempi:
  casoweb rileva-suoni serata.mp4
  casoweb rileva-suoni serata.mp4 --classe Applause --classe Laughter --soglia 0.15
  casoweb rileva-suoni --elenca-classi""",
)
@click.argument("ingresso", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--classe", "-c", "classi", multiple=True,
              help=f"Classe AudioSet da cercare, ripetibile [default: {', '.join(core.CLASSI_PREDEFINITE)}].")
@click.option("--elenca-classi", is_flag=True, expose_value=False, is_eager=True, callback=_elenca,
              help="Mostra le 521 classi disponibili ed esce.")
@click.option("--soglia", "-t", type=click.FloatRange(0, 1), default=0.2, show_default=True,
              help="Punteggio minimo (non è una probabilità: vedi la documentazione).")
@click.option("--pausa-max", type=float, default=1.0, show_default=True,
              help="Secondi di pausa sotto i quali due rilevamenti formano un solo evento.")
@click.option("--durata-min", type=float, default=0.0, show_default=True,
              help="Scarta gli eventi più brevi di N secondi.")
@click.option("--modello", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="File yamnet.tflite [default: scaricato una volta in ~/.cache/casoweb].")
@click.option("--scarica-modello", "scarica", is_flag=True, help="Riscarica il modello.")
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path),
              help="Dove scrivere .srt e .tsv [default: accanto all'ingresso].")
def rileva_suoni(ingresso: Path, classi: tuple[str, ...], soglia: float, pausa_max: float,
                 durata_min: float, modello: Path | None, scarica: bool, output_dir: Path | None) -> None:
    """Trova applausi (o altri suoni) in un audio o video e scrive <nome>_suoni.srt e .tsv.

    Usa YAMNet in versione TFLite: pochi MB, solo CPU. Il TSV ha lo stesso
    formato di separa-parlato e si può passare a `pulisci-audio --escludi`.
    Richiede ffmpeg, numpy e un runtime TFLite (ai-edge-litert).
    """
    core.run(ingresso, classi=classi or core.CLASSI_PREDEFINITE, soglia=soglia, pausa_max=pausa_max,
             durata_min=durata_min, modello=modello, scarica=scarica, uscita_dir=output_dir)
