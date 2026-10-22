# casoweb pulisci-audio

Ripulisce l'audio di una registrazione in presa diretta (anche dentro un video):
rumore di fondo, code di rimbombo, volume della voce. Nato per le riprese "di
fortuna" in una sala, con musica dal vivo alternata a interventi al microfono.

## Perché parlato e musica vanno trattati in modo diverso

Un denoiser pensato per la voce, applicato a una canzone, ne mangia le armoniche
e il suono diventa "acquoso". Per questo, con `--segmenti`, il trattamento forte
va solo ai tratti etichettati `parlato`, la musica riceve un semplice taglio dei
bassi, e al confine c'è una dissolvenza incrociata (`--dissolvenza`, 0,4 s).

## Uso

```bash
# 1) etichetta parlato e musica (produce <nome>_segmenti.tsv, correggibile a mano)
casoweb separa-parlato serata.mp4 --solo-segmenti

# 2) pulisci
casoweb pulisci-audio serata.mp4 --segmenti serata_segmenti.tsv
casoweb pulisci-audio serata.mp4 --segmenti serata_segmenti.tsv --motore deepfilternet --volume-finale
```

Se l'ingresso ha un video, viene copiato senza ricodificarlo e si sostituisce
solo l'audio. Il parlato ripulito esce in mono (copiato su due canali).

## Profili (`--profilo`)

| profilo | cosa fa |
|---|---|
| `leggero` | taglio dei bassi e un po' di riduzione del rumore |
| `sala` (predefinito) | + gate contro le code di rimbombo tra le parole + compressore |
| `forte` | come `sala`, più aggressivo; può suonare artificiale |

## Motori (`--motore`)

- `ffmpeg` (predefinito): nessuna installazione, risultati modesti.
- `deepfilternet`: rete neurale per il rumore. Serve il binario `deep-filter`
  ([Releases](https://github.com/Rikorose/DeepFilterNet/releases), niente
  PyTorch) oppure `deepFilter` del pacchetto Python (richiede prima PyTorch e
  torchaudio). Lavora a 48 kHz e solo su WAV; il programma converte da sé.
  `--post-filtro` lo rende più incisivo e più artificiale. Dopo il motore
  restano gate e compressore del profilo.

## Limiti

- Nessun vero anti-riverbero: ffmpeg non lo ha, e sulla musica dal vivo nessun
  filtro può fare miracoli. Il gate attenua le code tra una parola e l'altra.
- Se il TSV sbaglia un'etichetta, correggilo a mano: è un file di testo.
- Provare prima su un minuto (`ffmpeg -ss 600 -t 60 -i serata.mp4 prova.mp4`).
