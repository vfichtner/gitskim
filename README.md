# gitskim

Skim a git repository into **one compact Markdown file** for LLM context.

- **Single file, stdlib only.** `python3 gitskim.py <repo>`. No pip install, no token, no Rust, no Node.
- **Signatures, not bodies.** Default mode keeps structure: imports, classes, function signatures, docstrings. Python via `ast`, other languages via heuristics. `--full` for everything.
- **Git does the heavy lifting.** `git ls-files` gives the file set with correct `.gitignore` semantics. `git log` ranks files by how often they change, so the important ones come first.
- **Database schema section** from Liquibase changelogs (XML, formatted SQL, YAML) and SQL migrations.
- **Secret heuristics** on by default: files that look like they contain keys, tokens or passwords are skipped and flagged.

## Usage

    python3 gitskim.py .                       # -> SKIM.md
    python3 gitskim.py ~/code/project -o ctx.md
    python3 gitskim.py https://github.com/user/repo --stdout | pbcopy
    python3 gitskim.py . --full --include "src/*" --exclude "*.test.ts"
    python3 gitskim.py . --diff --log 20        # append working-tree diff + last 20 commits

| Flag | Default | Meaning |
|---|---|---|
| `-o FILE` | `SKIM.md` | output file |
| `--stdout` | | print instead of writing a file |
| `--full` | off | full file contents instead of signatures |
| `--include GLOB` | | only matching files (repeatable) |
| `--exclude GLOB` | | skip matching files (repeatable) |
| `--max-size KB` | `100` | larger files appear in the tree only |
| `--no-default-ignore` | | keep lockfiles, minified assets, images, and ignored directories |
| `--untracked` | off | include untracked, non-ignored files |
| `--sort changes\|path` | `changes` | file order |
| `--diff` | off | append `git diff` + staged diff |
| `--log N` | `0` | append last N commits |
| `--no-secret-scan` | | disable secret heuristics |

Requires Python ≥ 3.9 and `git` on PATH. The input must be a git repository (local path or clone URL).

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
- Remote clones are shallow (`--depth 1`), so change-frequency ranking degrades to path order for them.
- Change-frequency ranking does not follow renames; a renamed file starts counting from zero.
- Change-frequency ranking looks at the last 500 commits only.
- On Windows, prefer `-o FILE` over `--stdout` on legacy consoles (the tree uses UTF-8 box characters); temporary clones may leave read-only files behind in `%TEMP%`.

## Development

    python3 -m unittest -v

MIT License.
