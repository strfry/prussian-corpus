# Prusaspira `string=`-Endpunkt — Nachladen einzelner Flexionsformen

Stand: 2026-06-21. Dies dokumentiert **wie** man auf prusaspira.org einzelne
Voll-Flexionstabellen nachlädt, die in den zwischengespeicherten Buchstabenseiten
(`raw/prusaspira/by_letter/{a..z}.html`) nur als Stichwort-Link vorkommen.

Die eigentliche **linguistische Analyse** des dahinterliegenden Flexionsalgorithmus
(prusaspira generiert die Formen offensichtlich regelbasiert aus Lemma + Paradigma)
ist hier bewusst **nicht** enthalten und für später aufgehoben.

## Worum geht es

In den Buchstabenseiten erscheinen anklickbare Formen über JavaScript-Aufrufe:

```html
onclick="ens_str('abipuššaisis,28,cp,abipussis');"
```

Die Funktion `ens_str(str)` setzt ein verstecktes Formularfeld und submittet das
GET-Formular der Seite:

```js
function ens_str(str) {
    document.getElementById('string').disabled = '';   // Feld aktivieren
    document.getElementById('string').value = str;
    document.forms['form'].submit();
}
```

Das Formular (`<form id="form" method="get" action="">`) reicht dabei u. a. die
Felder `string`, `akc`, `tap`, `bila` und `wirds` mit ein. Der Server rendert dann
in den `<div id="rezultatai">` **eine einzelne** Flexionstabelle für die in `string`
kodierte Form.

## `string`-Format

Komma-getrennt, vier Felder:

```
<form>,<paradigm>,<type>,<lemma>
```

| Feld       | Beispiel        | Bedeutung                                  |
|------------|-----------------|--------------------------------------------|
| `form`     | `abipuššaisis`  | Die nachzuladende Stichform (Nom. Sg.)     |
| `paradigm` | `28`            | Paradigmennummer (ggf. mit Suffixbuchstabe)|
| `type`     | `cp`            | Form-Typ (siehe Tabelle unten)             |
| `lemma`    | `abipussis`     | Grundlemma, zu dem die Form gehört         |

### Form-Typen (`type`)

| Code      | Bedeutung              | Tabellenform                     | Vorkommen (roh, alle Buchstaben) |
|-----------|------------------------|----------------------------------|----------------------------------|
| `cp`      | Komparativ (Adjektiv)  | 6-Spalten Adjektiv-Deklination   | 2.408                            |
| `sp`      | Superlativ (Adjektiv)  | 6-Spalten Adjektiv-Deklination   | 4.816                            |
| `pcps`    | Partizip Präsens (Verb)| Adjektiv-artige Deklination      | 7.497                            |
| `pcptac`  | Partizip Aktiv (Verb)  | Adjektiv-artige Deklination      | 7.497                            |
| `pcptpa`  | Partizip Passiv (Verb) | Adjektiv-artige Deklination      | 5.182                            |

Eindeutige `cp`+`sp`-Tupel über alle Buchstaben: **3.027** (Rest sind
Querverweis-Duplikate zwischen Buchstabendateien). Die `pcp*`-Partizipien werden vom
Parser bereits inline aus den Verb-Tabellen gewonnen; ihre Volldeklination ließe sich
über denselben Endpunkt nachladen, falls je benötigt.

## HTTP-Aufruf

```
GET https://www.prusaspira.org/wirdeins?string=<urlencode(tupel)>&tap=W&bila=1&wirds=
```

**Zwingend: `wirds` muss leer sein.** Ist `wirds` gesetzt (z. B. `wirds=a`), ignoriert
der Server `string` komplett und liefert die gesamte Buchstabenseite zurück
(byte-identisch zur gecachten `a.html`, ~1,8 MB). Nur mit leerem `wirds` kommt die
isolierte Einzeltabelle (~11 KB).

`akc` ist optional/leer; `tap=W` und `bila=1` (Sprache = Englisch) wie beim normalen
Buchstaben-Fetch.

Beispiel (URL-encoded):

```
https://www.prusaspira.org/wirdeins?string=abipu%C5%A1%C5%A1aisis%2C28%2Ccp%2Cabipussis&tap=W&bila=1&wirds=
```

Rate-Limit wie beim bestehenden Scraper: 1 Anfrage / 2 s.

## Antwortstruktur

Die Antwort ist die normale Seiten-Hülle; relevanter Inhalt steht in
`<div id="rezultatai">`:

```html
<b>abipuššaisis</b> - kōmparatiws ezze adjaktīwu: <b>abipussis</b>
<table ...>
  <table CLASS="boldtable"> ... 
    <table>
      <th></th><th align="left">m sg</th><th align="left">m pl</th>
      <th align="left">f sg</th><th align="left">f pl</th>
      <th align="left">n sg</th><th align="left">n pl</th>
      <tr><td><b>Nōm: </b></td><td>abipuššaisis</td><td>abipuššaišai</td>
          <td>abipuššaisi</td><td>abipuššaisis</td><td>abipuššaisi</td><td>abipuššaišai</td></tr>
      <tr><td><b>Gēn: </b></td> ... </tr>
      <tr><td><b>Dāt: </b></td> ... </tr>
      <tr><td><b>Akk: </b></td> ... </tr>
    </table>
  ...
</table>
```

- Kopfzeile: `<b>FORM</b> - kōmparatiws|superlatīws ezze adjaktīwu: <b>LEMMA</b>`
  (bestätigt Form-Typ und Grundlemma).
- Genau **eine** 6-Spalten-Tabelle: Spalten `m sg | m pl | f sg | f pl | n sg | n pl`,
  Zeilen `Nōm | Gēn | Dāt | Akk`. Identische Struktur wie die Positiv-Deklination im
  Buchstaben-Seiten-`boldtable`.
- Diese Tabellen lassen sich mit demselben 6-Spalten-Parser verarbeiten, der für die
  Positiv-Deklination gebraucht wird → pd.json-Format
  `[{gender:"m"|"f"|"n", cases:[{case, singular, plural}]}]`.

## Verifikation (2026-06-21)

Manuell per `curl` gegen `abipussis` geprüft:
- `string=abipuššaisis,28,cp,abipussis` + leeres `wirds` → 11 KB, vollständige
  m/f/n × sg/pl-Tabelle für den Komparativ.
- Gleicher Aufruf mit `wirds=a` → 1,8 MB volle Buchstabenseite (`string` ignoriert).

## Offen / für später

- **Flexionsalgorithmus** rekonstruieren (Lemma + Paradigma → alle Formen), statt
  3.027 Einzelseiten zu fetchen. Die Regelmäßigkeit der Endungen legt einen
  deterministischen Generator nahe.
- Geplanter Fetch-/Parser-Anschluss (Cache nach `raw/prusaspira/degrees/`, Felder
  `comparative`/`superlative` im Parser) — noch nicht implementiert.
