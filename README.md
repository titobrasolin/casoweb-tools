# casoweb-tools

Raccolta di utility, script di servizio e piccoli esperimenti sviluppati per la rubrica **[Tecnologia @ CASO](https://www.casoweb.eu/?view=category&id=90)** su *casoweb.eu*.

Ogni esperimento è un sottocomando di `casoweb`.

---

## 🚀 Installazione

Serve [`uv`](https://docs.astral.sh/uv/) (consigliato) oppure `pipx`, più `ffmpeg` di sistema:

```bash
sudo apt install ffmpeg
```

### Solo i comandi leggeri

```bash
uv tool install git+https://github.com/titobrasolin/casoweb-tools.git
# oppure
pipx install git+https://github.com/titobrasolin/casoweb-tools.git
```

### Con `separa-parlato` (Whisper e inaSpeechSegmenter)

Le dipendenze di `separa-parlato` sono pesanti e stanno nell'extra `separa-parlato`
(Python 3.10–3.12).

**PC con scheda NVIDIA**: le librerie CUDA arrivano insieme a TensorFlow e
vengono usate anche da Whisper.

```bash
uv tool install --python 3.12 "casoweb-tools[separa-parlato] @ git+https://github.com/titobrasolin/casoweb-tools.git"
```

**PC senza GPU NVIDIA**: con l'extra `cpu` e il file di override si evitano
oltre 5 GB di librerie `nvidia-*` inutili (circa 2 GB in tutto invece di 7).

```bash
curl -LO https://raw.githubusercontent.com/titobrasolin/casoweb-tools/main/scripts/overrides-cpu.txt
uv tool install --python 3.12 --overrides overrides-cpu.txt \
    "casoweb-tools[separa-parlato,cpu] @ git+https://github.com/titobrasolin/casoweb-tools.git"
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
```

### Comandi disponibili

| Comando | Cosa fa | Articolo |
|---|---|---|
| `casoweb separa-parlato` | Separa parlato e musica in una registrazione (trasmissione radio, podcast, conferenza...), produce l'audio col solo parlato e quello con la musica silenziata, e i rispettivi sottotitoli `.srt` (faster-whisper, GPU se disponibile). | [Dall'intervista radio ai sottotitoli, senza la musica](https://www.casoweb.eu/?view=article&id=307&catid=90) |
| `casoweb video-da-audio` | Trasforma un audio con i suoi sottotitoli in video con sfondo fisso, in più profili: YouTube, web, WhatsApp (sotto i 16 MB per mezz'ora), quadrato e verticale. | idem |

Flusso tipico:

```bash
casoweb separa-parlato registrazione.webm --nomi "Mario Rossi, Anna Bianchi"
# eventualmente: correggere registrazione_output/registrazione_segmenti.tsv e rilanciare
casoweb video-da-audio registrazione_output/registrazione_parlato.mp3 --immagine locandina.jpg
```

Dettagli in [docs/separa-parlato.md](docs/separa-parlato.md).

---

## 📁 Struttura del Progetto

```text
casoweb-tools/
├── src/
│   └── casoweb_tools/
│       ├── cli.py              # gruppo click `casoweb`
│       ├── cmds/               # un sottocomando per esperimento (solo interfaccia)
│       ├── separa_parlato.py   # logica, importata solo quando serve
│       └── video_da_audio.py
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
