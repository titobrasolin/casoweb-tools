import io
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
    audio = bytes(range(256)) * (ca.campioni_audio(n, fps) // 256 + 1)
    return frames, audio[:ca.campioni_audio(n, fps)]


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


@pytest.mark.parametrize("n, fps, attesi", [
    # valori dei file Canon reali (A2000 IS): l'ultimo secondo è arrotondato per eccesso
    (80, 30, 29398), (76, 30, 27928), (79, 30, 29030), (44, 15, 32338),
    (30, 30, 11024),
])
def test_campioni_audio_per_eccesso(n, fps, attesi):
    assert ca.campioni_audio(n, fps) == attesi


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
    assert marcatori.count(0xDB) == 1 and len(out) % 2 == 0
    subprocess.run(["ffmpeg", "-v", "error", "-f", "mjpeg", "-i", "-", "-f", "null", "-"], input=out, check=True)


@needs_ffmpeg
def test_jpeg_422_come_canon(tmp_path):
    from PIL import Image

    im = Image.open(io.BytesIO(_jpeg(tmp_path, 640, 480))).convert("RGB")
    jpeg = vc.jpeg_422(im, 92)
    out = vc.a_stile_canon(jpeg)
    sof = dict(vc.segmenti(out)[0])[0xC0]
    # componenti (id, campionamento, tabella di quantizzazione): luma 2x1, croma 1x1, come nei file Canon
    assert sof[10:19] == bytes([1, 0x21, 0, 2, 0x11, 1, 3, 0x11, 1])

    def decodifica(j):
        return subprocess.run(["ffmpeg", "-v", "error", "-f", "mjpeg", "-i", "-", "-f", "rawvideo", "-"],
                              input=j, capture_output=True, check=True).stdout

    # senza DHT si decodifica uguale: Pillow usa le tabelle Huffman standard
    assert decodifica(out) == decodifica(jpeg)


@needs_ffmpeg
def test_patch_exif_aggiorna_numero_fotogrammi_e_date(tmp_path):
    modello = thm(_jpeg(tmp_path), fotogrammi=153, numero_file=1011259)
    quando = time.localtime(time.mktime((2026, 10, 16, 8, 22, 0, 0, 0, -1)))
    nuovo = vc.thm_da_modello(modello, _jpeg(tmp_path), quando, 1011266, 45)
    assert leggi_makernote(nuovo) == {"numero_file": 1011266, "fotogrammi": (45, 45, 45),
                                      "date": ["2026:10:16 08:22:00"] * 2, "fps": (15, 15, 15),
                                      "modalita": (2, 0), "dimensioni": (160, 120)}


@needs_ffmpeg
def test_patch_exif_da_modello_compact_a_standard(tmp_path):
    modello = thm(_jpeg(tmp_path))
    nuovo = vc.thm_da_modello(modello, _jpeg(tmp_path), time.localtime(), 1011266, 90, 30, (640, 480), 0)
    mn = leggi_makernote(nuovo)
    assert (mn["fps"], mn["modalita"], mn["dimensioni"]) == ((30, 30, 30), (0, 0), (640, 480))


@needs_ffmpeg
def test_patch_exif_azzera_indicatore_lp(tmp_path):
    modello = thm(_jpeg(tmp_path), fps=30, dimensioni=(640, 480), codice_modalita=0, lp=1)
    nuovo = vc.thm_da_modello(modello, _jpeg(tmp_path), time.localtime(), 1011266, 45, 15, (160, 120), 2)
    mn = leggi_makernote(nuovo)
    assert (mn["fps"], mn["modalita"], mn["dimensioni"]) == ((15, 15, 15), (2, 0), (160, 120))


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
    # modello da un clip 640x480 LP: il THM prodotto deve comunque descrivere un Compact
    modello.write_bytes(thm(_jpeg(tmp_path), fps=30, dimensioni=(640, 480), codice_modalita=0, lp=1))
    sd = tmp_path / "DCIM" / "101CANON"

    res = CliRunner().invoke(main, ["video-per-canon", str(video), "--numero", "1266",
                                    "--thm-modello", str(modello), "--cartella", str(sd)])
    assert res.exit_code == 0, res.output

    avi = (sd / "MVI_1266.AVI").read_bytes()
    frames, audio, w, h, fps, _ = ca.parse_canon(avi)
    assert (w, h, fps, len(frames)) == (160, 120, 15, 30)
    assert max(len(f) for f in frames) <= vc.PROFILI["compatto"][4]
    mn = leggi_makernote((sd / "MVI_1266.THM").read_bytes())
    assert mn["numero_file"] == 1011266 and mn["fotogrammi"] == (30, 30, 30)
    assert (mn["fps"], mn["modalita"], mn["dimensioni"]) == ((15, 15, 15), (2, 0), (160, 120))

    res = CliRunner().invoke(main, ["video-per-canon", "--verifica", str(sd / "MVI_1266.AVI")])
    assert res.exit_code == 0 and "identico byte per byte" in res.output


@needs_ffmpeg
def test_miniatura_a_un_terzo_del_video(tmp_path):
    from PIL import Image, ImageStat

    # 3 s di bianco che parte da nero con una dissolvenza di 0,4 s: il primo fotogramma è nero.
    # Il video non ha audio: l'AVI riceve silenzio
    video = tmp_path / "dissolvenza.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=320x240:r=25:d=3,fade=in:0:10",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    modello = tmp_path / "MVI_1259.THM"
    modello.write_bytes(thm(_jpeg(tmp_path)))
    res = CliRunner().invoke(main, ["video-per-canon", str(video), "--numero", "1267",
                                    "--thm-modello", str(modello), "--cartella", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert set(ca.parse_canon((tmp_path / "MVI_1267.AVI").read_bytes())[1]) == {0x80}
    miniatura = Image.open(io.BytesIO((tmp_path / "MVI_1267.THM").read_bytes())).convert("L")
    assert ImageStat.Stat(miniatura).mean[0] > 200


def test_cli_senza_argomenti():
    res = CliRunner().invoke(main, ["video-per-canon"])
    assert res.exit_code == 2
    assert "INGRESSO, --numero, --thm-modello" in res.output


def test_cli_numero_fuori_intervallo(tmp_path):
    f = tmp_path / "v.mp4"
    f.touch()
    res = CliRunner().invoke(main, ["video-per-canon", str(f), "--numero", "0", "--thm-modello", str(f)])
    assert res.exit_code == 2 and "--numero" in res.output


def test_avvisi_limiti():
    assert vc.avvisi_limiti("compatto", 180, 10_000) == []
    (avviso,) = vc.avvisi_limiti("compatto", 200, 10_000)
    assert "circa 3 min" in avviso and "--durata 180" in avviso
    assert vc.avvisi_limiti("640x480", 600, 10_000) == []
    assert any("4 GB" in a for a in vc.avvisi_limiti("640x480", 600, 5 * 1024 ** 3))
