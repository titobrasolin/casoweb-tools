import shutil
import struct
import subprocess
import time

import pytest
from click.testing import CliRunner

from canon_sintetico import leggi_makernote, thm
from casoweb_tools import canon_avi as ca
from casoweb_tools import video_per_canon as vc
from casoweb_tools.cli import main

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="serve ffmpeg")


def _jpeg(tmp_path, w=160, h=120, extra=()):
    out = tmp_path / f"f{w}.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={w}x{h}", "-frames:v", "1",
                    "-c:v", "mjpeg", "-pix_fmt", "yuvj422p", *extra, str(out)], check=True)
    return out.read_bytes()


def _sintetico(n=31, fps=15):
    frames = [b"\xff\xd8" + bytes([i % 251]) * (100 + i) + b"\xff\xd9" for i in range(n)]
    audio = bytes(range(256)) * ((n * ca.AUDIO_RATE) // fps // 256 + 1)
    return frames, audio[:(n * ca.AUDIO_RATE) // fps]


# --------------------------------------------------------------------------
# Contenitore
# --------------------------------------------------------------------------
def test_build_e_parse_andata_e_ritorno():
    frames, audio = _sintetico()
    avi = ca.build(frames, audio, 160, 120, 15, "Thu Oct 15 18:43:06 2026")
    assert ca.parse_canon(avi) == (frames, audio, 160, 120, 15, "Thu Oct 15 18:43:06 2026")


def test_struttura_canon():
    frames, audio = _sintetico(n=31, fps=15)
    avi = ca.build(frames, audio, 160, 120, 15, ca.idit_string(0))
    assert avi[:4] == b"RIFF" and struct.unpack("<I", avi[4:8])[0] == len(avi) - 8
    assert avi[ca.MOVI_START:ca.MOVI_START + 4] == b"LIST" and avi[ca.MOVI_START + 8:ca.MOVI_START + 12] == b"movi"
    assert b"CanonMVI06" in avi[:ca.MOVI_START]
    assert struct.unpack("<I", avi[0x20:0x24])[0] == 66666  # durata del fotogramma troncata
    # un secondo di audio, poi 15 fotogrammi
    pos = ca.MOVI_START + 12
    ids = []
    while avi[pos:pos + 2] != b"ix":
        cid, size = avi[pos:pos + 4], struct.unpack("<I", avi[pos + 4:pos + 8])[0]
        ids.append((cid, size))
        pos += 8 + size + (size & 1)
    assert ids[0] == (b"01wb", ca.AUDIO_RATE)
    assert [c for c, _ in ids[1:16]] == [b"00dc"] * 15 and ids[16][0] == b"01wb"
    # idx1 con due sole voci: primo blocco audio e primo fotogramma
    idx1 = avi.rindex(b"idx1")
    assert struct.unpack("<I", avi[idx1 + 4:idx1 + 8])[0] == 32


def test_build_rifiuta_audio_di_durata_diversa():
    frames, audio = _sintetico()
    with pytest.raises(AssertionError):
        ca.build(frames, audio[:-1], 160, 120, 15)


def test_idit_giorno_su_due_caratteri():
    s = ca.idit_string(time.mktime((2016, 6, 4, 12, 12, 52, 0, 0, -1)))
    assert s.endswith("Jun  4 12:12:52 2016") and len(s) == 24


def test_parse_rifiuta_avi_non_canon():
    with pytest.raises(ValueError, match="stile Canon"):
        ca.parse_canon(b"RIFF\0\0\0\0AVI " + b"\0" * 4000)


# --------------------------------------------------------------------------
# JPEG e THM
# --------------------------------------------------------------------------
@needs_ffmpeg
def test_a_stile_canon(tmp_path):
    jpeg = _jpeg(tmp_path, extra=["-huffman", "default"])
    out = vc.a_stile_canon(jpeg)
    segs, _ = vc.segmenti(out)
    marcatori = [m for m, _ in segs]
    assert marcatori[:2] == [0xE0, 0xDD] and segs[0][1][4:8] == b"AVI1"
    assert 0xC4 not in marcatori  # niente tabelle Huffman
    subprocess.run(["ffmpeg", "-v", "error", "-f", "mjpeg", "-i", "-", "-f", "null", "-"], input=out, check=True)


@needs_ffmpeg
def test_patch_exif_aggiorna_numero_fotogrammi_e_date(tmp_path):
    modello = thm(_jpeg(tmp_path), fotogrammi=153, numero_file=1011259)
    quando = time.localtime(time.mktime((2026, 10, 16, 8, 22, 0, 0, 0, -1)))
    nuovo = vc.thm_da_modello(modello, _jpeg(tmp_path), quando, 1011266, 45)
    assert leggi_makernote(nuovo) == {"numero_file": 1011266, "fotogrammi": (45, 45, 45),
                                      "date": ["2026:10:16 08:22:00"] * 2}


def test_thm_modello_senza_exif():
    with pytest.raises(SystemExit, match="Exif"):
        vc.thm_da_modello(b"\xff\xd8\xff\xe0", b"", time.localtime(), 1, 1)


# --------------------------------------------------------------------------
# Conversione completa
# --------------------------------------------------------------------------
@needs_ffmpeg
def test_conversione_completa(tmp_path):
    video = tmp_path / "prova.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:r=25:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-shortest", str(video)], check=True)
    modello = tmp_path / "MVI_1259.THM"
    modello.write_bytes(thm(_jpeg(tmp_path)))
    sd = tmp_path / "DCIM" / "101CANON"

    res = CliRunner().invoke(main, ["video-per-canon", str(video), "--numero", "1266",
                                    "--thm-modello", str(modello), "--cartella", str(sd)])
    assert res.exit_code == 0, res.output

    avi = (sd / "MVI_1266.AVI").read_bytes()
    frames, audio, w, h, fps, _ = ca.parse_canon(avi)
    assert (w, h, fps, len(frames)) == (160, 120, 15, 30)
    assert max(len(f) for f in frames) <= vc.PROFILI["compatto"][4] + 40
    mn = leggi_makernote((sd / "MVI_1266.THM").read_bytes())
    assert mn["numero_file"] == 1011266 and mn["fotogrammi"] == (30, 30, 30)

    res = CliRunner().invoke(main, ["video-per-canon", "--verifica", str(sd / "MVI_1266.AVI")])
    assert res.exit_code == 0 and "identico byte per byte" in res.output


def test_cli_senza_argomenti():
    res = CliRunner().invoke(main, ["video-per-canon"])
    assert res.exit_code == 2
    assert "INGRESSO, --numero, --thm-modello" in res.output


def test_cli_numero_fuori_intervallo(tmp_path):
    f = tmp_path / "v.mp4"
    f.touch()
    res = CliRunner().invoke(main, ["video-per-canon", str(f), "--numero", "0", "--thm-modello", str(f)])
    assert res.exit_code == 2 and "--numero" in res.output
