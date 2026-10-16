"""THM sintetico con la struttura di quelli Canon, per i test (nessun dato di una fotocamera reale)."""

from __future__ import annotations

import struct

DATA = b"2016:06:04 12:12:52\0"


def _ifd(entries: list[tuple[int, int, int, bytes]], start: int, next_ifd: int = 0) -> tuple[bytes, bytes]:
    """IFD TIFF little-endian che inizia a `start`; i valori oltre 4 byte vanno dopo la tabella.

    entries: (tag, tipo, conteggio, valore già codificato). Restituisce (ifd, dati esterni).
    """
    data_off = start + 2 + 12 * len(entries) + 4
    table, extra = struct.pack("<H", len(entries)), b""
    for tag, typ, count, value in entries:
        if len(value) <= 4:
            table += struct.pack("<HHI", tag, typ, count) + value.ljust(4, b"\0")
        else:
            table += struct.pack("<HHII", tag, typ, count, data_off + len(extra))
            extra += value + (b"\0" if len(value) & 1 else b"")
    return table + struct.pack("<I", next_ifd), extra


def thm(jpeg160: bytes, fotogrammi: int = 153, numero_file: int = 1011259) -> bytes:
    """THM: JPEG con APP1 Exif e MakerNote Canon (tag 0x0008, 0x000d, 0x0011)."""
    t16 = [0] * 20
    t16[2] = t16[4] = fotogrammi
    t32 = [0] * 100
    t32[92] = fotogrammi

    # disposizione nel TIFF: intestazione (8), IFD0 + data, Exif IFD + data, MakerNote
    exif_at = 8 + (2 + 12 * 2 + 4) + len(DATA)
    mn_at = exif_at + (2 + 12 * 2 + 4) + len(DATA)
    makernote = b"".join(_ifd([
        (0x0008, 4, 1, struct.pack("<I", numero_file)),
        (0x000D, 4, len(t32), struct.pack(f"<{len(t32)}I", *t32)),
        (0x0011, 3, len(t16), struct.pack(f"<{len(t16)}H", *t16)),
    ], mn_at))
    exif = b"".join(_ifd([(0x9003, 2, len(DATA), DATA), (0x927C, 7, len(makernote), makernote)], exif_at))
    ifd0 = b"".join(_ifd([(0x0132, 2, len(DATA), DATA), (0x8769, 4, 1, struct.pack("<I", exif_at))], 8))
    tiff = b"II*\0" + struct.pack("<I", 8) + ifd0 + exif
    assert tiff[mn_at:mn_at + 2] == struct.pack("<H", 3)

    payload = b"Exif\0\0" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    i = 2
    while jpeg160[i + 1] in (0xE0, 0xFE):  # salta JFIF e commenti del JPEG di partenza
        i += 2 + struct.unpack(">H", jpeg160[i + 2:i + 4])[0]
    return b"\xff\xd8" + app1 + jpeg160[i:]


def leggi_makernote(thm_bytes: bytes) -> dict[str, object]:
    """Valori che patch_exif aggiorna: numero del file, fotogrammi (tabelle 0x0011 e 0x000d), date."""
    tiff = thm_bytes[12:]  # FFD8, FFE1, lunghezza, 'Exif\0\0'

    def u16(o):
        return struct.unpack("<H", tiff[o:o + 2])[0]

    def u32(o):
        return struct.unpack("<I", tiff[o:o + 4])[0]

    def ifd(off):
        return {u16(off + 2 + 12 * i): off + 2 + 12 * i for i in range(u16(off))}

    ifd0 = ifd(u32(4))
    exif = ifd(u32(ifd0[0x8769] + 8))
    mn = ifd(u32(exif[0x927C] + 8))
    t16 = u32(mn[0x0011] + 8)
    t32 = u32(mn[0x000D] + 8)
    return {
        "numero_file": u32(mn[0x0008] + 8),
        "fotogrammi": (u16(t16 + 4), u16(t16 + 8), u32(t32 + 4 * 92)),
        "date": [tiff[u32(e + 8):u32(e + 8) + 19].decode() for e in (ifd0[0x0132], exif[0x9003])],
    }
