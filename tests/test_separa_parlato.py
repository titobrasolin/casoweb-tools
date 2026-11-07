import json
import shutil
import subprocess

import numpy as np
import pytest
from click.testing import CliRunner

from casoweb_tools import separa_parlato as ir
from casoweb_tools.cli import main

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="serve ffmpeg")


@pytest.fixture
def senza_modelli(monkeypatch):
    """Il comando controlla in anticipo tutto l'extra; con TSV e cache pronti bastano numpy e ffmpeg."""
    monkeypatch.setattr("casoweb_tools.cmds.separa_parlato.DIPENDENZE", ("numpy",))


def test_clean_segments_accorpa_frammenti():
    raw = [
        ("speech", 0.0, 10.0),
        ("noEnergy", 10.0, 11.0),  # pausa breve tra due parlati: resta parlato
        ("speech", 11.0, 20.0),
        ("music", 20.0, 21.0),  # musica troppo breve: diventa parlato
        ("speech", 21.0, 30.0),
        ("music", 30.0, 60.0),
        ("speech", 60.0, 61.0),  # parlato troppo breve (un verso cantato): diventa musica
        ("music", 61.0, 90.0),
    ]
    assert ir.clean_segments(raw) == [(ir.PARLATO, 0.0, 30.0), (ir.MUSICA, 30.0, 90.0)]


def test_tsv_andata_e_ritorno(tmp_path):
    segs = [(ir.PARLATO, 0.0, 12.5), (ir.MUSICA, 12.5, 40.0)]
    tsv = tmp_path / "s.tsv"
    ir.write_tsv(tsv, segs)
    assert ir.read_tsv(tsv) == segs


def test_read_tsv_rifiuta_etichette_sconosciute(tmp_path):
    tsv = tmp_path / "s.tsv"
    tsv.write_text("0\t5\tparlatto\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="parlatto"):
        ir.read_tsv(tsv)


def test_pad_intervals_fonde_e_resta_nei_limiti():
    assert ir.pad_intervals([(0.1, 5.0), (5.4, 8.0), (20.0, 29.9)], 0.3, 30.0) == [
        (0.0, 8.3), (19.7, 30.0)]


def test_subtract_e_union():
    assert ir.subtract([(0, 10), (20, 30)], [(2, 4), (25, 40)]) == [(0, 2), (4, 10), (20, 25)]
    assert ir.union([(5, 6), (0, 2), (1, 3)]) == [(0, 3), (5, 6)]


def test_timemap():
    tm = ir.TimeMap([(10.0, 20.0), (50.0, 55.0)])
    assert tm.locate_orig(15.0) == 0
    assert tm.locate_orig(30.0) is None
    assert tm.orig_to_cut(1, 52.0) == 12.0
    assert tm.cut_to_orig(12.0) == (1, 52.0)


def _words(text, start, step=0.4):
    return [{"s": start + k * step, "e": start + k * step + 0.3, "w": " " + w}
            for k, w in enumerate(text.split())]


def test_build_cues_non_attraversa_i_tagli():
    speech = [(0.0, 3.0), (10.0, 13.0)]
    # la frase prosegue senza pausa oltre il taglio: deve comunque spezzarsi
    words = _words("uno due tre quattro cinque sei", 0.5) + _words("sette otto nove", 10.0)
    cues = ir.build_cues(words, speech)
    assert [c["i"] for c in cues] == [0, 1]
    assert all(speech[c["i"]][0] <= c["s"] < c["e"] <= speech[c["i"]][1] for c in cues)


def test_build_cues_rispetta_max_car_e_righe():
    words = _words(" ".join(["parola"] * 40), 0.0, step=0.1)
    cues = ir.build_cues(words, [(0.0, 10.0)], max_chars=30, width=15)
    assert len(cues) > 1
    for c in cues:
        assert len(c["text"].replace("\n", " ")) <= 30
        assert c["text"].count("\n") <= 1


def test_gain_rampe_interne():
    t = np.arange(0, 3, 0.01)
    g = ir.gain(t, [(1.0, 2.0)], fade=0.1)
    assert g[t < 1.0].max() == 0 and g[t >= 2.0].max() == 0
    assert g[(t > 1.15) & (t < 1.85)].min() == 1.0
    assert 0 < g[np.searchsorted(t, 1.05)] < 1


@needs_ffmpeg
def test_end_to_end_senza_modelli(tmp_path, senza_modelli):
    """Con un TSV già pronto e senza trascrizione non servono né inaSpeechSegmenter né Whisper."""
    src = tmp_path / "reg.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=30",
                    "-ac", "2", str(src)], check=True)
    out = tmp_path / "reg_output"
    out.mkdir()
    ir.write_tsv(out / "reg_segmenti.tsv",
                 [(ir.PARLATO, 0.0, 10.0), (ir.MUSICA, 10.0, 20.0), (ir.PARLATO, 20.0, 30.0)])

    res = CliRunner().invoke(main, ["separa-parlato", str(src), "--no-trascrizione",
                                    "--formato", "wav", "--padding", "0.5"])
    assert res.exit_code == 0, res.output

    def dur(p):
        return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                     "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout)

    assert dur(out / "reg_parlato.wav") == pytest.approx(21.0, abs=0.05)  # 10.5 + 10.5
    assert dur(out / "reg_silenziato.wav") == pytest.approx(30.0, abs=0.05)
    assert not (out / "reg_trascrizione.json").exists()


@needs_ffmpeg
def test_srt_da_cache(tmp_path, senza_modelli):
    """Trascrizione in cache: si rigenerano gli SRT senza caricare Whisper."""
    src = tmp_path / "reg.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=30", str(src)], check=True)
    out = tmp_path / "reg_output"
    out.mkdir()
    ir.write_tsv(out / "reg_segmenti.tsv", [(ir.MUSICA, 0.0, 20.0), (ir.PARLATO, 20.0, 30.0)])
    cache = {"modello": "large-v3-turbo", "coperto": [[19.7, 30.0]],
             "parole": _words("buongiorno a tutti gli ascoltatori.", 21.0)}
    (out / "reg_trascrizione.json").write_text(json.dumps(cache), encoding="utf-8")

    res = CliRunner().invoke(main, ["separa-parlato", str(src), "--no-audio"])
    assert res.exit_code == 0, res.output
    parlato = (out / "reg_parlato.srt").read_text(encoding="utf-8")
    silenziato = (out / "reg_silenziato.srt").read_text(encoding="utf-8")
    assert "buongiorno a tutti gli ascoltatori." in parlato
    assert "00:00:01,300 -->" in parlato  # 21.0 - 19.7 sulla timeline tagliata
    assert "00:00:21,000 -->" in silenziato


def test_dipendenze_mancanti_messaggio_chiaro(tmp_path, monkeypatch):
    import importlib.util

    # su un PC senza AVX il controllo del processore scatterebbe prima di quello delle dipendenze
    monkeypatch.setenv("CASOWEB_IGNORA_CPU", "1")
    vera = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "faster_whisper" else vera(name, *a))
    src = tmp_path / "reg.wav"
    src.write_bytes(b"")
    res = CliRunner().invoke(main, ["separa-parlato", str(src)])
    assert res.exit_code == 1
    assert "faster_whisper" in res.output
    # i test girano in un'installazione editabile: il suggerimento è per l'ambiente di sviluppo
    assert "uv pip install" in res.output and "[dev,separa-parlato" in res.output


def test_blocchi():
    assert ir.blocchi(None, 600) == [(0.0, None)]  # durata ignota: tutto insieme
    assert ir.blocchi(500, 600) == [(0.0, None)]
    assert ir.blocchi(3600, 0) == [(0.0, None)]  # 0 = nessuna divisione
    assert ir.blocchi(620, 600) == [(0.0, None)]  # avanzo troppo corto per un blocco a sé
    assert ir.blocchi(1500, 600) == [(0.0, 600.0), (600.0, 1200.0), (1200.0, None)]
    assert ir.blocchi(1200, 600) == [(0.0, 600.0), (600.0, None)]
    assert ir.blocchi(1210, 600) == [(0.0, 600.0), (600.0, None)]  # i 10 s finali vanno nell'ultimo


def test_segment_audio_a_blocchi(tmp_path, monkeypatch):
    """Un Segmenter finto: una chiamata per blocco, segmenti uguali ai confini riuniti."""
    import sys
    import types

    chiamate = []

    class Segmenter:
        def __init__(self, **kw):
            pass

        def __call__(self, path, start_sec=None, stop_sec=None):
            chiamate.append((start_sec, stop_sec))
            s = start_sec or 0.0
            e = stop_sec or 1500.0
            # tempi assoluti, come inaSpeechSegmenter
            return [("speech", s, e)] if s < 600 else [("speech", s, s + 100), ("music", s + 100, e)]

    monkeypatch.setitem(sys.modules, "inaSpeechSegmenter", types.SimpleNamespace(Segmenter=Segmenter))
    monkeypatch.setattr(ir, "durata", lambda p: 1500.0)

    raw = ir.segment_audio(tmp_path / "x.aac", blocco=600)
    assert chiamate == [(None, 600.0), (600.0, 1200.0), (1200.0, None)]
    assert raw == [("speech", 0.0, 700.0), ("music", 700.0, 1200.0),
                   ("speech", 1200.0, 1300.0), ("music", 1300.0, 1500.0)]


@needs_ffmpeg
def test_durata_esatta_anche_per_aac_senza_indice(tmp_path):
    """Per l'AAC ADTS ffprobe stima la durata dal bitrate; qui serve quella vera."""
    f = tmp_path / "x.aac"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=95:c=pink",
                    "-c:a", "aac", "-b:a", "64k", "-f", "adts", str(f)], check=True)
    assert ir.durata(f) == pytest.approx(95.0, abs=0.1)
    assert ir.durata(tmp_path / "manca.aac") is None
