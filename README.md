# gitskim

Skim a git repository into **one compact Markdown file** for LLM context.

- **Single file, stdlib only.** `python3 gitskim.py <repo>`. No pip install, no token, no Rust, no Node.
- **Signatures, not bodies.** Default mode keeps structure: imports, classes, function signatures, docstrings. Python via `ast`, other languages via heuristics. `--full` for everything.
- **Git does the heavy lifting.** `git ls-files` gives the file set with correct `.gitignore` semantics. `git log` ranks files by how often they change, so the important ones come first.
- **Database schema section** from Liquibase changelogs (XML, formatted SQL, YAML) and SQL migrations.
- **Secret heuristics** on by default: files that look like they contain keys, tokens or passwords are skipped and flagged.
- **Anonymization** for sending company code to an external LLM: company, customer, people, host and project names become stable pseudonyms (`Acme` → `Company1`), and LLM answers can be translated back.

## Usage

    python3 gitskim.py .                       # -> <repo>_2026-10-12_1432.md
    python3 gitskim.py ~/code/project -o ctx.md
    python3 gitskim.py https://github.com/user/repo --stdout | pbcopy
    python3 gitskim.py . --full --include "src/*" --exclude "*.test.ts"
    python3 gitskim.py . --diff --log 20        # append working-tree diff + last 20 commits
    python3 gitskim.py ~/code                   # folder of repos -> one combined file
    python3 gitskim.py . --output-dir ~/skims   # -> ~/skims/<repo>_2026-10-12_1432.md
    python3 gitskim.py api                      # project from the configured workspace (see Config file)

| Flag | Default | Meaning |
|---|---|---|
| `-o FILE` | `<repo>_<YYYY-MM-DD_HHMM>.md` | output file (written to the current directory by default) |
| `--output-dir DIR` | `$GITSKIM_OUTPUT_DIR`, then config | write into DIR (created if missing) under the default name, or as `DIR/FILE` together with `-o` |
| `--stdout` | | print instead of writing a file |
| `--full` | off | full file contents instead of signatures |
| `--include GLOB` | | only matching files (repeatable) |
| `--exclude GLOB` | | skip matching files (repeatable) |
| `--max-size KB` | `100` (or config) | larger files appear in the tree only |
| `--no-default-ignore` | | keep lockfiles, minified assets, images, and ignored directories |
| `--untracked` | off | include untracked, non-ignored files |
| `--sort changes\|path` | `changes` | file order |
| `--diff` | off | append `git diff` + staged diff |
| `--log N` | `0` | append last N commits |
| `--no-secret-scan` | | disable secret heuristics |
| `--anonymize` | off (or config) | replace names with stable pseudonyms, see [Anonymization](#anonymization) |
| `--no-anonymize` | | turn it off when the config enables it |
| `--anon-prompt` | | write an LLM prompt plus the NOT anonymized skim, to build the config with an internal LLM |
| `--deanonymize [FILE]` | stdin | translate pseudonyms back to the originals, print to stdout |
| `--anon-map FILE` | `anon-map.json` next to the config | pseudonym mapping |
| `--multi` | | scan the folder for repos even if it is a repo itself |
| `--scan-depth N` | `3` (or config) | how deep to look for repos in multi-repo mode and the workspace |
| `--config FILE` | `$GITSKIM_CONFIG` or `~/.config/gitskim/config.ini` | config file |
| `--init-config` | | write a commented config template and exit |
| `--list` | | list the projects in the configured workspace and exit |
| `-q`, `--quiet` | | no progress messages; warnings and errors still go to stderr |

Progress goes to stderr: one line per repo with its file count, a `n/total files …` line every 2 s on large repos, and the output path and run time at the end.

Requires Python ≥ 3.9 and `git` on PATH. The input must be a git repository (local path or clone URL), a folder containing git repositories, or project names from the configured workspace.

### Config file

`python3 gitskim.py --init-config` writes a commented template to `~/.config/gitskim/config.ini` (or `$XDG_CONFIG_HOME/gitskim/config.ini`; `$GITSKIM_CONFIG` or `--config FILE` point elsewhere). A missing default file simply means no config.

```ini
[gitskim]
workspace = ~/code          # folder with your git projects
output_dir = ~/skims        # where output files go
exclude =                   # file globs never included, one per line or comma-separated
    *.log
    *.csv
ignore_dirs =               # directory names skipped anywhere, on top of node_modules, dist, ...
    generated
max_size = 100
scan_depth = 3
```

With a workspace configured:

- `gitskim api` skims `<workspace>/api` into `<output_dir>/api_<timestamp>.md`. Projects are found like in multi-repo mode and can be named by their path below the workspace (`team/web`) or, when unique, by folder name (`web`). A name that matches a project wins over a same-named folder in the current directory.
- `gitskim api web` skims several projects into one file (`api+web_<timestamp>.md`); plain `gitskim` skims the whole workspace.
- `gitskim --list` shows the project names. Paths and URLs keep working as before.

Command-line flags win over the config. `--exclude` adds to the configured `exclude` list; config `exclude` and `ignore_dirs` always apply, even with `--include` or `--no-default-ignore`. The output folder is taken from `--output-dir`, then `$GITSKIM_OUTPUT_DIR`, then `output_dir`.

### Multiple repositories

If the path is a folder that is not itself inside a git repository, gitskim looks for repositories below it (up to `--scan-depth` levels, skipping hidden and ignored directories, not descending into a found repo) and skims them all into one file. Use `--multi` to force this for a folder that is a repo itself.

- File paths are prefixed with the repo's path relative to the folder (`api/src/app.py`), so the tree, tables and file sections cover all repos together.
- A `## Repositories` table lists branch, commit, file count and tokens per repo.
- `--include` / `--exclude` match both the repo-relative and the prefixed path; `--exclude "legacy/*"` drops the `legacy` repo entirely.
- With `--sort changes`, files from all repos are ranked together by commit count.
- `--diff` / `--log` produce one subsection per repo.

Without `-o`, every run writes a new timestamped file, so add `*_20??-??-??_????.md` to the `.gitignore` of the repos you skim.

### Filtering rules

- `--exclude` always wins.
- The built-in ignore list has two parts: **file patterns** (lockfiles, minified bundles, images, archives, …) and **ignored directories** (`node_modules`, `.venv`, `venv`, `dist`, `build`, `vendor`, `target`, `coverage`, …).
- `--include` overrides the built-in file patterns (so `--include "*.png"` really gives you the PNGs) but **not** the ignored directories; `--include "*.js"` will not pull in `node_modules`. `--no-default-ignore` lifts both.
- Files larger than `--max-size` and binary files stay in the tree with a note but have no content section.

### Secret handling

Two heuristics, both on by default and disabled together by `--no-secret-scan`:

- **Filename:** `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `*.keystore` are skipped. Template variants are not: `.env.example`, `.env.sample`, `.env.template`, `.env.dist` and any `*.example` / `*.sample` / `*.template` file are treated as ordinary files.
- **Content:** AWS, GitHub, Slack, Google and Stripe key shapes, JWTs, `BEGIN … PRIVATE KEY` blocks, and quoted assignments to names ending in `api_key`, `secret_key`, `secret`, `password`, `passwd` or `token`. Obvious placeholders (`${VAR}`, `{{ var }}`, `<redacted>`, `your-…`, `changeme`, `example`, …) are not flagged.

Skipped files are listed in the tree with a warning marker and reported on stderr.

## Anonymization

For handing company code to an external LLM without naming the company, its customers, colleagues or internal hosts. Names are replaced consistently and by type, so the LLM still understands the code:

    package com.acme.billing;                        package com.company1.billing;
    // For Müller GmbH, ask Max Mustermann     →     // For Customer1, ask Person1
    class AcmeInvoice                                class Company1Invoice
    URL = "https://api.acme.internal"                URL = "https://api.domain1.example"

**First-time setup**, done once with an *internal* LLM, which knows which names matter:

1. `gitskim --anonymize api` finds no `[anonymize]` section, creates the config (or appends the section to an existing one), explains the next steps and exits with code 2 without writing anything. The section's comments contain the prompt.
2. `gitskim --anon-prompt api` writes `api_anon-prompt_<timestamp>.md`: the prompt, what gitskim already detects on its own, the current terms, and a normal, **not anonymized** skim.
3. Give that file to an internal LLM. It returns an `[anonymize]` block and a check list (term, kind, reason, location). It does not rewrite the skim; gitskim does that, so the output stays complete and matches the mapping.
4. Paste the block into the config. From then on `gitskim --anonymize api` (or `enabled = true`) writes the anonymized skim.

```ini
[anonymize]
enabled = true
terms =                 # "Name" or "Name = kind" (company, customer, person, project, ...; default company)
    Acme
    Müller GmbH = customer
    Erika Musterfrau = person
domains =               # hosts below them are replaced too
    acme.internal
keep =                  # never replaced
    Spring
```

**Detected automatically:** git authors (full name, name parts of 4+ letters and the e-mail login get the same `PersonN`), every e-mail address and IPv4 address in the text, internal git remote domains, remote owners and repo/folder names. Common words (`api`, `web`, `core`, `test`, …), loopback and documentation addresses, `example.com` and public git hosts are left alone. Terms shorter than 3 characters are ignored.

**Matching** is case-insensitive and keeps the case (`ACME` → `COMPANY1`, `acme` → `company1`), at word, camelCase and separator boundaries (`AcmeBilling`, `com.acme.api`, `acme_core`), but not inside a word (`Acmeist`) or before a digit (`acme2`). It runs over the whole rendered file, including tree, paths, `--log` and `--diff`; the default output file name uses the pseudonym too. The header says `mode: skim · anonymized`.

**Mapping:** `anon-map.json` next to the config (mode `0600`) is reused across runs and repos, so `Acme` stays `Company1`. It contains the originals: keep it out of any repository. `gitskim --deanonymize answer.md` (or `pbpaste | gitskim --deanonymize`) translates an LLM answer back.

**Leak check:** afterwards gitskim reports every original that still appears in any spelling, e.g. `'Acme' still appears 1x (first in line 37)`, and a summary such as `anonymized: 2 persons, 1 company, 1 domain`.

With `confirmed = true` and no terms, only the automatic detection runs. Names that are neither git authors nor in `terms` are not found, and code structure or comments can still give the company away: review the output before sharing it.

## Output

    # repo-name
    Branch main @ 3f2a9c1 · 2026-10-01 · 42/48 files · ~12.3k tokens (est.) · mode: skim

    ## Structure        ASCII tree, skipped files annotated
    ## Largest files    top 10 by estimated tokens
    ## Database schema  only when changelogs/migrations are found
    ## Files            one section per file, most-changed first
    ## Recent changes   only with --diff / --log

Every `README.md` is included in full in skim mode (as long as it is under `--max-size`); a very large README can dominate the token budget. See [`examples/SKIM-self.md`](examples/SKIM-self.md) for gitskim applied to itself.

## How it compares

| | gitskim | Repomix | gitingest | code2prompt |
|---|---|---|---|---|
| Install | copy one file | npm / npx | pip (deps) | cargo / brew |
| Dependencies | none | many (tree-sitter, secretlint, …) | several | Rust crates |
| Signature mode | yes (ast + heuristics) | yes (tree-sitter, more languages) | no | no |
| DB schema from changelogs | yes | no | no | no |
| Exact token count | no (estimate) | yes | yes | yes |
| Templates / TUI / MCP | no | yes | no | yes |

If you need exact token counts, many languages with precise parsing, or an MCP server, use Repomix. If you want something you can read in ten minutes and drop into any repo, use gitskim.

## Limitations

- Token counts are `len(text) / 4`, labelled as estimates.
- Non-Python skimming is regex-based and will miss or over-include lines in unusual code styles.
- Secret detection is a handful of patterns, not a security tool. Review output before sharing.
- Anonymization only knows git authors, the configured terms and e-mail/IPv4 shapes; it is a help, not a guarantee. IPv6 addresses are not replaced.
- Remote clones are shallow (`--depth 1`), so change-frequency ranking degrades to path order for them.
- Change-frequency ranking does not follow renames; a renamed file starts counting from zero.
- Change-frequency ranking looks at the last 500 commits only.
- On Windows, prefer `-o FILE` over `--stdout` on legacy consoles (the tree uses UTF-8 box characters); temporary clones may leave read-only files behind in `%TEMP%`.

## Development

    python3 -m unittest -v

MIT License.
