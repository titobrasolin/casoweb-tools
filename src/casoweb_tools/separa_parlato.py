"""
Dall'intervista radio ai sottotitoli, senza la musica.

Dato un file audio/video di una trasmissione radiofonica:
  1. riconosce dove si parla e dove c'è musica (inaSpeechSegmenter);
  2. produce due audio: <nome>_parlato (musica tagliata) e
     <nome>_silenziato (durata originale, musica a volume zero);
  3. trascrive il parlato con faster-whisper e genera due file SRT,
     ciascuno sincronizzato con il proprio audio.

Revisione "umano nel mezzo":
  - <nome>_segmenti.tsv  -> si può correggere a mano (parlato <-> musica);
  - <nome>_trascrizione.json -> trascrizione grezza, con i tempi della
    registrazione ORIGINALE. Dopo una correzione ai segmenti si rilancia
    lo stesso comando: i sottotitoli si rigenerano in pochi secondi e
    Whisper lavora solo sulle parti diventate "parlato".

Interfaccia a riga di comando: `casoweb separa-parlato` (vedi
casoweb_tools.cmds.separa_parlato).
"""

from __future__ import annotations

import bisect
import json
import logging
import os
import shutil
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from casoweb_tools import log
from casoweb_tools.ambiente import manca_programma

logger = logging.getLogger(__name__)

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

SR = 48000  # frequenza di lavoro per il montaggio
PARLATO, MUSICA = "parlato", "musica"

CODEC = {
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".opus": ["-c:a", "libopus", "-b:a", "96k"],
    ".flac": ["-c:a", "flac"],
    ".wav": [],
}


@dataclass
class Opzioni:
    outdir: Path | None = None
    formato: str = "mp3"
    bitrate: str | None = None  # None: quello predefinito del formato
    modo: str = "entrambi"  # taglia | silenzia | entrambi
    modello: str = "large-v3-turbo"
    device: str = "auto"
    compute_type: str = "auto"
    lingua: str = "it"
    nomi: str | None = None
    prompt: str | None = None
    min_parlato: float = 3.0
    min_musica: float = 2.0
    padding: float = 0.3
    dissolvenza: float = 0.05
    max_car: int = 80
    max_riga: int = 42
    max_durata: float = 6.0
    risegmenta: bool = False
    blocco_segmentazione: float = 300.0  # secondi per chiamata di inaSpeechSegmenter (0 = tutto insieme)
    ritrascrivi: bool = False
    solo_segmenti: bool = False
    no_audio: bool = False
    no_trascrizione: bool = False


# --------------------------------------------------------------------------
# Utilità
# --------------------------------------------------------------------------
def fmt_mmss(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:06.3f}"


def fmt_srt(t: float) -> str:
    t = max(t, 0.0)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def merge(segs: list[list]) -> list[list]:
    """Unisce segmenti contigui con la stessa etichetta."""
    out: list[list] = []
    for lab, s, e in segs:
        if out and out[-1][0] == lab:
            out[-1][2] = e
        else:
            out.append([lab, s, e])
    return out


def subtract(intervals, covered):
    """Parti di `intervals` non coperte da `covered` (liste di (s, e))."""
    res = []
    cov = sorted(covered)
    for s, e in intervals:
        cur = s
        for cs, ce in cov:
            if ce <= cur or cs >= e:
                continue
            if cs > cur:
                res.append((cur, cs))
            cur = max(cur, ce)
            if cur >= e:
                break
        if cur < e:
            res.append((cur, e))
    return [(s, e) for s, e in res if e - s > 0.05]


def pad_intervals(intervals, padding: float, end: float):
    """Allarga gli intervalli di `padding` secondi (senza uscire da 0..end) e fonde quelli che si toccano."""
    return union([(max(0.0, s - padding), min(end, e + padding)) for s, e in intervals])


def union(intervals):
    out: list[list[float]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


# --------------------------------------------------------------------------
# 1) Segmentazione parlato / musica
# --------------------------------------------------------------------------
def durata(path: Path) -> float | None:
    """Durata esatta in secondi, contando i campioni decodificati (pochi secondi per ora di audio).

    ffprobe da solo non basta: per i flussi senza indice, come l'AAC registrato da una radio
    in streaming, stima la durata dal bitrate e può sbagliare di parecchi secondi.
    """
    p = ffmpeg_decode(path, 8000, 1)
    campioni = 0
    while dati := p.stdout.read(1 << 20):
        campioni += len(dati) // 2  # s16le mono
    return campioni / 8000 if p.wait() == 0 and campioni else None


MIN_ULTIMO_BLOCCO = 30.0  # un avanzo più corto si aggiunge al blocco precedente


def blocchi(totale: float | None, blocco: float) -> list[tuple[float, float | None]]:
    """Intervalli (inizio, fine) in cui dividere la segmentazione; l'ultimo arriva in fondo (fine None).

    inaSpeechSegmenter tiene in memoria le caratteristiche dell'intero tratto che analizza:
    mezz'ora di audio supera già i 3 GB, mentre a blocchi di 5 minuti resta sotto 1,3 GB
    e non dipende dalla durata della registrazione.
    """
    if not blocco or blocco <= 0 or totale is None or totale <= blocco + MIN_ULTIMO_BLOCCO:
        return [(0.0, None)]
    n = int(totale // blocco)
    if totale - n * blocco >= MIN_ULTIMO_BLOCCO:
        n += 1
    return [(i * blocco, (i + 1) * blocco if i < n - 1 else None) for i in range(n)]


def segment_audio(path: Path, blocco: float = 300.0) -> list[tuple[str, float, float]]:
    import warnings

    warnings.filterwarnings("ignore", category=UserWarning, module="keras")
    logger.debug("Carico inaSpeechSegmenter (TensorFlow)...")
    from inaSpeechSegmenter import Segmenter

    logger.debug("inaSpeechSegmenter caricato")

    logger.info("Segmentazione parlato/musica (inaSpeechSegmenter)...")
    t0 = time.time()
    seg = Segmenter(vad_engine="smn", detect_gender=False)
    parti = blocchi(durata(path) if blocco and blocco > 0 else None, blocco)
    raw = []
    for i, (inizio, fine) in enumerate(parti, 1):
        if len(parti) > 1:
            logger.info(f"  blocco {i}/{len(parti)} da {fmt_mmss(inizio)}")
        # i tempi restituiti sono già assoluti (inaSpeechSegmenter somma start_sec)
        raw += seg(str(path), start_sec=inizio or None, stop_sec=fine)
    # ai confini tra blocchi lo stesso tipo di segmento risulta spezzato in due
    raw = [(str(lab), float(s), float(e)) for lab, s, e in merge([[lab, s, e] for lab, s, e in raw])]
    logger.info(f"  fatto in {time.time() - t0:.0f} s, {len(raw)} segmenti grezzi")
    return raw


def clean_segments(raw, min_speech=3.0, min_music=2.0, max_pause=2.5):
    """
    Riduce la segmentazione grezza a due sole etichette e accorpa i frammenti:
      - "speech" -> parlato (anche con musica sotto); "music"/"noise" -> musica;
      - un silenzio ("noEnergy") tra due parlati, se breve, resta parlato;
      - parlato più corto di `min_speech` in mezzo alla musica diventa musica
        (un verso cantato, un colpo di batteria);
      - musica più corta di `min_music` in mezzo al parlato diventa parlato.
    """
    labs = []
    for lab, s, e in raw:
        if lab == "speech":
            labs.append([PARLATO, s, e])
        elif lab == "noEnergy":
            labs.append(["silenzio", s, e])
        else:
            labs.append([MUSICA, s, e])

    for i, seg in enumerate(labs):
        if seg[0] != "silenzio":
            continue
        prev = next((labs[j][0] for j in range(i - 1, -1, -1) if labs[j][0] != "silenzio"), None)
        nxt = next((labs[j][0] for j in range(i + 1, len(labs)) if labs[j][0] != "silenzio"), None)
        if prev == nxt == PARLATO and seg[2] - seg[1] <= max_pause:
            seg[0] = PARLATO
        elif prev is not None:
            seg[0] = prev if (prev == nxt or nxt is None) else MUSICA
        else:
            seg[0] = nxt or MUSICA

    segs = merge(labs)
    while len(segs) > 1:
        worst = None
        for i, (lab, s, e) in enumerate(segs):
            lim = min_speech if lab == PARLATO else min_music
            if e - s < lim and (worst is None or e - s < segs[worst][2] - segs[worst][1]):
                worst = i
        if worst is None:
            break
        segs[worst][0] = MUSICA if segs[worst][0] == PARLATO else PARLATO
        segs = merge(segs)
    return [(lab, s, e) for lab, s, e in segs]


def write_tsv(path: Path, segs) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Correggi a mano la colonna 'etichetta' (parlato / musica) e rilancia lo script.\n")
        f.write("inizio_s\tfine_s\tetichetta\tinizio_mm:ss\tdurata_s\n")
        for lab, s, e in segs:
            f.write(f"{s:.3f}\t{e:.3f}\t{lab}\t{fmt_mmss(s)}\t{e - s:.1f}\n")


def read_tsv(path: Path):
    segs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#") or line.startswith("inizio_s"):
            continue
        cols = line.split("\t")
        lab = cols[2].strip().lower()
        if lab not in (PARLATO, MUSICA):
            raise SystemExit(f"Etichetta non valida in {path.name}: {lab!r} (usa 'parlato' o 'musica')")
        segs.append([lab, float(cols[0].replace(",", ".")), float(cols[1].replace(",", "."))])
    return [(lab, s, e) for lab, s, e in merge(segs)]


# --------------------------------------------------------------------------
# 2) Audio
# --------------------------------------------------------------------------
def ffmpeg_decode(path: Path, sr: int, channels: int) -> subprocess.Popen:
    """ffmpeg che decodifica la prima traccia audio in PCM s16le su stdout."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-vn",
           "-ac", str(channels), "-ar", str(sr), "-f", "s16le", "-"]
    logger.debug(f"Avvio: {' '.join(cmd)}")
    return subprocess.Popen(cmd, stdout=subprocess.PIPE)


def ffmpeg_encode(out: Path, bitrate: str | None) -> subprocess.Popen:
    extra = list(CODEC.get(out.suffix.lower(), []))
    if bitrate and "-b:a" in extra:
        extra[extra.index("-b:a") + 1] = bitrate
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "2",
           "-i", "-", *extra, str(out)]
    logger.debug(f"Avvio: {' '.join(cmd)}")
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def gain(t: np.ndarray, intervals, fade: float) -> np.ndarray:
    """1 dentro gli intervalli, 0 fuori, con rampe lineari interne ai bordi
    (così anche nel file tagliato le giunture sono morbide)."""
    g = np.zeros_like(t)
    for s, e in intervals:
        if e < t[0] or s > t[-1]:
            continue
        k = min(fade, (e - s) / 2)
        ramp = np.minimum(t - s, e - t) / k if k > 0 else np.ones_like(t)
        g = np.maximum(g, np.clip(ramp, 0.0, 1.0) * ((t >= s) & (t < e)))
    return g


def render_audio(path: Path, intervals, out_cut: Path | None, out_mute: Path | None,
                 fade: float, bitrate: str | None) -> None:
    """Taglio e silenziamento in un solo passaggio, a blocchi: la memoria usata
    non dipende dalla durata della registrazione."""
    dec = ffmpeg_decode(path, SR, 2)
    encs = {k: ffmpeg_encode(o, bitrate) for k, o in (("cut", out_cut), ("mute", out_mute)) if o}
    block = SR * 10
    n0 = 0
    while raw := dec.stdout.read(block * 2 * 2):
        x = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
        t = (n0 + np.arange(len(x))) / SR
        y = (x.astype(np.float32) * gain(t, intervals, fade)[:, None]).astype(np.int16)
        if "mute" in encs:
            encs["mute"].stdin.write(y.tobytes())
        if "cut" in encs:
            inside = np.zeros(len(x), dtype=bool)
            for s, e in intervals:
                inside |= (t >= s) & (t < e)
            encs["cut"].stdin.write(y[inside].tobytes())
        n0 += len(x)
        log.progresso(f"  montaggio audio {fmt_mmss(n0 / SR)}")
    log.fine_progresso()
    if dec.wait() != 0:
        raise SystemExit(f"ffmpeg non riesce a leggere {path}")
    for k, p in encs.items():
        p.stdin.close()
        if p.wait() != 0:
            raise SystemExit(f"ffmpeg ha fallito scrivendo {out_cut if k == 'cut' else out_mute}")


def load_mono16k(path: Path) -> np.ndarray:
    """Audio mono a 16 kHz per Whisper, int16 (~115 MB per ora di registrazione)."""
    p = ffmpeg_decode(path, 16000, 1)
    data = p.stdout.read()
    if p.wait() != 0:
        raise SystemExit(f"ffmpeg non riesce a leggere {path}")
    return np.frombuffer(data, dtype=np.int16)


# --------------------------------------------------------------------------
# Mappa dei tempi: registrazione originale <-> audio "tagliato"
# --------------------------------------------------------------------------
class TimeMap:
    def __init__(self, intervals):
        self.iv = list(intervals)
        self.starts = [s for s, _ in self.iv]
        self.cum = []
        c = 0.0
        for s, e in self.iv:
            self.cum.append(c)
            c += e - s

    def locate_orig(self, t: float) -> int | None:
        i = bisect.bisect_right(self.starts, t) - 1
        if i >= 0 and t < self.iv[i][1]:
            return i
        return None

    def orig_to_cut(self, i: int, t: float) -> float:
        return self.cum[i] + (t - self.iv[i][0])

    def cut_to_orig(self, t: float) -> tuple[int, float]:
        i = max(bisect.bisect_right(self.cum, t) - 1, 0)
        s, e = self.iv[i]
        return i, min(s + (t - self.cum[i]), e)


# --------------------------------------------------------------------------
# 3) Trascrizione
# --------------------------------------------------------------------------
def preload_cuda_libs() -> None:
    """Rende visibili a CTranslate2 le librerie cuBLAS/cuDNN arrivate via pip (uv --with)."""
    import ctypes
    import glob
    import importlib.util

    for pkg in ("nvidia.cuda_runtime", "nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(pkg)
        except (ModuleNotFoundError, ValueError):
            continue
        if not spec or not spec.submodule_search_locations:
            continue
        libdir = Path(list(spec.submodule_search_locations)[0]) / "lib"
        for _ in range(2):  # due passaggi: alcune lib dipendono da altre
            for so in sorted(glob.glob(str(libdir / "*.so*"))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def load_model(name: str, device: str, compute_type: str = "auto"):
    logger.debug("Carico faster-whisper (CTranslate2)...")
    import ctranslate2
    from faster_whisper import WhisperModel

    logger.debug(f"CTranslate2 {ctranslate2.__version__} caricato")

    use_cuda = False
    if device in ("auto", "cuda"):
        preload_cuda_libs()
        try:
            use_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception as exc:
            logger.debug(f"Ricerca delle GPU CUDA fallita: {exc}")
            use_cuda = False
        if device == "cuda" and not use_cuda:
            raise SystemExit("Nessuna GPU CUDA disponibile per CTranslate2.")
    if use_cuda:
        ctype = compute_type
        if ctype == "auto":
            supported = ctranslate2.get_supported_compute_types("cuda")
            ctype = next((c for c in ("float16", "int8_float16", "int8_float32", "int8") if c in supported),
                         "float32")
        logger.info(f"Whisper '{name}' su GPU (compute_type={ctype})")
        try:
            model = WhisperModel(name, device="cuda", compute_type=ctype)
            # prova a vuoto: gli errori di cuBLAS/cuDNN emergono solo alla prima trascrizione
            list(model.transcribe(np.zeros(16000, dtype=np.float32))[0])
            return model, "cuda"
        except Exception as exc:
            if device == "cuda":
                raise
            logger.warning(f"GPU non utilizzabile ({exc}); ripiego sul processore.")
    ctype = "int8" if compute_type == "auto" else compute_type
    logger.info(f"Whisper '{name}' su CPU ({ctype})")
    return WhisperModel(name, device="cpu", compute_type=ctype, cpu_threads=os.cpu_count() or 2), "cpu"


def run_whisper(model, audio: np.ndarray, total: float, args: Opzioni):
    prompt = args.prompt or (f"Intervista radiofonica. Nomi: {args.nomi}." if args.nomi else None)
    segments, _ = model.transcribe(
        audio,
        language=args.lingua,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,  # evita i cicli di frasi ripetute
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
        initial_prompt=prompt,
    )
    words = []
    t0 = time.time()
    for seg in segments:
        for w in seg.words or []:
            words.append({"s": float(w.start), "e": float(w.end), "w": w.word})
        done = min(seg.end / total, 1.0) if total else 0
        log.progresso(f"  trascrizione {done * 100:5.1f}%  ({time.time() - t0:.0f} s)")
    log.fine_progresso()
    return words


def transcribe_intervals(todo, mono: np.ndarray, args: Opzioni, state) -> list[dict]:
    """Trascrive gli intervalli `todo` (tempi originali) e restituisce parole con tempi originali."""
    parts = [mono[int(s * 16000):int(e * 16000)] for s, e in todo]
    audio = np.concatenate(parts).astype(np.float32) / 32768.0 if parts else np.zeros(0, np.float32)
    total = len(audio) / 16000
    tmap = TimeMap(todo)

    try:
        if state["model"] is None:
            state["model"], state["device"] = load_model(args.modello, args.device, args.compute_type)
        rel = run_whisper(state["model"], audio, total, args)
    except Exception as exc:
        cuda_err = re.search(r"cuda|cublas|cudnn|libcu|CUDA", str(exc), re.I)
        if state.get("device") != "cuda" or args.device == "cuda" or not cuda_err:
            raise
        logger.warning(f"GPU non utilizzabile ({exc}); ripiego sul processore.")
        state["model"], state["device"] = load_model(args.modello, "cpu", args.compute_type)
        rel = run_whisper(state["model"], audio, total, args)

    out = []
    for w in rel:
        i, s = tmap.cut_to_orig(w["s"])
        _, e = tmap.cut_to_orig(w["e"])
        out.append({"s": round(s, 3), "e": round(min(max(e, s), tmap.iv[i][1]), 3), "w": w["w"]})
    return out


# --------------------------------------------------------------------------
# 4) Sottotitoli
# --------------------------------------------------------------------------
def build_cues(words, speech, max_chars=80, max_dur=6.0, gap=0.8, width=42):
    """Raggruppa le parole in sottotitoli; ogni sottotitolo sta dentro un solo tratto di parlato."""
    tmap = TimeMap(speech)
    placed = []
    for w in words:
        i = tmap.locate_orig((w["s"] + w["e"]) / 2)
        if i is not None:
            placed.append((i, w))
    placed.sort(key=lambda x: x[1]["s"])

    cues, cur = [], []

    def flush():
        if cur:
            cues.append(list(cur))
            cur.clear()

    for i, w in placed:
        if cur:
            prev_i, prev = cur[-1]
            text = "".join(x["w"] for _, x in cur).strip()
            dur = prev["e"] - cur[0][1]["s"]
            if (i != prev_i or w["s"] - prev["e"] > gap or dur > max_dur
                    or len(text) + len(w["w"]) > max_chars
                    or (re.search(r"[.?!…]$", text) and dur >= 1.5)
                    or (text.endswith(",") and len(text) >= 55)):
                flush()
        cur.append((i, w))
    flush()

    result = []
    for k, c in enumerate(cues):
        i = c[0][0]
        start = c[0][1]["s"]
        end = c[-1][1]["e"] + 0.15
        nxt = cues[k + 1][0][1]["s"] if k + 1 < len(cues) else None
        if end - start < 0.8:
            end = start + 0.8
        if nxt is not None:
            end = min(end, nxt - 0.02)
        end = max(min(end, tmap.iv[i][1]), start + 0.2)
        text = re.sub(r"\s+", " ", "".join(x["w"] for _, x in c)).strip()
        if text:
            result.append({"i": i, "s": start, "e": end, "text": wrap(text, width)})
    return result


def wrap(text: str, width: int = 42) -> str:
    if len(text) <= width:
        return text
    mid = len(text) / 2
    spaces = [m.start() for m in re.finditer(" ", text)]
    if not spaces:
        return text
    cut = min(spaces, key=lambda p: abs(p - mid))
    return text[:cut] + "\n" + text[cut + 1:]


def write_srt(path: Path, cues, tmap: TimeMap | None) -> None:
    """tmap=None -> tempi della registrazione originale; altrimenti tempi dell'audio tagliato."""
    with open(path, "w", encoding="utf-8") as f:
        for n, c in enumerate(cues, 1):
            s, e = c["s"], c["e"]
            if tmap is not None:
                s, e = tmap.orig_to_cut(c["i"], s), tmap.orig_to_cut(c["i"], e)
            f.write(f"{n}\n{fmt_srt(s)} --> {fmt_srt(e)}\n{c['text']}\n\n")


# --------------------------------------------------------------------------
# Programma principale
# --------------------------------------------------------------------------
def run(input: Path, args: Opzioni) -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit(manca_programma("ffmpeg"))
    if not input.is_file():
        raise SystemExit(f"File non trovato: {input}")
    stem = input.stem
    args.outdir = args.outdir or input.parent / f"{stem}_output"
    args.outdir.mkdir(parents=True, exist_ok=True)
    ext = "." + args.formato

    tsv = args.outdir / f"{stem}_segmenti.tsv"
    cache = args.outdir / f"{stem}_trascrizione.json"
    out_cut = args.outdir / f"{stem}_parlato{ext}"
    out_mute = args.outdir / f"{stem}_silenziato{ext}"

    # 1) segmentazione (o TSV già presente / corretto a mano)
    if tsv.exists() and not args.risegmenta:
        logger.info(f"Uso la segmentazione esistente: {tsv.name}")
        segs = read_tsv(tsv)
    else:
        segs = clean_segments(segment_audio(input, args.blocco_segmentazione), args.min_parlato, args.min_musica)
        write_tsv(tsv, segs)
        logger.info(f"Segmentazione salvata in {tsv}")
    tot = segs[-1][2] if segs else 0
    raw_speech = [(s, e) for lab, s, e in segs if lab == PARLATO]
    tot_sp = sum(e - s for s, e in raw_speech)
    logger.info(f"Parlato: {tot_sp / 60:.1f} min   Musica: {(tot - tot_sp) / 60:.1f} min   ({len(raw_speech)} tratti di parlato)")
    if args.solo_segmenti:
        logger.info("Controlla la colonna 'etichetta' del TSV e rilancia senza --solo-segmenti.")
        return
    # margine attorno al parlato: non taglia l'attacco e la coda delle parole
    speech = pad_intervals(raw_speech, args.padding, tot)

    # 2) trascrizione incrementale: Whisper lavora solo sul parlato non ancora coperto
    data = {"modello": args.modello, "coperto": [], "parole": []}
    if cache.exists() and not args.ritrascrivi:
        old = json.loads(cache.read_text(encoding="utf-8"))
        if old.get("modello") == args.modello:
            data = old
        else:
            logger.warning("Cache di un altro modello: la ignoro.")
    todo = [] if args.no_trascrizione else subtract(speech, data["coperto"])
    # i ritagli minimi (es. il solo margine aggiunto a una cache già esistente) farebbero allucinare Whisper
    todo = [(s, e) for s, e in todo if e - s >= 1.0]

    if todo:
        logger.info(f"Da trascrivere: {sum(e - s for s, e in todo) / 60:.1f} min di parlato")
        state = {"model": None, "device": None}
        data["parole"] += transcribe_intervals(todo, load_mono16k(input), args, state)
        data["coperto"] = union(list(map(tuple, data["coperto"])) + todo)
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    elif not args.no_trascrizione:
        logger.info("Trascrizione già in cache: rigenero solo i sottotitoli.")

    # 3) audio
    if not args.no_audio:
        cut = out_cut if args.modo in ("taglia", "entrambi") else None
        mute = out_mute if args.modo in ("silenzia", "entrambi") else None
        render_audio(input, speech, cut, mute, args.dissolvenza, args.bitrate)
        for o in (cut, mute):
            if o:
                logger.info(f"  {o.name}")

    # 4) sottotitoli
    if not args.no_trascrizione:
        cues = build_cues(data["parole"], speech, args.max_car, args.max_durata, width=args.max_riga)
        write_srt(args.outdir / f"{stem}_parlato.srt", cues, TimeMap(speech))
        write_srt(args.outdir / f"{stem}_silenziato.srt", cues, None)
        logger.info(f"Sottotitoli: {len(cues)}  ->  {stem}_parlato.srt, {stem}_silenziato.srt")
    logger.info(f"Fatto. Cartella: {args.outdir}")
