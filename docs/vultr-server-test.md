# Server di test "usa e getta" su Vultr

Questa guida attiva un server Ubuntu temporaneo su Vultr che:

1. registra in background lo stream radio
   `https://rastu91li2018.radioca.st/stream`, in file da un'ora;
2. ha già installato `casoweb` con l'extra `separa-parlato` (variante CPU),
   per ricavare i sottotitoli SRT dalle registrazioni, a mano o in automatico
   man mano che ogni ora si chiude.

## Piano consigliato

> ℹ️ Nomi dei piani, prezzi e condizioni di fatturazione di Vultr citati in
> questa guida sono quelli **al 30 ottobre 2026** e possono cambiare:
> prima di creare un'istanza conviene verificarli sulla
> [pagina dei prezzi](https://www.vultr.com/pricing/) o con
> `vultr-cli plans list`.

| Voce         | Valore                                          |
|--------------|-------------------------------------------------|
| Piano        | `vhf-1c-2gb` (High Frequency, 1 vCPU, 2 GB RAM) |
| Regione      | `fra` (Frankfurt), in alternativa `ams` o `cdg` |
| OS           | Ubuntu LTS, stessa versione della produzione    |
| Fatturazione | oraria, con tetto pari al prezzo mensile        |

Non usare il piano da 2,50 $ (solo IPv6): GitHub non è raggiungibile in
IPv6, quindi l'installazione da `git+https://github.com/...` fallirebbe.

> ⚠️ Un'istanza **spenta** continua a essere fatturata. Per smettere di
> pagare bisogna fare **Destroy**.

Il piano piccolo basta per registrare e per provare `separa-parlato` con un
modello Whisper leggero (`small`). Per elaborare con `large-v3-turbo`, o per
stare al passo con una registrazione continua, serve un piano con più vCPU e
RAM (vedi [sezione 5](#5-estrazione-srt-con-casoweb)).

## 1. File cloud-init

Salvare come `cloud-init.yaml`:

```yaml
#cloud-config
timezone: Europe/Rome
package_update: true
packages:
  - ffmpeg
  - screen

# TensorFlow e Whisper insieme stanno stretti in 2 GB: lo swap evita
# che il kernel interrompa l'elaborazione per mancanza di memoria
swap:
  filename: /swapfile
  size: 4G

write_files:
  - path: /usr/local/bin/registra-stream
    permissions: "0755"
    content: |
      #!/usr/bin/env bash
      # Registra lo stream in file da 1 ora, senza ricodifica.
      # ffmpeg si riconnette da solo; se esce comunque, si riparte dopo 10 s.
      URL="https://rastu91li2018.radioca.st/stream"
      DIR=/root/registrazioni
      mkdir -p "$DIR"
      while true; do
        # estensione adatta al codec dello stream (con -c copy deve corrispondere)
        codec=$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name \
                  -of csv=p=0 "$URL" 2>/dev/null)
        case "$codec" in
          aac) EXT=aac ;;
          *)   EXT=mp3 ;;
        esac
        ffmpeg -hide_banner -loglevel warning -nostdin \
          -reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1 \
          -reconnect_delay_max 30 \
          -i "$URL" -map 0:a -c copy \
          -f segment -segment_time 3600 -reset_timestamps 1 -strftime 1 \
          "$DIR/radio_%Y%m%d_%H%M%S.$EXT"
        echo "$(date '+%F %T') ffmpeg uscito ($?), riparto tra 10 s"
        sleep 10
      done

  - path: /usr/local/bin/elabora-registrazioni
    permissions: "0755"
    content: |
      #!/usr/bin/env bash
      # Ricava gli SRT da ogni file orario già chiuso che non li ha ancora.
      # Variabili: MODELLO (default small), ATTESA tra un giro e l'altro (s).
      DIR=/root/registrazioni
      MODELLO="${MODELLO:-small}"
      ATTESA="${ATTESA:-300}"
      while true; do
        # -mmin +2: il file in scrittura cambia di continuo e resta escluso
        find "$DIR" -maxdepth 1 -type f \
             \( -name 'radio_*.mp3' -o -name 'radio_*.aac' \) -mmin +2 | sort |
        while read -r f; do
          nome=$(basename "${f%.*}")
          [ -e "$DIR/${nome}_output/${nome}_silenziato.srt" ] && continue
          echo "$(date '+%F %T') elaboro $f"
          casoweb separa-parlato "$f" --no-audio --modello "$MODELLO" < /dev/null \
            || echo "$(date '+%F %T') errore su $f"
        done
        sleep "$ATTESA"
      done

runcmd:
  - [ bash, -c, "curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh" ]
  - - bash
    - -c
    - >-
      env HOME=/root UV_TOOL_BIN_DIR=/usr/local/bin
      uv tool install --python 3.12
      --overrides https://raw.githubusercontent.com/titobrasolin/casoweb-tools/main/scripts/overrides-cpu.txt
      "casoweb-tools[separa-parlato,cpu] @ git+https://github.com/titobrasolin/casoweb-tools.git"
  - [ screen, -dmS, radio, /usr/local/bin/registra-stream ]
```

Note:

- `uv` scarica da solo Python 3.12, richiesto da inaSpeechSegmenter; non
  serve il pacchetto `python3` di sistema.
- La variante CPU (`overrides-cpu.txt` + extra `cpu`) evita i diversi GB di
  librerie NVIDIA inutili su un server senza GPU: vedi il
  [README](../README.md#con-separa-parlato).
- L'elaborazione automatica **non** parte da sola: si avvia a mano dopo aver
  provato un file (sezione 5).

## 2. Creazione dell'istanza

Da pannello web: *Deploy → Cloud Compute → High Frequency → 1 vCPU / 2 GB*,
caricare la chiave SSH, incollare `cloud-init.yaml` in **User data**,
disattivare i backup automatici.

Oppure con `vultr-cli`.

### Installare vultr-cli

Su **Windows**: scaricare l'archivio `windows_amd64` dalla pagina
[Releases](https://github.com/vultr/vultr-cli/releases), estrarre
`vultr-cli.exe` in una cartella presente nel `PATH` (o aggiungerla), poi
impostare la chiave API, che si genera dal pannello Vultr in
*Account → API*:

```powershell
setx VULTR_API_KEY "la-mia-chiave"   # vale dai terminali aperti dopo
vultr-cli account info               # verifica
```

Su Linux e macOS ci sono i pacchetti (`brew install vultr/vultr-cli/vultr-cli`,
`dnf install vultr-cli`, ...) e si usa `export VULTR_API_KEY=...`. Tutte le
opzioni, Chocolatey e Docker compresi, sono nel
[README ufficiale](https://github.com/vultr/vultr-cli#installation).

### Creare l'istanza

```bash
vultr-cli os list | grep -i ubuntu      # ID del sistema operativo
vultr-cli ssh-key list                  # ID della chiave SSH

vultr-cli instance create \
  --region fra \
  --plan vhf-1c-2gb \
  --os <OS_ID> \
  --ssh-keys <SSH_KEY_ID> \
  --label casoweb-test \
  --userdata "$(cat cloud-init.yaml)"
```

In PowerShell cambiano solo la ricerca, l'a capo e la lettura del file:

```powershell
vultr-cli os list | Select-String ubuntu

vultr-cli instance create `
  --region fra `
  --plan vhf-1c-2gb `
  --os <OS_ID> `
  --ssh-keys <SSH_KEY_ID> `
  --label casoweb-test `
  --userdata (Get-Content -Raw cloud-init.yaml)
```

`ssh` e `scp` sono già inclusi in Windows 10 e 11.

Dopo un paio di minuti: `ssh root@<IP>`, poi:

```bash
cloud-init status --wait     # attende la fine dell'installazione
casoweb --version
swapon --show                # deve comparire /swapfile
```

Se qualcosa non va, il dettaglio è in `/var/log/cloud-init-output.log`.

### Controllo del processore

`separa-parlato` usa librerie precompilate che richiedono AVX (TensorFlow)
e il livello x86-64-v2 (NumPy, CTranslate2). Le CPU dei piani High Frequency
sono recenti, ma conviene verificarlo:

```bash
grep -o -w -E 'ssse3|sse4_1|sse4_2|popcnt|avx2?' /proc/cpuinfo | sort -u
```

In ogni caso `casoweb` controlla da solo prima di partire e, se manca
qualcosa, lo spiega invece di interrompersi con "Illegal instruction".

## 3. Verifica del formato dello stream

```bash
ffprobe -hide_banner https://rastu91li2018.radioca.st/stream
ls /root/registrazioni
```

`registra-stream` sceglie da solo l'estensione `.mp3` o `.aac` in base al
codec. Se `ffprobe` riporta un codec diverso (per esempio `opus`), aggiungere
il caso corrispondente nello script e riavviare la registrazione.

## 4. Gestione della registrazione

```bash
screen -ls                                         # verifica che giri
screen -r radio                                    # entra (esci: Ctrl-A, poi D)
screen -S radio -X quit                            # ferma
screen -dmS radio /usr/local/bin/registra-stream   # riavvia
ls -lh /root/registrazioni                         # file registrati
```

I file sono divisi per ora (~58 MB/ora a 128 kbps, ~1,4 GB/giorno).
Si possono elaborare i file già chiusi mentre la registrazione continua.

## 5. Estrazione SRT con casoweb

Il comando è `casoweb separa-parlato`: separa parlato e musica
(inaSpeechSegmenter), trascrive solo il parlato con faster-whisper e scrive
i sottotitoli. Dettagli e opzioni in [separa-parlato.md](separa-parlato.md).

### Un file a mano

Prima prova, su un file già chiuso, misurando il tempo:

```bash
cd /root/registrazioni
time casoweb separa-parlato radio_AAAAMMGG_HHMMSS.mp3 --no-audio --modello small
```

Accanto al file si crea `radio_AAAAMMGG_HHMMSS_output/` con:

| File | Contenuto |
|---|---|
| `<nome>_silenziato.srt` | sottotitoli con i tempi della registrazione originale |
| `<nome>_parlato.srt` | sottotitoli per l'audio col solo parlato (musica tagliata) |
| `<nome>_segmenti.tsv` | segmentazione parlato/musica, correggibile a mano |
| `<nome>_trascrizione.json` | parole trascritte (cache) |

Con `--no-audio` si ottengono solo i sottotitoli; togliendolo si generano
anche `<nome>_parlato.mp3` e `<nome>_silenziato.mp3` (con `--modo` si sceglie
quale dei due). Opzioni utili:

- `--nomi "Mario Rossi, Anna Bianchi"`: nomi propri da suggerire a Whisper;
- `--modello`: `small` sul piano da 2 GB, `large-v3-turbo` (il predefinito,
  più preciso) su un piano più grande;
- `-v DEBUG` prima del sottocomando (`casoweb -v DEBUG separa-parlato ...`)
  per vedere ogni passo se qualcosa si blocca.

Il primo lancio scarica il modello Whisper da Hugging Face. Se un tratto
risulta classificato male, si corregge il TSV e si rilancia lo stesso
comando: Whisper lavora solo sulle parti nuove (vedi
[Revisione a mano](separa-parlato.md#revisione-a-mano)).

### Elaborazione automatica

`elabora-registrazioni` controlla la cartella ogni 5 minuti ed elabora, uno
alla volta, i file chiusi da almeno 2 minuti che non hanno ancora
`<nome>_silenziato.srt`:

```bash
screen -dmS elabora /usr/local/bin/elabora-registrazioni
screen -r elabora                                  # log (esci: Ctrl-A, poi D)
screen -S elabora -X quit                          # ferma

# con un altro modello o un altro intervallo
screen -dmS elabora env MODELLO=large-v3-turbo ATTESA=600 /usr/local/bin/elabora-registrazioni
```

Un file che va in errore viene ritentato al giro successivo; il motivo si
legge nella sessione `elabora`.

### Prestazioni

Su CPU la trascrizione è lenta: come riferimento,
[separa-parlato.md](separa-parlato.md#gpu-e-prestazioni) riporta circa
40 minuti per 31 minuti di parlato con `large-v3-turbo` su un i5-5200U
(2 core). Con 1 vCPU l'elaborazione di un'ora di registrazione può durare
più di un'ora, e allora la coda cresce. Il `time` della prova dice se il
piano regge; altrimenti:

- usare un modello più leggero (`small`, `base`);
- creare l'istanza su un piano con più vCPU e RAM (lo stesso
  `cloud-init.yaml` va bene);
- registrare soltanto su questo server ed elaborare altrove (vedi sotto).

## 6. Recupero dei risultati e chiusura

```bash
# dal proprio computer: solo i sottotitoli
scp 'root@<IP>:/root/registrazioni/*_output/*.srt' .

# oppure tutto (registrazioni comprese; su Windows: scp -r)
rsync -av root@<IP>:/root/registrazioni/ ./registrazioni/

# distruzione dell'istanza (ferma l'addebito)
vultr-cli instance list
vultr-cli instance delete <INSTANCE_ID>
```

Per ripartire ogni volta dallo stesso stato, fare uno snapshot prima del
Destroy: costa molto meno di un'istanza accesa.
