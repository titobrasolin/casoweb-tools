import re
import shutil
import subprocess

import pytest
from click.testing import CliRunner

from casoweb_tools import video_per_social as vs
from casoweb_tools.cli import main

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="serve ffmpeg")


def _senza_tag(testo: str) -> str:
    return re.sub(r"\{[^}]*\}", "", testo)


def _eventi(righe: list[str]) -> list[tuple[str, str, str]]:
    """(inizio, fine, testo) delle righe Dialogue."""
    out = []
    for r in righe:
        campi = r.split(",", 9)
        out.append((campi[1], campi[2], campi[9]))
    return out


# --------------------------------------------------------------------------
# Utilità pure
# --------------------------------------------------------------------------
@pytest.mark.parametrize("s, atteso", [
    ("33", 33.0), ("33.5", 33.5), ("33,5", 33.5), ("0:33", 33.0), ("1:02:03", 3723.0),
    ("00:00:33,500", 33.5),
])
def test_parse_tempo(s, atteso):
    assert vs.parse_tempo(s) == pytest.approx(atteso)


def test_parse_tempo_non_valido():
    with pytest.raises(ValueError, match="tempo non valido"):
        vs.parse_tempo("mezzo minuto")


def test_parse_ritaglio():
    assert vs.parse_ritaglio("378:672:571:20") == (378, 672, 571, 20)
    with pytest.raises(ValueError):
        vs.parse_ritaglio("378:672")


def test_ritaglio_centrato():
    assert vs.ritaglio_centrato(1920, 1080, 9 / 16) == (606, 1080, 657, 0)
    assert vs.ritaglio_centrato(1080, 1920, 16 / 9) == (1080, 606, 0, 657)


def test_risoluzione_uscita_soglia():
    assert vs.risoluzione_uscita("9:16", 672) == (720, 1280)
    assert vs.risoluzione_uscita("9:16", 1080) == (1080, 1920)
    assert vs.risoluzione_uscita("4:5", 616) == (864, 1080)


def test_leggi_srt_e_sposta(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nprima\nriga\n\n"
                   "2\n00:00:05,000 --> 00:00:09,000\nseconda\n\n"
                   "3\n00:00:20,000 --> 00:00:21,000\nfuori\n", encoding="utf-8")
    eventi = vs.leggi_srt(srt)
    assert eventi[0] == (1.0, 3.0, "prima riga")
    # clip da 2 a 8 s: il primo si accorcia, il secondo si tronca alla fine, il terzo sparisce
    assert vs.sposta(eventi, 2.0, 6.0) == [(0.0, 1.0, "prima riga"), (3.0, 6.0, "seconda")]


def test_leggi_nomi(tmp_path):
    f = tmp_path / "nomi.tsv"
    f.write_text("# commento\n0:12.5\tMario Rossi, chitarra\n3\tAnna Bianchi\n", encoding="utf-8")
    assert vs.leggi_nomi(f) == [(3.0, "Anna Bianchi"), (12.5, "Mario Rossi, chitarra")]
    f.write_text("12 Mario Rossi\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="riga 1"):
        vs.leggi_nomi(f)


def test_spezza_divide_il_tempo_per_caratteri():
    gruppi = vs.spezza(0.0, 6.0, ["aaaa", "bbbb", "cccccccc"], 2)
    assert [g[2] for g in gruppi] == [["aaaa", "bbbb"], ["cccccccc"]]
    assert gruppi[0][:2] == (0.0, 3.0) and gruppi[1][:2] == (3.0, 6.0)


def test_battuta_impaginazione_fissa():
    righe = vs.battuta(1.0, 5.0, ["Grazie a tutti", "per essere qui"], "Sottotitoli", velocita=18)
    eventi = _eventi(righe)
    completo = r"Grazie a tutti\Nper essere qui"
    # ogni evento contiene il testo intero: cambia solo quanta parte è trasparente
    assert all(_senza_tag(t) == completo for _, _, t in eventi)
    visibili = [t.split("{")[0] for _, _, t in eventi]
    assert visibili == sorted(visibili, key=len) and visibili[-1] == completo
    assert "{" not in eventi[-1][2]
    # eventi contigui, dall'inizio alla fine del sottotitolo
    assert eventi[0][0] == "0:00:01.00" and eventi[-1][1] == "0:00:05.00"
    assert all(a[1] == b[0] for a, b in zip(eventi, eventi[1:]))
    # nessun evento per i soli spazi
    assert not any(v.endswith(" ") for v in visibili)


def test_battuta_finisce_entro_la_frazione():
    eventi = _eventi(vs.battuta(0.0, 1.0, ["x" * 50], "S", velocita=18, frazione=0.6))
    ultimo_inizio = vs.parse_tempo(eventi[-1][0].replace(".", ",", 0))
    assert ultimo_inizio <= 0.61


def test_battuta_neutralizza_i_comandi_ass():
    eventi = _eventi(vs.battuta(0.0, 2.0, [r"{\b1}ciao\N"], "S", velocita=18))
    assert _senza_tag(eventi[-1][2]) == "(/b1)ciao/N"


def test_raggruppa_nomi():
    gruppi = vs.raggruppa_nomi([(10.0, "C"), (1.0, "A"), (3.0, "B")], 4.0)
    assert gruppi == [[(1.0, "A"), (3.0, "B")], [(10.0, "C")]]


def test_costruisci_ass_titolo_e_nomi():
    o = vs.Opzioni(titolo=["PROVING GROUNDS"], durata_titolo=2.0, durata_nome=3.0)
    ass = vs.costruisci_ass(720, 1280, o, [(1.0, 3.0, "ciao")],
                            [(1.0, "Mario Rossi"), (2.0, "Anna Bianchi"), (7.0, "Luca")], 9.0)
    assert "PlayResX: 720" in ass and "PlayResY: 1280" in ass
    stili = {r.split(",")[0]: r.split(",") for r in ass.splitlines() if r.startswith("Style:")}
    assert stili["Style: Sottotitoli"][-2] == "179"  # 14% di 1280, sopra i comandi delle storie
    dialoghi = [r.split(",", 9) for r in ass.splitlines() if r.startswith("Dialogue:")]
    nomi = {_senza_tag(d[9]): d for d in dialoghi if d[3] == "Nome" and "{" not in d[9]}
    # il nome compare quando viene detto, anche durante il titolo, ma sotto di esso
    mario = next(d for d in dialoghi if d[3] == "Nome")
    assert mario[1] == "0:00:01.00" and int(mario[7]) > 64
    # nomi ravvicinati: impilati, e restano insieme fino a 3 s dopo l'ultimo
    assert int(nomi["Anna Bianchi"][7]) > int(nomi["Mario Rossi"][7])
    assert nomi["Mario Rossi"][2] == nomi["Anna Bianchi"][2] == "0:00:05.00"
    # gruppo nuovo: di nuovo in cima
    assert nomi["Luca"][7] == nomi["Mario Rossi"][7]


def test_nome_a_fine_clip_avvisa(caplog):
    ass = vs.costruisci_ass(720, 1280, vs.Opzioni(), [], [(3.9, "Mario Rossi")], 4.0)
    assert "Mario Rossi" not in ass
    assert "Mario Rossi" in caplog.text and "omesso" in caplog.text


def test_build_vf_bn():
    o = vs.Opzioni()
    vf = vs.build_vf(o, (378, 672, 571, 20), 720, 1280, "x.ass", 10.0)
    assert vf.startswith("crop=378:672:571:20,scale=720:1280")
    assert "colorchannelmixer" in vf and "curves" in vf and "vignette" in vf
    # grana solo sulla luminanza e dopo i testi, così li "sporca" come la carta
    assert vf.index("ass=x.ass") < vf.index("noise=c0s=")
    assert "fade=t=out:st=9.500" in vf


def test_build_vf_colore_senza_look():
    vf = vs.build_vf(vs.Opzioni(look="colore", vignettatura=0, dissolvenza=0), (100, 100, 0, 0), 100, 100,
                     None, 5.0)
    assert vf == "crop=100:100:0:0,scale=100:100:flags=lanczos,setsar=1,format=yuv420p"


# --------------------------------------------------------------------------
# Con ffmpeg
# --------------------------------------------------------------------------
@pytest.fixture
def ripresa(tmp_path):
    """Clip sintetica 640x360 a colori di 3 s, con audio."""
    f = tmp_path / "serata.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(f)], check=True)
    return f


def _probe(f, voce):
    return subprocess.run(["ffprobe", "-v", "error", "-show_entries", voce, "-of", "csv=p=0", str(f)],
                          check=True, capture_output=True, text=True).stdout.strip()


def _media_croma(f, t):
    """Media di U e V a un certo istante: 128 = nessun colore."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(f), "-frames:v", "1",
                          "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"],
                         check=True, capture_output=True, text=True).stdout
    u = float(re.search(r"UAVG=([\d.]+)", out).group(1))
    v = float(re.search(r"VAVG=([\d.]+)", out).group(1))
    return u, v


@needs_ffmpeg
def test_video_per_social(ripresa, tmp_path):
    srt = tmp_path / "serata.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,800\nGrazie a tutti per essere qui\n\n", encoding="utf-8")
    nomi = tmp_path / "nomi.tsv"
    nomi.write_text("1.5\tMario Rossi\n", encoding="utf-8")
    res = CliRunner().invoke(main, [
        "video-per-social", str(ripresa), "--inizio", "0.5", "--ritaglio", "180:320:230:20",
        "--srt", str(srt), "--nomi", str(nomi), "--titolo", "PROVA", "--durata-titolo", "0.8",
    ])
    assert res.exit_code == 0, res.output
    out = tmp_path / "serata_social_9x16.mp4"
    # altezza utile 320 < 700: esce a 720x1280
    assert _probe(out, "stream=width,height").splitlines()[0] == "720,1280"
    assert float(_probe(out, "format=duration")) == pytest.approx(2.5, abs=0.1)
    assert "aac" in _probe(out, "stream=codec_name")
    u, v = _media_croma(out, 1.2)
    assert abs(u - 128) < 2 and abs(v - 128) < 2  # bianco e nero, grana compresa

    ass = tmp_path / "serata_social_9x16.ass"
    testo = ass.read_text(encoding="utf-8")
    assert "Grazie a tutti" in testo and "Mario Rossi" in testo and "PROVA" in testo
    # tempi spostati di --inizio: il sottotitolo a 1,0 s della ripresa parte a 0,5 s della clip
    assert "Dialogue: 0,0:00:00.50," in testo


@needs_ffmpeg
def test_video_per_social_ass_corretto_a_mano(ripresa, tmp_path):
    ass = tmp_path / "mio.ass"
    o = vs.Opzioni(formato="16:9")
    ass.write_text(vs.costruisci_ass(640, 360, o, [(0.2, 2.0, "testo corretto")], [], 3.0), encoding="utf-8")
    res = CliRunner().invoke(main, ["video-per-social", str(ripresa), "--formato", "16:9", "--look", "colore",
                                    "--ass", str(ass), "--risoluzione", "640x360", "-o", str(tmp_path / "o.mp4")])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "o.ass").read_text(encoding="utf-8") == ass.read_text(encoding="utf-8")
    assert _probe(tmp_path / "o.mp4", "stream=width,height").splitlines()[0] == "640,360"


@needs_ffmpeg
def test_ritaglio_fuori_inquadratura(ripresa):
    res = CliRunner().invoke(main, ["video-per-social", str(ripresa), "--ritaglio", "180:320:500:100"])
    assert res.exit_code != 0
    assert "esce dall'inquadratura" in str(res.exception)


def test_opzioni_non_valide(tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"")
    res = CliRunner().invoke(main, ["video-per-social", str(f), "--risoluzione", "721x1280"])
    assert res.exit_code == 2 and "pari" in res.output
    res = CliRunner().invoke(main, ["video-per-social", str(f), "--inizio", "domani"])
    assert res.exit_code == 2 and "tempo non valido" in res.output
