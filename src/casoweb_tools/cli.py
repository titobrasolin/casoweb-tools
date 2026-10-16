"""Punto d'ingresso del comando `casoweb`."""

import click

from casoweb_tools import __version__
from casoweb_tools.cmds.video_da_audio import video_da_audio
from casoweb_tools.cmds.separa_parlato import separa_parlato


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="casoweb")
def main() -> None:
    """Strumenti ed esperimenti della rubrica Tecnologia @ CASO.

    https://www.casoweb.eu/?view=category&id=90
    """


main.add_command(separa_parlato)
main.add_command(video_da_audio)
