"""casoweb trova-offset: dove inizia un estratto audio dentro una registrazione più lunga."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import click

from casoweb_tools import trova_offset as core

logger = logging.getLogger(__name__)
_FILE = click.Path(exists=True, dir_okay=False, path_type=Path)


@click.command(
    "trova-offset",
    epilog="""\b
Esempi:
  casoweb trova-offset estratto.mp3 registrazione_completa.mp3
  casoweb trova-offset estratto.mp3 registrazione_completa.mp3 --cerca-da 3600 --json""",
)
@click.argument("estratto", type=_FILE)
@click.argument("registrazione", type=_FILE)
@click.option("--sonde", type=click.IntRange(1, 9), default=3, show_default=True,
              help="Quanti campioni dell'estratto confrontare: se danno lo stesso istante, la corrispondenza è sicura.")
@click.option("--durata-sonda", type=float, default=30.0, show_default=True, help="Secondi di ciascun campione.")
@click.option("--cerca-da", type=float, default=0.0, show_default=True,
              help="Cerca solo a partire da questo secondo della registrazione.")
@click.option("--cerca-fino", type=float, help="Cerca solo fino a questo secondo.")
@click.option("--fps", type=float, default=25.0, show_default=True,
              help="Fotogrammi al secondo, per dare l'offset anche in fotogrammi (timeline del video).")
@click.option("--json", "come_json", is_flag=True, help="Risultato in JSON, per gli script.")
def trova_offset(estratto: Path, registrazione: Path, sonde: int, durata_sonda: float, cerca_da: float,
                 cerca_fino: float | None, fps: float, come_json: bool) -> None:
    """Trova in che istante della REGISTRAZIONE comincia l'ESTRATTO.

    Confronta l'impronta spettrale di alcuni campioni dell'estratto con quella
    della registrazione (regge a codifiche e volumi diversi), poi raffina sulla
    forma d'onda. Richiede ffmpeg e numpy.
    """
    r = core.run(estratto, registrazione, sonde=sonde, durata_sonda=durata_sonda, cerca_da=cerca_da,
                 cerca_fino=cerca_fino)
    r["fotogrammi"] = round(r["offset"] * fps)
    if come_json:
        click.echo(json.dumps(r, ensure_ascii=False, indent=2))
        return
    click.echo(f"\nL'estratto comincia a {core.fmt_tempo(r['offset'])}  ({r['offset']:.3f} s, "
               f"fotogramma {r['fotogrammi']} a {fps:g} fps)")
    click.echo(f"e finisce a {core.fmt_tempo(r['fine'])}.")
    click.echo(f"Somiglianza {r['somiglianza']}, scarto tra i campioni {r['scarto']} s.")
    for a in r["avvisi"]:
        logger.warning(a)
