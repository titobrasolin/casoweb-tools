import logging
import os
import signal
import subprocess
import sys

import pytest
from click.testing import CliRunner

from casoweb_tools import ambiente
from casoweb_tools.cli import main

VECCHIA = {"fpu", "sse", "sse2", "sse3", "3dnowext"}  # es. AMD Turion X2 (2008)
MODERNA = {"sse3", "ssse3", "sse4_1", "sse4_2", "popcnt", "avx", "avx2"}
SENZA_AVX = {"sse3", "ssse3", "sse4_1", "sse4_2", "popcnt"}  # es. Intel Core i3 M 370 (2010)


def test_verbosita_debug_e_warning():
    res = CliRunner().invoke(main, ["-v", "DEBUG", "video-da-audio", "--elenca-profili"])
    assert res.exit_code == 0 and "debug: casoweb" in res.output
    res = CliRunner().invoke(main, ["-v", "warning", "video-da-audio", "--elenca-profili"])
    assert res.exit_code == 0 and "debug:" not in res.output and "whatsapp_480p" in res.output


def test_verbosita_non_valida():
    res = CliRunner().invoke(main, ["-v", "boh", "--help"])
    assert res.exit_code == 2


@pytest.mark.skipif(not hasattr(signal, "SIGILL"), reason="serve un sistema POSIX")
def test_crash_mostra_dove_si_trovava():
    """Un "Illegal instruction" vero: faulthandler deve stampare il traceback Python."""
    codice = ("import os, signal\n"
              "from casoweb_tools import cli\n"
              "def carica_libreria():\n"
              "    os.kill(os.getpid(), signal.SIGILL)\n"
              "cli.main = carica_libreria\n"
              "cli.avvia()\n")
    r = subprocess.run([sys.executable, "-c", codice], capture_output=True, text=True)
    assert r.returncode == -signal.SIGILL
    assert "Fatal Python error" in r.stderr and "carica_libreria" in r.stderr


# --------------------------------------------------------------------------
# Processore
# --------------------------------------------------------------------------
def test_istruzioni_cpu_da_cpuinfo():
    cpuinfo = "processor\t: 0\nmodel name\t: AMD Turion(tm) X2\nflags\t\t: fpu sse sse2 sse3\n\n"
    assert ambiente.istruzioni_cpu(cpuinfo) == {"fpu", "sse", "sse2", "sse3"}
    assert ambiente.istruzioni_cpu("processor\t: 0\n") is None


def test_manca_cpu(monkeypatch):
    monkeypatch.delenv("CASOWEB_IGNORA_CPU", raising=False)
    req = {"TensorFlow": {"avx"}, "NumPy": {"ssse3", "sse4_2"}}
    assert ambiente.manca_cpu(req, MODERNA) is None
    m = ambiente.manca_cpu(req, VECCHIA)
    assert "TensorFlow: AVX" in m and "NumPy: SSE4.2, SSSE3" in m and "Illegal instruction" in m
    assert ambiente.manca_cpu({"TensorFlow": {"avx"}}, set()) is not None
    monkeypatch.setenv("CASOWEB_IGNORA_CPU", "1")
    assert ambiente.manca_cpu(req, VECCHIA) is None


def test_versione_installata():
    assert ambiente.versione_installata("click")[0] >= 8
    assert ambiente.versione_installata("pacchetto-che-non-esiste") is None


@pytest.mark.parametrize("numpy, con_numpy", [((2, 3, 5), False), ((2, 5, 4), True), (None, True)])
def test_requisiti_dipendono_da_numpy(numpy, con_numpy, monkeypatch):
    from casoweb_tools.cmds import separa_parlato as cmd

    monkeypatch.setattr(ambiente, "versione_installata", lambda d: numpy)
    attesi = {cmd.SEGMENTAZIONE, "CTranslate2 (faster-whisper)"} | ({"NumPy 2.4 o successivo"} if con_numpy else set())
    assert set(cmd.requisiti_cpu()) == attesi


def test_requisiti_dipendono_dai_passi():
    from casoweb_tools.cmds import separa_parlato as cmd

    assert cmd.SEGMENTAZIONE not in cmd.requisiti_cpu(segmenta=False)
    assert "CTranslate2 (faster-whisper)" not in cmd.requisiti_cpu(trascrive=False)


def test_percorso_tsv_coincide_con_quello_di_run(tmp_path):
    from casoweb_tools.cmds import separa_parlato as cmd

    src = tmp_path / "rec.aac"
    assert cmd.percorso_tsv(src, None) == tmp_path / "rec_output" / "rec_segmenti.tsv"
    assert cmd.percorso_tsv(src, tmp_path / "out") == tmp_path / "out" / "rec_segmenti.tsv"


def test_separa_parlato_su_cpu_vecchia_non_va_in_crash(tmp_path, monkeypatch):
    monkeypatch.delenv("CASOWEB_IGNORA_CPU", raising=False)
    monkeypatch.setattr(ambiente, "istruzioni_cpu", lambda: VECCHIA)
    src = tmp_path / "rec.aac"
    src.write_bytes(b"")
    res = CliRunner().invoke(main, ["separa-parlato", str(src)])
    assert res.exit_code == 1
    assert "non ha le istruzioni richieste" in res.output and "AVX" in res.output
    assert "Dipendenze mancanti" not in res.output  # il processore viene prima delle dipendenze
    assert "--solo-segmenti" not in res.output  # qui non gira nemmeno il resto


def test_senza_avx_e_senza_tsv_suggerisce_di_segmentare_altrove(tmp_path, monkeypatch):
    monkeypatch.delenv("CASOWEB_IGNORA_CPU", raising=False)
    monkeypatch.setattr(ambiente, "istruzioni_cpu", lambda: SENZA_AVX)
    src = tmp_path / "rec.aac"
    src.write_bytes(b"")
    res = CliRunner().invoke(main, ["separa-parlato", str(src)])
    assert res.exit_code == 1
    assert "AVX" in res.output and "SSE4" not in res.output
    assert "--solo-segmenti" in res.output and "rec_segmenti.tsv" in res.output


def test_senza_avx_con_tsv_pronto_si_procede(tmp_path, monkeypatch):
    """Con la segmentazione già fatta TensorFlow non serve: il controllo deve lasciar passare."""
    from casoweb_tools import separa_parlato as core

    monkeypatch.delenv("CASOWEB_IGNORA_CPU", raising=False)
    monkeypatch.setattr(ambiente, "istruzioni_cpu", lambda: SENZA_AVX)
    monkeypatch.setattr("casoweb_tools.cmds.separa_parlato.DIPENDENZE", ("numpy",))
    chiamate = []
    monkeypatch.setattr(core, "run", lambda input, o: chiamate.append(input))
    src = tmp_path / "rec.aac"
    src.write_bytes(b"")
    (tmp_path / "rec_output").mkdir()
    (tmp_path / "rec_output" / "rec_segmenti.tsv").write_text("0\t5\tparlato\n", encoding="utf-8")
    res = CliRunner().invoke(main, ["separa-parlato", str(src)])
    assert res.exit_code == 0, res.output
    assert chiamate == [src]
    res = CliRunner().invoke(main, ["separa-parlato", str(src), "--risegmenta"])
    assert res.exit_code == 1 and "AVX" in res.output
