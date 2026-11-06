# gitskim – Anonymisierung (Design)

*Datum: 2026-11-05 · Status: implementiert (19 Tests), Abweichungen unten unter „Umsetzung“*

## Ziel

Firmen-Code an ein **externes LLM** geben, ohne dass Firma, Kunden, Kollegen,
interne Hosts oder Projektnamen erkennbar sind. Der Leser ist ein LLM, das den
Code weiterhin verstehen soll: Ersetzungen sind **konsistent und typisiert**
(`Acme` ist ueberall `Company1`), nicht geschwaerzt.

Abgrenzung zum Secret-Scan: der Secret-Scan *ueberspringt* Dateien mit
Zugangsdaten, die Anonymisierung *ersetzt* identifizierende Begriffe im Text.
Beide laufen unabhaengig; der Secret-Scan zuerst, auf den Originaldateien.

## Nicht-Ziele

- NLP-/NER-Erkennung (spaCy, Presidio): bricht stdlib-only. Diese Luecke
  schliesst stattdessen das firmeninterne LLM bei der Ersteinrichtung (s. u.).
- Garantie. Es ist eine Hilfe; die Ausgabe vor dem Teilen pruefen.
- Anonymisierung von Code-Logik oder Bezeichnern, die nicht in der Liste stehen.

## Zwei-Phasen-Workflow

```
Phase 1 – Ersteinrichtung (intern, einmalig pro Firma/Workspace)

  gitskim --anonymize api          Config fehlt / ohne [anonymize]
        │                          → Config wird angelegt, Hinweis, Exit 2
        ▼
  gitskim --anon-prompt api        → api_anon-prompt_<ts>.md
        │                            (Prompt + Original-Skim, nicht anonymisiert)
        ▼
  firmeninternes LLM               → [anonymize]-Block + Pruefliste
        │
        ▼
  Block in config.ini einfuegen, Pruefliste durchsehen

Phase 2 – Normalbetrieb (extern)

  gitskim --anonymize api          → Project1_<ts>.md  (anonymisiert)
        │                            + Mapping-Update, Leak-Check
        ▼
  externes LLM                     → antwort.md (mit Pseudonymen)
        │
        ▼
  gitskim --deanonymize antwort.md → Originalbegriffe zurueck
```

Das interne LLM liefert **nur die Config und eine Pruefliste**, keine
anonymisierte MD. Die erzeugt gitskim deterministisch: vollstaendig (keine
Kuerzung durch Output-Limits), ohne stille Code-Aenderungen, passend zum
stabilen Mapping und damit umkehrbar.

## CLI

| Flag | Bedeutung |
|---|---|
| `--anonymize` | anonymisieren (auch per Config `enabled = true`) |
| `--no-anonymize` | abschalten, auch wenn die Config es aktiviert |
| `--anon-prompt` | statt Skim: Bundle aus LLM-Prompt + Original-Skim schreiben, fuer das **interne** LLM |
| `--deanonymize [FILE]` | Pseudonyme in FILE (oder stdin) zurueckuebersetzen → stdout |
| `--anon-map FILE` | Mapping-Datei (Default s. u.) |

`--anon-prompt` und `--anonymize` schliessen sich aus. `--anon-prompt` druckt
eine deutliche stderr-Warnung: *Ausgabe enthaelt Originaldaten, nur an ein
internes LLM geben.*

## Auto-Anlage der Config

Bei `--anonymize` (oder `enabled = true` ohne Begriffe):

- **Config fehlt:** komplettes Template (bestehendes `CONFIG_TEMPLATE` plus
  `[anonymize]`-Abschnitt) wird angelegt.
- **Config ohne `[anonymize]`:** Abschnitt wird **angehaengt**, nichts
  ueberschrieben.
- Danach Hinweis auf stderr (Pfad, naechster Schritt `--anon-prompt`) und
  **Exit 2, ohne Ausgabe**. Begruendung: nur mit automatischer Erkennung
  (E-Mails, IPs, Git-Autoren) wuerden Firmen- und Kundennamen durchrutschen;
  ein halb anonymisierter Skim ist gefaehrlicher als keiner.
- Ist `terms` bewusst leer, aber `confirmed = true` gesetzt, laeuft gitskim
  nur mit der automatischen Erkennung.

`--init-config` schreibt das Template ab jetzt inklusive `[anonymize]`.

### Template-Abschnitt (Entwurf)

```ini
[anonymize]
# Ersteinrichtung:
#   1. gitskim --anon-prompt <projekt>   (erzeugt Prompt + Skim in einer Datei)
#   2. Datei an ein FIRMENINTERNES LLM geben, NIE an ein externes.
#   3. Den gelieferten [anonymize]-Block hier einfuegen, Pruefliste lesen.
#
# Der Prompt (steckt auch in der --anon-prompt-Datei):
#
#   Du bekommst eine Markdown-Zusammenfassung eines Code-Repositories. Sie soll
#   spaeter an ein externes LLM gehen. Finde alles, was Firma, Kunden, Partner,
#   Personen, interne Infrastruktur oder interne Projekte erkennbar macht:
#   Firmen- und Kundennamen (auch in Paketnamen wie com.acme.*), Produkt- und
#   Codenamen, Personennamen (auch in Kommentaren, @author, TODOs), interne
#   Domains und Hostnamen. Ignoriere Open-Source-Bibliotheken, Frameworks,
#   Programmiersprachen und allgemeine Fachbegriffe.
#   Antworte mit genau zwei Teilen:
#   1. Ein INI-Block, beginnend mit [anonymize], mit den Schluesseln
#      terms (kommagetrennt, optional "Begriff = kategorie" mit kategorie aus
#      company, person, project, product, customer), domains, keep.
#   2. Eine Pruefliste als Markdown-Tabelle: Begriff | Kategorie | Grund |
#      Beispiel-Fundstelle. Markiere unsichere Faelle mit "?".
#   Erfinde nichts, was nicht im Text vorkommt. Schreibe keine anonymisierte
#   Fassung des Dokuments.

enabled = false
terms =
domains =
keep =
# map = ~/.config/gitskim/anon-map.json
```

Der Prompt steht einmal als Konstante `ANON_PROMPT` im Code; Template-Kommentar
und `--anon-prompt`-Bundle werden daraus erzeugt (keine zwei Kopien pflegen).

### `--anon-prompt`-Bundle

```
# Anonymisierungs-Analyse: api
<ANON_PROMPT>
---
## Bereits automatisch erkannt (nicht noetig zu listen)
Git-Autoren: Max Mustermann, …   Remote: git.acme.internal   E-Mail-Domains: acme.com
---
<normaler Skim, nicht anonymisiert>
```

Die Liste der automatisch erkannten Werte spart dem LLM Arbeit und zeigt, was
gitskim ohnehin abdeckt. Bestehende `terms` aus der Config werden ebenfalls
mitgegeben, damit das LLM sie ergaenzt statt neu erfindet.

## Woerterbuch

| Quelle | Beispiel | Pseudonym |
|---|---|---|
| Config `terms` | `Acme`, `Mueller GmbH`, `Falke = project` | `Company1`, `Project2` (Default-Kategorie `company`) |
| Config `domains` | `acme.com`, `acme.internal` | Host darunter → `host1.example.internal`; URLs entsprechend |
| Git-Autoren (auto) | `Max Mustermann <max@acme.com>` | `Person1 <person1@example.com>`; Vor-/Nachname einzeln, wenn ≥ 4 Zeichen |
| Repo-Metadaten (auto) | Repo-Name, Ordner, Remote-URL, Branch | `Project1`; Branch `feature/ACME-123` → `feature/PROJECT1-123` |
| Regex (auto) | beliebige E-Mail, IPv4/IPv6 | `user3@example.com`, `10.0.0.3` |

Ausnahmen (nie ersetzt): `example.com/.org`, `localhost`, `127.0.0.1`,
`0.0.0.0`, private Doku-Netze, `noreply@…`, oeffentliche Hoster (`github.com`,
`gitlab.com`, …), plus Config `keep`. Begriffe unter 3 Zeichen werden mit
Warnung ignoriert.

## Ersetzung

- **Stelle in der Pipeline:** ueber den fertig gerenderten Markdown-String
  (`render()` → `anonymize(text)`), damit Titel, Baum, Pfade, Tabellen,
  Inhalte, `--log`, `--diff` und die Repositories-Tabelle gleich behandelt
  werden. Auch der Default-Ausgabename nutzt das Pseudonym
  (`Project1_2026-11-05_1432.md`).
- **Ein kombinierter Regex**, laengste Begriffe zuerst, case-insensitiv.
- **Grenzen:** Wortgrenze *oder* camelCase-/Trenner-Grenze. `AcmeBilling` →
  `Company1Billing`, `com.acme.api` → `com.company1.api`, `acme_core` →
  `company1_core`; kein Treffer mitten im Wort (`Acmeist`).
- **Schreibweise uebernehmen:** `acme` → `company1`, `ACME` → `COMPANY1`,
  `Acme` → `Company1`.
- **Reihenfolge:** URLs/E-Mails vor Domains vor Begriffen, damit
  `max@acme.com` ein Pseudonym bekommt und nicht zu `max@company1.com` wird.

## Mapping

- Default `~/.config/gitskim/anon-map.json` (neben der Config), Rechte `0600`.
- **Stabil:** wird geladen, ergaenzt, gespeichert. `Acme` bleibt ueber Laeufe
  und Repos hinweg `Company1`; Zaehler je Kategorie.
- Format: `{"version": 1, "entries": {"Company1": {"original": "Acme", "kind": "company"}, …}}`.
- Enthaelt Originaldaten: nie in ein Repo legen; README-Hinweis.

## `--deanonymize`

Liest FILE oder stdin, ersetzt Pseudonyme (laengste zuerst, mit Wortgrenze,
`Company10` vor `Company1`) durch die Originale, schreibt nach stdout.
Schreibweisen-Varianten (`company1`, `COMPANY1`) werden mit uebersetzt.

## Absicherung

- **Leak-Check** nach dem Ersetzen: kommt ein Originalbegriff noch vor (z. B.
  in einer Schreibweise, die der Regex nicht trifft), Warnung mit
  Dateiabschnitt und Zeile; Exit-Code bleibt 0.
- **Zusammenfassung** auf stderr:
  `anonymized: 2 companies, 7 persons, 3 hosts, 11 emails, 1 project`.
- Kopfzeile im Skim: `mode: skim · anonymized`, damit das externe LLM weiss,
  dass Namen Platzhalter sind.

## Grenzen (README)

- Personennamen, die weder im Git-Log noch in `terms` stehen, werden nicht
  erkannt; dafuer ist die Analyse durch das interne LLM da.
- Sehr generische Begriffe in `terms` (z. B. ein Firmenname, der ein normales
  Wort ist) erzeugen Fehltreffer; `keep` bzw. laengere Formen nutzen.
- Code-Struktur, Fachlogik und Kommentarinhalte koennen die Firma trotzdem
  verraten.

## Tests (Skizze)

- Schreibweisen und Grenzen: `Acme`, `ACME`, `acmeBilling`, `com.acme.x`, `Acmeist`.
- Reihenfolge E-Mail/Domain/Begriff; Ausnahmen (`example.com`, `127.0.0.1`).
- Mapping-Stabilitaet ueber zwei Laeufe; `Company10` vs. `Company1` bei Rueckweg.
- Roundtrip `anonymize` → `deanonymize` ergibt den Originaltext (fuer
  Texte ohne Schreibweisen-Kollisionen).
- Auto-Anlage: fehlende Config, Config ohne Abschnitt (angehaengt, Rest
  unveraendert), Exit 2.
- `--anon-prompt`-Bundle enthaelt Prompt, Auto-Erkanntes und unveraenderten Skim.
- Leak-Check meldet einen absichtlich nicht ersetzten Begriff.

## Offene Punkte

- Sollen Pseudonyme sprechender sein (`Company1` vs. `RetailCustomer1`) ueber
  frei waehlbare Kategorien?
- Dateinamen und Pfade mit Begriffen werden ersetzt; reicht das, oder braucht
  es eine Option, Pfade zu erhalten?

## Umsetzung (Abweichungen zum Design)

- **Prompt und Ausgaben auf Englisch**, wie der Rest von gitskim. Steht einmal
  als `ANON_PROMPT` im Code; Config-Kommentar und Bundle werden daraus erzeugt.
- **Kategorien frei waehlbar** (offener Punkt 1): `Name = kind` mit einem Wort
  a-z, Pseudonym `Kind1`. Default `company`.
- **Pfade werden immer ersetzt** (offener Punkt 2), keine Option.
- **Domains:** `acme.internal` → `domain1.example`, Subdomains bleiben
  (`api.acme.internal` → `api.domain1.example`) statt `host1.example.internal`.
  Interne Remote-Hosts gehen als Basisdomain ein (`git.acme.internal` → `acme.internal`).
- **Neu erkannt:** Remote-Owner (`acme` in `git@host:acme/x.git`) als `OrgN`,
  E-Mail-Login der Autoren und Namensteile von `person`-Terms aus der Config
  (teilen das Pseudonym der Person).
- **Generische Woerter** (`api`, `web`, `core`, `test`, …) und Teile unter
  4 Zeichen werden bei automatisch erkannten Namen uebersprungen, sonst zerlegt
  ein Repo namens `api` den Code.
- **Keine Ersetzung vor einer Ziffer** (`acme2`): waere als `company12`
  mehrdeutig; die Leak-Pruefung meldet die Stelle.
- **IPv6** nicht umgesetzt (zu viele Fehltreffer mit `::` in C++/Rust).
- **Ersteinrichtung:** fehlt die Config, wird eine minimale Datei
  (`[gitskim]` leer + `[anonymize]`) angelegt, nicht das volle Template, damit
  kein Beispiel-`workspace = ~/code` aktiv wird. `--init-config` enthaelt den
  Abschnitt ebenfalls.
- Exit 2 auch bei vorhandenem, aber leerem Abschnitt (ohne `confirmed = true`).
