# gitskim – Design (v1)

*Datum: 2026-10-01 · Status: v1 implementiert (81 Tests), Abweichungen zum Brainstorming inline markiert*

## Was es ist

Ein einzelnes Python-Script ohne Abhaengigkeiten, das ein Git-Repository in eine
komprimierte Markdown-Datei (`SKIM.md`) fuer LLM-Kontext verwandelt.
Default-Modus „skim": Struktur und Signaturen statt Code-Bodies. `--full` liefert
den gefilterten Volltext.

**USP gegenueber Repomix, gitingest, code2prompt:** eine Datei, nur Stdlib,
`python gitskim.py <repo>` und fertig. Kein pip install, kein Token, kein Rust,
kein Node.

## Nicht-Ziele (v1)

Exakte Tokenzaehlung (tiktoken), Tree-sitter, Secretlint-Grad Secret-Scanning,
Templates, TUI, Watch-Modus, MCP-Server, Config-Dateien, Clipboard.

## CLI

```
gitskim.py <pfad-oder-url> [-o SKIM.md] [--full] [--stdout]
                           [--include GLOB]... [--exclude GLOB]...
                           [--max-size KB=100] [--no-default-ignore]
                           [--untracked] [--sort changes|path]
                           [--diff] [--log N] [--no-secret-scan]
```

## Architektur

Eine Datei `gitskim.py`, Python >= 3.9, Stdlib: `argparse subprocess pathlib ast
re fnmatch json tempfile shutil xml.etree collections`.

Lineare Pipeline, ein Schritt = eine Funktion:

| Schritt | Funktion | Mechanik |
|---|---|---|
| 1 | `resolve_source(arg)` | lokaler Pfad, oder `git clone --depth 1` in Tempdir |
| 2 | `collect_files(repo)` | `git ls-files -z`, dann Filterkette |
| 3 | `rank_files(repo, files)` | `git log --name-only --format= -n 500` → Counter |
| 4 | `process_file(path, mode)` | Secret-Scan, dann Skimmer oder Volltext |
| 5 | `render(ctx)` | Markdown-String |

`SKIMMERS: dict[str, Callable]` mappt Dateiendung → Extraktor.

**Harte Entscheidung:** kein Fallback ohne Git. `git ls-files` ist der Kern und
erspart eigenes `.gitignore`-Parsing. Kein Repo → Exit 1 mit klarer Meldung.

## Dateiauswahl

Reihenfolge der Filterkette (billig vor teuer):

1. **Default-Ignore** (`fnmatch` auf relativen Pfad): Lockfiles (`*.lock`,
   `package-lock.json`, `poetry.lock`, `yarn.lock`, `Cargo.lock`), Minified
   (`*.min.js`, `*.min.css`, `*.map`), Binaer-Endungen (`png jpg gif ico pdf
   woff* ttf zip gz pyc so dll exe`), Verzeichnisse `dist/ build/ vendor/
   node_modules/ __pycache__/`. Abschaltbar via `--no-default-ignore`.
2. `--exclude` Globs (gewinnt immer), dann `--include`: wenn gesetzt, muss eines
   passen. Ein expliziter Include ueberstimmt die Default-Ignore-*Dateimuster*
   (Lockfiles, Bilder …), nicht aber die ignorierten *Verzeichnisse*
   (`node_modules`, `.venv`, `dist` …). Nur `--no-default-ignore` hebt beides auf.
   (Entscheidung 01.10. nach Code-Review; urspruenglich: Default-Ignore vor Include.)
3. `--max-size` KB, Default 100. Groessere Dateien nur im Baum mit Vermerk.
4. **Binaer-Sniff:** erste 8 KB, Null-Byte → nur im Baum.

`--untracked` haengt `--others --exclude-standard` an. Default aus.

## Ranking

Dateien sortiert nach Aenderungshaeufigkeit (Commits der letzten 500), absteigend,
dann Pfad alphabetisch. Dateien ohne Treffer hinten. `--sort path` fuer rein
alphabetisch. Begruendung: LLM liest von oben, oft geaenderte Dateien tragen
die Logik.

## Skim-Modus (Default)

- **Python via `ast`:** Modul-Docstring, Imports (eine Zeile pro Quelle),
  Klassen mit Basisklassen und Methoden-Signaturen, Top-Level-Funktionen mit
  Typannotationen und Rueckgabetyp, Dekoratoren, erste Docstring-Zeile.
  Bodies → `...`. `SyntaxError` → Fallback-Skimmer.
- **Regex-Skimmer** fuer JS/TS, Go, Rust, Java, C#: Zeilen beginnend mit
  `import|export|function|class|interface|type|func|fn|pub|struct|enum|impl|def`
  oder Zugriffsmodifikator, plus Zeile vor `{` auf Einrueckungstiefe 0–1.
  Dokumentiert als Heuristik.
- **Markdown:** nur Ueberschriften. **Ausnahme README.md: immer komplett.**
- **Fallback** (YAML, TOML, Shell, Dockerfile, …): erste 30 Zeilen, dann
  `… (N more lines)`.

`--full` schaltet alle Skimmer ab.

## Liquibase-/DB-Skimmer

Greift bei Pfaden mit `changelog` oder `liquibase` im Namen und Endung
`.xml`, `.yaml/.yml`, `.sql`. Ausgabe im eigenen Abschnitt „Database schema".

- **XML** via `xml.etree`: pro `changeSet` eine Zeile
  `id/author: createTable users(id uuid PK, email varchar(255) NOT NULL, …)`.
  Verstanden: `createTable addColumn dropColumn renameColumn
  addForeignKeyConstraint createIndex dropTable`, Rest → `other: <tag>`.
- **Formatted SQL** (`--changeset author:id`): Regex auf `CREATE TABLE`,
  `ALTER TABLE … ADD/DROP`, `CREATE INDEX`.
- **YAML:** Regex auf `- changeSet:` / `createTable:` / `tableName:` /
  `column: name:`. Grob, dokumentiert.

**v1 = Chronologie pro Changeset. v2 = Replay zum Endschema.** Datenstruktur
`dict[table, list[column]]` so anlegen, dass Replay spaeter nur eine Funktion
ist. Flyway/Alembic/Django-Migrationen koennen spaeter denselben SQL-Pfad nutzen.

## Secret-Scan

Immer an, `--no-secret-scan` zum Abschalten. ~8 Regexe: AWS-Keys, `ghp_`/`gho_`,
Slack-Tokens, `-----BEGIN … PRIVATE KEY-----`, Google-API-Keys, JWTs, Stripe
`sk_live_`, generisch `(api[_-]?key|secret|password)\s*[=:]\s*["'][^"']{8,}`.
Pfad-Regel: `.env*`, `*.pem`, `*.key`, `id_rsa*` werden nie gelesen; Ausnahmen
`.env.example`/`.sample`/`.template`/`.dist`. Platzhalter-Werte (`${…}`, `your-…`,
`changeme`) loesen den generischen Regex nicht aus.
Treffer → Datei im Baum mit `⚠ skipped (possible secret)`, Meldung auf stderr.
Kein Entropie-Check in v1 (False Positives bei Hashes/Base64).

## Output `SKIM.md`

```
# <repo-name>
Branch main @ 3f2a9c1 · 2026-10-01 · 142 files · ~38k tokens (est.) · mode: skim

## Structure          ASCII-Baum, Skips mit Vermerk
## Largest files      Top 10 nach geschaetzten Tokens
## Database schema    nur wenn Liquibase/SQL-Migrationen gefunden
## Files              pro Datei: ### path · 12 commits · ~400 tokens, Codeblock
## Recent changes     nur bei --diff / --log
```

Token-Schaetzung: `len(text) / 4`, als „est." markiert.

## Fehlerbehandlung

- `git` fehlt / kein Repo → Exit 1, klare Meldung.
- Clone fehlgeschlagen → git-stderr durchreichen, Exit 1.
- Datei nicht UTF-8 → `errors="replace"`, weiter.
- Einzelne Datei kaputt → Warnung auf stderr, weiter.
- Tempdir immer aufraeumen (`finally`).

## Tests

`tests/test_gitskim.py` mit `unittest` (Stdlib). Fixtures: temporaeres Git-Repo
per `subprocess` aufbauen. Abgedeckt: Filterkette, Ranking, Python-ast-Skimmer,
Regex-Skimmer, Liquibase-XML, Secret-Scan, Render-Header.

## Repo-Layout

```
gitskim/
├── gitskim.py
├── README.md          Pitch, Usage, Vergleichstabelle
├── LICENSE            MIT
├── tests/test_gitskim.py
├── examples/SKIM-self.md   Dogfooding-Demo
└── docs/plans/        Design + Implementierungsplan
```

## Spaeter (nicht v1)

Liquibase-Replay zum Endschema · Flyway/Alembic/Django · `pyproject.toml` +
PyPI · `--copy` · Token-Budget mit automatischem Fallback skim/full pro Datei ·
Entropie-Check.
