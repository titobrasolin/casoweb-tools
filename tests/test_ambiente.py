import json
import sys
from pathlib import Path

import pytest

from casoweb_tools import ambiente as amb

EXTRA = amb.Extra("separa-parlato", max_python=(3, 12), variante_cpu=True)
LEGGERO = amb.Extra("leggero")


def msg(tipo, gpu=False, versione=(3, 12), extra=EXTRA, sorgente=None):
    return amb.manca_extra(extra, ["faster_whisper"], tipo=tipo, gpu=gpu, versione=versione, sorgente=sorgente)


def test_installazione_uv_tool_e_pipx(tmp_path):
    (tmp_path / "uv-receipt.toml").touch()
    assert amb.installazione(tmp_path) == amb.UV_TOOL
    other = tmp_path / "pipx"
    other.mkdir()
    (other / "pipx_metadata.json").write_text(json.dumps({}))
    assert amb.installazione(other) == amb.PIPX


def test_installazione_editabile_rilevata():
    # i test girano su un'installazione `-e`: deve riconoscerla e trovare il sorgente
    assert amb.installazione(Path(sys.prefix)) == amb.SVILUPPO
    assert (amb.sorgente_editabile() / "pyproject.toml").is_file()


def test_uv_tool_senza_gpu_propone_variante_leggera():
    m = msg(amb.UV_TOOL)
    assert "Nessuna GPU NVIDIA" in m
    assert f"--overrides {amb.OVERRIDES_CPU}" in m
    assert "'casoweb-tools[separa-parlato,cpu] @ git+" in m
    assert "--python 3.12" in m and "pipx" not in m


def test_uv_tool_con_gpu_niente_overrides():
    m = msg(amb.UV_TOOL, gpu=True)
    assert "--overrides" not in m and "[separa-parlato] @" in m


def test_python_troppo_nuovo_lo_dice():
    m = msg(amb.UV_TOOL, gpu=True, versione=(3, 13))
    assert "Python 3.13" in m and "Python 3.12 o precedente" in m
    assert "--python 3.12" in m


def test_pipx_con_gpu_resta_su_pipx():
    m = msg(amb.PIPX, gpu=True)
    assert "pipx install --force 'casoweb-tools[separa-parlato] @" in m
    assert "uv tool" not in m


def test_pipx_senza_gpu_suggerisce_uv():
    m = msg(amb.PIPX)
    assert "Conviene passare a uv" in m and "NVIDIA" in m
    assert "pipx uninstall casoweb-tools" in m and "uv tool install" in m


def test_pipx_python_troppo_nuovo_suggerisce_uv():
    m = msg(amb.PIPX, gpu=True, versione=(3, 13))
    assert "uv procura da solo Python 3.12" in m


def test_sviluppo_usa_il_sorgente_e_gli_overrides_locali(tmp_path):
    m = msg(amb.SVILUPPO, sorgente=tmp_path)
    assert f"uv pip install --python {sys.executable}" in m
    assert f"--overrides {tmp_path / 'scripts' / 'overrides-cpu.txt'}" in m
    assert f"-e '{tmp_path}[dev,separa-parlato,cpu]'" in m


def test_sviluppo_python_troppo_nuovo_ricrea_venv(tmp_path):
    m = msg(amb.SVILUPPO, gpu=True, versione=(3, 13), sorgente=tmp_path)
    assert "uv venv -p 3.12" in m


def test_altro_e_extra_senza_vincoli():
    m = msg(amb.ALTRO, extra=LEGGERO)
    assert "uv tool install --force 'casoweb-tools[leggero] @" in m
    assert "--python" not in m and "--overrides" not in m and "GPU" not in m


def test_sempre_il_link_al_readme():
    for tipo in (amb.UV_TOOL, amb.PIPX, amb.SVILUPPO, amb.ALTRO):
        assert msg(tipo).endswith(f"Dettagli: {amb.README}")


@pytest.mark.parametrize("gestore, atteso", [
    ("apt", "sudo apt install ffmpeg"),
    ("dnf", "sudo dnf install ffmpeg-free"),
    ("pacman", "sudo pacman -S ffmpeg"),
    ("brew", "brew install ffmpeg"),
    ("winget", "winget install Gyan.FFmpeg"),
    (None, "gestore di pacchetti del sistema"),
])
def test_manca_programma(gestore, atteso, monkeypatch):
    monkeypatch.setattr(amb, "gestore_pacchetti", lambda: gestore)
    assert atteso in amb.manca_programma("ffmpeg")
