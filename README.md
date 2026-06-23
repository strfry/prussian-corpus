# prussian-corpus

Archiv der Rohdaten für die Rekonstruktion des Altpreußischen. Enthält Scraping-Skripte
und State-Dateien; die Rohdaten selbst (`raw/`) sind nicht im Repository und werden über
GitHub Releases verteilt.

## Quellen

### wirdeins.twanksta.org

Elektronisches Altpreußisches Wörterbuch (Sambischer Dialekt). Betrieben von Rantawa.org.

Scraping-Methode:
- Phase 1 (`twanksta_enumerate.py`): Rekursive Präfix-Suche (`/search/?s={prefix}&language=engl&dia=semba`), 2-Buchstaben-Basis mit Unterteilung bei ≥30 Treffern.
- Phase 2 (`twanksta_fetch.py`): Pro Wort alle 6 Sprachversionen (`engl`, `miks`, `leit`, `latt`, `pols`, `mask`) und Paradigmentabellen (`POST /more/`). Die Such-Antworten enthalten bereits inline Artikel-Inhalte (`<div class="descripcio">`) und die Paradigmentabellen enthalten Partizip-Deklinationstabellen als `spoiler-body2`-Divs.

Gespeichert:
- `raw/twanksta/entries/{word}/{lang}.html` — Such-Antwort pro Wort und Sprache
- `raw/twanksta/forms/{num}_{word}.html` — Paradigmentabelle pro Lemma

### prusaspira.org

Inflektionstabellen für altpreußische Wörter (prusaspira.org/wirdeins). Betrieben von
der prusaspira-Gemeinschaft.

Scraping-Methode (`prusaspira_fetch.py`): Pro Buchstabe des altpreußischen Alphabets
wird `GET /wirdeins?wirds={letter}&akc=Iz&tap=W&bila=1` abgerufen. Jede Antwort enthält
**alle** Einträge mit diesem Anfangsbuchstaben inklusive Deklinationstabellen — keine
Einzelwort-Abfrage nötig. Rate: 1 Anfrage / 2 Sekunden.

Komparativ-/Superlativ-Volldeklinationen (sowie Verb-Partizipien) stehen in den
Buchstabenseiten nur als Stichwort-Link (`ens_str(...)`) und lassen sich über einen
`string=`-Formularaufruf einzeln nachladen. Mechanik und Parameter:
[docs/prusaspira_string_endpoint.md](docs/prusaspira_string_endpoint.md).

Gespeichert:
- `raw/prusaspira/by_letter/{letter}.html` — alle Einträge pro Anfangsbuchstabe

### Tabula Nova (`data/sources/tabula.html`)

Manuell gepflegte HTML-Referenztabelle aller altpreußischen Flexionsparadigmen
(Nr. 1–144) von `donelaitis.vdu.lt/prussian/tabula.htm` (Spiegel auf
prusaspira.org). Das kaputte Roh-HTML wurde **halb-manuell** zu `tabula.html`
korrigiert; diese Korrektur ist die einzige nicht-automatische Vorstufe und wird
deshalb als Quelldatei mit eingecheckt (nicht neu gescrapt). Begleitend:
`data/sources/gramm.htm` (Grammatiktafeln, Referenz). Verbraucht von
`prussian-fst` (Paradigmenvergleich → Goldstandard).

## Dictionary-Build

Aus den geparsten Einträgen wird das kanonische `prussian_dictionary.json`
gebaut (`scripts/build_dictionary.py`, `make dictionary`). Das ist das
Artefakt, das **prussian-mcp** (als Eingabe für `generate_embeddings.py`) und
**prussian-lora** (Vokabel-Korpus) konsumieren. Standardmäßig entspricht es
exakt dem Twanksta-Parse — so bleibt die Einträgemenge (und mcps
Embedding-Ausrichtung) stabil; `make dictionary WITH_PRUSASPIRA=1` ergänzt
zusätzlich nur-in-Prusaspira vorhandene Lemmata.

## Downstream-Konsumenten

Dieses Repo ist die **einzige** Stelle, an der altpreußische Quelldaten
gescrapt/gesammelt und geparst werden. Andere Repos scrapen nicht selbst,
sondern beziehen die Release-Artefakte:

| Repo | Konsumiert | Wie |
|---|---|---|
| `prussian-mcp` | `prussian_dictionary.json` | Eingabe für `generate_embeddings.py` |
| `prussian-fst` | `twanksta_entries.json`, `prusaspira_entries.json`, `tabula.html` | unter `data/external/` ablegen |
| `prussian-lora` | `prussian_dictionary.json` | Vokabel-Korpus-Generierung |

## Verwendung

```bash
# Vollständiges Scraping (bei Abbruch fortsetzbar)
make twanksta-enumerate   # Phase 1: Wortliste aufbauen
make twanksta-fetch       # Phase 2: Twanksta HTML cachen (Stunden)
make prusaspira-fetch     # Prusaspira HTML cachen (Stunden)
make twanksta-parse       # HTML → parsed/twanksta_entries.json
make prusaspira-parse     # HTML → parsed/prusaspira_entries.json
make dictionary           # parsed/* → parsed/prussian_dictionary.json

# Fortschritt
make status

# Release erstellen und auf GitHub hochladen
make release
```

## Releases herunterladen

```bash
gh release download v2026-06-20 --repo strfry/prussian-corpus --pattern "*.tar.zst"
tar --zstd -xf prussian_raw_v2026-06-20.tar.zst
```

## Struktur

```
prussian-corpus/
├── scripts/
│   ├── twanksta_enumerate.py   # Phase 1: Wortliste
│   ├── twanksta_fetch.py       # Phase 2: HTML-Cache
│   ├── twanksta_parse.py       # HTML → twanksta_entries.json
│   ├── prusaspira_fetch.py     # Prusaspira HTML-Cache (by-letter + extended)
│   ├── prusaspira_parse.py     # HTML → prusaspira_entries.json
│   ├── prusaspira_extended_parse.py
│   └── build_dictionary.py     # parsed/* → prussian_dictionary.json
├── data/sources/               # eingecheckte Quelldateien (in Git)
│   ├── tabula.html             # Paradigmentafel 1–144 (halb-manuell korrigiert)
│   └── gramm.htm               # Grammatiktafeln (Referenz)
├── state/                      # Scraping-Fortschritt (in Git)
│   ├── twanksta_wordlist.json  # 10.698+ Lemmata
│   ├── fetch_state.json        # Fortschritt Phase 2
│   └── prusaspira_state.json   # Prusaspira-Fortschritt
├── raw/                        # Rohdaten (.gitignore)
│   ├── twanksta/{entries,forms}/
│   └── prusaspira/by_letter/   # {letter}.html (alle Einträge je Anfangsbuchstabe)
├── parsed/                     # geparste Artefakte (.gitignore; via Release)
│   ├── twanksta_entries.json
│   ├── prusaspira_entries.json
│   └── prussian_dictionary.json
└── Makefile
```
