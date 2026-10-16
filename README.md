# casoweb-tools

Raccolta di utility, script di servizio e piccoli esperimenti sviluppati per la rubrica **[Tecnologia @ CASO](https://www.casoweb.eu/?view=category&id=90)** su *casoweb.eu*.

---

## 🚀 Installazione

Il pacchetto può essere installato direttamente dal repository GitHub.

### Utilizzo consigliato con `pipx`
```bash
pipx install git+[https://github.com/titobrasolin/casoweb-tools.git](https://github.com/titobrasolin/casoweb-tools.git)
```

### Installazione con `pip`
```bash
pip install git+[https://github.com/titobrasolin/casoweb-tools.git](https://github.com/titobrasolin/casoweb-tools.git)
```

### Installazione per sviluppo locale
Se desideri apportare modifiche agli script o testarli in locale:

```bash
git clone [https://github.com/titobrasolin/casoweb-tools.git](https://github.com/titobrasolin/casoweb-tools.git)
cd casoweb-tools
pip install -e .
```

---

## 🛠️ Utilizzo

Una volta installato, il comando principale disponibile a riga di comando è `casoweb`:

```bash
casoweb --help
```

### Strumenti e comandi disponibili

* `casoweb <comando>`: Descrizione sintetica della funzione/esperimento.

---

## 📁 Struttura del Progetto

```text
casoweb-tools/
├── src/
│   └── casoweb_tools/
│       ├── __init__.py
│       ├── cli.py
│       └── cmds/
├── scripts/          # Script Bash e utility non-Python
├── pyproject.toml
├── LICENSE
└── README.md
```

---

## 📄 Licenza

Questo progetto è distribuito con licenza **MIT**. Consulta il file [LICENSE](LICENSE) per ulteriori dettagli.
