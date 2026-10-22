"""Punto d'ingresso del comando `casoweb`."""

import faulthandler
import logging
import platform
import sys

import click

from casoweb_tools import __version__, log
from casoweb_tools.cmds.pulisci_audio import pulisci_audio
from casoweb_tools.cmds.separa_parlato import separa_parlato
from casoweb_tools.cmds.video_da_audio import video_da_audio
from casoweb_tools.cmds.video_per_canon import video_per_canon

logger = logging.getLogger(__name__)
log.configura()


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="casoweb")
@log.opzione_verbosita
def main() -> None:
    """Strumenti ed esperimenti della rubrica Tecnologia @ CASO.

    https://www.casoweb.eu/?view=category&id=90
    """
    logger.debug(f"casoweb {__version__}, Python {platform.python_version()} ({sys.executable}), "
                 f"{platform.platform()}")


main.add_command(pulisci_audio)
main.add_command(separa_parlato)
main.add_command(video_da_audio)
main.add_command(video_per_canon)


def avvia() -> None:
    """Comando `casoweb`: come `main`, ma con faulthandler attivo.

    Se una libreria compilata va in crash ("Illegal instruction", "Segmentation fault"),
    Python stampa almeno dove si trovava il programma, invece del solo "core dumped".
    """
    faulthandler.enable()
    main()
