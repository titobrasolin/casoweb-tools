# Video sul display di una vecchia Canon PowerShot

Note tecniche per `casoweb video-per-canon`, nato da un esperimento con una
Canon PowerShot A2000 IS (2008, DIGIC III): far vedere sul display della
fotocamera, nella sua galleria, foto e video copiati sulla scheda dal PC.

## Cosa si è scoperto

**Foto.** Basta il nome giusto. Una foto copiata in `DCIM/1NNCANON/` con un
nome `IMG_NNNN.JPG` che rispetta la numerazione viene mostrata, anche se il
catalogo `DCIM/CANONMSC/M0NNN.CTG` non la elenca: la fotocamera non ne ha
bisogno per la galleria.

**Video.** Servono due file con lo stesso numero:

| File | Cosa contiene |
|---|---|
| `MVI_NNNN.AVI` | Motion JPEG 4:2:2 e audio PCM 8 bit mono a **11024 Hz** (non 11025), in un AVI costruito esattamente come quelli della fotocamera |
| `MVI_NNNN.THM` | JPEG 160x120, l'anteprima della galleria, con un blocco Exif Canon che contiene anche il **numero di fotogrammi** |

Senza il THM il video non compare. Se il THM non concorda con l'AVI, il
video compare ma non parte. La durata mostrata dalla galleria viene dal
THM, non dall'AVI.

**Numeri.** Da 0001 a 9999, non usati da nessun altro file della cartella:
foto e video condividono la numerazione. La galleria segue l'ordine dei
numeri.

## Perché ffmpeg non basta

Anche un semplice `ffmpeg -i MVI_1259.AVI -c copy`, che copia fotogrammi e
audio identici, produce un file che la fotocamera rifiuta ("Immagine non
identificata"). A non andare è il contenitore. Un AVI Canon ("CanonMVI06")
ha:

- un'intestazione fissa di 2 KB: il blocco `LIST movi` inizia sempre a
  `0x800`, con un `JUNK` di riempimento;
- l'audio a blocchi di esattamente un secondo (11024 byte), ciascuno
  seguito da tanti fotogrammi quanti sono gli fps;
- l'indice vero nei blocchi OpenDML `ix00`/`ix01` in fondo ai dati, mentre
  `idx1` ha solo due voci (il primo blocco audio e il primo fotogramma).
  ffmpeg fa il contrario;
- la durata del fotogramma troncata (66666 µs a 15 fps), la data nel blocco
  `IDIT` e il programma "CanonMVI06".

Anche i fotogrammi sono diversi da quelli di ffmpeg: marcatore `AVI1` al
posto di `JFIF`, un `DRI` e nessuna tabella Huffman (si usano quelle
standard del JPEG).

`casoweb_tools.canon_avi` riproduce questa struttura: rigenerando un AVI
della fotocamera dai suoi fotogrammi e dal suo audio si ottiene un file
**identico byte per byte**. Lo si può controllare su qualunque AVI della
propria fotocamera:

```bash
casoweb video-per-canon --verifica MVI_1259.AVI
```

## Uso

```bash
casoweb video-per-canon video.mp4 --numero 1266 --thm-modello MVI_1259.THM \
    --cartella /media/$USER/SCHEDA/DCIM/101CANON
```

- `--thm-modello` è un `.THM` qualsiasi prodotto dalla fotocamera: lo
  script ne riusa il blocco Exif, aggiornando date, numero del file
  (cartella × 10000 + numero, es. 1011266) e numero di fotogrammi. Così nel
  codice non c'è nessun dato di una fotocamera specifica.
- `--cartella-fotocamera` (101 per `101CANON`) entra nel numero scritto nel
  THM.
- Lo script alza la compressione finché il fotogramma più pesante resta
  sotto il limite del profilo (circa 5 KB nel 160x120, come nei file
  Canon), e stampa dimensione media e massima.
- Copiare sempre AVI e THM insieme.

## Stato delle verifiche

| | Esito |
|---|---|
| Foto copiata e rinominata | si vede |
| Catalogo `.CTG` | non serve, né per le foto né per i video |
| AVI rimuxato da ffmpeg | rifiutato |
| AVI di `canon_avi` con fotogrammi Canon e THM coerente | si riproduce |
| Video qualsiasi convertito, profilo `compatto` (160x120, 15 fps) | si riproduce |
| THM con numero del file non aggiornato | si riproduce: quel campo non è determinante |
| THM con numero di fotogrammi diverso dall'AVI | compare ma non parte |
| Profili `320x240` e `640x480` a 30 fps | **da verificare**: struttura dedotta, non provata |
| Modalità LP | da esplorare |

Resta aperto se l'arresto dopo 5 secondi osservato con fotogrammi da 6-7 KB
dipendesse dalla loro dimensione o dai metadati incoerenti, perché nella
prova successiva sono cambiate entrambe le cose. Per prudenza il limite
resta.

Per estendere i profili serve un esemplare vero di ciascuna modalità
(bastano pochi secondi di AVI e THM): se `--verifica` lo rigenera identico,
la struttura è confermata.
