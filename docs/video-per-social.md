# Una ripresa di fortuna, in bianco e nero e battuta a macchina

Note per `casoweb video-per-social`, nato da una ripresa fatta con il
telefono dalla platea: i saluti finali al finissage di una mostra di
fotografie in bianco e nero, al Caffè Pedrocchi di Padova. Nell'inquadratura
entravano il bordo di un tavolo, altri ospiti e il pubblico. L'obiettivo era
una clip breve per il sito e i social, con un'impronta volutamente grezza
e coerente con la mostra.

## Flusso

```bash
# 1. guardare i fotogrammi e scegliere taglio e ritaglio (vedi sotto)
ffmpeg -i serata.mp4 -vf "fps=12/40,scale=960:-2" fotogramma_%02d.png

# 2. sottotitoli e audio ripulito, con i tempi della ripresa intera
casoweb separa-parlato serata.mp4 --modo silenzia
casoweb pulisci-audio serata.mp4 --profilo sala -o serata_pulito.m4a

# 3. la clip
casoweb video-per-social serata.mp4 --inizio 4.5 --fine 33 --ritaglio 378:672:571:20 \
    --srt serata_output/serata_silenziato.srt --audio serata_pulito.m4a \
    --titolo "PROVING GROUNDS" --titolo "finissage, Caffè Pedrocchi" --nomi nomi.tsv

# 4. correggere a mano i testi generati e rifare solo il video
casoweb video-per-social serata.mp4 --inizio 4.5 --fine 33 --ritaglio 378:672:571:20 \
    --audio serata_pulito.m4a --ass serata_social_9x16.ass
```

I tempi dell'SRT, di `--nomi` e dell'audio ripulito sono quelli della
ripresa intera: è `video-per-social` a spostarli di `--inizio`. Per questo
conviene `separa-parlato --modo silenzia`, che non taglia nulla.

**Audio saturato.** Nella ripresa di partenza (telefono in platea, circa
−8 LUFS, picchi oltre 0 dBFS) `separa-parlato` ha prodotto un SRT vuoto: il
filtro VAD di faster-whisper non riconosceva come voce nessun tratto. Senza
VAD e con l'ingresso abbassato di 8 dB Whisper trascriveva, ma a frammenti
e con nomi poco credibili. Con riprese così l'SRT va controllato parola per
parola, e i nomi vanno scritti a mano.

## Scegliere il ritaglio

Il ritaglio è fisso, per scelta: se chi parla sta fermo, un'inquadratura
che lo segue tremola più di una ferma. Prima di sceglierlo vanno guardati
alcuni fotogrammi lungo tutta la clip, per due motivi:

- **chi entra nell'inquadratura**: ospiti che passano davanti, pubblico
  riconoscibile. Spesso basta tagliare inizio e fine;
- **i bordi neri della stabilizzazione**: uno stabilizzatore che ruota
  l'immagine lascia triangoli neri negli angoli, anche molto grandi. Il
  ritaglio deve evitarli per tutta la clip, e questo può limitare l'altezza
  utile più della risoluzione.

Nella ripresa da cui è nato il comando (1920x1080, stabilizzata) il
riquadro 9:16 più alto senza nero era di 387x688 pixel: niente figura
intera, ma un piano americano, dalla testa a metà coscia.

**Risoluzione d'uscita.** Sotto i 700 pixel di altezza ritagliata il video
esce a 720 sul lato corto (720x1280 in verticale), altrimenti a 1080:
ingrandire di più non aggiunge dettaglio, solo peso. Oltre 2x di
ingrandimento il comando avvisa.

| Formato | Uscita (sotto i 700 / da 700 in su) | Margine dei sottotitoli |
|---|---|---|
| 9:16, storie e reel | 720x1280 / 1080x1920 | 14% dal basso, sopra i comandi dell'app |
| 4:5, feed | 864x1080 / 1080x1350 | 8% |
| 16:9, sito | 1280x720 / 1920x1080 | 6% |

## Il look

Catena di filtri ffmpeg, nell'ordine:

1. **Bianco e nero** con `colorchannelmixer`: rosso 0,40, verde 0,45, blu
   0,15. Rispetto alla luminanza video (0,30 / 0,59 / 0,11) dà un po' più
   di peso al rosso, come una pellicola pancromatica: la pelle viene più
   chiara.
2. **Curva a S** (`curves`), regolata da `--contrasto` da 0 a 1: chiude un
   po' i neri, apre i bianchi e allarga i mezzitoni.
3. **Vignettatura** (`vignette`).
4. **Testi** (`ass`), prima della grana, così la grana li "sporca" come se
   fossero stampati.
5. **Grana** (`noise`) solo sulla luminanza e diversa a ogni fotogramma.
   Applicata a tutti i canali darebbe puntini colorati in un video che
   dovrebbe essere in bianco e nero.
   - `pellicola`: disturbo gaussiano;
   - `fotocopia`: disturbo uniforme, più ruvido, con contrasto più alto e
     una maschera di contrasto (`unsharp`) per i bordi duri del toner.
6. **Dissolvenze** dal nero e verso il nero.

**Il peso.** Una grana che cambia a ogni fotogramma non si comprime: a
`crf` 20 senza limiti la prova di 6 secondi pesava 30 MB, oltre 40 Mbit/s.
Con `crf` 23, `-tune grain` e un tetto di 8 Mbit/s si scende a circa
6,5 MB per 6 secondi. I social ricomprimono comunque: la grana fine si
perde in parte, e conviene tenerla un po' più marcata di come la si
vorrebbe.

## La macchina da scrivere

Un sottotitolo diventa una serie di eventi ASS, uno per ogni carattere
battuto. In ogni evento il testo è intero, ma la parte non ancora battuta
è resa trasparente con `{\alpha&HFF&}`:

```text
Dialogue: 0,0:00:01.00,0:00:01.06,Sottotitoli,,0,0,0,,Gra{\alpha&HFF&}zie a tutti\Nper essere qui
Dialogue: 0,0:00:01.06,0:00:01.11,Sottotitoli,,0,0,0,,Graz{\alpha&HFF&}ie a tutti\Nper essere qui
```

Così libass impagina sempre lo stesso testo e le righe non si spostano
mentre crescono, come farebbero con il solo testo già battuto.
Con lo stile `carta` (`BorderStyle=3`, un riquadro al posto del contorno)
anche la striscia chiara dietro al testo si allunga lettera per lettera:
il riquadro prende la trasparenza dei caratteri.

- **Velocità**: `--velocita` caratteri al secondo (18 di default), ma la
  battitura non occupa mai più del 60% della durata del sottotitolo: il
  resto del tempo il testo si legge per intero.
- **Righe corte**: al massimo circa 27 caratteri a 720 di larghezza (font
  al 5% della larghezza), due righe per volta. Un sottotitolo più lungo
  si divide in più schermate, con il tempo distribuito in proporzione ai
  caratteri.
- **Titolo** (`--titolo`, una riga per opzione): in alto a sinistra, come
  la didascalia di una mostra, per `--durata-titolo` secondi.
- **Nomi** (`--nomi`): un file con una riga `tempo<TAB>testo` per nome. Il
  nome si batte in alto a sinistra, sotto il titolo, nel momento in cui
  viene pronunciato, anche se il titolo è ancora sullo schermo. I nomi
  ravvicinati (meno di `--durata-nome` secondi l'uno dall'altro) si
  impilano come titoli di coda e restano insieme fino a `--durata-nome`
  secondi dopo l'ultimo; il gruppo successivo riparte dalla prima riga.
  Titolo e nomi vanno tenuti brevi: nella clip di partenza, con un titolo
  di cinque righe, i nomi scendevano sul viso di chi parlava; con due
  righe di titolo tre nomi stanno sopra la testa.
- Graffe e barre rovesciate nel testo diventano parentesi e barre normali:
  un SRT non può inserire comandi ASS.

L'ASS generato resta accanto al video. Si corregge con un editor di testo
o con Aegisub e si riusa con `--ass`, che sostituisce `--srt`, `--titolo` e
`--nomi`.

**Font.** Il default è Liberation Mono, presente in quasi tutte le
distribuzioni Linux. Courier Prime (licenza OFL, su Google Fonts) è più
vicino a una vera macchina da scrivere: dopo averlo installato basta
`--font "Courier Prime"`. Se il font non c'è, libass ne sceglie un altro
senza errori: va controllato il risultato.

## Cosa non fa (ancora)

- **Seguire chi parla.** Per una persona che si sposta servirebbe un
  ritaglio che scorre solo in orizzontale, con zona morta e movimento lento
  (es. rilevamento dei volti con YuNet di OpenCV). Nella clip di partenza
  non serviva.
- **Cornice da provino a contatto** (bordo di pellicola con fori e numeri).
- **Coprire o sfocare** un volto che entra nell'inquadratura: per ora si
  evita con il taglio e il ritaglio.
