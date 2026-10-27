import shutil
import subprocess
import sys

import pytest
from click.testing import CliRunner

from casoweb_tools import pulisci_audio as pa
from casoweb_tools.cli import main

TSV = (
    "# commento\n"
    "inizio_s\tfine_s\tetichetta\tinizio_mm:ss\tdurata_s\n"
    "0.000\t1.000\tmusica\t00:00\t1.0\n"
    "1.000\t2.000\tparlato\t00:01\t1.0\n"
    "2.000\t3.000\tmusica\t00:02\t1.0\n"
)
ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="serve ffmpeg")


def test_leggi_parlato(tmp_path):
    f = tmp_path / "s.tsv"
    f.write_text(TSV, encoding="utf-8")
    assert pa.leggi_parlato(f) == [(1.0, 2.0)]


def test_espressione_gate():
    assert pa.espressione_gate([], 0.4) == "0"
    e = pa.espressione_gate([(1.0, 2.0), (5.0, 6.0)], 0.4)
    assert e.startswith("min(1,") and e.count("clip(") == 2


def test_elenca_profili():
    res = CliRunner().invoke(main, ["pulisci-audio", "--elenca-profili"])
    assert res.exit_code == 0 and "sala" in res.output


def test_deepfilternet_mancante(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "trova_deepfilternet", lambda: None)
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    res = CliRunner().invoke(main, ["pulisci-audio", str(f), "--motore", "deepfilternet"])
    assert res.exit_code != 0 and "Manca DeepFilterNet" in res.output


def _sinusoide(path, secondi=3):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=440:d={secondi}",
                    "-ac", "2", str(path)], check=True)


def _durata(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True).stdout
    return float(out)


@ffmpeg
def test_pulizia_completa(tmp_path):
    src = tmp_path / "serata.wav"
    _sinusoide(src)
    tsv = tmp_path / "serata_segmenti.tsv"
    tsv.write_text(TSV, encoding="utf-8")
    res = CliRunner().invoke(main, ["pulisci-audio", str(src), "--segmenti", str(tsv), "--volume-finale"])
    assert res.exit_code == 0, res.output
    out = tmp_path / "serata_pulito.wav"
    assert abs(_durata(out) - 3.0) < 0.3


@ffmpeg
def test_senza_segmenti_mp3(tmp_path):
    src = tmp_path / "a.wav"
    _sinusoide(src, 2)
    res = CliRunner().invoke(main, ["pulisci-audio", str(src), "-o", str(tmp_path / "b.mp3"), "--profilo", "leggero"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "b.mp3").stat().st_size > 0


@ffmpeg
def test_video_con_video_copiato(tmp_path):
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)],
                   check=True)
    res = CliRunner().invoke(main, ["pulisci-audio", str(src)])
    assert res.exit_code == 0, res.output
    out = tmp_path / "v_pulito.mp4"
    assert pa.ha_video(out)


@ffmpeg
@pytest.mark.skipif(sys.platform == "win32", reason="motore finto: script di shell")
def test_motore_finto(tmp_path, monkeypatch):
    finto = tmp_path / "deep-filter"
    # come il vero binario: legge l'ultimo argomento e scrive lo stesso nome nella cartella di -o
    finto.write_text('#!/bin/sh\nout="$2"\nfor a; do f="$a"; done\ncp "$f" "$out/$(basename "$f")"\n')
    finto.chmod(0o755)
    monkeypatch.setattr(pa, "trova_deepfilternet", lambda: (str(finto), "rust"))
    src = tmp_path / "a.wav"
    _sinusoide(src, 2)
    res = CliRunner().invoke(main, ["pulisci-audio", str(src), "--motore", "deepfilternet"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "a_pulito.wav").stat().st_size > 0


def test_uscita_predefinita(tmp_path):
    assert pa.uscita_predefinita(tmp_path / "a.mp3", False).name == "a_pulito.mp3"
    assert pa.uscita_predefinita(tmp_path / "a.webm", True).name == "a_pulito.webm"
    assert pa.uscita_predefinita(tmp_path / "a.avi", True).name == "a_pulito.mkv"
    assert pa.uscita_predefinita(tmp_path / "a.wma", False).name == "a_pulito.m4a"


def test_spazio_necessario():
    assert pa.spazio_necessario(3600) == pytest.approx(1.38e9, rel=0.01)


@ffmpeg
def test_spazio_insufficiente(tmp_path, monkeypatch):
    src = tmp_path / "a.wav"
    _sinusoide(src, 2)
    monkeypatch.setattr(pa.shutil, "disk_usage", lambda _p: type("U", (), {"free": 1000})())
    res = CliRunner().invoke(main, ["pulisci-audio", str(src)])
    assert res.exit_code != 0 and "Spazio insufficiente" in res.output


@ffmpeg
def test_ingresso_avi_ha_uscita_valida(tmp_path):
    src = tmp_path / "v.avi"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-shortest", "-c:v", "mpeg4", "-c:a", "mp3", str(src)],
                   check=True)
    res = CliRunner().invoke(main, ["pulisci-audio", str(src)])
    assert res.exit_code == 0, res.output
    assert pa.ha_video(tmp_path / "v_pulito.mkv")

def test_sottrai():
    assert pa.sottrai([(0, 10)], [(2, 3), (5, 6)]) == [(0, 2), (3, 5), (6, 10)]
    assert pa.sottrai([(0, 10)], [(0, 10)]) == []
    assert pa.sottrai([(0, 4), (8, 12)], [(3, 9)]) == [(0, 3), (9, 12)]
    assert pa.sottrai([(0, 4)], [(5, 6)]) == [(0, 4)]


def test_escludi_senza_segmenti(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    t = tmp_path / "e.tsv"
    t.write_text(TSV, encoding="utf-8")
    res = CliRunner().invoke(main, ["pulisci-audio", str(f), "--escludi", str(t)])
    assert res.exit_code != 0 and "--segmenti" in res.output


@ffmpeg
def test_pulizia_con_escludi(tmp_path):
    src = tmp_path / "serata.wav"
    _sinusoide(src)
    tsv = tmp_path / "s.tsv"
    tsv.write_text(TSV, encoding="utf-8")
    ap = tmp_path / "ap.tsv"
    ap.write_text("inizio_s\tfine_s\tetichetta\n1.200\t1.800\tApplause\n", encoding="utf-8")
    res = CliRunner().invoke(main, ["pulisci-audio", str(src), "--segmenti", str(tsv), "--escludi", str(ap)])
    assert res.exit_code == 0, res.output


def test_sottrai_con_esclusi_disordinati_o_sovrapposti():
    assert pa.sottrai([(0, 10)], [(8, 12), (-1, 1), (3, 4), (3.5, 5)]) == [(1, 3), (5, 8)]


def test_leggi_intervalli_fonde_e_segnala_righe_errate(tmp_path):
    f = tmp_path / "a.tsv"
    f.write_text("inizio_s\tfine_s\n5\t7\n1\t3\n2\t4\n", encoding="utf-8")
    assert pa.leggi_intervalli(f) == [(1, 4), (5, 7)]
    f.write_text("abc\tdef\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        pa.leggi_intervalli(f)
