# Coding-Agent im regulierten Umfeld – Marktanalyse und Eigenbau-Bewertung

*Datum: 2026-11-09 · Status: Analyse, keine Implementierung*

## Fragestellung

gitskim loest "Repo -> Kontext fuer ein LLM" (Vergleich mit Repomix, gitingest,
code2prompt steht im README). Der naechste Schritt waere ein **Agent** wie Claude
Code oder Codex CLI: das Modell liest selbst Dateien, sucht, editiert, fuehrt
Befehle aus und iteriert. Randbedingung: reguliertes Umfeld, nur Python, keine
frei gezogenen Abhaengigkeiten (kein npm, cargo, Go-Binary, idealerweise nur
Stdlib).

Untersucht (Code geklont und gelesen): **Aider**, **Codex CLI**, **opencode**,
**mini-swe-agent**. Dazu ein Marktueberblick per Web-Recherche.

## 1. Marktueberblick (Stand Nov 2026)

| Tool | Sprache | Abhaengigkeiten | Agent-Loop mit Tools | Python-only tauglich |
|---|---|---|---|---|
| Codex CLI (OpenAI) | Rust | Binary/npm | ja, mit OS-Sandbox | nein |
| opencode (sst) | TypeScript/Bun | npm | ja | nein |
| Cline, Kilo Code, Continue CLI, Qwen Code | TypeScript | npm | ja | nein |
| Goose, Crush, Plandex | Rust/Go | Binary | ja | nein |
| Pi (pi-mono) | TypeScript | npm | ja, nur 4 Tools | nein, aber gutes Minimal-Vorbild |
| **Aider** | Python | ~105 gepinnte Pakete (litellm, tree-sitter, networkx, numpy, scipy, ...) | **nein** (Chat + Edit-Format) | nur mit grossem Vetting |
| **OpenHands** | Python | SDK + Tools: litellm, fastmcp, browser-use, tree-sitter, ...; Docker bevorzugt | ja, sehr vollstaendig | schwer (Abhaengigkeiten) |
| gptme | Python | ~25 (openai, anthropic, mcp, tree-sitter, ipython, ...) | ja | mittel |
| Kimi Code CLI | Python | ~29 (fastapi, uvicorn, aiohttp, ...) | ja | mittel, Anbieter-Herkunft pruefen |
| **mini-swe-agent** | Python | 14, Kern braucht fast nichts | ja, nur `bash` | **Kern ist abschreibbar** |
| smolagents (HF) | Python | 6 | Framework, kein Coding-CLI | als Basis denkbar |
| Gemini CLI, Roo Code, Mentat, Open Interpreter (Python) | – | – | – | eingestellt bzw. abgeloest |

Auffaelligkeiten:

- **Fast alle Python-Agenten haengen an `litellm`.** litellm wurde im Maerz 2026
  auf PyPI kompromittiert (1.82.7 / 1.82.8, Backdoor; mini-swe-agent schliesst
  diese Versionen in `pyproject.toml` explizit aus). Fuer ein reguliertes Umfeld
  ist das allein ein Ausschlussgrund fuer "einfach installieren". (Ein weiterer
  gemeldeter Vorfall im Oktober 2026 ist nicht gegengeprueft.)
- **Codex CLI spricht nur noch die Responses API.** Ein internes Gateway, das
  nur `/v1/chat/completions` anbietet, braucht davor einen Uebersetzer.
- Die wirklich guten Agenten sind TypeScript/Rust. In Python gibt es entweder
  schwer (OpenHands) oder minimal (mini-swe-agent), dazwischen wenig Solides.

## 2. Bester Konkurrent im Detail

Fuer "Python, ausgereift, Code lesbar" ist das **Aider** (Apache-2.0, ~20k LOC
in `aider/`). Als Referenz fuer das, was ein *echter* Agent ist, dazu **Codex
CLI** und **opencode**.

### Aider – Feature-Set

| Feature | Dateien (LOC) | Bewertung fuer Nachbau |
|---|---|---|
| Hauptschleife | `coders/base_coder.py` (2485) | Kern, aber Architektur nicht uebernehmen (s. u.) |
| Edit-Formate: whole, SEARCH/REPLACE, udiff, patch, editor-* | `coders/*_coder.py` (je 150–700) | **ein** Format reicht |
| Architect-Modus (Planer-Modell -> Editor-Modell) | `architect_coder.py` (48) | billig, nuetzlich bei schwachen Modellen |
| Repo-Map: tree-sitter-Tags, Graph, PageRank, Token-Budget per Binaersuche | `repomap.py` (867) + 32 `.scm`-Queries | Idee sehr gut, gitskim hat schon die Haelfte |
| Git: Auto-Commit mit LLM-Commit-Message, `/undo`, `/diff` | `repo.py` (622) | einfach per `git`-CLI |
| Lint/Test-Loop: Fehler gehen als naechste User-Nachricht zurueck (max. 3 Runden) | `linter.py` (304) | einfach |
| ~40 Slash-Commands | `commands.py` (1712) | 10 reichen |
| History-Zusammenfassung durch "weak model" | `history.py` (143) | noetig |
| Prompt-Caching (Anthropic), Cache-Warming-Thread | `chat_chunks.py` | optional |
| Modell-Datenbank | `resources/model-settings.yml` (3128 Zeilen) | fuer 1–2 interne Modelle unnoetig |
| Voice, Streamlit-GUI, Watch-Mode, `/web` (Playwright), Clipboard, OpenRouter-OAuth | diverse | weglassen |

### Was Aider *nicht* ist

Aider ist **kein Tool-Calling-Agent**. Tools werden nur gesendet, wenn
`functions` gesetzt ist (`models.py:1006`), und alle Function-Coder sind
auskommentiert. Ablauf pro User-Eingabe: Prompt bauen (System, Repo-Map,
Dateien im Chat) -> eine Antwort -> Edit-Bloecke anwenden -> ggf. bis zu 3
Reparaturrunden. Das Modell kann nicht selbst greppen oder Dateien oeffnen; es
nennt Dateinamen und der User bestaetigt. Shell-Befehle schlaegt das Modell in
```` ```bash ````-Bloecken vor, der User bestaetigt jeden einzeln.

Das war 2023 die richtige Wahl (Modelle konnten kein verlaessliches Tool
Calling). 2026 ist es der Hauptgrund, warum sich Aider weniger "agentisch"
anfuehlt als Claude Code. Fuer den Nachbau: **die Architektur von Codex/opencode
nehmen, die Detail-Ideen von Aider.**

Gute Detail-Ideen aus Aider:

- SEARCH/REPLACE-Matching gestaffelt: exakt, dann Einrueckung ignorieren, dann
  fuehrende Leerzeile weg, dann `...`-Elision (`editblock_coder.py:157`). Die
  Fuzzy-Stufe mit `SequenceMatcher` ist uebrigens toter Code (nacktes `return`
  in Zeile 183) – exakt plus whitespace-tolerant genuegt also in der Praxis.
- Fehlermeldung bei nicht passendem Block mit "Did you mean these actual lines"
  (`find_similar_lines`) – das bringt Modelle zuverlaessig auf Kurs.
- Repo-Map-Gewichtung: Bezeichner im Chat x10, lange snake/camel-Namen x10,
  `_privat` x0.1, Dateien im Chat x50 (`repomap.py:487-514`).

### Risiken von Aider im regulierten Umfeld

- **Telemetrie:** fest eingebauter PostHog-Key (`analytics.py:56`), Opt-in-Abfrage
  bei zufaelligen 10 % der User (`PERCENT = 10`). Abschaltbar, aber vorhanden.
- **Netzwerk beim Start:** taeglicher Versions-Check gegen pypi.org
  (`versioncheck.py:78`, Default an), Modell-Metadaten von
  `raw.githubusercontent.com` (`models.py:163`), litellm laedt ggf. seine
  Kostentabelle nach.
- **Kein Sandbox, kein Regelwerk:** Befehle laufen mit `shell=True` und vollen
  Rechten; Schutz ist nur y/n-Abfrage, `--yes-always` hebelt alles aus.
- **Projektzustand:** seit 0.86 (Aug 2025) keine Feature-Releases mehr.

### Codex / opencode – was ein "echter" Agent braucht

Beide teilen denselben Kern (Codex: `core/src/session/turn.rs`, opencode:
`core/src/session/runner/`):

1. **Loop:** Modell aufrufen mit Tool-Definitionen -> Tool-Calls ausfuehren ->
   Ergebnisse anhaengen -> wiederholen, bis keine Tool-Calls mehr kommen.
   Lesende Tools parallel, schreibende serialisiert (Codex: RwLock in
   `tools/parallel.rs`).
2. **Tools** (wenige!): Shell, Datei lesen, Patch/Edit, Schreiben, Grep/Glob,
   Plan/Todo, optional Subagent. Codex hat nicht einmal Read/Grep – das laeuft
   ueber die Shell mit `rg`.
3. **Edit-Format:** Codex-`apply_patch` (Grammatik in 19 Zeilen,
   `core/assets/tools/apply_patch.lark`; Add/Update/Delete/Move, Kontext-Zeilen,
   4-stufiges Fuzzy-Matching inkl. Unicode-Normalisierung). opencode hat das
   Format uebernommen – es funktioniert also auch modelluebergreifend.
   Alternative: Claude-Code-artiges `edit(old, new)` mit Eindeutigkeitspruefung.
4. **Kompaktierung:** bei ~80 % Kontext Zusammenfassung als "Handoff"
   (Fortschritt, Entscheidungen, Dateien, naechster Schritt), letzte Nachrichten
   bleiben woertlich.
5. **Sicherheit:**
   - Codex: OS-Sandbox (Linux: seccomp + bubblewrap, macOS: Seatbelt),
     Modi `read-only` / `workspace-write` / `danger-full-access`, Freigabe-Policies
     `untrusted` / `on-request` / `never`, Befehls-Prefix-Regeln; Befehle werden an
     `| && ;` zerlegt, Redirects/Substitutionen nie automatisch freigegeben.
   - opencode: keine OS-Sandbox, aber Regelwerk `{tool, glob, allow|ask|deny}`,
     letzte passende Regel gewinnt, Default `ask`.
6. **Projektregeln:** `AGENTS.md` vom Git-Root bis cwd einsammeln, Byte-Limit.
7. **Sessions:** JSONL-Log pro Session, `resume` spielt es ab. Gleichzeitig
   Audit-Trail.
8. **Headless:** `codex exec --json` bzw. `opencode run` fuer CI.

Groessenordnung: Codex ~1,2 Mio Zeilen Rust (davon TUI 326k), opencode ~770k
TypeScript (UI ist der Grossteil). Der **fachliche Kern** ist in beiden klein;
der Umfang steckt in UI, Providern, MCP, Plattform-Sandboxes.

**mini-swe-agent** zeigt das Extrem: Agent-Klasse 190 Zeilen, Umgebung 92
Zeilen, einziges Tool ist `bash` (jeder Befehl ein frischer `subprocess`, kein
persistenter Shell-Zustand), laut README >74 % auf SWE-bench Verified. Die
Qualitaet kommt also ueberwiegend vom **Modell**, nicht vom Werkzeug.

## 3. Bewertung: Eigenbau in Python-Stdlib

### Machbarkeit

Technisch **gut machbar**. Alles Notwendige liegt in der Stdlib:

| Baustein | Stdlib-Mittel | Aufwand (LOC) |
|---|---|---|
| LLM-Client (OpenAI-kompatibel, Streaming per SSE, Tool-Calls, Retry/Backoff) | `urllib.request`, `json`, `ssl` | 400–550 |
| Agent-Loop (Turns, Limits fuer Schritte/Zeit/Tokens, Ctrl-C, Format-Fehler zurueckspielen) | – | 250–350 |
| Tools: `shell`, `read`, `grep`, `glob`, `apply_patch`/`edit`, `write`, `plan` | `subprocess`, `pathlib`, `re`, `fnmatch`, `difflib` | 700–900 |
| Berechtigungen: Regeln allow/ask/deny, Pfad-Scoping auf Repo, Befehlszerlegung, Freigabe-Modi | `shlex`, `fnmatch` | 300–400 |
| Audit-/Session-Log (JSONL), Resume | `json` | 100–150 |
| Kompaktierung (Token-Schaetzung, Handoff-Zusammenfassung) | – | 150–250 |
| Git: Checkpoint vor Edits, Undo, Diff | `git`-CLI | 150–200 |
| Repo-Map | **aus gitskim** (`skim_python`, `skim_regex`, Ranking) + PageRank (~40 LOC) | 150–300 |
| REPL: `readline`, ~10 Slash-Commands, ANSI-Farben, Diff-Anzeige | `readline`, `cmd` | 300–400 |
| Config (`tomllib`/INI), `AGENTS.md` | `configparser` (wie gitskim) | 100–150 |
| Headless-Modus `-p "..." --json` | `argparse` | 50–100 |
| **Summe Kern** | | **~2,7–3,8k** |
| Tests | `unittest` | +1,5–2k |
| optional: MCP-Client (stdio JSON-RPC) | `subprocess`, `json` | 300–400 |
| optional: OS-Sandbox via `bwrap`, falls im Umfeld vorhanden | `subprocess` | 100–150 |

Zum Vergleich: gitskim ist heute ~1,8k Zeilen Code plus ~1,3k Zeilen Tests. Der Agent waere
ungefaehr **das Zwei- bis Dreifache von gitskim**.

### Was der Eigenbau *nicht* loest

Das sind die eigentlichen Risiken, nicht der Code:

1. **Modellqualitaet.** Der Abstand zwischen einem 100-Zeilen-Agent und Claude
   Code ist kleiner als gedacht – *wenn* das Modell stark ist. Mit einem
   schwachen internen Modell hilft auch das beste Werkzeug wenig. Entscheidend:
   kann das interne Modell **zuverlaessig Tool Calling** (OpenAI `tools`-Format)?
   Wenn nein, braucht es einen Text-Fallback (wie mini-swe-agent: ein Befehl pro
   Antwort in einem Code-Fence) und eher Aiders "Architect"-Ansatz.
2. **Sicherheit der Befehlsausfuehrung.** Ein Agent, der Shell-Befehle ausfuehrt,
   ist im regulierten Umfeld der kritische Punkt. Ohne OS-Sandbox bleibt nur:
   Regelwerk + Freigabe pro Befehl + Audit-Log + Ausfuehrung in einem
   Wegwerf-Container/VM. Das ist eine **Betriebs**-Frage, keine Code-Frage.
3. **Externes LLM + Agent vertraegt sich schlecht mit Anonymisierung.** gitskim
   anonymisiert *einen* Text, den man vorher pruefen kann. In einem Agent fliesst
   jedes Tool-Ergebnis (Dateiinhalte, Befehlsausgaben, Fehlermeldungen) ungeprueft
   ans Modell, und Tool-Aufrufe des Modells (Pfade, Bezeichner) muessten
   zurueckuebersetzt werden. Technisch ginge das mit `AnonMap` in beide
   Richtungen, aber es gibt keine menschliche Kontrolle mehr vor dem Versand.
   **Ein Agent gehoert an ein internes Modell.** Fuer externe Modelle bleibt
   gitskim + manuelle Pruefung der saubere Weg.
4. **Pflege.** Modelle aendern ihr Verhalten; Prompts und Edit-Format brauchen
   laufende Nachjustierung. Bei Codex/opencode macht das ein grosses Team.

### Meine Meinung

- **Nicht forken, neu bauen – aber schmal.** Kein vorhandenes Python-Tool ist
  ohne grosses Abhaengigkeits-Vetting einsetzbar (litellm ist ueberall drin).
  Aider forken heisst 20k Zeilen alte Architektur plus ~105 Pakete
  zurueckbauen; das ist mehr Arbeit als ein sauberer Neubau.
- **Architektur von Codex/opencode, Minimalismus von mini-swe-agent / Pi,
  Edit-Details von Aider.** Eine Datei bzw. ein kleines Paket, Stdlib-only, wie
  gitskim.
- **gitskim ist kein Wegwerf-Vorlaeufer, sondern ein Baustein:** Datei-Auswahl
  (`collect_files`), Ranking nach Aenderungshaeufigkeit, Skimmer als Repo-Map,
  Secret-Scan (als Lese-Sperre fuer das `read`-Tool), Config-Mechanik.
- **Realistischer Aufwand:** ein brauchbarer Prototyp (Loop, 6 Tools, Regeln,
  JSONL-Log, REPL) in **1–2 Wochen** konzentrierter Arbeit mit KI-Unterstuetzung;
  produktionsreif fuer ein reguliertes Team (Tests, Freigabe-Modi, Review der
  Sicherheitsregeln, Doku, Evaluierung mit dem internen Modell) eher **4–8
  Wochen**. Die meiste Zeit geht in Prompt-Tuning und Sicherheit, nicht in Code.
- **Bewusst weglassen:** Multi-Provider, TUI-Framework, MCP (anfangs), Web-Fetch,
  Subagenten, Voice, GUI, Telemetrie, Auto-Updates. Jede Netzverbindung ausser
  zum konfigurierten LLM-Endpoint ist verboten und testbar.

### Vorschlag fuer Phasen

1. **Spike (2–3 Tage):** 300-Zeilen-Agent im Stil mini-swe-agent (nur `shell`,
   Freigabe pro Befehl) gegen das interne Modell. Beantwortet die wichtigste
   Frage: taugt das Modell als Agent?
2. **MVP:** Tool-Calling-Loop, Tools `read`/`grep`/`glob`/`edit`/`write`/`shell`/`plan`,
   Regelwerk + Freigabe-Modi, JSONL-Audit, `AGENTS.md`, Git-Checkpoint/Undo.
3. **Ausbau:** Kompaktierung, Repo-Map aus gitskim, Headless-Modus fuer CI,
   Resume, ggf. `bwrap`-Sandbox.
4. **Optional:** MCP-Client, Subagent fuer read-only Recherche, Architect-Modus.

## 4. Offene Fragen

1. **Welches LLM?** Internes Modell (welches, welche Kontextgroesse?), und
   bietet das Gateway eine OpenAI-kompatible API mit `tools`/Function Calling
   und Streaming? Oder soll ein externes Modell (Claude, GPT) angebunden werden?
2. **"Python-nativ" genau:** strikt Stdlib, oder gibt es einen internen
   PyPI-Mirror mit freigegebenen Paketen (z. B. `httpx`, `prompt_toolkit`)?
   Darf `git` vorausgesetzt werden (gitskim tut das bereits)? `rg`?
3. **Ausfuehrungsumgebung:** Entwickler-Laptop (Windows? Linux?), Jump-Host,
   Container? Ist `bwrap`/Docker verfuegbar? Windows wuerde die Shell-Seite
   (PowerShell vs bash) deutlich beeinflussen.
4. **Autonomiegrad:** soll der Agent Befehle selbst ausfuehren duerfen (mit
   Regeln), oder reicht "liest selbst, schlaegt Edits und Befehle vor, Mensch
   bestaetigt"? Letzteres ist im regulierten Umfeld leichter freizugeben.
5. **Nachweis/Audit:** gibt es Anforderungen an Protokollierung (wer hat welche
   Aenderung durch welchen Prompt ausgeloest)?
6. **Ort:** eigenes Repo, oder als zweites Werkzeug neben `gitskim.py` hier?
