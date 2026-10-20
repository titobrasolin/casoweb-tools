"""
Da un video qualsiasi a una coppia MVI_NNNN.AVI + MVI_NNNN.THM riproducibile
sul display di una Canon PowerShot (provato sulla A2000 IS).

La fotocamera mostra un video copiato sulla scheda solo se:
  - l'AVI è costruito come i suoi (vedi casoweb_tools.canon_avi);
  - i fotogrammi JPEG sono "alla Canon": marcatore AVI1 al posto di JFIF,
    un DRI, nessuna tabella Huffman (si usano quelle standard);
  - accanto c'è un .THM con lo stesso numero: JPEG 160x120 con un blocco
    Exif Canon che contiene anche il numero di fotogrammi, da cui la
    fotocamera ricava la durata. Se THM e AVI non concordano, il video
    non parte.

Il THM si costruisce a partire da un THM prodotto dalla fotocamera
(--thm-modello), di cui si aggiornano date, numero del file e numero di
fotogrammi: nessun dato di una fotocamera specifica sta nel codice.

Interfaccia a riga di comando: `casoweb video-per-canon` (vedi
casoweb_tools.cmds.video_per_canon).
"""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from casoweb_tools.ambiente import manca_programma
from casoweb_tools.canon_avi import AUDIO_RATE, build, campioni_audio, idit_string, parse_canon

# nome: (larghezza, altezza, fps, qualità -q:v di partenza, dimensione massima di un fotogramma in byte)
# Modalità e limiti: guida dell'utente Canon della A2000 IS (CDI-E388-010), pp. 90-91.
PROFILI = {
    # Compact. Struttura verificata byte per byte su un file Canon e provata sulla
    # fotocamera; fotogrammi Canon da 1,7 a 5,3 KB
    "compatto": (160, 120, 15, 3, 5000),
    # Standard 320x240. Struttura verificata byte per byte su un file Canon (fotogrammi
    # da 11 a 22 KB); conversione non ancora provata sulla fotocamera
    "320x240": (320, 240, 30, 3, None),
    # Standard 640x480. Struttura verificata byte per byte su file Canon (fotogrammi fino a
    # 68 KB); conversione non ancora provata sulla fotocamera
    "640x480": (640, 480, 30, 3, None),
}
VERIFICATI = {"compatto", "320x240", "640x480"}
# durata massima di un clip registrato dalla fotocamera (circa 3 minuti in Compact, 1 ora in
# Standard) e dimensione massima del file in Standard (4 GB)
DURATA_MASSIMA = {"compatto": 180, "320x240": 3600, "640x480": 3600}
DIMENSIONE_MASSIMA = 4 * 1024 ** 3


@dataclass
class Opzioni:
    numero: int
    thm_modello: Path
    cartella: Path = Path(".")
    cartella_fotocamera: int = 101
    profilo: str = "compatto"
    qualita: int | None = None
    max_fotogramma: int | None = None  # 0 = nessun limite
    durata: float | None = None


# --------------------------------------------------------------------------
# JPEG
# --------------------------------------------------------------------------
def segmenti(jpeg: bytes) -> tuple[list[tuple[int, bytes]], int]:
    """Segmenti che precedono l'SOS, come (marcatore, byte), e posizione dell'SOS."""
    out, i = [], 2
    while i < len(jpeg):
        if jpeg[i] != 0xFF:
            raise ValueError("JPEG non valido")
        m = jpeg[i + 1]
        if m == 0xDA:
            return out, i
        ln = struct.unpack(">H", jpeg[i + 2:i + 4])[0]
        out.append((m, jpeg[i:i + 2 + ln]))
        i += 2 + ln
    raise ValueError("SOS non trovato")


def a_stile_canon(jpeg: bytes) -> bytes:
    """JFIF -> AVI1, aggiunge DRI, toglie le tabelle Huffman (la Canon usa quelle standard)."""
    segs, sos = segmenti(jpeg)
    avi1 = b"\xff\xe0\x00\x10AVI1" + b"\0" * 10
    dri = b"\xff\xdd\x00\x04\x00\x00"
    resto = b"".join(s for m, s in segs if m not in (0xE0, 0xC4, 0xFE, 0xDD))
    return b"\xff\xd8" + avi1 + dri + resto + jpeg[sos:]


def ffmpeg(*args: str) -> subprocess.CompletedProcess:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True)
    if r.returncode:
        raise SystemExit("ffmpeg ha fallito:\n" + r.stderr.decode(errors="replace"))
    return r


# --------------------------------------------------------------------------
# THM
# --------------------------------------------------------------------------
def _tiff_ifd(tiff: bytes, off: int) -> dict[int, int]:
    n = struct.unpack("<H", tiff[off:off + 2])[0]
    return {struct.unpack("<H", tiff[off + 2 + 12 * i:off + 4 + 12 * i])[0]: off + 2 + 12 * i for i in range(n)}


def patch_exif(app1: bytes, quando: time.struct_time | None = None, numero_file: int | None = None,
               fotogrammi: int | None = None) -> bytes:
    """Aggiorna nel blocco Exif del THM: date, numero del file (cartella*10000+numero) e numero di fotogrammi."""
    app1 = bytearray(app1)
    if quando is not None:
        data = time.strftime("%Y:%m:%d %H:%M:%S", quando).encode() + b"\0"
        app1 = bytearray(re.sub(rb"\d{4}:\d\d:\d\d \d\d:\d\d:\d\d\0", data, bytes(app1)))
    tiff = memoryview(app1)[10:]  # dopo ffe1, lunghezza e 'Exif\0\0'

    def u16(o):
        return struct.unpack("<H", tiff[o:o + 2])[0]

    def u32(o):
        return struct.unpack("<I", tiff[o:o + 4])[0]

    ifd0 = _tiff_ifd(bytes(tiff), u32(4))
    exif = _tiff_ifd(bytes(tiff), u32(ifd0[0x8769] + 8))
    base = u32(exif[0x927C] + 8)  # MakerNote Canon: offset rispetto all'intestazione TIFF
    ent = {u16(base + 2 + 12 * i): base + 2 + 12 * i for i in range(u16(base))}
    if numero_file is not None and 0x0008 in ent:
        struct.pack_into("<I", tiff, ent[0x0008] + 8, numero_file)
    if fotogrammi is not None:
        voff = u32(ent[0x0011] + 8)  # tabella di 20 valori a 16 bit: [2] e [4] = n. fotogrammi
        vecchi = u16(voff + 2 * 2)
        for pos in (2, 4):
            struct.pack_into("<H", tiff, voff + 2 * pos, fotogrammi)
        voff = u32(ent[0x000D] + 8)  # tabella a 32 bit: la posizione 92 ripete il n. fotogrammi
        if u32(voff + 4 * 92) == vecchi:
            struct.pack_into("<I", tiff, voff + 4 * 92, fotogrammi)
    return bytes(app1)


def thm_da_modello(modello: bytes, jpeg160: bytes, quando: time.struct_time, numero_file: int,
                   fotogrammi: int) -> bytes:
    if modello[:4] != b"\xff\xd8\xff\xe1":
        raise SystemExit("Il THM modello non ha un blocco Exif: serve un .THM prodotto dalla fotocamera.")
    ln = struct.unpack(">H", modello[4:6])[0]
    app1 = patch_exif(modello[2:4 + ln], quando, numero_file, fotogrammi)
    segs, sos = segmenti(jpeg160)
    resto = b"".join(s for m, s in segs if m not in (0xE0, 0xFE))
    return b"\xff\xd8" + app1 + resto + jpeg160[sos:]


# --------------------------------------------------------------------------
# Conversione
# --------------------------------------------------------------------------
def _codifica(ingresso: Path, durata: list[str], w: int, h: int, fps: int, q: int, tmp: Path) -> list[Path]:
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1"
    for f in tmp.glob("f_*.jpg"):
        f.unlink()
    ffmpeg("-i", str(ingresso), *durata, "-an", "-vf", vf, "-c:v", "mjpeg", "-q:v", str(q), "-pix_fmt", "yuvj422p",
           "-huffman", "default", "-flags", "+bitexact", str(tmp / "f_%06d.jpg"))
    return sorted(tmp.glob("f_*.jpg"))


def converti(ingresso: Path, o: Opzioni) -> tuple[Path, Path]:
    if shutil.which("ffmpeg") is None:
        raise SystemExit(manca_programma("ffmpeg"))
    if not ingresso.is_file():
        raise SystemExit(f"File non trovato: {ingresso}")
    if not 1 <= o.numero <= 9999:
        raise SystemExit("Il numero del file va da 1 a 9999 (MVI_0001 ... MVI_9999).")
    modello = Path(o.thm_modello).read_bytes()

    w, h, fps, q, cap = PROFILI[o.profilo]
    if o.qualita is not None:
        q = o.qualita
    if o.max_fotogramma is not None:
        cap = o.max_fotogramma or None
    if o.profilo not in VERIFICATI:
        print(f"Attenzione: il profilo {o.profilo} non è ancora stato verificato su un file Canon reale.")
    durata = ["-t", str(o.durata)] if o.durata else []
    quando = time.localtime()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # alza la compressione finché il fotogramma più pesante non sta nel limite
        while True:
            files = _codifica(ingresso, durata, w, h, fps, q, tmp)
            if not files:
                raise SystemExit("ffmpeg non ha prodotto nessun fotogramma.")
            massimo = max(f.stat().st_size for f in files)
            if not cap or massimo <= cap + 40 or q >= 31:  # +40: le intestazioni vengono sostituite
                break
            q += 1
        medio = sum(f.stat().st_size for f in files) // len(files)
        print(f"Qualità -q:v {q}: fotogramma medio {medio} byte, massimo {massimo} byte (Canon: ~4000 / 5272)")
        frames = [a_stile_canon(f.read_bytes()) for f in files]
        n = len(frames)

        voluti = campioni_audio(n, fps)
        raw = tmp / "a.raw"
        ffmpeg("-i", str(ingresso), *durata, "-vn", "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "u8", str(raw))
        audio = raw.read_bytes() if raw.stat().st_size else b""
        audio = (audio + b"\x80" * voluti)[:voluti]  # taglia o completa con silenzio (0x80)
        avi = build(frames, audio, w, h, fps, idit_string(time.mktime(quando)))

        ffmpeg("-i", str(ingresso), *durata, "-an",
               "-vf", "scale=160:120:force_original_aspect_ratio=decrease,pad=160:120:(ow-iw)/2:(oh-ih)/2,setsar=1",
               "-frames:v", "1", "-c:v", "mjpeg", "-q:v", "3", "-pix_fmt", "yuvj422p", "-huffman", "default",
               "-flags", "+bitexact", str(tmp / "t.jpg"))
        thm = thm_da_modello(modello, (tmp / "t.jpg").read_bytes(), quando, o.cartella_fotocamera * 10000 + o.numero, n)

    for avviso in avvisi_limiti(o.profilo, n / fps, len(avi)):
        print(f"Attenzione: {avviso}")
    nome = f"MVI_{o.numero:04d}"
    o.cartella.mkdir(parents=True, exist_ok=True)
    out_avi, out_thm = o.cartella / f"{nome}.AVI", o.cartella / f"{nome}.THM"
    out_avi.write_bytes(avi)
    out_thm.write_bytes(thm)
    print(f"Creati {nome}.AVI ({len(avi) // 1024} KiB, {n} fotogrammi, {n / fps:.1f} s) e {nome}.THM in {o.cartella}")
    return out_avi, out_thm


def avvisi_limiti(profilo: str, secondi: float, dimensione: int) -> list[str]:
    """Avvisi quando il video supera ciò che la fotocamera stessa registrerebbe in quella modalità.

    Non si sa se la fotocamera rifiuti un file oltre questi limiti: l'avviso serve a non
    scoprirlo per caso.
    """
    avvisi = []
    limite = DURATA_MASSIMA[profilo]
    if secondi > limite:
        avvisi.append(f"{secondi:.0f} s superano la durata massima di un clip in questa modalità "
                      f"(circa {limite // 60} min): con --durata {limite} si resta nei limiti.")
    if dimensione > DIMENSIONE_MASSIMA:
        avvisi.append("il file supera i 4 GB, oltre i quali la fotocamera interrompe la registrazione.")
    return avvisi


def verifica(percorso: Path) -> bool:
    """Rigenera un AVI Canon dai suoi fotogrammi e dal suo audio: deve risultare identico byte per byte."""
    d = percorso.read_bytes()
    try:
        frames, audio, w, h, fps, idit = parse_canon(d)
    except ValueError as exc:
        raise SystemExit(f"{percorso}: {exc}") from exc
    identico = build(frames, audio, w, h, fps, idit) == d
    print(f"{percorso.name}: {'identico byte per byte' if identico else 'DIVERSO'} "
          f"({len(frames)} fotogrammi, {w}x{h}, {fps} fps)")
    return identico
