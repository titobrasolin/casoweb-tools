"""
Pulizia dell'audio di una registrazione in presa diretta.

Pensato per le riprese "di fortuna" in una sala: interventi al microfono
alternati a esibizioni musicali, rumore di fondo, rimbombo. Il parlato e la
musica vogliono trattamenti opposti (un denoiser per la voce rovina una
canzone), quindi con --segmenti (il TSV prodotto da `casoweb separa-parlato`)
il trattamento "forte" viene applicato solo ai tratti parlati e la musica
riceve solo un ritocco leggero, con dissolvenze incrociate al confine.

Motori per il parlato (--motore):
  ffmpeg         filtri di ffmpeg (taglio dei bassi, riduzione del rumore,
                 gate, compressore): niente da installare, risultati modesti.
  deepfilternet  rete neurale per la riduzione del rumore; si usa il programma
                 `deep-filter` (binario precompilato) o `deepFilter` (Python),
                 cercato nel PATH. Lavora a 48 kHz.

Nota onesta sul rimbombo: ffmpeg non ha un vero filtro anti-riverbero; il gate
e un buon microfono vicino alla bocca aiutano più di qualsiasi filtro.

Serve solo ffmpeg/ffprobe: nessuna libreria Python. Il motore deepfilternet
è un programma esterno e viene cercato solo se richiesto, così `casoweb --help`
funziona anche senza.

Interfaccia a riga di comando: `casoweb pulisci-audio` (vedi
casoweb_tools.cmds.pulisci_audio).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from casoweb_tools.ambiente import manca_programma

logger = logging.getLogger(__name__)

SR = 48000  # DeepFilterNet lavora solo a 48 kHz

MOTORI = ("ffmpeg", "deepfilternet")

# Catene di filtri ffmpeg per profilo: "voce" per i tratti parlati (o per tutto,
# senza --segmenti), "musica" per il resto.
PROFILI: dict[str, dict] = {
    "leggero": dict(
        voce="highpass=f=80,afftdn=nr=8:nf=-40",
        musica="highpass=f=35",
        desc="solo taglio dei bassi e un po' di riduzione del rumore",
    ),
    "sala": dict(
        voce="highpass=f=120,afftdn=nr=14:nf=-38,"
             "agate=threshold=0.02:ratio=3:attack=5:release=180,"
             "acompressor=threshold=0.08:ratio=3:attack=10:release=250:makeup=2",
        musica="highpass=f=35",
        desc="sala con pubblico e microfono: rumore, code di rimbombo tra le parole, volume della voce",
    ),
    "forte": dict(
        voce="highpass=f=150,afftdn=nr=20:nf=-35,"
             "agate=threshold=0.03:ratio=4:attack=5:release=120,"
             "acompressor=threshold=0.06:ratio=4:attack=8:release=200:makeup=3",
        musica="highpass=f=40",
        desc="registrazioni molto rumorose; può dare un suono più \"artificiale\"",
    ),
}
PROFILO_PREDEFINITO = "sala"

PARLATO = "parlato"

_CODEC = {
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".aac": ["-c:a", "aac", "-b:a", "192k"],
    ".mp4": ["-c:a", "aac", "-b:a", "192k"],
    ".mkv": ["-c:a", "aac", "-b:a", "192k"],
    ".mov": ["-c:a", "aac", "-b:a", "192k"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".flac": ["-c:a", "flac"],
    ".opus": ["-c:a", "libopus", "-b:a", "128k"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    ".webm": ["-c:a", "libopus", "-b:a", "128k"],
}


# --------------------------------------------------------------------------
# Utilità pure
# --------------------------------------------------------------------------
def leggi_parlato(tsv: Path) -> list[tuple[float, float]]:
    """Intervalli (inizio, fine) in secondi etichettati 'parlato' nel TSV di separa-parlato."""
    out: list[tuple[float, float]] = []
    for riga in tsv.read_text(encoding="utf-8").splitlines():
        if not riga.strip() or riga.startswith("#") or riga.startswith("inizio_s"):
            continue
        col = riga.split("\t")
        if len(col) < 3:
            raise SystemExit(f"Riga non valida in {tsv.name}: {riga!r}")
        if col[2].strip().lower() == PARLATO:
            out.append((float(col[0].replace(",", ".")), float(col[1].replace(",", "."))))
    return sorted(out)


def espressione_gate(intervalli: list[tuple[float, float]], dissolvenza: float) -> str:
    """Espressione ffmpeg g(t) in [0, 1]: 1 dentro gli intervalli, rampa lineare di `dissolvenza` secondi ai bordi.

    Ogni intervallo vale clip(min(t-(a-f/2), (b+f/2)-t)/f, 0, 1): la rampa è centrata sul confine.
    Gli intervalli non si sovrappongono, quindi basta sommare (con tetto a 1).
    """
    if not intervalli:
        return "0"
    f = max(dissolvenza, 0.001)
    termini = [f"clip(min(t-{a - f / 2:.3f},{b + f / 2:.3f}-t)/{f:.3f},0,1)" for a, b in intervalli]
    return "min(1," + "+".join(termini) + ")"


# --------------------------------------------------------------------------
# Motori
# --------------------------------------------------------------------------
def trova_deepfilternet() -> tuple[str, str] | None:
    """(percorso, tipo) di un programma DeepFilterNet nel PATH; tipo 'rust' o 'python'."""
    for nome, tipo in (("deep-filter", "rust"), ("deepFilter", "python"), ("deepfilter", "python")):
        p = shutil.which(nome)
        if p:
            return p, tipo
    return None


def manca_deepfilternet() -> str:
    return (
        "Manca DeepFilterNet. Due modi per averlo:\n"
        "  - binario precompilato `deep-filter` (consigliato: niente PyTorch), da\n"
        "    https://github.com/Rikorose/DeepFilterNet/releases ; rendilo eseguibile e mettilo nel PATH;\n"
        "  - pacchetto Python: prima PyTorch e torchaudio (CPU o CUDA), poi `pip install deepfilternet`.\n"
        "Senza, usa --motore ffmpeg."
    )


def comando_deepfilternet(exe: str, tipo: str, wav: Path, outdir: Path, post_filtro: bool) -> list[str]:
    if tipo == "rust":
        cmd = [exe, "-o", str(outdir)]
    else:
        cmd = [exe, "--output-dir", str(outdir)]
    if post_filtro:
        cmd.append("--pf")
    cmd.append(str(wav))
    return cmd


def esegui(cmd: list[str], **kw) -> None:
    logger.debug("Avvio: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, **kw)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"{Path(cmd[0]).name} ha fallito (codice {e.returncode}).") from e


def ha_video(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True,
    ).stdout
    return "video" in out


def uscita_predefinita(ingresso: Path, video: bool) -> Path:
    """<nome>_pulito con la stessa estensione dell'ingresso; se non la gestiamo (avi, ts, wma...), .mkv o .m4a."""
    suffisso = ingresso.suffix if ingresso.suffix.lower() in _CODEC else (".mkv" if video else ".m4a")
    return ingresso.with_name(f"{ingresso.stem}_pulito{suffisso}")


def durata(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def spazio_necessario(secondi: float) -> int:
    """Byte dei file temporanei: originale stereo 48 kHz a 16 bit + due versioni mono (circa 0,38 MB al secondo)."""
    return int(secondi * SR * 2 * (2 + 1 + 1))


def elenca_profili() -> None:
    for nome, p in PROFILI.items():
        print(f"{nome:10s} {p['desc']}")


# --------------------------------------------------------------------------
# Programma
# --------------------------------------------------------------------------
def run(ingresso: Path, *, uscita: Path | None, profilo: str, motore: str, segmenti: Path | None,
        dissolvenza: float, post_filtro: bool, volume_finale: bool, tieni_temporanei: bool) -> Path:
    for b in ("ffmpeg", "ffprobe"):
        if shutil.which(b) is None:
            raise SystemExit(manca_programma("ffmpeg"))
    if profilo not in PROFILI:
        raise SystemExit(f"Profilo sconosciuto: {profilo}  (vedi --elenca-profili)")
    p = PROFILI[profilo]

    df = None
    if motore == "deepfilternet":
        df = trova_deepfilternet()
        if df is None:
            raise SystemExit(manca_deepfilternet())

    intervalli: list[tuple[float, float]] = []
    if segmenti is not None:
        intervalli = leggi_parlato(segmenti)
        if not intervalli:
            raise SystemExit(f"Nessun tratto 'parlato' in {segmenti.name}.")

    video = ha_video(ingresso)
    uscita = uscita or uscita_predefinita(ingresso, video)
    codec = _CODEC.get(uscita.suffix.lower())
    if codec is None:
        raise SystemExit(f"Formato di uscita non gestito: {uscita.suffix} ({', '.join(_CODEC)})")
    uscita.parent.mkdir(parents=True, exist_ok=True)

    necessario = spazio_necessario(durata(ingresso))
    libero = shutil.disk_usage(uscita.parent).free
    if necessario > 0.9 * libero:
        raise SystemExit(f"Spazio insufficiente in {uscita.parent}: servono circa {necessario / 1e9:.1f} GB per i file "
                         f"temporanei, ne restano {libero / 1e9:.1f}. Scegli con -o una cartella su un disco più capiente.")
    if necessario > 1e9:
        logger.info(f"File temporanei: circa {necessario / 1e9:.1f} GB (accanto all'uscita, cancellati a fine lavoro).")
    tmp = Path(tempfile.mkdtemp(prefix="casoweb_pulisci_", dir=uscita.parent))
    try:
        # 1) audio originale a 48 kHz (stereo) e sua versione mono per il motore
        orig = tmp / "originale.wav"
        mono = tmp / "mono.wav"
        logger.info("Estraggo l'audio...")
        esegui(["ffmpeg", "-y", "-v", "error", "-i", str(ingresso), "-vn", "-ar", str(SR), "-ac", "2",
                "-c:a", "pcm_s16le", str(orig)])
        esegui(["ffmpeg", "-y", "-v", "error", "-i", str(orig), "-ac", "1", str(mono)])

        # 2) parlato ripulito dal motore
        pulito = tmp / "pulito.wav"
        if df:
            logger.info(f"Riduzione del rumore con DeepFilterNet ({Path(df[0]).name})...")
            outdir = tmp / "df"
            outdir.mkdir()
            esegui(comando_deepfilternet(df[0], df[1], mono, outdir, post_filtro))
            trovati = sorted(outdir.glob("*.wav"))
            if not trovati:
                raise SystemExit("DeepFilterNet non ha prodotto nessun file .wav.")
            # dopo il motore, la catena ffmpeg (gate, compressore) rifinisce il risultato
            esegui(["ffmpeg", "-y", "-v", "error", "-i", str(trovati[0]), "-af",
                    _senza_afftdn(p["voce"]) or "anull", "-ar", str(SR), str(pulito)])
        else:
            logger.info(f"Filtri ffmpeg, profilo {profilo}...")
            esegui(["ffmpeg", "-y", "-v", "error", "-i", str(mono), "-af", p["voce"], "-ar", str(SR), str(pulito)])

        # 3) miscela (solo parlato ripulito, oppure dissolvenze incrociate con la musica) e uscita
        pannello = "pan=stereo|c0=c0|c1=c0"
        grafo = _grafo(p, bool(intervalli), intervalli, dissolvenza, volume_finale, pannello)
        logger.info(f"Scrivo {uscita.name}...")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(orig), "-i", str(pulito)]
        if video:
            cmd += ["-i", str(ingresso)]
        cmd += _opzione_grafo(grafo, tmp) + ["-map", "[out]"]
        if video:
            cmd += ["-map", "2:v:0", "-c:v", "copy"]
        cmd += codec + [str(uscita)]
        esegui(cmd)
    finally:
        if tieni_temporanei:
            logger.info(f"File temporanei conservati in {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    logger.info(f"Fatto: {uscita}")
    return uscita


def _opzione_grafo(grafo: str, tmp: Path) -> list[str]:
    """Il grafo come argomento, o in un file se è lungo (molti segmenti: Windows ha un limite di ~32 KB)."""
    if len(grafo) < 8000:
        return ["-filter_complex", grafo]
    script = tmp / "grafo.txt"
    script.write_text(grafo, encoding="utf-8")
    # ffmpeg 7 ha introdotto -/opzione e dichiarato obsoleto -filter_complex_script
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout
    m = re.search(r"version\D*(\d+)", out)
    return ["-/filter_complex", str(script)] if m and int(m.group(1)) >= 7 else ["-filter_complex_script", str(script)]


def _senza_afftdn(catena: str) -> str:
    """La catena del profilo senza il filtro afftdn (la riduzione del rumore l'ha già fatta il motore)."""
    parti = [x for x in catena.split(",") if not x.startswith("afftdn")]
    return ",".join(parti)


def _grafo(p: dict, con_segmenti: bool, intervalli, dissolvenza: float, volume_finale: bool, pannello: str) -> str:
    fin = ",loudnorm=I=-16:TP=-1.5:LRA=11" if volume_finale else ""
    if not con_segmenti:
        return f"[1:a]{pannello}{fin}[out]"
    g = espressione_gate(intervalli, dissolvenza)
    return (
        f"[0:a]{p['musica']},volume='1-({g})':eval=frame[o];"
        f"[1:a]{pannello},volume='{g}':eval=frame[c];"
        # amix divide ogni ingresso per il loro numero (qui 2): volume=2 lo compensa. Si evita
        # l'opzione normalize=0, che ffmpeg < 4.4 (es. Ubuntu 20.04) non conosce.
        f"[o][c]amix=inputs=2:duration=first:dropout_transition=0,volume=2{fin}[out]"
    )
