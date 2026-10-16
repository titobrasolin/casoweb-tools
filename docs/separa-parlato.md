# Dall'intervista radio ai sottotitoli, senza la musica

Note tecniche per `casoweb separa-parlato` e `casoweb video-da-audio`.
Il racconto dell'esperimento è nell'articolo
[su casoweb.eu](https://www.casoweb.eu/?view=article&id=307&catid=90); qui ci
sono i dettagli che cambiano insieme al codice.

## `casoweb separa-parlato`

```bash
casoweb separa-parlato registrazione.webm --nomi "Mario Rossi, Anna Bianchi"
```

Accanto al file crea `registrazione_output/` con:

| File | Contenuto |
|---|---|
| `<nome>_segmenti.tsv` | segmentazione parlato/musica, **modificabile a mano** |
| `<nome>_trascrizione.json` | parole trascritte con i tempi della registrazione originale (cache) |
| `<nome>_parlato.mp3` + `.srt` | solo il parlato, musica tagliata |
| `<nome>_silenziato.mp3` + `.srt` | durata originale, musica a volume zero |

### Le fasi

1. **Segmentazione** con [inaSpeechSegmenter](https://github.com/ina-foss/inaSpeechSegmenter).
   Le etichette grezze (`speech`, `music`, `noise`, `noEnergy`) diventano
   soltanto *parlato* e *musica*: una pausa breve tra due parlati resta
   parlato, il parlato più corto di `--min-parlato` in mezzo alla musica (un
   verso cantato) diventa musica, la musica più corta di `--min-musica` in
   mezzo al parlato (un jingle) diventa parlato.
2. **Margine**: ogni tratto di parlato si allarga di `--padding` secondi
   (0,3 predefiniti), così non si perdono l'attacco e la coda delle parole.
3. **Trascrizione** con [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
   solo del parlato. I nomi passati con `--nomi` vanno nel prompt iniziale.
4. **Audio**: taglio e silenziamento in un solo passaggio, a blocchi di 10
   secondi, con brevi dissolvenze a ogni giuntura. La memoria usata non
   dipende dalla durata della registrazione.
5. **Sottotitoli**: si costruiscono dalle singole parole e non attraversano
   mai un taglio. Si regolano con `--max-car`, `--max-riga` e `--max-durata`.

### Revisione a mano

```bash
casoweb separa-parlato registrazione.webm --solo-segmenti
# correggere la colonna "etichetta" (parlato | musica) del TSV
casoweb separa-parlato registrazione.webm
```

A ogni rilancio il TSV esistente prevale sulla segmentazione automatica
(`--risegmenta` per rifarla). La trascrizione è **incrementale**: Whisper
lavora solo sulle parti diventate "parlato" che non sono ancora in cache,
quindi dopo una correzione i sottotitoli si rigenerano in pochi secondi.
Con `--no-audio` si rigenerano soltanto gli SRT.

### GPU e prestazioni

- `--device auto` (predefinito) usa la GPU NVIDIA se CTranslate2 la vede,
  dopo una prova a vuoto che fa emergere subito eventuali problemi di
  cuBLAS e cuDNN; altrimenti usa il processore.
- `--compute-type auto` sceglie la precisione migliore supportata dalla GPU
  (int8 su CPU). Su schede Pascal come la GTX 1060 può convenire forzare
  `--compute-type int8`.
- Riferimenti misurati per l'articolo, con il modello `large-v3-turbo` su
  31 minuti di parlato: circa 40 minuti con un i5-5200U, meno di 2 con una
  GTX 1060.

Il modello si può scaricare in anticipo:

```bash
HF_HUB_DISABLE_XET=1 uvx --from huggingface_hub hf download mobiuslabsgmbh/faster-whisper-large-v3-turbo
```

## `casoweb video-da-audio`

```bash
casoweb video-da-audio registrazione_output/registrazione_parlato.mp3 --immagine locandina.jpg
casoweb video-da-audio --elenca-profili
```

Se manca `--srt` usa il file `.srt` con lo stesso nome dell'audio. Per ogni
profilo (`--profili youtube_1080p,web_720p,whatsapp_480p` per impostazione
predefinita) produce un `.mp4` con immagine fissa (o sfondo nero),
sottotitoli incorporati (`--soft` per averli come traccia attivabile),
mezzo secondo di "respiro" e dissolvenze a inizio e fine.

Con un'immagine fissa la voce più costosa sono i fotogrammi chiave: il
profilo WhatsApp ne mette uno ogni 30 secondi, a 5 fotogrammi al secondo, e
resta sotto i 16 MB per circa mezz'ora. I parametri usati finiscono in
`<nome>_params.json` e si riusano con `--load-params`.

## Origine

Importato dal repository privato `toolbox` (`media/intervista_radio.py` e
`media/audio_a_video.py`). Della prima versione sviluppata in parallelo
(`intervista_radio_v1.py`) sono confluiti l'elaborazione audio a blocchi, il
margine attorno al parlato, la selezione della prima traccia audio, la prova
a vuoto della GPU, `--compute-type`, `--solo-segmenti`, `--modo`, `--bitrate`
e i limiti configurabili dei sottotitoli.
