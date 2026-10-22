# casoweb rileva-suoni

Trova applausi (o altri suoni noti) in una registrazione, con YAMNet, e scrive
i tempi in due file accanto all'ingresso:

- `<nome>_suoni.srt`: sottotitoli, importabili in Shotcut o Kdenlive come segnaposto;
- `<nome>_suoni.tsv`: stesso formato di `separa-parlato` (l'etichetta è il nome
  della classe). Si passa a `casoweb pulisci-audio --escludi` perché gli applausi
  non vengano trattati come parlato.

```bash
casoweb rileva-suoni serata.mp4
casoweb rileva-suoni serata.mp4 -c Applause -c Laughter --soglia 0.15
casoweb rileva-suoni --elenca-classi
```

## Installazione

```bash
uv tool install --force 'casoweb-tools[rileva-suoni] @ git+https://github.com/titobrasolin/casoweb-tools.git'
```

L'extra porta `ai-edge-litert` (~20 MB) e numpy: niente TensorFlow (1,5-2 GB). Se
TensorFlow è già installato va bene anche quello. Il modello (`yamnet.tflite`,
pochi MB) si scarica da solo la prima volta in `~/.cache/casoweb`; con `--modello`
se ne indica uno già scaricato.

## Soglie e classi

I punteggi di YAMNet non sono probabilità e non sono calibrati tra le classi.
Un applauso scrosciante a scena aperta supera facilmente 0,35; quello finale,
magari sommesso e misto a voci, può stare sotto. Per questo il predefinito è
0,2 e si cercano insieme `Applause`, `Clapping` e `Cheering`. Se manca ancora
qualcosa, abbassa `--soglia`; se il rilevatore scatta troppo, alzala o usa
`--durata-min`. `--pausa-max` decide quanto devono distare due rilevamenti per
restare due eventi distinti.

## Dettagli

- L'audio è decodificato da ffmpeg a 16 kHz mono: va bene anche un video.
- Il modello TFLite analizza finestre fisse di 15.600 campioni (0,975 s), con
  passo di mezza finestra: la risoluzione dei tempi è di circa mezzo secondo.
- L'elenco delle 521 classi è dentro il file del modello.
