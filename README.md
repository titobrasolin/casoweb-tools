# casoweb-tools

[![test](https://github.com/titobrasolin/casoweb-tools/actions/workflows/test.yml/badge.svg)](https://github.com/titobrasolin/casoweb-tools/actions/workflows/test.yml)

Raccolta di utility, script di servizio e piccoli esperimenti sviluppati per la rubrica **[Tecnologia @ CASO](https://www.casoweb.eu/?view=category&id=90)** su *casoweb.eu*.

Ogni esperimento è un sottocomando di `casoweb`.

---

## 🚀 Installazione

### Cosa serve

**ffmpeg**, il programma che legge e scrive audio e video:

```bash
sudo apt install ffmpeg        # Debian, Ubuntu e derivate
```

**[uv](https://docs.astral.sh/uv/)**, per installare `casoweb`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> **Perché non basta `pip install`?** uv (come il più noto
> [pipx](https://pipx.pypa.io/)) installa un programma Python in un ambiente
> tutto suo: non tocca il Python di sistema né gli altri programmi, e si
> rimuove senza lasciare tracce. Qui uv è preferibile per due motivi: procura
> da solo la versione di Python giusta, e soprattutto sa escludere le librerie
> NVIDIA quando non servono, cosa che pipx non sa fare (vedi sotto).
>
> Se dopo l'installazione il comando `casoweb` non viene trovato, lancia
> `uv tool update-shell` e apri un nuovo terminale.

### I comandi leggeri

`video-da-audio` e gli altri comandi senza dipendenze pesanti:

```bash
uv tool install git+https://github.com/titobrasolin/casoweb-tools.git
```

Per questi va bene anche `pipx install git+https://github.com/titobrasolin/casoweb-tools.git`.

### Con `separa-parlato`

`separa-parlato` usa Whisper e inaSpeechSegmenter: le sue dipendenze,
pesanti, stanno nell'extra `separa-parlato` e richiedono Python 3.12 o
precedente (uv lo scarica da solo se manca).

**PC con scheda NVIDIA**: le librerie CUDA arrivano insieme a TensorFlow e
servono anche a Whisper.

```bash
uv tool install --python 3.12 \
    "casoweb-tools[separa-parlato] @ git+https://github.com/titobrasolin/casoweb-tools.git"
```

**PC senza GPU NVIDIA**: variante leggera, circa 2 GB invece di oltre 7.

```bash
uv tool install --python 3.12 \
    --overrides https://raw.githubusercontent.com/titobrasolin/casoweb-tools/main/scripts/overrides-cpu.txt \
    "casoweb-tools[separa-parlato,cpu] @ git+https://github.com/titobrasolin/casoweb-tools.git"
```

Se qualcosa manca, `casoweb` se ne accorge e suggerisce il comando adatto a
come è stato installato e alla presenza o meno della GPU.

### Processori meno recenti

Le librerie precompilate di `separa-parlato` richiedono istruzioni che i
processori più vecchi non hanno: AVX per TensorFlow (la segmentazione),
SSE4.1 per Whisper (CTranslate2), il livello x86-64-v2 per NumPy 2.4 e
successivi. Su un PC più vecchio `casoweb` se ne accorge prima di partire e
lo spiega, invece di interrompersi con "Illegal instruction". Senza AVX
(processori all'incirca fino al 2011) si può comunque trascrivere,
preparando la segmentazione su un altro PC: vedi
[docs/separa-parlato.md](docs/separa-parlato.md#requisiti-del-processore).
Per controllare il proprio processore:

```bash
grep -o -w -E 'ssse3|sse4_1|sse4_2|popcnt|avx2?' /proc/cpuinfo | sort -u
```

`video-da-audio` e gli altri comandi leggeri non hanno questi requisiti.

### Aggiornare e disinstallare

```bash
uv tool upgrade casoweb-tools
uv tool uninstall casoweb-tools
```

### Sviluppo locale

```bash
git clone https://github.com/titobrasolin/casoweb-tools.git
cd casoweb-tools
uv venv -p 3.12
uv pip install --overrides scripts/overrides-cpu.txt -e '.[dev,separa-parlato,cpu]'   # oppure solo '.[dev]'
.venv/bin/pytest
```

---

## 🛠️ Utilizzo

```bash
casoweb --help
casoweb <comando> --help
casoweb -v DEBUG <comando> ...   # mostra ogni passo: librerie caricate, programmi lanciati
```

Con `-v WARNING` restano solo avvisi ed errori.

### Comandi disponibili

| Comando | Cosa fa | Articolo |
|---|---|---|
| `casoweb separa-parlato` | Separa parlato e musica in una registrazione (trasmissione radio, podcast, conferenza...), produce l'audio col solo parlato e quello con la musica silenziata, e i rispettivi sottotitoli `.srt` (faster-whisper, GPU se disponibile). | [Dall'intervista radio ai sottotitoli, senza la musica](https://www.casoweb.eu/?view=article&id=307&catid=90) |
| `casoweb pulisci-audio` | Ripulisce l'audio di una registrazione in presa diretta (rumore, rimbombo, volume), con trattamenti diversi per parlato e musica; motore ffmpeg o DeepFilterNet. Vedi [docs/pulisci-audio.md](docs/pulisci-audio.md). | in preparazione |
| `casoweb video-da-audio` | Trasforma un audio con i suoi sottotitoli in video con sfondo fisso, in più profili: YouTube, web, WhatsApp (sotto i 16 MB per mezz'ora), quadrato e verticale. | idem |
| `casoweb video-per-canon` | Converte un video qualsiasi nella coppia `MVI_NNNN.AVI` + `.THM` che una vecchia Canon PowerShot riproduce sul proprio display (provato sulla A2000 IS in tutte e quattro le modalità video: Compact 160x120 e Standard 320x240, 640x480 e 640x480 LP). Richiede ffmpeg; Pillow si installa con il pacchetto. | in preparazione |

Flusso tipico:

```bash
casoweb separa-parlato registrazione.webm --nomi "Mario Rossi, Anna Bianchi"
# eventualmente: correggere registrazione_output/registrazione_segmenti.tsv e rilanciare
casoweb video-da-audio registrazione_output/registrazione_parlato.mp3 --immagine locandina.jpg
```

Dettagli in [docs/separa-parlato.md](docs/separa-parlato.md) e [docs/video-per-canon.md](docs/video-per-canon.md).

---

## 📁 Struttura del Progetto

```text
casoweb-tools/
├── src/
│   └── casoweb_tools/
│       ├── cli.py              # gruppo click `casoweb`
│       ├── cmds/               # un sottocomando per esperimento (solo interfaccia)
│       ├── ambiente.py         # come è installato casoweb, per i messaggi d'errore
│       ├── separa_parlato.py   # logica, importata solo quando serve
│       ├── video_da_audio.py
│       ├── canon_avi.py        # contenitore AVI nello stile Canon PowerShot
│       └── video_per_canon.py
├── tests/
├── docs/             # note sugli esperimenti e bozze per gli articoli
├── scripts/          # script e file di supporto non Python
├── pyproject.toml
├── LICENSE
└── README.md
```

I moduli in `cmds/` non importano dipendenze pesanti: `casoweb --help` funziona
anche senza gli extra installati.

---

## 📄 Licenza

Questo progetto è distribuito con licenza **MIT**. Consulta il file [LICENSE](LICENSE) per ulteriori dettagli.
