"""
Trova dove inizia un estratto audio dentro una registrazione più lunga.

Caso tipico: due ore di stream radiofonico registrato con VLC e, per altra via,
un'ora della stessa trasmissione. In che istante della registrazione completa
comincia l'estratto?

Il metodo, in due passi, regge a codifiche e volumi diversi (MP3 contro AAC...):
  1. Impronta grossolana: ogni 20 ms si calcola l'energia in 24 bande di
     frequenza (in dB). Il confronto tra le impronte di un campione dell'estratto
     e della registrazione intera, fatto con la FFT, dà un candidato in pochi
     secondi anche su due ore di audio (con la correlazione diretta dei campioni
     servirebbero gigabyte di memoria).
  2. Raffinamento: intorno al candidato si correla la forma d'onda vera, per
     portare la precisione da 20 ms a una frazione di millisecondo.

Si prendono tre campioni (al 10%, 50% e 90% dell'estratto): se danno lo stesso
risultato la corrispondenza è affidabile; se divergono, l'estratto ha tagli o la
velocità è diversa (deriva).

Servono numpy e ffmpeg. Interfaccia: `casoweb trova-offset`
(vedi casoweb_tools.cmds.trova_offset).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from casoweb_tools.ambiente import manca_programma

logger = logging.getLogger(__name__)

SR = 8000          # basta e avanza per il parlato e la radio
N_FFT = 320        # 40 ms
HOP = 160          # 20 ms
BANDE = 24
F_MIN, F_MAX = 100.0, 3800.0


@dataclass
class Risultato:
    offset: float                 # secondi: inizio dell'estratto nella registrazione lunga
    punteggio: float              # somiglianza (0-1) del campione migliore
    margine: float                # quanto il picco supera il secondo miglior candidato (rapporto)
    scarto: float                 # massima differenza (s) tra i risultati dei campioni
    per_sonda: list[float] = field(default_factory=list)


# --------------------------------------------------------------------------
# Impronta spettrale
# --------------------------------------------------------------------------
def _bordi_bande():
    import numpy as np  # noqa: PLC0415

    f = np.geomspace(F_MIN, F_MAX, BANDE + 1)
    k = np.round(f * N_FFT / SR).astype(int)
    for i in range(1, len(k)):  # ogni banda almeno un coefficiente
        k[i] = max(k[i], k[i - 1] + 1)
    return k


def impronta(audio):
    """Matrice (n_frame, BANDE) di energie in dB; 20 ms per riga. Elabora a blocchi (poca memoria)."""
    import numpy as np  # noqa: PLC0415

    n = 1 + max(len(audio) - N_FFT, 0) // HOP
    k = _bordi_bande()
    finestra = np.hanning(N_FFT).astype(np.float32)
    out = np.empty((n, BANDE), dtype=np.float32)
    blocco = 20000
    for a in range(0, n, blocco):
        b = min(n, a + blocco)
        idx = np.arange(a, b)[:, None] * HOP + np.arange(N_FFT)[None, :]
        pezzo = audio[np.minimum(idx, len(audio) - 1)] * finestra
        spettro = np.abs(np.fft.rfft(pezzo, axis=1)) ** 2
        for j in range(BANDE):
            out[a:b, j] = spettro[:, k[j]:k[j + 1]].sum(axis=1)
    return 10 * np.log10(out + 1e-10)


def _cerca_grossolano(imp_lunga, imp_corta):
    """(indice_frame, punteggio, margine) del miglior allineamento dell'impronta corta in quella lunga."""
    import numpy as np  # noqa: PLC0415

    nl, ns = len(imp_lunga), len(imp_corta)
    if ns >= nl:
        raise SystemExit("L'estratto è più lungo (o uguale) della registrazione in cui cercarlo.")
    L = imp_lunga - imp_lunga.mean(axis=0)
    S = imp_corta - imp_corta.mean(axis=0)
    N = 1 << int(np.ceil(np.log2(nl + ns)))
    corr = np.zeros(N)
    for j in range(BANDE):
        corr += np.fft.irfft(np.fft.rfft(L[:, j], N) * np.conj(np.fft.rfft(S[:, j], N)), N)
    corr = corr[:nl - ns + 1]
    # normalizzazione: norma dell'impronta lunga sotto ogni finestra (somiglianza coseno)
    quad = (L ** 2).sum(axis=1)
    cum = np.concatenate([[0.0], np.cumsum(quad)])
    norma_l = np.sqrt(np.maximum(cum[ns:nl + 1] - cum[:nl - ns + 1], 1e-9))
    norma_s = np.sqrt((S ** 2).sum())
    sim = corr / (norma_l * norma_s)
    i = int(np.argmax(sim))
    # secondo candidato: almeno 2 s distante
    lontano = int(2.0 * SR / HOP)
    maschera = sim.copy()
    maschera[max(0, i - lontano):i + lontano + 1] = -np.inf
    secondo = float(maschera.max()) if np.isfinite(maschera.max()) else 0.0
    migliore = float(sim[i])
    margine = migliore / secondo if secondo > 1e-6 else float("inf")
    return i, migliore, margine


def _raffina(lunga, sonda, centro: int, raggio: int) -> int:
    """Posizione (in campioni) della sonda nella forma d'onda lunga, cercando entro ±raggio da `centro`."""
    import numpy as np  # noqa: PLC0415

    a = max(0, centro - raggio)
    b = min(len(lunga), centro + raggio + len(sonda))
    pezzo = lunga[a:b]
    if len(pezzo) < len(sonda):
        return centro
    N = 1 << int(np.ceil(np.log2(len(pezzo) + len(sonda))))
    c = np.fft.irfft(np.fft.rfft(pezzo, N) * np.conj(np.fft.rfft(sonda, N)), N)[:len(pezzo) - len(sonda) + 1]
    return a + int(np.argmax(c))


def trova(lungo, corto, *, sonde: int = 3, durata_sonda: float = 30.0) -> Risultato:
    """`lungo` e `corto`: forme d'onda mono float32 a SR Hz. Restituisce l'inizio di `corto` in `lungo`."""
    durata_corto = len(corto) / SR
    durata_sonda = min(durata_sonda, durata_corto / 2)
    if durata_sonda < 3:
        raise SystemExit("L'estratto è troppo breve per cercarlo (servono almeno 6 secondi).")
    sonde = max(1, sonde)
    imp_lunga = impronta(lungo)
    lung_sonda = int(durata_sonda * SR)
    posizioni = [0.5] if sonde == 1 else [0.1 + 0.8 * k / (sonde - 1) for k in range(sonde)]

    offset_sonde, punteggi, margini = [], [], []
    for p in posizioni:
        inizio = int(min(p * len(corto), len(corto) - lung_sonda))
        sonda = corto[inizio:inizio + lung_sonda]
        i, punteggio, margine = _cerca_grossolano(imp_lunga, impronta(sonda))
        pos = _raffina(lungo, sonda, i * HOP, raggio=HOP * 3)
        offset_sonde.append((pos - inizio) / SR)
        punteggi.append(punteggio)
        margini.append(margine)
        logger.debug(f"Sonda al {p:.0%}: offset {offset_sonde[-1]:.3f} s, somiglianza {punteggio:.2f}, margine {margine:.2f}")

    ordinati = sorted(offset_sonde)
    mediano = ordinati[len(ordinati) // 2]
    return Risultato(offset=mediano, punteggio=max(punteggi), margine=min(margini),
                     scarto=ordinati[-1] - ordinati[0], per_sonda=offset_sonde)


# --------------------------------------------------------------------------
# Programma
# --------------------------------------------------------------------------
def decodifica(path: Path, da: float = 0.0, durata: float | None = None):
    import numpy as np  # noqa: PLC0415

    cmd = ["ffmpeg", "-v", "error"]
    if da:
        cmd += ["-ss", f"{da}"]
    if durata:
        cmd += ["-t", f"{durata}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    logger.debug("Avvio: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg non riesce a leggere {path.name}: {r.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def fmt_tempo(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def run(estratto: Path, registrazione: Path, *, sonde: int, durata_sonda: float, cerca_da: float,
        cerca_fino: float | None) -> dict:
    if shutil.which("ffmpeg") is None:
        raise SystemExit(manca_programma("ffmpeg"))
    try:
        import numpy  # noqa: F401, PLC0415
    except ImportError:
        raise SystemExit("Manca numpy: pip install numpy (o reinstalla casoweb con uv).") from None

    logger.info("Decodifico le due registrazioni...")
    corto = decodifica(estratto)
    durata = None if cerca_fino is None else cerca_fino - cerca_da
    lungo = decodifica(registrazione, cerca_da, durata)
    logger.info(f"Estratto: {len(corto) / SR / 60:.1f} min  |  registrazione: {len(lungo) / SR / 60:.1f} min"
                + (f" (da {fmt_tempo(cerca_da)})" if cerca_da else ""))

    logger.info("Cerco...")
    r = trova(lungo, corto, sonde=sonde, durata_sonda=durata_sonda)
    offset = r.offset + cerca_da
    durata_estratto = len(corto) / SR

    avvisi = []
    if r.punteggio < 0.5:
        avvisi.append("somiglianza bassa: l'estratto potrebbe non appartenere a questa registrazione")
    if r.margine < 1.1:
        avvisi.append("più posizioni quasi equivalenti (jingle o brani ripetuti?): usa --cerca-da/--cerca-fino")
    if r.scarto > 0.1:
        avvisi.append(f"i campioni divergono di {r.scarto:.2f} s: l'estratto ha tagli o una velocità diversa")
    return dict(offset=offset, fine=offset + durata_estratto, durata_estratto=durata_estratto,
                somiglianza=round(r.punteggio, 3), margine=None if r.margine == float("inf") else round(r.margine, 2),
                scarto=round(r.scarto, 3), avvisi=avvisi)
