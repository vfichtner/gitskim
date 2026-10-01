# gitskim
Branch main @ 0c21edd · 2026-10-01 · 8/8 files · ~4.4k tokens (est.) · mode: skim

## Structure
```
├── docs/
│   └── plans/
│       ├── 2026-10-01-gitskim-design.md
│       └── 2026-10-01-gitskim-implementation.md
├── tests/
│   ├── __init__.py
│   └── test_gitskim.py
├── .gitignore
├── gitskim.py
├── LICENSE
└── README.md
```

## Largest files

| File | Commits | ~Tokens |
|---|---:|---:|
| README.md | 0 | 1.3k |
| gitskim.py | 13 | 1.2k |
| tests/test_gitskim.py | 14 | 1.1k |
| docs/plans/2026-10-01-gitskim-implementation.md | 1 | 391 |
| LICENSE | 1 | 267 |
| docs/plans/2026-10-01-gitskim-design.md | 1 | 141 |
| .gitignore | 1 | 9 |
| tests/__init__.py | 1 | 0 |

## Files

### tests/test_gitskim.py · 14 commits · ~1.1k tokens
```python
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import gitskim

ROOT = ...
def git(repo: Path, *args: str) -> str: ...

def make_repo(files: dict, extra_commits: dict | None=None, case: unittest.TestCase | None=None) -> Path:
    """Create a temp git repo. files: {relpath: str|bytes}. extra_commits: {relpath: new_content} committed one by one."""

class TestCli(unittest.TestCase):
    def test_help_exits_zero(self): ...

class TestResolveSource(unittest.TestCase):
    def test_local_repo_resolves_to_toplevel(self): ...
    def test_non_repo_raises(self): ...
    def test_missing_dir_raises(self): ...
    def test_local_url_clone(self): ...
    def test_bad_url_raises(self): ...

class TestRepoName(unittest.TestCase):
    def test_ssh_scp_style(self): ...
    def test_https_with_suffix(self): ...
    def test_trailing_slash_no_suffix(self): ...

class TestRunGit(unittest.TestCase):
    def test_run_git_returns_stdout(self): ...
    def test_run_git_failure_raises(self): ...

class TestCollectFiles(unittest.TestCase):
    def setUp(self): ...
    def paths(self, entries): ...
    def test_default_ignore_drops_lockfiles_dist_and_images(self): ...
    def test_no_default_ignore_keeps_them(self): ...
    def test_include_and_exclude_globs(self): ...
    def test_max_size_marks_too_large_but_keeps_in_tree(self): ...
    def test_binary_sniff(self): ...
    def test_include_bypasses_default_ignore(self): ...
    def test_exclude_beats_include(self): ...
    def test_default_ignore_is_case_insensitive(self): ...
    def test_nested_ignored_dir(self): ...
    def test_include_keeps_ignore_dirs_active(self): ...
    def test_untracked_files_only_with_flag(self): ...

class TestRankFiles(unittest.TestCase):
    @staticmethod
    def touch(repo, rel, content): ...
    def test_sorted_by_commit_count_then_path(self): ...
    def test_non_ascii_path_counts(self): ...
    def test_sort_path(self): ...

class TestTokensAndSecrets(unittest.TestCase):
    def test_estimate_tokens(self): ...
    def test_fmt_tokens(self): ...
    def test_detects_aws_key(self): ...
    def test_detects_private_key_block(self): ...
    def test_detects_generic_assignment(self): ...
    def test_generic_assignment_ignores_lookalikes_and_placeholders(self): ...
    def test_detects_vendor_tokens(self): ...
    def test_clean_text_passes(self): ...
    def test_secret_paths(self): ...
    def test_is_secret_path_exempts_templates(self): ...

PY_SAMPLE = ...
class TestSkimPython(unittest.TestCase):
    def setUp(self): ...
    def test_module_docstring_first_line(self): ...
    def test_imports(self): ...
    def test_constants(self): ...
    def test_class_with_bases_fields_and_methods(self): ...
    def test_empty_class(self): ...
    def test_top_level_functions(self): ...
    def test_no_bodies(self): ...
    def test_syntax_error_falls_back(self): ...
    def test_nested_class(self): ...
    def test_long_default_truncated(self): ...
    def test_module_constant_annotation_keeps_ellipsis(self): ...
    def test_bom_is_stripped(self): ...

TS_SAMPLE = ...
JAVA_SAMPLE = ...
class TestSkimRegex(unittest.TestCase):
    def test_typescript_keeps_declarations_drops_bodies(self): ...
    def test_java_keeps_annotations_and_methods(self): ...
    def test_caps_output(self): ...

class TestSkimMarkdown(unittest.TestCase):
    def test_headings_only(self): ...
    def test_no_headings_falls_back(self): ...

class TestRegistry(unittest.TestCase):
    def test_skimmer_for_ext(self): ...
    def test_lang_for(self): ...

LB_XML = ...
LB_SQL = ...
LB_YAML = ...
class TestLiquibase(unittest.TestCase):
    def test_is_db_changelog(self): ...
    def test_xml(self): ...
    def test_xml_parse_error_returns_empty(self): ...
    def test_sql(self): ...
    def test_yaml(self): ...

class TestRenderTree(unittest.TestCase):
    def test_tree_with_notes(self): ...

class TestEndToEnd(unittest.TestCase):
    def setUp(self): ...
    def run_cli(self, *args): ...
    def test_skim_mode_output(self): ...
    def test_full_mode(self): ...
    def test_no_secret_scan_includes_config(self): ...
    def test_output_file(self): ...
    def test_not_a_repo_exits_1(self): ...

class TestDiffAndLog(unittest.TestCase):
    def test_log_and_diff_sections(self): ...
    def test_no_section_without_flags(self): ...
```

### gitskim.py · 13 commits · ~1.2k tokens
```python
"""gitskim – skim a git repository into one compact Markdown file for LLM context."""
from __future__ import annotations
import argparse
import ast
import datetime as _dt
import fnmatch
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

class GitskimError(Exception):
    """Fatal, user-facing error. main() prints it and exits 1."""

@dataclass
class Options:
    full: bool = False
    include: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    max_size_kb: int = 100
    default_ignore: bool = True
    untracked: bool = False
    sort: str = 'changes'
    secret_scan: bool = True

@dataclass
class FileEntry:
    path: str
    size: int
    status: str = 'ok'
    note: str = ''
    commits: int = 0
    tokens: int = 0
    lang: str = ''
    content: str = ''
    db_schema: list = field(default_factory=list)

def warn(msg: str) -> None: ...

URL_RE = ...
def run_git(repo: Path, *args: str) -> str:
    """Run a git command in repo and return stdout. Raises GitskimError on failure."""

def repo_name_from_url(url: str) -> str:
    """Last path segment of a git URL without .git, e.g. git@host:user/repo.git -> repo."""

def resolve_source(arg: str) -> tuple:
    """Return (repo_path, tempdir_or_None, repo_name)."""

def cleanup(tmp: Optional[Path]) -> None: ...

IGNORE_DIRS = ...
DEFAULT_IGNORE = ...
def matches_any(rel: str, patterns: list, casefold: bool=False) -> bool:
    """fnmatch against the full relative path and the basename."""

def in_ignored_dir(rel: str) -> bool: ...

def is_binary(path: Path) -> bool: ...

def collect_files(repo: Path, opts: Options) -> list:
    """List candidate files via git ls-files and apply the filter chain."""

def rank_files(repo: Path, entries: list, sort: str='changes', max_commits: int=500) -> list:
    """Sort entries in place. 'changes' = most-committed first (LLMs read top-down)."""

def estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 characters per token. Marked 'est.' in output."""

def fmt_tokens(n: int) -> str: ...

SECRET_PATHS = ...
SECRET_PATH_EXCEPTIONS = ...
def is_secret_path(rel: str) -> bool:
    """Secret-looking filename, unless it is an example/template variant."""

SECRET_PATTERNS = ...
def find_secret(text: str) -> Optional[str]:
    """Return a short label for the first secret-looking pattern found, else None."""

FALLBACK_LINES = ...
def skim_fallback(text: str, keep: int=FALLBACK_LINES) -> str:
    """Unknown file type: first N lines, then a count of the rest."""

def _first_doc_line(node) -> Optional[str]: ...

def _py_func(node, indent: int) -> list: ...

MAX_DEFAULT_LEN = ...
def _py_value(node) -> str:
    """Unparsed default/value, truncated so giant literals don't bloat the skim."""

def _py_class(node, indent: int=0) -> list: ...

def skim_python(text: str) -> str:
    """Signatures only: docstring, imports, constants, classes, functions."""

_MODIFIERS = ...
_KEYWORDS = ...
SIG_RE = ...
ANNOTATION_RE = ...
CONTROL_RE = ...
def skim_regex(text: str, max_lines: int=200) -> str:
    """Heuristic skimmer for brace languages: declarations and annotations, no bodies."""

HEADING_RE = ...
def skim_markdown(text: str) -> str: ...

LANG_BY_EXT = ...
LANG_BY_NAME = ...
REGEX_EXTS = ...
SKIMMERS: dict = ...
def skimmer_for(rel: str) -> Callable[[str], str]: ...

def lang_for(rel: str) -> str: ...

DB_PATH_RE = ...
DB_EXTS = ...
def is_db_changelog(rel: str) -> bool: ...

def _tag(el) -> str: ...

def _xml_col(col) -> str: ...

def _xml_cols(el) -> str: ...

_XML_SKIP = ...
def _xml_op(ch) -> Optional[str]: ...

def skim_liquibase_xml(text: str) -> list: ...

SQL_CHANGESET_RE = ...
SQL_DDL_RE = ...
def skim_liquibase_sql(text: str, max_len: int=200) -> list:
    """One line per --changeset with its DDL statements, whitespace squashed."""

_YAML_OPS = ...
def skim_liquibase_yaml(text: str) -> list:
    """Very rough YAML changelog reader: changeSet id/author, op, tableName, columns."""

def skim_db_changelog(rel: str, text: str) -> list: ...

def _tree_note(e: FileEntry) -> str: ...

def render_tree(entries: list) -> str:
    """ASCII tree. Directories first, then files, both case-insensitively sorted."""

def process_file(repo: Path, e: FileEntry, opts: Options) -> None:
    """Fill content/tokens/lang/db_schema for one 'ok' entry. Mutates e."""

def _fence(content: str) -> str: ...

def render(name: str, branch: str, commit: str, entries: list, opts: Options, diff: str='', log: str='') -> str: ...

def build_parser() -> argparse.ArgumentParser: ...

def repo_meta(repo: Path) -> tuple: ...

def main(argv: Optional[list]=None) -> int: ...
```

### .gitignore · 1 commits · ~9 tokens
```
__pycache__/
*.pyc
/SKIM.md
.DS_Store
```

### LICENSE · 1 commits · ~267 tokens
```
MIT License

Copyright (c) 2026 Vitali Fichtner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### docs/plans/2026-10-01-gitskim-design.md · 1 commits · ~141 tokens
```markdown
# gitskim – Design (v1)
## Was es ist
## Nicht-Ziele (v1)
## CLI
## Architektur
## Dateiauswahl
## Ranking
## Skim-Modus (Default)
## Liquibase-/DB-Skimmer
## Secret-Scan
## Output `SKIM.md`
# <repo-name>
## Structure          ASCII-Baum, Skips mit Vermerk
## Largest files      Top 10 nach geschaetzten Tokens
## Database schema    nur wenn Liquibase/SQL-Migrationen gefunden
## Files              pro Datei: ### path · 12 commits · ~400 tokens, Codeblock
## Recent changes     nur bei --diff / --log
## Fehlerbehandlung
## Tests
## Repo-Layout
## Spaeter (nicht v1)
```

### docs/plans/2026-10-01-gitskim-implementation.md · 1 commits · ~391 tokens
```markdown
# gitskim v1 Implementation Plan
## Task 1: Scaffold, CLI skeleton, test harness
# ── Errors & options ──────────────────────────────────────────────────────────
# ── Git helpers ───────────────────────────────────────────────────────────────
# (Task 2)
# ── File collection ───────────────────────────────────────────────────────────
# (Task 3)
# ── Ranking ───────────────────────────────────────────────────────────────────
# (Task 4)
# ── Tokens & secrets ──────────────────────────────────────────────────────────
# (Task 5)
# ── Skimmers ──────────────────────────────────────────────────────────────────
# (Tasks 6, 7)
# ── Database changelogs ───────────────────────────────────────────────────────
# (Task 8)
# ── Rendering ─────────────────────────────────────────────────────────────────
# (Tasks 9, 10)
# ── CLI ───────────────────────────────────────────────────────────────────────
## Task 2: Git helpers and `resolve_source`
## Task 3: `collect_files` with filter chain
## Task 4: `rank_files` by change frequency
## Task 5: Token estimate and secret scan
## Task 6: Python skimmer via `ast`
## Task 7: Regex skimmer, Markdown skimmer, skimmer registry
# Lines that start a declaration in C-like / Go / Rust / Java / C# / TS code.
## Task 8: Liquibase / SQL changelog skimmer
## Task 9: Directory tree rendering
## Task 10: `process_file`, `render`, `main` wiring (end to end)
## Task 11: `--diff` and `--log`
## Task 12: README, dogfooding example, push
# gitskim
## Usage
## Output
## How it compares
## Limitations
## Development
## Done criteria
```

### tests/__init__.py · 1 commits · ~0 tokens
```python

```

### README.md · 0 commits · ~1.3k tokens
```markdown
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

`README.md` is always included in full, even in skim mode. See [`examples/SKIM-self.md`](examples/SKIM-self.md) for gitskim applied to itself.

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

## Development

    python3 -m unittest -v

MIT License.
```
