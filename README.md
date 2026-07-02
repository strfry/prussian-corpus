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

### youtube.com/@prusiskataliwidasna

YouTube-Kanal mit 660+ Videos, die alle altpreußische Untertitel enthalten
(unter der YouTube-Sprache `en` abgelegt, da YouTube kein Altpreußisch unterstützt).

Scraping-Methode (`youtube_fetch.sh`): Extrahiert Videoliste aus der `playlist`-Datei
und lädt pro Video mit `yt-dlp --write-subs --sub-lang en` die Untertitel als SRT
herunter.

Gespeichert:
- `raw/youtube/subs/{video_id}.en.srt` — Untertitel pro Video

## Verwendung

```bash
# Vollständiges Scraping (bei Abbruch fortsetzbar)
make enumerate    # Phase 1: Wortliste aufbauen
make fetch        # Phase 2: Twanksta HTML cachen (Stunden)
make prusaspira   # Prusaspira HTML cachen (Stunden)
make youtube-fetch # YouTube-Untertitel von @prusiskataliwidasna

# Fortschritt
make status

# Release erstellen und auf GitHub hochladen
make release
```

## Releases herunterladen

Jedes Release enthält drei Artefakte:
- `twanksta_entries.json` — Wörterbuch Twanksta (strukturiert)
- `prusaspira_entries.json` — Wörterbuch Prusaspira (strukturiert)
- `prussian_corpus_*.tar.zst` — Artikeltexte und weitere Korpusdaten

```bash
# Alles herunterladen
gh release download v2026-06-20 --repo strfry/prussian-corpus
tar --zstd -xf prussian_corpus_v2026-06-20.tar.zst

# Nur die Wörterbücher
gh release download v2026-06-20 --repo strfry/prussian-corpus --pattern "*.json"
```

## Struktur

```
prussian-corpus/
├── scripts/
│   ├── twanksta_enumerate.py   # Phase 1: Wortliste
│   ├── twanksta_fetch.py       # Phase 2: HTML-Cache
│   ├── prusaspira_fetch.py     # Prusaspira HTML-Cache
│   └── youtube_fetch.sh        # YouTube-Untertitel-Cache
├── state/                      # Scraping-Fortschritt (in Git)
│   ├── twanksta_wordlist.json  # 10.698+ Lemmata
│   ├── enumerate_state.json    # Fortschritt Phase 1
│   ├── fetch_state.json        # Fortschritt Phase 2
│   └── prusaspira_state.json   # Prusaspira-Fortschritt
├── raw/                        # Rohdaten (.gitignore)
│   ├── twanksta/
│   │   ├── entries/            # {word}/{lang}.html
│   │   └── forms/              # {num}_{word}.html
│   ├── prusaspira/
│   │   └── by_letter/          # {letter}.html (alle Einträge je Anfangsbuchstabe)
│   └── youtube/
│       └── subs/               # {video_id}.en.srt (Untertitel)
└── Makefile
```
