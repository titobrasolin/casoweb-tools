# casoweb trova-offset

Trova in che istante di una registrazione lunga comincia un estratto della stessa
trasmissione. Caso d'uso: due ore di stream radiofonico registrato con VLC e, per
altra via, un'ora della stessa trasmissione: dove si colloca l'estratto?

```bash
casoweb trova-offset estratto.mp3 registrazione_completa.mp3
casoweb trova-offset estratto.mp3 registrazione_completa.mp3 --cerca-da 3600 --json
```

L'uscita dà l'istante di inizio e di fine (`HH:MM:SS.mmm`), i secondi e il
fotogramma corrispondente (`--fps`, 25 per default): utile per allineare le due
tracce su una timeline di Shotcut.

## Come funziona

1. **Impronta grossolana.** Ogni 20 ms si calcola l'energia in 24 bande di
   frequenza (in dB) su audio a 8 kHz. Il confronto con la FFT tra l'impronta di un
   campione dell'estratto e quella della registrazione dà il candidato in
   pochi secondi. È l'impronta, non la forma d'onda, a rendere il metodo robusto a
   codifiche e volumi diversi (MP3 contro AAC, un file più forte dell'altro...).
2. **Raffinamento.** Intorno al candidato si correla la forma d'onda: la
   precisione passa da 20 ms a una frazione di millisecondo.
3. **Controllo.** Si usano tre campioni da 30 s (al 10%, 50% e 90% dell'estratto,
   `--sonde`, `--durata-sonda`). Se danno lo stesso istante la corrispondenza è
   affidabile; se divergono (`scarto`), l'estratto ha tagli o una velocità diversa.

Su due ore di audio sintetico l'elaborazione richiede qualche secondo e meno di
700 MB di memoria; la correlazione diretta dei campioni ne richiederebbe molti di più.

## Avvisi

- *somiglianza bassa*: l'estratto potrebbe non appartenere alla registrazione;
- *più posizioni quasi equivalenti*: jingle o brani ripetuti; restringi la ricerca
  con `--cerca-da` e `--cerca-fino`;
- *i campioni divergono*: tagli o deriva di velocità; in tal caso l'offset è
  valido solo vicino ai punti dove i campioni concordano.

Richiede ffmpeg e numpy.
