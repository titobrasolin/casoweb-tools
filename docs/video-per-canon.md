# Video sul display di una vecchia Canon PowerShot

Note tecniche per `casoweb video-per-canon`, nato da un esperimento con una
Canon PowerShot A2000 IS (2008, DIGIC III): far vedere sul display della
fotocamera, nella sua galleria, foto e video copiati sulla scheda dal PC.

Ogni affermazione qui sotto ha una fonte, indicata così:

- **[prova]**: provato sulla fotocamera;
- **[ispezione]**: ricavato analizzando file prodotti dalla fotocamera;
- **[Canon]**: documentazione ufficiale (vedi [Fonti](#fonti));
- **[DCF]**: standard DCF, cui Canon dichiara la fotocamera conforme, ma
  non verificato sulla fotocamera;
- **[deduzione]**: ragionamento non ancora verificato.

## Cosa sa fare la fotocamera

| Modalità | Risoluzione | fps | Durata massima di un clip | Stato in `video-per-canon` |
|---|---|---|---|---|
| Compact | 160x120 | 15 | circa 3 minuti | profilo `compatto`, verificato **[ispezione]** **[prova]** |
| Standard | 320x240 | 30 | circa 1 ora o 4 GB | profilo `320x240`, struttura verificata **[ispezione]** |
| Standard | 640x480 | 30 | circa 1 ora o 4 GB | profilo `640x480`, struttura verificata **[ispezione]** |
| Standard LP | 640x480 | 30 | circa 1 ora o 4 GB | nessun profilo: qualità più bassa e durata doppia **[Canon]**; stesso AVI dello Standard con fotogrammi più compressi **[ispezione]** |

Modalità, risoluzioni, durate e la descrizione di LP vengono dalla guida
dell'utente, pp. 90-91 **[Canon]**. Il formato è AVI con video Motion JPEG
e audio WAVE mono a 8 bit **[Canon]**; la frequenza, che Canon arrotonda a
"11 kHz", è in realtà **11024 Hz** **[ispezione]**.

## Come la fotocamera trova i file

**Numeri e cartelle.** La fotocamera numera i file da 0001 a 9999 e li
salva in cartelle di al massimo 2000 immagini **[Canon]** (guida, p. 119),
chiamate `1NNCANON` sotto `DCIM` **[ispezione]**. Foto e video condividono
la numerazione **[ispezione]**: un numero va scelto tra quelli non usati da
nessun file della cartella. Che la galleria segua l'ordine dei numeri è
**[DCF]**.

**Foto.** Basta il nome giusto: una foto della fotocamera, duplicata e
rinominata con un numero libero, compare nella galleria **[prova]**.

**Catalogo.** `DCIM/CANONMSC/M0NNN.CTG` elenca i file della cartella, ma
non serve né per le foto né per i video **[prova]**.

**Video.** Servono due file con lo stesso numero **[prova]**:

| File | Cosa contiene |
|---|---|
| `MVI_NNNN.AVI` | il filmato, in un AVI costruito esattamente come quelli della fotocamera |
| `MVI_NNNN.THM` | JPEG 160x120 con un blocco Exif Canon **[ispezione]**: è il file di miniatura previsto dallo standard **[DCF]** |

- Senza il THM il video non compare **[prova]**.
- La durata che la galleria mostra viene dal THM, che contiene anche il
  numero di fotogrammi **[ispezione]** **[prova]**.
- Se il THM non concorda con l'AVI, il video compare ma non parte o si
  ferma **[prova]**.
- Il numero del file scritto nel THM non è determinante **[prova]**.

Confrontando i THM di un clip per modalità (Standard 640, Standard 640 LP,
Standard 320, Compact) **[ispezione]**:

| Dove, nel MakerNote Canon | Cosa contiene |
|---|---|
| tabella `0x0011`, valori 2 e 4 | numero di fotogrammi |
| tabella `0x0011`, valori 1, 6 e 9 | fps (30 o 15) |
| tabella `0x0011`, valore 8 | 2 in Compact, 0 in Standard |
| tabella `0x0011`, valore 19 | 1 in Standard LP, 0 altrimenti |
| tabella `0x0026`, valori 4 e 5 | larghezza e altezza del video |

`video-per-canon` aggiorna solo il numero di fotogrammi, quindi il THM
modello va preso da un clip della stessa modalità del profilo: con un THM
Compact per un video a 30 fps la durata mostrata sarebbe doppia
**[deduzione]**.

Il messaggio "Immagine non identificata" è quello che la guida prevede per
immagini "manipolate su un computer, rinominate o riprese con un'altra
fotocamera" **[Canon]** (p. 93 e risoluzione dei problemi): la fotocamera
non garantisce di mostrare file che non ha prodotto lei. Quello che segue
la convince che li ha prodotti lei.

## Perché ffmpeg non basta

Anche un semplice `ffmpeg -i MVI_1259.AVI -c copy`, che copia fotogrammi e
audio identici, produce un file che la fotocamera rifiuta **[prova]**. A non
andare è il contenitore. Un AVI Canon ("CanonMVI06") ha **[ispezione]**:

- un'intestazione fissa di 2 KB: il blocco `LIST movi` inizia sempre a
  `0x800`, con un `JUNK` di riempimento;
- l'audio a blocchi di esattamente un secondo (11024 byte), ciascuno
  seguito da tanti fotogrammi quanti sono gli fps;
- l'indice vero nei blocchi OpenDML `ix00`/`ix01` in fondo ai dati, mentre
  `idx1` ha solo due voci (il primo blocco audio e il primo fotogramma).
  ffmpeg fa il contrario;
- la durata del fotogramma troncata (66666 µs a 15 fps), la data nel blocco
  `IDIT` e il programma "CanonMVI06";
- l'audio lungo quanto il video, arrotondato per eccesso: 80 fotogrammi a
  30 fps danno 29397,33 campioni, e l'ultimo blocco porta il totale a 29398.

Anche i fotogrammi sono diversi da quelli di ffmpeg **[ispezione]**:
marcatore `AVI1` al posto di `JFIF`, un `DRI` e nessuna tabella Huffman (si
usano quelle standard del JPEG).

`casoweb_tools.canon_avi` riproduce questa struttura: rigenerando un AVI
della fotocamera dai suoi fotogrammi e dal suo audio si ottiene un file
**identico byte per byte**, in tutte e tre le modalità Compact, 320x240 e
640x480 **[ispezione]**. A 30 fps ogni blocco ha un secondo di audio e 30
fotogrammi.

## Uso

```bash
casoweb video-per-canon video.mp4 --numero 1266 --thm-modello MVI_1259.THM \
    --cartella /media/$USER/SCHEDA/DCIM/101CANON
```

- `--thm-modello` è un `.THM` prodotto dalla fotocamera nella stessa
  modalità del profilo (vedi sopra): lo script ne riusa il blocco Exif, aggiornando date, numero del file
  (cartella × 10000 + numero, es. 1011266) e numero di fotogrammi. Così nel
  codice non c'è nessun dato di una fotocamera specifica.
- `--cartella-fotocamera` (101 per `101CANON`) entra nel numero scritto nel
  THM.
- Lo script alza la compressione finché il fotogramma più pesante resta
  sotto il limite del profilo: circa 5 KB nel 160x120, come nei file Canon
  **[ispezione]**. Stampa poi dimensione media e massima.
- Avvisa quando il video supera la durata massima che la fotocamera
  registrerebbe in quella modalità (3 minuti in Compact) o i 4 GB. Non si
  sa se la fotocamera rifiuti un file più lungo **[deduzione]**: l'avviso
  serve a non scoprirlo per caso.
- Copiare sempre AVI e THM insieme.

## Stato delle verifiche

| | Esito |
|---|---|
| Foto copiata e rinominata | si vede **[prova]** |
| Catalogo `.CTG` | non serve **[prova]** |
| AVI rimuxato da ffmpeg | rifiutato **[prova]** |
| AVI di `canon_avi` con fotogrammi Canon e THM coerente | si riproduce **[prova]** |
| Video qualsiasi convertito, profilo `compatto` | si riproduce **[prova]** |
| THM con numero del file non aggiornato | si riproduce **[prova]** |
| THM con numero di fotogrammi diverso dall'AVI | compare ma non parte **[prova]** |
| Struttura AVI dei profili `320x240` e `640x480` | rigenerata identica da file Canon **[ispezione]** |
| Video convertito, profili `320x240` e `640x480` | da provare sulla fotocamera |
| Modalità LP | AVI identico allo Standard con fotogrammi circa a metà (30 KB invece di 61 KB); il THM ha un indicatore LP **[ispezione]**. Nessun profilo per ora |
| Clip più lunghi della durata massima | da provare |

Resta aperto se l'arresto dopo 5 secondi osservato con fotogrammi da 6-7 KB
dipendesse dalla loro dimensione o dai metadati incoerenti, perché nella
prova successiva sono cambiate entrambe le cose. Per prudenza il limite
resta.

Per confermare un profilo serve un esemplare vero di quella modalità
(bastano pochi secondi di AVI e THM): se

```bash
casoweb video-per-canon --verifica MVI_NNNN.AVI
```

lo rigenera identico, la struttura è confermata.

## Fonti

- Canon, *PowerShot A2000 IS Camera User Guide* (CDI-E388-010, 2008),
  [PDF in inglese sul sito di download Canon](http://gdlp01.c-wss.com/gds/5/0300001235/01/PSA2000IS_CUG_EN.pdf):
  modalità video (p. 90), durate e limiti dei clip (p. 91), numerazione dei
  file e cartelle (p. 119), messaggi per i file non riconosciuti (p. 93 e
  risoluzione dei problemi). In italiano dalla
  [pagina di supporto Canon Italia](https://www.canon.it/support/consumer/products/cameras/powershot/a-series/powershot-a2000-is.html?type=manuals).
- Canon, [specifiche della PowerShot A2000 IS](https://in.canon/en/support/6200025000):
  formati, modalità video e conformità DCF e DPOF.
- [Design rule for Camera File system](https://en.wikipedia.org/wiki/Design_rule_for_Camera_File_system)
  (Wikipedia): cartelle, nomi dei file ed estensione THM per le miniature.
