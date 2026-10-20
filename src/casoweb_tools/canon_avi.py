"""
Contenitore AVI nello stile Canon PowerShot ("CanonMVI06").

Le PowerShot dell'epoca della A2000 IS riproducono solo AVI costruiti
esattamente come i loro: fotogrammi Motion JPEG e audio PCM 8 bit mono a
11024 Hz, ma soprattutto una disposizione che ffmpeg non produce.

  - intestazione di 2 KB: il blocco LIST movi inizia sempre a 0x800,
    con un JUNK di riempimento;
  - dati a blocchi: un secondo di audio (01wb), poi `fps` fotogrammi (00dc);
  - l'indice vero sta nei blocchi ix00/ix01 in fondo a movi, richiamati
    dagli indx delle intestazioni (OpenDML); idx1 ha solo due voci, il
    primo blocco audio e il primo fotogramma;
  - durata del fotogramma troncata (66666 µs a 15 fps), data nel blocco
    IDIT e programma "CanonMVI06" in INFO/ISFT;
  - audio lungo quanto il video, arrotondato per eccesso al campione.

`build` è verificata rigenerando file Canon reali (160x120 a 15 fps,
320x240 e 640x480 a 30 fps) identici byte per byte; `parse_canon` fa il
percorso inverso.
"""

from __future__ import annotations

import struct
import time

AUDIO_RATE = 11024  # campioni al secondo (mono, 8 bit senza segno)
MOVI_START = 0x800  # il blocco LIST movi inizia sempre a 0x800


def p32(x: int) -> bytes:
    return struct.pack("<I", x)


def chunk(cid: bytes, data: bytes) -> bytes:
    return cid + p32(len(data)) + data + (b"\0" if len(data) & 1 else b"")


def lst(form: bytes, data: bytes) -> bytes:
    return b"LIST" + p32(len(data) + 4) + form + data


def strh(ftype, handler, scale, rate, length, bufsize, quality, samplesize, rect) -> bytes:
    return (ftype + handler
            + struct.pack("<IHHIIIIIIII", 0, 0, 0, 0, scale, rate, 0, length, bufsize, quality, samplesize)
            + struct.pack("<hhhh", *rect))


def idit_string(ts: float | None = None) -> str:
    """Data nel formato del blocco IDIT, es. 'Thu Oct 15 18:43:06 2026' (giorno su due caratteri)."""
    t = time.localtime(ts if ts is not None else time.time())
    return time.strftime("%a %b ", t) + f"{t.tm_mday:2d}" + time.strftime(" %H:%M:%S %Y", t)


def campioni_audio(n: int, fps: int) -> int:
    """Campioni audio di un video di n fotogrammi: la Canon arrotonda per eccesso.

    Es. 80 fotogrammi a 30 fps: 80 * 11024 / 30 = 29397,33 -> 29398 (osservato su file reali).
    """
    return -(-n * AUDIO_RATE // fps)


def build(frames: list[bytes], audio: bytes, width: int, height: int, fps: int, idit: str | None = None) -> bytes:
    """AVI Canon da fotogrammi JPEG (già in stile Canon) e audio u8 mono a AUDIO_RATE Hz.

    L'audio deve durare esattamente quanto il video: campioni_audio(len(frames), fps) campioni.
    """
    n = len(frames)
    assert n > 0 and len(audio) == campioni_audio(n, fps), (len(audio), campioni_audio(n, fps))
    usec = 1_000_000 // fps  # 66666 a 15 fps, 33333 a 30 fps (troncato)
    maxframe = max(len(f) for f in frames)
    maxbps = AUDIO_RATE + fps * maxframe
    suggested = max(maxframe, AUDIO_RATE)

    # --- dati: un blocco audio da 1 s, poi fps fotogrammi
    movi, vid, aud = bytearray(), [], []
    rel, ai, first_a, first_v = 4, 0, None, None  # rel: posizione relativa alla parola 'movi'
    for i, f in enumerate(frames):
        if i % fps == 0 and ai < len(audio):
            a = audio[ai:ai + AUDIO_RATE]
            ai += len(a)
            c = chunk(b"01wb", a)
            if first_a is None:
                first_a = (rel, len(a))
            aud.append((rel + 4, len(a)))  # offset dei dati rispetto a qwBaseOffset
            movi += c
            rel += len(c)
        c = chunk(b"00dc", f)
        if first_v is None:
            first_v = (rel, len(f))
        vid.append((rel + 4, len(f)))
        movi += c
        rel += len(c)
    assert ai == len(audio)

    base = MOVI_START + 12  # posizione del primo blocco

    def ix(cid, entries):
        body = struct.pack("<HBBI4sQI", 2, 0, 1, len(entries), cid, base, 0)
        body += b"".join(struct.pack("<II", o, s) for o, s in entries)
        return chunk(b"ix" + cid[:2], body)

    ix_v, ix_a = ix(b"00dc", vid), ix(b"01wb", aud)
    ix_v_pos = base + len(movi)
    ix_a_pos = ix_v_pos + len(ix_v)
    movi_list = lst(b"movi", bytes(movi) + ix_v + ix_a)

    # --- intestazioni (hdrl)
    avih = struct.pack("<14I", usec, maxbps, 0, 0x00010010, n, 0, 2, suggested, width, height, 0, 0, 0, 0)

    def indx(cid, pos, size, dur):
        return (struct.pack("<HBBI4sIII", 4, 0, 0, 1, cid, 0, 0, 0)
                + struct.pack("<QII", pos, size, dur)).ljust(120, b"\0")

    strl_v = lst(b"strl",
                 chunk(b"strh", strh(b"vids", b"mjpg", usec, 1_000_000, n, maxframe, 10000, 0,
                                     (0, 0, width, height)))
                 + chunk(b"strf", struct.pack("<IiiHH4sIiiII", 40, width, height, 1, 24, b"MJPG",
                                              width * height * 3, 0, 0, 0, 0))
                 + chunk(b"indx", indx(b"00dc", ix_v_pos, len(ix_v), n)))
    strl_a = lst(b"strl",
                 chunk(b"strh", strh(b"auds", b"\0\0\0\0", 1, AUDIO_RATE, len(audio), AUDIO_RATE, 10000, 1,
                                     (0, 0, 0, 0)))
                 + chunk(b"strf", struct.pack("<HHIIHH", 1, 1, AUDIO_RATE, AUDIO_RATE, 1, 8))
                 + chunk(b"indx", indx(b"01wb", ix_a_pos, len(ix_a), len(audio))))
    odml = lst(b"odml", chunk(b"dmlh", p32(n).ljust(248, b"\0")))
    idit_b = (idit or idit_string()).encode("ascii") + b"\n\0"
    hdrl = lst(b"hdrl", chunk(b"avih", avih) + strl_v + strl_a + odml + chunk(b"IDIT", idit_b))
    info = lst(b"INFO", chunk(b"ISFT", b"CanonMVI06\0\0"))
    head = b"AVI " + hdrl + info
    junk_total = MOVI_START - (8 + len(head))
    junk = b"JUNK" + p32(junk_total - 8) + b"\0" * (junk_total - 8)
    idx1 = chunk(b"idx1", b"01wb" + struct.pack("<III", 0, *first_a) + b"00dc" + struct.pack("<III", 0x10, *first_v))
    body = head + junk + movi_list + idx1
    return b"RIFF" + p32(len(body)) + body


def parse_canon(d: bytes) -> tuple[list[bytes], bytes, int, int, int, str]:
    """Fotogrammi, audio, larghezza, altezza, fps e data IDIT di un AVI Canon."""
    def u32(o):
        return struct.unpack("<I", d[o:o + 4])[0]

    if d[:4] != b"RIFF" or d[8:12] != b"AVI " or d[MOVI_START:MOVI_START + 4] != b"LIST":
        raise ValueError("non è un AVI nello stile Canon (LIST movi atteso a 0x800)")
    pos, end = MOVI_START + 12, MOVI_START + 8 + u32(MOVI_START + 4)
    frames, audio = [], bytearray()
    while pos + 8 <= end:
        cid, size = d[pos:pos + 4], u32(pos + 4)
        if cid == b"00dc":
            frames.append(bytes(d[pos + 8:pos + 8 + size]))
        elif cid == b"01wb":
            audio += d[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    w, h = u32(0x40), u32(0x44)
    fps = round(1_000_000 / u32(0x20))
    i = d.find(b"IDIT")
    idit = d[i + 8:i + 8 + 24].decode()
    return frames, bytes(audio), w, h, fps, idit
