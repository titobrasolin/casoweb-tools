import json
import shutil
import subprocess
import wave

import pytest
from click.testing import CliRunner

from casoweb_tools import trova_offset as to
from casoweb_tools.cli import main

np = pytest.importorskip("numpy")


def segnale(secondi: float, seme: int, sr: int = to.SR):
    """Segnale che somiglia vagamente al parlato: toni e rumore con inviluppi che cambiano ogni ~0,3 s."""
    rng = np.random.default_rng(seme)
    out = []
    for _ in range(int(secondi / 0.3) + 1):
        t = np.arange(int(0.3 * sr)) / sr
        freqs = rng.uniform(150, 3000, size=3)
        amp = rng.uniform(0.05, 0.5, size=3)
        x = sum(a * np.sin(2 * np.pi * f * t) for a, f in zip(amp, freqs))
        x += rng.uniform(0.0, 0.3) * rng.standard_normal(len(t))
        out.append(x)
    return np.concatenate(out)[:int(secondi * sr)].astype(np.float32)


def test_trova_estratto_con_rumore_e_volume_diverso():
    lungo = segnale(180, seme=1)
    inizio = 71.37
    corto = lungo[int(inizio * to.SR):int((inizio + 60) * to.SR)] * 0.4
    corto = corto + 0.01 * np.random.default_rng(5).standard_normal(len(corto)).astype(np.float32)
    r = to.trova(lungo, corto)
    assert r.offset == pytest.approx(inizio, abs=0.01)
    assert r.scarto < 0.01 and r.punteggio > 0.8 and r.margine > 1.2


def test_estratto_non_presente():
    lungo = segnale(120, seme=1)
    altro = segnale(40, seme=2)
    r = to.trova(lungo, altro)
    assert r.punteggio < 0.5


def test_estratto_con_taglio_ha_scarto():
    lungo = segnale(180, seme=3)
    inizio = 20.0
    a = lungo[int(inizio * to.SR):int((inizio + 25) * to.SR)]
    b = lungo[int((inizio + 40) * to.SR):int((inizio + 75) * to.SR)]  # 15 s tagliati nel mezzo
    r = to.trova(lungo, np.concatenate([a, b]))
    assert r.scarto > 5


def test_estratto_troppo_breve():
    with pytest.raises(SystemExit):
        to.trova(segnale(60, 1), segnale(4, 1))


def test_fmt_tempo():
    assert to.fmt_tempo(3723.4567) == "01:02:03.457"


def _scrivi_wav(path, x, sr=to.SR):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 30000).astype("<i2").tobytes())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="serve ffmpeg")
def test_cli_con_codifiche_diverse(tmp_path):
    lungo = segnale(150, seme=7)
    _scrivi_wav(tmp_path / "lunga.wav", lungo / 3)
    inizio = 48.2
    _scrivi_wav(tmp_path / "e.wav", lungo[int(inizio * to.SR):int((inizio + 50) * to.SR)] / 3)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp_path / "e.wav"), "-b:a", "64k",
                    str(tmp_path / "estratto.mp3")], check=True)
    res = CliRunner().invoke(main, ["trova-offset", str(tmp_path / "estratto.mp3"), str(tmp_path / "lunga.wav"),
                                    "--json"])
    assert res.exit_code == 0, res.output
    dati = json.loads(res.output[res.output.index("{"):])
    assert dati["offset"] == pytest.approx(inizio, abs=0.08)
    assert dati["fotogrammi"] == round(dati["offset"] * 25)
