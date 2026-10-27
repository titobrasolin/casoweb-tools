"""
Da una ripresa amatoriale a una clip per il sito e i social, con un'impronta
volutamente grezza: bianco e nero ad alto contrasto, grana, vignettatura e
testi in carattere da macchina da scrivere che compaiono una lettera alla volta.

Cosa fa, nell'ordine:
  - taglia la clip (--inizio, --fine) e la ritaglia (--ritaglio) in un
    formato verticale, 4:5 o orizzontale, senza seguire nessuno: il
    ritaglio è fisso per tutta la clip;
  - sceglie la risoluzione d'uscita in base a quanti pixel restano: sotto
    i 700 di altezza utile esce a 720 (es. 720x1280), altrimenti a 1080;
  - applica il look (--look bn): bianco e nero con i canali pesati come una
    pellicola pancromatica, curva a S, vignettatura, grana solo sulla
    luminanza (nessun disturbo colorato), dissolvenze dal nero;
  - converte l'SRT in sottotitoli ASS "battuti a macchina", alzati dal bordo
    inferiore (14% in verticale, per i comandi delle storie), a righe corte;
    aggiunge un titolo d'apertura (--titolo) e i nomi (--nomi) che si
    scrivono nel momento in cui vengono pronunciati.

L'effetto macchina da scrivere: ogni sottotitolo diventa una serie di eventi
ASS in cui il testo è sempre completo, ma i caratteri non ancora "battuti"
sono trasparenti ({\\alpha&HFF&}). Così l'impaginazione non cambia mentre il
testo cresce, e con lo stile "carta" anche la striscia chiara dietro al testo
si allunga lettera per lettera.

L'ASS generato resta accanto al video (<nome>_social.ass): si può correggere a
mano e riusare con --ass.

I tempi di SRT e --nomi sono quelli della ripresa originale, non della clip
tagliata: vengono spostati di --inizio.

Servono solo ffmpeg (con libass) e ffprobe.

Interfaccia a riga di comando: `casoweb video-per-social` (vedi
casoweb_tools.cmds.video_per_social).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

from casoweb_tools.ambiente import manca_programma
from casoweb_tools.video_da_audio import media_duration, parse_ts, sanitize_name

logger = logging.getLogger(__name__)

# nome -> (rapporto larghezza/altezza, dimensioni a 720, dimensioni a 1080, margine dei sottotitoli in %)
FORMATI = {
    "9:16": (9 / 16, (720, 1280), (1080, 1920), 14),
    "4:5": (4 / 5, (864, 1080), (1080, 1350), 8),
    "16:9": (16 / 9, (1280, 720), (1920, 1080), 6),
}
# sotto quest'altezza utile (in pixel della ripresa) si esce a 720: ingrandire di più non aggiunge dettaglio
SOGLIA_720 = 700

LOOK = ("bn", "colore")
GRANA = ("pellicola", "fotocopia", "nessuna")
STILI_TESTO = ("carta", "contorno")
MAX_BITRATE = "8M"

# pesi R, G, B del bianco e nero: un po' più di rosso e verde della luminanza video,
# come le pellicole pancromatiche (pelle più chiara, blu più scuri)
PESI_BN = (0.40, 0.45, 0.15)


@dataclass
class Opzioni:
    formato: str = "9:16"
    ritaglio: tuple[int, int, int, int] | None = None  # larghezza, altezza, x, y in pixel della ripresa
    risoluzione: tuple[int, int] | None = None
    inizio: float = 0.0
    fine: float | None = None
    audio: Path | None = None
    srt: Path | None = None
    ass: Path | None = None
    titolo: list[str] = field(default_factory=list)
    durata_titolo: float = 4.0
    nomi: Path | None = None
    durata_nome: float = 4.0
    look: str = "bn"
    contrasto: float = 0.75
    grana: str = "pellicola"
    intensita_grana: float = 14.0
    vignettatura: float = 0.5
    font: str = "Liberation Mono"
    dimensione_font: float = 5.0  # % della larghezza
    stile_testo: str = "carta"
    velocita: float = 18.0  # caratteri al secondo
    dissolvenza: float = 0.5
    dissolvenza_audio: float = 0.3
    crf: int = 23
    uscita: Path | None = None


# --------------------------------------------------------------------------
# Utilità pure
# --------------------------------------------------------------------------
def parse_tempo(s: str) -> float:
    """Secondi da "33", "33.5", "0:33", "1:02:03" o "00:00:33,500"."""
    s = s.strip().replace(",", ".")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)", s)
    if not m:
        raise ValueError(f"tempo non valido: {s!r} (es. 33.5, 0:33, 00:00:33,500)")
    h, mi, sec = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + float(sec)


def parse_ritaglio(s: str) -> tuple[int, int, int, int]:
    """"L:A:X:Y" (come il filtro crop di ffmpeg) -> (larghezza, altezza, x, y)."""
    parti = re.split(r"[:x,]", s.strip())
    if len(parti) != 4 or not all(p.isdigit() for p in parti):
        raise ValueError(f"ritaglio non valido: {s!r} (es. 378:672:571:20 = larghezza:altezza:x:y)")
    return tuple(int(p) for p in parti)  # type: ignore[return-value]


def ritaglio_centrato(w: int, h: int, rapporto: float) -> tuple[int, int, int, int]:
    """Il ritaglio più grande del formato richiesto, al centro dell'inquadratura (dimensioni pari)."""
    if w / h > rapporto:
        rh = h - h % 2
        rw = int(rh * rapporto) // 2 * 2
    else:
        rw = w - w % 2
        rh = int(rw / rapporto) // 2 * 2
    return rw, rh, (w - rw) // 2, (h - rh) // 2


def risoluzione_uscita(formato: str, altezza_utile: int) -> tuple[int, int]:
    _, piccola, grande, _ = FORMATI[formato]
    return piccola if altezza_utile < SOGLIA_720 else grande


def leggi_srt(path: Path) -> list[tuple[float, float, str]]:
    """Sottotitoli come (inizio, fine, testo su una riga sola)."""
    blocchi = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").replace("\r\n", "\n"))
    out = []
    for b in blocchi:
        m = re.search(r"(\d+:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d+:\d{2}:\d{2}[,.]\d{3})\s*\n(.*)", b, re.S)
        if m:
            testo = " ".join(r.strip() for r in m.group(3).splitlines() if r.strip())
            out.append((parse_ts(m.group(1)), parse_ts(m.group(2)), testo))
    return out


def leggi_nomi(path: Path) -> list[tuple[float, str]]:
    """Righe "tempo<TAB>testo" (il tempo come in parse_tempo); # per i commenti."""
    out = []
    for n, riga in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not riga.strip() or riga.lstrip().startswith("#"):
            continue
        tempo, _, testo = riga.partition("\t")
        try:
            out.append((parse_tempo(tempo), testo.strip()))
        except ValueError as e:
            raise SystemExit(f"{path.name}, riga {n}: {e}") from None
        if not testo.strip():
            raise SystemExit(f"{path.name}, riga {n}: manca il testo dopo il tempo (separati da un TAB)")
    return sorted(out)


def sposta(eventi: list[tuple[float, float, str]], inizio: float, durata: float) -> list[tuple[float, float, str]]:
    """Porta i tempi della ripresa a quelli della clip tagliata; scarta o accorcia ciò che cade fuori."""
    out = []
    for a, b, t in eventi:
        a, b = max(a - inizio, 0.0), min(b - inizio, durata)
        if b - a > 0.05:
            out.append((a, b, t))
    return out


def a_capo(testo: str, larghezza: int) -> list[str]:
    return textwrap.wrap(testo, larghezza, break_long_words=True, break_on_hyphens=False) or [""]


def spezza(a: float, b: float, righe: list[str], max_righe: int) -> list[tuple[float, float, list[str]]]:
    """Gruppi di al massimo `max_righe` righe, con il tempo diviso in proporzione ai caratteri."""
    gruppi = [righe[i:i + max_righe] for i in range(0, len(righe), max_righe)]
    pesi = [sum(len(r) for r in g) or 1 for g in gruppi]
    out, t = [], a
    for g, p in zip(gruppi, pesi):
        dt = (b - a) * p / sum(pesi)
        out.append((t, t + dt, g))
        t += dt
    return out


def ts_ass(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _sicuro(testo: str) -> str:
    """Il testo non deve contenere comandi ASS: graffe e barre rovesciate diventano caratteri innocui."""
    return testo.replace("\\", "/").replace("{", "(").replace("}", ")")


def battuta(a: float, b: float, righe: list[str], stile: str, velocita: float,
            frazione: float = 0.6) -> list[str]:
    """Righe Dialogue che "battono" il testo una lettera alla volta tra a e b.

    Il testo è sempre intero, quello non ancora battuto è trasparente: l'impaginazione non si muove.
    La battitura dura al massimo `frazione` della durata, così il testo resta leggibile per intero.
    """
    unita = []  # caratteri visibili; "\N" (a capo) conta come uno
    for i, r in enumerate(righe):
        if i:
            unita.append(r"\N")
        unita.extend(_sicuro(r))
    n = len(unita)
    dt = min(1 / velocita, (b - a) * frazione / max(n, 1))
    out = []
    cs_prec = None
    for i in range(1, n + 1):
        if unita[i - 1] in (" ", r"\N") and i < n:
            continue  # uno spazio non si vede: niente evento a sé
        t0 = a + (i - 1) * dt
        t1 = a + i * dt if i < n else b
        if out and ts_ass(t0) == cs_prec:
            out.pop()  # stesso centesimo di secondo del precedente: vince quello con più testo
        visibile = "".join(unita[:i])
        resto = "".join(unita[i:])
        testo = visibile + (r"{\alpha&HFF&}" + resto if resto else "")
        out.append(f"Dialogue: 0,{ts_ass(t0)},{ts_ass(t1)},{stile},,0,0,0,,{testo}")
        cs_prec = ts_ass(t0)
    # ogni evento finisce dove inizia il successivo
    for j in range(len(out) - 1):
        campi = out[j].split(",", 9)
        campi[2] = out[j + 1].split(",", 9)[1]
        out[j] = ",".join(campi)
    return out


def stili_ass(w: int, h: int, o: Opzioni) -> list[str]:
    px = max(12.0, w * o.dimensione_font / 100)
    margine_basso = h * FORMATI[o.formato][3] / 100
    lato = w * 0.06
    if o.stile_testo == "carta":
        # testo quasi nero su una striscia color carta (BorderStyle 3: riquadro al posto del contorno)
        colori, bordo, spessore = "&H00141414,&H00141414,&H00DCE4E8,&H00000000", 3, px * 0.18
    else:
        colori, bordo, spessore = "&H00F0F0F0,&H00F0F0F0,&H00000000,&H00000000", 1, max(1.5, px / 14)

    def stile(nome: str, dim: float, allineamento: int, mv: float) -> str:
        return (f"Style: {nome},{o.font},{dim:.1f},{colori},0,0,0,0,100,100,0,0,"
                f"{bordo},{spessore:.1f},0,{allineamento},{lato:.0f},{lato:.0f},{mv:.0f},1")

    return [
        stile("Sottotitoli", px, 2, margine_basso),
        stile("Titolo", px * 1.1, 7, h * 0.05),  # in alto a sinistra, come una didascalia
        stile("Nome", px * 1.1, 7, h * 0.05),
    ]


def costruisci_ass(w: int, h: int, o: Opzioni, sottotitoli: list[tuple[float, float, str]],
                   nomi: list[tuple[float, str]], durata: float) -> str:
    px = max(12.0, w * o.dimensione_font / 100)
    # un carattere di un font monospaziato è largo circa 0,6 em
    car = max(8, int((w - 2 * w * 0.06 - px) / (0.6 * px)))
    eventi: list[str] = []
    for a, b, testo in sottotitoli:
        for ga, gb, righe in spezza(a, b, a_capo(testo, car), 2):
            eventi += battuta(ga, gb, righe, "Sottotitoli", o.velocita)
    fine_titolo = 0.0
    if o.titolo:
        fine_titolo = min(o.durata_titolo, durata)
        righe = [r for t in o.titolo for r in a_capo(t, int(car / 1.1))]
        eventi += battuta(0.0, fine_titolo, righe, "Titolo", o.velocita)
    for i, (t, testo) in enumerate(nomi):
        t = max(t, fine_titolo)
        fine = min(t + o.durata_nome, durata, nomi[i + 1][0] if i + 1 < len(nomi) else durata)
        if fine - t > 0.2:
            eventi += battuta(t, fine, a_capo(testo, int(car / 1.1)), "Nome", o.velocita)
        else:
            logger.warning(f"il nome {testo!r} non trova spazio (titolo ancora in corso o nome successivo "
                           f"troppo vicino): omesso.")
    intestazione = [
        "[Script Info]", "; casoweb video-per-social", "ScriptType: v4.00+",
        f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        *stili_ass(w, h, o), "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(intestazione + eventi) + "\n"


def curva_contrasto(c: float) -> str:
    """Curva a S per il filtro curves: c = 0 lineare, 1 molto dura (neri e bianchi chiusi)."""
    c = min(max(c, 0.0), 1.0)
    nero, bianco = 0.06 * c, 1 - 0.04 * c
    return f"0/0 {nero:.3f}/0 0.25/{0.25 - 0.12 * c:.3f} 0.75/{0.75 + 0.12 * c:.3f} {bianco:.3f}/1 1/1"


def build_vf(o: Opzioni, ritaglio: tuple[int, int, int, int], w: int, h: int, ass: str | None,
             durata: float) -> str:
    rw, rh, rx, ry = ritaglio
    parti = [f"crop={rw}:{rh}:{rx}:{ry}", f"scale={w}:{h}:flags=lanczos", "setsar=1"]
    grana = o.grana if o.look == "bn" else "nessuna"
    if o.look == "bn":
        r, g, b = PESI_BN
        mix = ":".join(f"{c}{k}={v}" for c in "rgb" for k, v in zip("rgb", (r, g, b)))
        contrasto = o.contrasto + (0.3 if grana == "fotocopia" else 0.0)  # la fotocopia chiude i grigi
        parti += ["format=gbrp", f"colorchannelmixer={mix}", f"curves=all='{curva_contrasto(contrasto)}'"]
        if grana == "fotocopia":
            parti.append("unsharp=5:5:1.2")  # bordi duri, come il toner
    if o.vignettatura > 0:
        parti.append(f"vignette=angle={min(o.vignettatura, 1.0) * 0.8:.3f}")
    parti.append("format=yuv420p")
    if ass:
        parti.append(f"ass={ass}")
    if grana != "nessuna" and o.intensita_grana > 0:
        # solo sulla luminanza (c0): il bianco e nero resta senza disturbi colorati; t = cambia a ogni fotogramma
        flag = "t+u" if grana == "fotocopia" else "t"
        parti.append(f"noise=c0s={o.intensita_grana:g}:c0f={flag}")
    if o.dissolvenza > 0:
        d = min(o.dissolvenza, durata / 2)
        parti += [f"fade=t=in:st=0:d={d:g}", f"fade=t=out:st={max(durata - d, 0):.3f}:d={d:g}"]
    return ",".join(parti)


def build_af(o: Opzioni, durata: float) -> str:
    if o.dissolvenza_audio <= 0:
        return "anull"
    d = min(o.dissolvenza_audio, durata / 2)
    return f"afade=t=in:st=0:d={d:g},afade=t=out:st={max(durata - d, 0):.3f}:d={d:g}"


def build_command(ingresso: Path, o: Opzioni, vf: str, af: str, durata: float, uscita: Path) -> list[str]:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if logger.isEnabledFor(logging.INFO):
        cmd.append("-stats")
    taglio = ["-ss", f"{o.inizio:.3f}", "-t", f"{durata:.3f}"]
    cmd += [*taglio, "-i", str(ingresso.resolve())]
    if o.audio:
        cmd += [*taglio, "-i", str(o.audio.resolve())]
    cmd += ["-map", "0:v:0", "-map", f"{1 if o.audio else 0}:a:0?",
            "-vf", vf, "-af", af,
            # la grana cambia a ogni fotogramma e non si comprime: senza tetto il bitrate sale oltre i 30 Mbit/s,
            # che i social ricomprimono comunque
            "-c:v", "libx264", "-preset", "medium", "-tune", "grain", "-crf", str(o.crf),
            "-maxrate", MAX_BITRATE, "-bufsize", "16M", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", uscita.name]
    return cmd


# --------------------------------------------------------------------------
# Programma
# --------------------------------------------------------------------------
def dimensioni(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def converti(ingresso: Path, o: Opzioni) -> Path:
    for b in ("ffmpeg", "ffprobe"):
        if shutil.which(b) is None:
            sys.exit(manca_programma("ffmpeg"))

    sw, sh = dimensioni(ingresso)
    rapporto = FORMATI[o.formato][0]
    ritaglio = o.ritaglio or ritaglio_centrato(sw, sh, rapporto)
    rw, rh, rx, ry = ritaglio
    if rx + rw > sw or ry + rh > sh:
        sys.exit(f"Il ritaglio {rw}x{rh} in ({rx}, {ry}) esce dall'inquadratura {sw}x{sh}.")
    if abs(rw / rh - rapporto) / rapporto > 0.02:
        logger.warning(f"il ritaglio {rw}x{rh} non è in formato {o.formato}: l'immagine verrà deformata.")
    w, h = o.risoluzione or risoluzione_uscita(o.formato, rh)
    zoom = w / rw
    logger.info(f"Ripresa {sw}x{sh}, ritaglio {rw}x{rh} in ({rx}, {ry}) -> {w}x{h} "
                f"(ingrandimento {zoom:.2f}x)")
    if zoom > 2:
        logger.warning("ingrandimento oltre 2x: l'immagine risulterà morbida.")

    totale = media_duration(ingresso)
    fine = min(o.fine, totale) if o.fine is not None else totale
    durata = fine - o.inizio
    if durata <= 0:
        sys.exit(f"Intervallo vuoto: inizio {o.inizio:g} s, fine {fine:g} s (la ripresa dura {totale:.1f} s).")

    base = sanitize_name(ingresso)
    uscita = (o.uscita or ingresso.parent / f"{base}_social_{o.formato.replace(':', 'x')}.mp4").resolve()
    uscita.parent.mkdir(parents=True, exist_ok=True)
    # nome semplice (lettere, cifre, _ e -), così nel filtro ass non serve nessun escape
    ass_lavoro = uscita.parent / f"{sanitize_name(uscita)}.ass"

    if o.ass:
        if o.ass.resolve() != ass_lavoro:
            shutil.copyfile(o.ass, ass_lavoro)
        logger.info(f"Testi: {o.ass} (usato così com'è)")
    else:
        sottotitoli = sposta(leggi_srt(o.srt), o.inizio, durata) if o.srt else []
        nomi = [(t - o.inizio, n) for t, n in leggi_nomi(o.nomi) if 0 <= t - o.inizio < durata] if o.nomi else []
        ass_lavoro.write_text(costruisci_ass(w, h, o, sottotitoli, nomi, durata), encoding="utf-8")
        logger.info(f"Testi: {len(sottotitoli)} sottotitoli, {len(nomi)} nomi"
                    f"{', titolo' if o.titolo else ''} -> {ass_lavoro.name}")

    vf = build_vf(o, ritaglio, w, h, ass_lavoro.name, durata)
    af = build_af(o, durata)
    cmd = build_command(ingresso, o, vf, af, durata, uscita)
    logger.info(f"Clip da {o.inizio:.2f} a {fine:.2f} s ({durata:.1f} s), look {o.look}, grana {o.grana}")
    logger.debug(f"Avvio: {' '.join(cmd)}")
    t0 = time.time()
    try:
        subprocess.run(cmd, check=True, cwd=uscita.parent)
    except subprocess.CalledProcessError as e:
        sys.exit(f"\nffmpeg ha fallito (codice {e.returncode}).")
    logger.info(f"\nFatto in {time.time() - t0:.0f} s: {uscita} ({uscita.stat().st_size / 1_000_000:.1f} MB)")
    logger.info(f"Testi in {ass_lavoro}: si possono correggere e riusare con --ass.")
    return uscita
