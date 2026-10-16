"""
Dall'audio al video sottotitolato.

Trasforma un file audio (es. il *_parlato.mp3 prodotto da separa-parlato)
in uno o più video sottotitolati, con un'immagine fissa come sfondo (o sfondo
nero se non ne viene data una), in versioni adatte a piattaforme diverse.

Per ogni profilo scelto produce <nome>_<profilo>.mp4, con:
  - sottotitoli incorporati nelle immagini (default) oppure, con --soft,
    come traccia attivabile dal player;
  - "respiro" di nero e silenzio a inizio e fine (i sottotitoli vengono
    spostati di conseguenza) e dissolvenze video/audio.

Servono solo ffmpeg e ffprobe (sudo apt install ffmpeg): nessuna libreria Python.

Interfaccia a riga di comando: `casoweb video-da-audio` (vedi
casoweb_tools.cmds.video_da_audio).

Se --srt manca, cerca accanto all'audio un file con lo stesso nome e
estensione .srt. I parametri usati vengono salvati in <nome>_params.json e
si possono riusare con --load-params.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# nome -> parametri del profilo
#   w, h          dimensioni del video
#   crf           qualità x264 (più alto = più leggero)
#   abr, ac, ar   bitrate, canali e frequenza dell'audio (ar=None: invariata)
#   fps           fotogrammi al secondo (l'immagine è fissa: pochi bastano)
#   gop           secondi tra un fotogramma chiave e il successivo: in un video fisso
#                 sono la voce più costosa, quindi vanno tenuti lontani
#   margine       distanza dei sottotitoli dal bordo inferiore, in % dell'altezza
PROFILES: dict[str, dict] = {
    "youtube_1080p": dict(w=1920, h=1080, crf=26, abr="192k", ac=2, ar=None, fps=10, gop=10, margine=5,
                          desc="16:9 Full HD, per YouTube e proiezioni"),
    "web_720p": dict(w=1280, h=720, crf=28, abr="96k", ac=2, ar=None, fps=10, gop=10, margine=5,
                     desc="16:9 HD più leggero, per il sito"),
    "whatsapp_480p": dict(w=960, h=540, crf=34, abr="32k", ac=1, ar=32000, fps=5, gop=30, margine=6,
                          desc="16:9 molto leggero (~14 MB per 30 min), per messaggistica"),
    "quadrato_1080": dict(w=1080, h=1080, crf=28, abr="96k", ac=2, ar=None, fps=10, gop=10, margine=7,
                          desc="1:1, per i feed dei social"),
    "verticale_1080x1920": dict(w=1080, h=1920, crf=28, abr="96k", ac=2, ar=None, fps=10, gop=10, margine=14,
                                desc="9:16, per storie/reel (margine alto per i pulsanti dell'app)"),
}
DEFAULT_PROFILES = "youtube_1080p,web_720p,whatsapp_480p"

BUILTIN_DEFAULTS = {
    "srt": None, "immagine": None, "profili": DEFAULT_PROFILES,
    "adattamento": "contain", "colore_barre": "black",
    "font": "DejaVu Sans Mono", "dimensione_font": 2.8, "fps": None,
    "respiro": 0.5, "dissolvenza": 0.5, "dissolvenza_audio": 0.2,
    "soft": False, "titolo": None, "output_dir": None,
}


# --------------------------------------------------------------------------
# Utilità pure
# --------------------------------------------------------------------------
_TS = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def parse_ts(s: str) -> float:
    h, m, sec, ms = map(int, _TS.fullmatch(s.strip()).groups())
    return h * 3600 + m * 60 + sec + ms / 1000


def fmt_ts(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def shift_srt(src: Path, dst: Path, offset: float) -> int:
    """Copia un SRT spostando tutti i tempi di `offset` secondi. Restituisce il numero di sottotitoli."""
    text = src.read_text(encoding="utf-8-sig")
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        n += 1
        return f"{fmt_ts(parse_ts(m.group(1)) + offset)} --> {fmt_ts(parse_ts(m.group(2)) + offset)}"

    out = re.sub(r"(\d+:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d+:\d{2}:\d{2}[,.]\d{3})", repl, text)
    dst.write_text(out, encoding="utf-8")
    return n


def sanitize_name(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", path.stem).strip("_") or "video"


def media_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def build_vf(p: dict, *, has_image: bool, adattamento: str, colore: str, soft: bool,
             font: str, dim_font: float, total: float, fade: float) -> str:
    w, h = p["w"], p["h"]
    parts = []
    if has_image:
        if adattamento == "cover":
            parts.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}")
        else:
            parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                         f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={colore}")
    parts.append("setsar=1")

    if not soft:
        # Il filtro "subtitles" converte l'SRT in ASS con una griglia interna di 384x288:
        # FontSize, Outline e margini vanno espressi in quelle unità, non in pixel.
        ky, kx = 288 / h, 384 / w
        px = max(8.0, w * dim_font / 100)  # altezza del testo in pixel reali
        style = (f"FontName={font},FontSize={px * ky:.2f},PrimaryColour=&H00FFFFFF,"
                 f"OutlineColour=&H00000000,BorderStyle=1,Outline={max(1.0, px / 16) * ky:.2f},Shadow=0,"
                 f"Alignment=2,MarginV={h * p['margine'] / 100 * ky:.1f},"
                 f"MarginL={w * 0.04 * kx:.1f},MarginR={w * 0.04 * kx:.1f}")
        parts.append(f"subtitles=_sub.srt:original_size={w}x{h}:force_style='{style}'")

    if fade > 0:
        parts.append(f"fade=t=in:st=0:d={fade}:color=black")
        parts.append(f"fade=t=out:st={max(0.0, total - fade):.3f}:d={fade}:color=black")
    parts.append("format=yuv420p")
    return ",".join(parts)


def build_af(*, respiro: float, total: float, afade: float) -> str:
    parts = []
    if respiro > 0:
        parts.append(f"adelay={int(round(respiro * 1000))}:all=1")
        parts.append(f"apad=pad_dur={respiro}")
    if afade > 0:
        parts.append(f"afade=t=in:st=0:d={afade}")
        parts.append(f"afade=t=out:st={max(0.0, total - afade):.3f}:d={afade}")
    return ",".join(parts) or "anull"


def build_command(*, audio: Path, image: Path | None, profile: dict, out_file: Path, args: dict,
                  total: float) -> list[str]:
    w, h = profile["w"], profile["h"]
    fps = args["fps"] or profile["fps"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats"]
    if image:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(image)]
    else:
        cmd += ["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps}"]
    cmd += ["-i", str(audio.resolve())]
    if args["soft"]:
        cmd += ["-i", "_sub.srt"]

    vf = build_vf(profile, has_image=image is not None, adattamento=args["adattamento"],
                  colore=args["colore_barre"], soft=args["soft"], font=args["font"],
                  dim_font=args["dimensione_font"], total=total, fade=args["dissolvenza"])
    af = build_af(respiro=args["respiro"], total=total, afade=args["dissolvenza_audio"])

    cmd += ["-map", "0:v", "-map", "1:a"]
    if args["soft"]:
        cmd += ["-map", "2:s"]
    cmd += [
        "-vf", vf, "-af", af, "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", str(profile["crf"]),
        "-r", str(fps), "-g", str(max(1, int(fps * profile["gop"]))), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", profile["abr"], "-ac", str(profile["ac"]),
    ]
    if profile["ar"]:
        cmd += ["-ar", str(profile["ar"])]
    if args["soft"]:
        cmd += ["-c:s", "mov_text", "-metadata:s:s:0", "language=ita"]
    if args["titolo"]:
        cmd += ["-metadata", f"title={args['titolo']}"]
    cmd += ["-movflags", "+faststart", out_file.name]
    return cmd


# --------------------------------------------------------------------------
# Programma
# --------------------------------------------------------------------------
def elenca_profili() -> None:
    for name, p in PROFILES.items():
        print(f"{name:22s} {p['w']}x{p['h']:<5d} {p['desc']}")


def run(audio: Path, cli: dict, *, load_params: str | None = None, anteprima: bool = False) -> None:
    """`cli` ha le chiavi di BUILTIN_DEFAULTS; i valori None prendono il default."""
    for b in ("ffmpeg", "ffprobe"):
        if shutil.which(b) is None:
            sys.exit(f"Manca {b}: sudo apt install ffmpeg")
    if not audio.is_file():
        sys.exit(f"File non trovato: {audio}")

    loaded = {}
    if load_params:
        try:
            loaded = json.loads(Path(load_params).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"Attenzione: impossibile leggere {load_params} ({e}); ignorato.", file=sys.stderr)

    # precedenza: flag esplicito > file --load-params > default
    cfg = {k: (cli.get(k) if cli.get(k) is not None else loaded.get(k, d)) for k, d in BUILTIN_DEFAULTS.items()}

    audio_arg, audio = audio, audio.resolve()
    base = sanitize_name(audio)
    out_dir = Path(cfg["output_dir"]) if cfg["output_dir"] else audio.parent / f"{base}_video"
    out_dir.mkdir(parents=True, exist_ok=True)

    srt = Path(cfg["srt"]).expanduser() if cfg["srt"] else audio.with_suffix(".srt")
    if not srt.is_file():
        sys.exit(f"Sottotitoli non trovati: {srt}\nIndicali con --srt.")
    image = None
    if cfg["immagine"]:
        image = Path(cfg["immagine"]).expanduser().resolve()
        if not image.is_file():
            sys.exit(f"Immagine non trovata: {image}")

    names = [n.strip() for n in str(cfg["profili"]).split(",") if n.strip()]
    unknown = [n for n in names if n not in PROFILES]
    if unknown:
        sys.exit(f"Profili sconosciuti: {', '.join(unknown)}  (vedi --elenca-profili)")

    duration = media_duration(audio)
    pad = max(float(cfg["respiro"]), 0.0)
    total = duration + 2 * pad
    fade = min(float(cfg["dissolvenza"]), total / 2)
    afade = min(float(cfg["dissolvenza_audio"]), total / 2)
    cfg["dissolvenza"], cfg["dissolvenza_audio"] = fade, afade

    # parametri salvati subito, così restano disponibili anche se un render fallisce
    params = {**cfg, "audio": str(audio), "srt": str(srt.resolve()),
              "immagine": str(image) if image else None, "output_dir": str(out_dir)}
    params_path = out_dir / f"{base}_params.json"
    params_path.write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")

    # SRT di lavoro con nome semplice (evita problemi di escaping nel filtro) e tempi spostati del respiro
    work_srt = out_dir / "_sub.srt"
    n = shift_srt(srt, work_srt, pad)
    print(f"Audio: {duration / 60:.1f} min  |  sottotitoli: {n}  |  sfondo: {image.name if image else 'nero'}")
    print(f"Profili: {', '.join(names)}  |  sottotitoli {'attivabili' if cfg['soft'] else 'incorporati'}\n")

    generated = []
    try:
        for name in names:
            p = PROFILES[name]
            out_file = out_dir / f"{base}_{name}.mp4"
            print(f"[{name}] {p['w']}x{p['h']} -> {out_file.name}")
            cmd = build_command(audio=audio, image=image, profile=p, out_file=out_file, args=cfg, total=total)
            t0 = time.time()
            subprocess.run(cmd, check=True, cwd=out_dir)
            size = out_file.stat().st_size / 1_000_000
            print(f"\n   fatto in {time.time() - t0:.0f} s, {size:.1f} MB")
            if name.startswith("whatsapp") and size > 16:
                print("   Attenzione: supera i 16 MB; per l'invio come video potrebbe essere troppo grande.")
            generated.append(out_file)
    except subprocess.CalledProcessError as e:
        sys.exit(f"\nffmpeg ha fallito (codice {e.returncode}).")
    finally:
        work_srt.unlink(missing_ok=True)

    print("\nFile generati:")
    for g in generated:
        print(f"  - {g}")
    print(f"\nParametri salvati in: {params_path}\n"
          f"Per ripetere modificando qualcosa: casoweb video-da-audio {audio_arg} --load-params {params_path}")

    if anteprima and generated:
        player = shutil.which("mpv")
        if player:
            subprocess.run([player, str(generated[0])])
        else:
            print("mpv non trovato (sudo apt install mpv)")

