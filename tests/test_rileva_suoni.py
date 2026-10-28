import shutil
import subprocess
import zipfile

import pytest
from click.testing import CliRunner

from casoweb_tools import rileva_suoni as rs
from casoweb_tools.cli import main

np = pytest.importorskip("numpy")


def test_unisci_eventi():
    # finestre da 0,4875 s; 3 finestre alte, pausa breve, 1 alta, pausa lunga, 1 alta
    p = [0.0, 0.9, 0.8, 0.9, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.7]
    ev = rs.unisci_eventi(p, 0.4, pausa_max=1.0)
    assert len(ev) == 2
    assert ev[0][0] == pytest.approx(0.4875) and ev[0][2] == 0.9
    assert rs.unisci_eventi(p, 0.4, pausa_max=0.0, durata_min=1.2)[0][2] == 0.9
    assert rs.unisci_eventi([0.1], 0.4) == []


def test_srt_e_tsv(tmp_path):
    ev = [("Applause", 1.0, 3.5, 0.83)]
    rs.scrivi_srt(tmp_path / "a.srt", ev)
    rs.scrivi_tsv(tmp_path / "a.tsv", ev)
    assert "00:00:01,000 --> 00:00:03,500\nApplause (83%)" in (tmp_path / "a.srt").read_text(encoding="utf-8")
    assert "1.000\t3.500\tApplause" in (tmp_path / "a.tsv").read_text(encoding="utf-8")


def test_etichette_da_modello(tmp_path):
    m = tmp_path / "yamnet.tflite"
    with zipfile.ZipFile(m, "w") as z:
        z.writestr("yamnet_label_list.txt", "Speech\nMusic\nApplause\n")
    assert rs.etichette_da_modello(m) == ["Speech", "Music", "Applause"]


class Finto:
    """Interprete TFLite finto: 'Applause' (indice 2) alto dal secondo 2 al 4."""

    def __init__(self):
        self.n = 0

    def allocate_tensors(self): ...
    def get_input_details(self): return [{"index": 0, "shape": [rs.FINESTRA]}]
    def get_output_details(self): return [{"index": 1}]
    def set_tensor(self, i, v): assert v.shape == (rs.FINESTRA,)

    def invoke(self): self.n += 1

    def get_tensor(self, i):
        t = (self.n - 1) * rs.PASSO / rs.SR
        s = np.zeros((1, 3), dtype=np.float32)
        if 2 <= t <= 4:
            s[0, 2] = 0.9
        return s


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="serve ffmpeg")
def test_run_con_interprete_finto(tmp_path, monkeypatch):
    m = tmp_path / "yamnet.tflite"
    with zipfile.ZipFile(m, "w") as z:
        z.writestr("labels.txt", "Speech\nMusic\nApplause\n")
    wav = tmp_path / "s.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=7", str(wav)], check=True)
    monkeypatch.setattr(rs, "carica_interprete", lambda _m: Finto())
    res = CliRunner().invoke(main, ["rileva-suoni", str(wav), "-c", "applause", "--modello", str(m)])
    assert res.exit_code == 0, res.output
    tsv = (tmp_path / "s_suoni.tsv").read_text(encoding="utf-8").splitlines()
    assert len(tsv) == 2 and tsv[1].split("\t")[2] == "Applause"
    inizio, fine = float(tsv[1].split("\t")[0]), float(tsv[1].split("\t")[1])
    assert 1.5 < inizio < 2.6 and 4.0 < fine < 5.2
    assert "Applause" in (tmp_path / "s_suoni.srt").read_text(encoding="utf-8")


def test_classe_sconosciuta(tmp_path, monkeypatch):
    m = tmp_path / "yamnet.tflite"
    with zipfile.ZipFile(m, "w") as z:
        z.writestr("labels.txt", "Speech\n")
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    res = CliRunner().invoke(main, ["rileva-suoni", str(f), "-c", "Pippo", "--modello", str(m)])
    assert res.exit_code != 0 and "Classe sconosciuta" in res.output
