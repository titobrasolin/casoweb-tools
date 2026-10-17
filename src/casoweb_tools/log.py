"""Messaggi di casoweb: un logger per modulo, figli di "casoweb_tools", mostrati con click-log.

Il livello si sceglie con `casoweb -v LIVELLO` (DEBUG, INFO, WARNING, ERROR); il
predefinito è INFO. In DEBUG ogni comando dice anche quali librerie carica e quali
programmi esterni lancia: se qualcosa si blocca o va in crash, l'ultima riga indica dove.
"""

from __future__ import annotations

import logging
import sys

import click
import click_log

radice = logging.getLogger("casoweb_tools")

_PREFISSI = {
    logging.DEBUG: ("debug", "blue"),
    logging.WARNING: ("attenzione", "yellow"),
    logging.ERROR: ("errore", "red"),
    logging.CRITICAL: ("errore", "red"),
}


class Formatter(logging.Formatter):
    """Come quello di click-log, ma con i prefissi in italiano; INFO resta senza prefisso."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if record.levelno not in _PREFISSI:
            return msg
        nome, colore = _PREFISSI[record.levelno]
        prefisso = click.style(f"{nome}: ", fg=colore)
        return "\n".join(prefisso + riga for riga in msg.splitlines())


def configura() -> None:
    """Un solo gestore su "casoweb_tools", che scrive su stderr tramite click."""
    if not any(isinstance(h, click_log.ClickHandler) for h in radice.handlers):
        h = click_log.ClickHandler()
        h.setFormatter(Formatter())
        radice.addHandler(h)
        radice.propagate = False
    radice.setLevel(logging.INFO)


opzione_verbosita = click_log.simple_verbosity_option(
    radice, "-v", "--verbosita", metavar="LIVELLO", show_default=True,
    help="Quanto dettaglio mostrare: DEBUG, INFO, WARNING o ERROR.",
)


def progresso(testo: str) -> None:
    """Riga di avanzamento che si sovrascrive (es. percentuale); solo da INFO in giù."""
    if radice.isEnabledFor(logging.INFO):
        print(f"\r{testo}", end="", file=sys.stderr, flush=True)


def fine_progresso() -> None:
    if radice.isEnabledFor(logging.INFO):
        print(file=sys.stderr)
