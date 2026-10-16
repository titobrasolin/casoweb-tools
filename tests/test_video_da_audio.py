import json
import shutil
import subprocess

import pytest
from click.testing import CliRunner

from casoweb_tools import video_da_audio as av
from casoweb_tools.cli import main


def test_shift_srt(tmp_path):
    src, dst = tmp_path / "a.srt", tmp_path / "b.srt"
    src.write_text("1\n00:00:01,000 --> 00:00:02,500\nciao\n\n", encoding="utf-8")
    assert av.shift_srt(src, dst, 0.5) == 1
    assert "00:00:01,500 --> 00:00:03,000" in dst.read_text(encoding="utf-8")


def test_sanitize_name():
    assert av.sanitize_name(av.Path("Intervista, parte 1 (radio).mp3")) == "Intervista__parte_1__radio"


def test_elenca_profili_senza_audio():
    res = CliRunner().invoke(main, ["video-da-audio", "--elenca-profili"])
    assert res.exit_code == 0
    assert "whatsapp_480p" in res.output


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="serve ffmpeg")
def test_video_whatsapp(tmp_path):
    audio = tmp_path / "intervista_parlato.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=3", str(audio)], check=True)
    audio.with_suffix(".srt").write_text("1\n00:00:00,500 --> 00:00:02,000\nprova\n\n", encoding="utf-8")

    res = CliRunner().invoke(main, ["video-da-audio", str(audio), "--profili", "whatsapp_480p"])
    assert res.exit_code == 0, res.output
    out = tmp_path / "intervista_parlato_video"
    assert (out / "intervista_parlato_whatsapp_480p.mp4").stat().st_size > 0
    params = json.loads((out / "intervista_parlato_params.json").read_text(encoding="utf-8"))
    assert params["profili"] == "whatsapp_480p"
    assert "casoweb video-da-audio" in res.output
