# gitskim v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `gitskim.py`, a single stdlib-only Python script that turns a git repository into one compact Markdown file (`SKIM.md`) for LLM context.

**Architecture:** Linear pipeline in one file: `resolve_source` → `collect_files` (via `git ls-files`) → `rank_files` (via `git log`) → `process_file` (secret scan, then per-language skimmer or full text) → `render`. Signature extraction uses `ast` for Python, regex heuristics for other languages, `xml.etree` for Liquibase changelogs. No third-party dependencies, ever.

**Tech Stack:** Python ≥ 3.9, stdlib only (`argparse subprocess pathlib ast re fnmatch tempfile shutil xml.etree collections dataclasses datetime`). Tests with `unittest`. Design: `docs/plans/2026-10-01-gitskim-design.md`.

**Conventions for every task:**
- Repo root: `~/Development/gitskim`. All commands run from there.
- Run tests: `python3 -m unittest -v` (discovers `tests/`).
- Commit after every green task. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Never put real-looking secrets as literals in tests; build them by string concatenation (otherwise gitskim's own secret scan skips the test file when dogfooding).
- All code goes into `gitskim.py` in the section order given below. Keep the section comments (`# ── Section ──`) so later tasks know where to insert.

---

## Task 1: Scaffold, CLI skeleton, test harness

**Files:**
- Create: `gitskim.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_gitskim.py`
- Create: `LICENSE` (MIT)
- Create: `.gitignore`

**Step 1: Write the failing test**

`tests/test_gitskim.py`:

```python
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gitskim  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def make_repo(files: dict, extra_commits: dict | None = None) -> Path:
    """Create a temp git repo. files: {relpath: str|bytes}. extra_commits: {relpath: new_content} committed one by one."""
    tmp = Path(tempfile.mkdtemp(prefix="gitskim-test-"))
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp)], check=True)
    git(tmp, "config", "user.email", "test@example.com")
    git(tmp, "config", "user.name", "Test")
    for rel, content in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-q", "-m", "init")
    for rel, content in (extra_commits or {}).items():
        (tmp / rel).write_text(content, encoding="utf-8")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-q", "-m", f"touch {rel}")
    return tmp


class TestCli(unittest.TestCase):
    def test_help_exits_zero(self):
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("gitskim", res.stdout)
        self.assertIn("--full", res.stdout)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'gitskim'`

**Step 3: Write minimal implementation**

`gitskim.py`:

```python
#!/usr/bin/env python3
"""gitskim – skim a git repository into one compact Markdown file for LLM context.

Single file, standard library only. Usage: python3 gitskim.py <path-or-url> [-o SKIM.md]
"""
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

__version__ = "0.1.0"


# ── Errors & options ──────────────────────────────────────────────────────────


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
    sort: str = "changes"
    secret_scan: bool = True


@dataclass
class FileEntry:
    path: str                      # repo-relative, posix separators
    size: int
    status: str = "ok"             # ok | too_large | binary | secret | error
    note: str = ""
    commits: int = 0
    tokens: int = 0
    lang: str = ""
    content: str = ""              # what goes into SKIM.md (skimmed or full)
    db_schema: list = field(default_factory=list)


def warn(msg: str) -> None:
    print(f"gitskim: {msg}", file=sys.stderr)


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gitskim",
        description="Skim a git repository into one compact Markdown file for LLM context.",
    )
    p.add_argument("source", help="local path or git URL")
    p.add_argument("-o", "--output", default="SKIM.md", help="output file (default: SKIM.md)")
    p.add_argument("--stdout", action="store_true", help="write to stdout instead of a file")
    p.add_argument("--full", action="store_true", help="full file contents instead of signatures")
    p.add_argument("--include", action="append", default=[], metavar="GLOB", help="only files matching GLOB (repeatable)")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip files matching GLOB (repeatable)")
    p.add_argument("--max-size", type=int, default=100, metavar="KB", help="skip files larger than KB (default: 100)")
    p.add_argument("--no-default-ignore", action="store_true", help="disable built-in ignore list")
    p.add_argument("--untracked", action="store_true", help="include untracked (non-ignored) files")
    p.add_argument("--sort", choices=["changes", "path"], default="changes", help="file order (default: changes)")
    p.add_argument("--diff", action="store_true", help="append working-tree and staged diff")
    p.add_argument("--log", type=int, default=0, metavar="N", help="append last N commits")
    p.add_argument("--no-secret-scan", action="store_true", help="disable secret heuristics")
    p.add_argument("--version", action="version", version=f"gitskim {__version__}")
    return p


def main(argv: Optional[list] = None) -> int:
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`tests/__init__.py`: empty file.

`.gitignore`:

```
__pycache__/
*.pyc
/SKIM.md
.DS_Store
```

`LICENSE`: standard MIT text, `Copyright (c) 2026 Vitali Fichtner`.

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest -v`
Expected: `test_help_exits_zero ... ok`

**Step 5: Commit**

```bash
git add gitskim.py tests/ LICENSE .gitignore
git commit -m "feat: CLI skeleton and test harness"
```

---

## Task 2: Git helpers and `resolve_source`

**Files:**
- Modify: `gitskim.py` (section `Git helpers`)
- Test: `tests/test_gitskim.py`

**Step 1: Write the failing tests**

Append to `tests/test_gitskim.py` (before `if __name__`):

```python
class TestResolveSource(unittest.TestCase):
    def test_local_repo_resolves_to_toplevel(self):
        repo = make_repo({"a.txt": "a", "sub/b.txt": "b"})
        path, tmp, name = gitskim.resolve_source(str(repo / "sub"))
        self.assertEqual(path, repo.resolve())
        self.assertIsNone(tmp)
        self.assertEqual(name, repo.name)

    def test_non_repo_raises(self):
        plain = Path(tempfile.mkdtemp())
        with self.assertRaises(gitskim.GitskimError):
            gitskim.resolve_source(str(plain))

    def test_missing_dir_raises(self):
        with self.assertRaises(gitskim.GitskimError):
            gitskim.resolve_source("/definitely/not/here")

    def test_local_url_clone(self):
        repo = make_repo({"a.txt": "a"})
        path, tmp, name = gitskim.resolve_source(f"file://{repo}")
        try:
            self.assertTrue((path / "a.txt").exists())
            self.assertIsNotNone(tmp)
            self.assertEqual(name, repo.name)
        finally:
            gitskim.cleanup(tmp)
        self.assertFalse(tmp.exists())

    def test_bad_url_raises(self):
        with self.assertRaises(gitskim.GitskimError):
            gitskim.resolve_source("file:///nonexistent/repo.git")


class TestRunGit(unittest.TestCase):
    def test_run_git_returns_stdout(self):
        repo = make_repo({"a.txt": "a"})
        self.assertEqual(gitskim.run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")

    def test_run_git_failure_raises(self):
        repo = make_repo({"a.txt": "a"})
        with self.assertRaises(gitskim.GitskimError):
            gitskim.run_git(repo, "rev-parse", "--verify", "nope")
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m unittest -v`
Expected: ERRORs `AttributeError: module 'gitskim' has no attribute 'resolve_source'`

**Step 3: Write minimal implementation**

Replace `# (Task 2)` with:

```python
URL_RE = re.compile(r"^(https?://|git@|ssh://|git://|file://)")


def run_git(repo: Path, *args: str) -> str:
    """Run a git command in repo and return stdout. Raises GitskimError on failure."""
    try:
        res = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        raise GitskimError("git not found on PATH") from None
    except subprocess.CalledProcessError as e:
        raise GitskimError(f"git {' '.join(args)} failed: {e.stderr.strip()}") from None
    return res.stdout


def resolve_source(arg: str) -> tuple:
    """Return (repo_path, tempdir_or_None, repo_name).

    URLs are shallow-cloned into a tempdir the caller must remove via cleanup().
    """
    if URL_RE.match(arg) or arg.endswith(".git"):
        tmp = Path(tempfile.mkdtemp(prefix="gitskim-"))
        dest = tmp / "repo"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", arg, str(dest)],
                capture_output=True, text=True, check=True,
            )
        except FileNotFoundError:
            cleanup(tmp)
            raise GitskimError("git not found on PATH") from None
        except subprocess.CalledProcessError as e:
            cleanup(tmp)
            raise GitskimError(f"clone failed: {e.stderr.strip()}") from None
        name = arg.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        return dest, tmp, name

    p = Path(arg).expanduser()
    if not p.is_dir():
        raise GitskimError(f"not a directory: {arg}")
    try:
        top = run_git(p, "rev-parse", "--show-toplevel").strip()
    except GitskimError:
        raise GitskimError(f"not a git repository: {arg}") from None
    top_path = Path(top).resolve()
    return top_path, None, top_path.name


def cleanup(tmp: Optional[Path]) -> None:
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m unittest -v`
Expected: all `ok`

**Step 5: Commit**

```bash
git add gitskim.py tests/test_gitskim.py
git commit -m "feat: git helpers and source resolution (local path or clone)"
```

---

## Task 3: `collect_files` with filter chain

**Files:**
- Modify: `gitskim.py` (section `File collection`)
- Test: `tests/test_gitskim.py`

**Step 1: Write the failing tests**

```python
class TestCollectFiles(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({
            "README.md": "# hi",
            "src/app.py": "print(1)",
            "package-lock.json": "{}",
            "dist/bundle.js": "x",
            "assets/logo.png": b"\x89PNG\x00\x00",
            "data/blob.dat": b"\x00\x01\x02",
            "big.txt": "x" * 2048,
            "notes.txt": "n",
        })

    def paths(self, entries):
        return [e.path for e in entries]

    def test_default_ignore_drops_lockfiles_dist_and_images(self):
        entries = gitskim.collect_files(self.repo, gitskim.Options())
        p = self.paths(entries)
        self.assertNotIn("package-lock.json", p)
        self.assertNotIn("dist/bundle.js", p)
        self.assertNotIn("assets/logo.png", p)
        self.assertIn("src/app.py", p)
        self.assertIn("README.md", p)

    def test_no_default_ignore_keeps_them(self):
        entries = gitskim.collect_files(self.repo, gitskim.Options(default_ignore=False))
        self.assertIn("package-lock.json", self.paths(entries))

    def test_include_and_exclude_globs(self):
        inc = gitskim.collect_files(self.repo, gitskim.Options(include=["*.py"]))
        self.assertEqual(self.paths(inc), ["src/app.py"])
        exc = gitskim.collect_files(self.repo, gitskim.Options(exclude=["*.md", "src/*"]))
        p = self.paths(exc)
        self.assertNotIn("README.md", p)
        self.assertNotIn("src/app.py", p)
        self.assertIn("notes.txt", p)

    def test_max_size_marks_too_large_but_keeps_in_tree(self):
        entries = gitskim.collect_files(self.repo, gitskim.Options(max_size_kb=1))
        big = next(e for e in entries if e.path == "big.txt")
        self.assertEqual(big.status, "too_large")

    def test_binary_sniff(self):
        entries = gitskim.collect_files(self.repo, gitskim.Options())
        blob = next(e for e in entries if e.path == "data/blob.dat")
        self.assertEqual(blob.status, "binary")

    def test_untracked_files_only_with_flag(self):
        (self.repo / "new.txt").write_text("new")
        default = gitskim.collect_files(self.repo, gitskim.Options())
        self.assertNotIn("new.txt", self.paths(default))
        with_flag = gitskim.collect_files(self.repo, gitskim.Options(untracked=True))
        self.assertIn("new.txt", self.paths(with_flag))
        self.assertIn("src/app.py", self.paths(with_flag))
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m unittest -v`
Expected: ERRORs `no attribute 'collect_files'`

**Step 3: Write minimal implementation**

Replace `# (Task 3)` with:

```python
IGNORE_DIRS = {
    "node_modules", "dist", "build", "vendor", "__pycache__", ".git",
    ".idea", ".vscode", "target", ".next", ".venv", "venv", "coverage",
}

DEFAULT_IGNORE = [
    # lockfiles
    "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "Pipfile.lock", "composer.lock", "Gemfile.lock", "go.sum",
    # generated / minified
    "*.min.js", "*.min.css", "*.map", "*.bundle.js",
    # binaries & media
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.svg", "*.webp", "*.pdf",
    "*.woff", "*.woff2", "*.ttf", "*.eot", "*.otf",
    "*.zip", "*.gz", "*.tar", "*.tgz", "*.7z", "*.jar", "*.war",
    "*.pyc", "*.pyo", "*.so", "*.dylib", "*.dll", "*.exe", "*.bin", "*.class", "*.o",
    "*.mp3", "*.mp4", "*.mov", "*.wav",
    ".DS_Store", "Thumbs.db",
]


def matches_any(rel: str, patterns: list) -> bool:
    """fnmatch against the full relative path and the basename."""
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p) for p in patterns)


def in_ignored_dir(rel: str) -> bool:
    return bool(set(rel.split("/")[:-1]) & IGNORE_DIRS)


def is_binary(path: Path) -> bool:
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)


def collect_files(repo: Path, opts: Options) -> list:
    """List candidate files via git ls-files and apply the filter chain.

    Filtered-out files are dropped. Too-large and binary files are kept with a
    status so they still appear in the tree.
    """
    args = ["ls-files", "-z"]
    if opts.untracked:
        args += ["--cached", "--others", "--exclude-standard"]
    out = run_git(repo, *args)
    entries = []
    for rel in sorted(filter(None, out.split("\0"))):
        if opts.default_ignore and (in_ignored_dir(rel) or matches_any(rel, DEFAULT_IGNORE)):
            continue
        if opts.exclude and matches_any(rel, opts.exclude):
            continue
        if opts.include and not matches_any(rel, opts.include):
            continue
        p = repo / rel
        if not p.is_file():          # submodule dirs, deleted-but-indexed files
            continue
        size = p.stat().st_size
        entry = FileEntry(path=rel, size=size)
        if size > opts.max_size_kb * 1024:
            entry.status = "too_large"
        elif is_binary(p):
            entry.status = "binary"
        entries.append(entry)
    return entries
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m unittest -v`
Expected: all `ok`

**Step 5: Commit**

```bash
git add gitskim.py tests/test_gitskim.py
git commit -m "feat: collect files via git ls-files with ignore/include/size/binary filters"
```

---

## Task 4: `rank_files` by change frequency

**Files:**
- Modify: `gitskim.py` (section `Ranking`)
- Test: `tests/test_gitskim.py`

**Step 1: Write the failing tests**

```python
class TestRankFiles(unittest.TestCase):
    def test_sorted_by_commit_count_then_path(self):
        repo = make_repo(
            {"a.txt": "1", "b.txt": "1", "c.txt": "1"},
            extra_commits={"c.txt": "2", "b.txt": "2", "c.txt ": "3"},
        )
        # note: "c.txt " with trailing space is a different file; harmless, tests robustness
        entries = gitskim.collect_files(repo, gitskim.Options())
        gitskim.rank_files(repo, entries, "changes")
        order = [e.path for e in entries if e.path in ("a.txt", "b.txt", "c.txt")]
        self.assertEqual(order, ["c.txt", "b.txt", "a.txt"])
        self.assertEqual(next(e for e in entries if e.path == "c.txt").commits, 2)

    def test_sort_path(self):
        repo = make_repo({"b.txt": "1", "a.txt": "1"}, extra_commits={"b.txt": "2"})
        entries = gitskim.collect_files(repo, gitskim.Options())
        gitskim.rank_files(repo, entries, "path")
        self.assertEqual([e.path for e in entries], ["a.txt", "b.txt"])
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m unittest -v`
Expected: ERRORs `no attribute 'rank_files'`

**Step 3: Write minimal implementation**

Replace `# (Task 4)` with:

```python
def rank_files(repo: Path, entries: list, sort: str = "changes", max_commits: int = 500) -> list:
    """Sort entries in place. 'changes' = most-committed first (LLMs read top-down)."""
    if sort == "changes":
        try:
            out = run_git(repo, "log", "--name-only", "--format=", f"-n{max_commits}")
        except GitskimError:          # e.g. repo without commits
            out = ""
        counts = Counter(line for line in out.splitlines() if line)
        for e in entries:
            e.commits = counts.get(e.path, 0)
        entries.sort(key=lambda e: (-e.commits, e.path))
    else:
        entries.sort(key=lambda e: e.path)
    return entries
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m unittest -v`
Expected: all `ok`

**Step 5: Commit**

```bash
git add gitskim.py tests/test_gitskim.py
git commit -m "feat: rank files by git change frequency"
```

---

## Task 5: Token estimate and secret scan

**Files:**
- Modify: `gitskim.py` (section `Tokens & secrets`)
- Test: `tests/test_gitskim.py`

**Step 1: Write the failing tests**

```python
class TestTokensAndSecrets(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertEqual(gitskim.estimate_tokens(""), 0)
        self.assertEqual(gitskim.estimate_tokens("x" * 400), 100)

    def test_fmt_tokens(self):
        self.assertEqual(gitskim.fmt_tokens(950), "950")
        self.assertEqual(gitskim.fmt_tokens(38_400), "38.4k")

    def test_detects_aws_key(self):
        text = "key = " + "AKIA" + "IOSFODNN7EXAMPLE"
        self.assertEqual(gitskim.find_secret(text), "AWS access key")

    def test_detects_private_key_block(self):
        text = "-----BEGIN " + "RSA PRIVATE KEY-----\nabc"
        self.assertEqual(gitskim.find_secret(text), "private key")

    def test_detects_generic_assignment(self):
        text = 'db_' + 'password = "' + "s3cr3tpassw0rd" + '"'
        self.assertEqual(gitskim.find_secret(text), "credential assignment")

    def test_clean_text_passes(self):
        self.assertIsNone(gitskim.find_secret("def main():\n    return 42\n"))
        self.assertIsNone(gitskim.find_secret('password = os.environ["DB_PASSWORD"]'))

    def test_secret_paths(self):
        self.assertTrue(gitskim.matches_any(".env", gitskim.SECRET_PATHS))
        self.assertTrue(gitskim.matches_any("config/.env.local", gitskim.SECRET_PATHS))
        self.assertTrue(gitskim.matches_any("certs/server.pem", gitskim.SECRET_PATHS))
        self.assertFalse(gitskim.matches_any("src/env.py", gitskim.SECRET_PATHS))
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m unittest -v`
Expected: ERRORs `no attribute 'estimate_tokens'`

**Step 3: Write minimal implementation**

Replace `# (Task 5)` with:

```python
def estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 characters per token. Marked 'est.' in output."""
    return len(text) // 4


def fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


SECRET_PATHS = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "*.keystore"]

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("Stripe live key", re.compile(r"\bsk_live_[0-9a-zA-Z]{20,}\b")),
    ("credential assignment", re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|token)\w*\s*[=:]\s*[\"'][^\"'\s]{8,}[\"']")),
]


def find_secret(text: str) -> Optional[str]:
    """Return a short label for the first secret-looking pattern found, else None."""
    for label, pat in SECRET_PATTERNS:
        if pat.search(text):
            return label
    return None
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m unittest -v`
Expected: all `ok`

**Step 5: Commit**

```bash
git add gitskim.py tests/test_gitskim.py
git commit -m "feat: token estimate and secret heuristics"
```

---

## Task 6: Python skimmer via `ast`

**Files:**
- Modify: `gitskim.py` (section `Skimmers`)
- Test: `tests/test_gitskim.py`

**Step 1: Write the failing tests**

```python
PY_SAMPLE = '''"""Module docstring line one.

More docs."""
import os
import sys as system
from pathlib import Path
from . import sibling

MAX_RETRIES = 3

@dataclass
class User(Base, Mixin):
    """A user."""
    name: str
    age: int = 0

    def greet(self, loud: bool = False) -> str:
        """Say hi."""
        return "hi"

    @property
    async def token(self):
        return await fetch()


class Empty:
    pass


def helper(x: int, *args, key=None, **kw) -> list[int]:
    return [x]


async def run():
    pass
'''


class TestSkimPython(unittest.TestCase):
    def setUp(self):
        self.out = gitskim.skim_python(PY_SAMPLE)

    def test_module_docstring_first_line(self):
        self.assertIn('"""Module docstring line one."""', self.out)
        self.assertNotIn("More docs", self.out)

    def test_imports(self):
        self.assertIn("import os", self.out)
        self.assertIn("import sys as system", self.out)
        self.assertIn("from pathlib import Path", self.out)
        self.assertIn("from . import sibling", self.out)

    def test_constants(self):
        self.assertIn("MAX_RETRIES = ...", self.out)

    def test_class_with_bases_fields_and_methods(self):
        self.assertIn("@dataclass", self.out)
        self.assertIn("class User(Base, Mixin):", self.out)
        self.assertIn('    """A user."""', self.out)
        self.assertIn("    name: str", self.out)
        self.assertIn("    age: int", self.out)
        self.assertIn("    def greet(self, loud: bool=False) -> str: ...", self.out)
        self.assertIn('        """Say hi."""', self.out)
        self.assertIn("    @property", self.out)
        self.assertIn("    async def token(self): ...", self.out)

    def test_empty_class(self):
        self.assertIn("class Empty:\n    ...", self.out)

    def test_top_level_functions(self):
        self.assertIn("def helper(x: int, *args, key=None, **kw) -> list[int]: ...", self.out)
        self.assertIn("async def run(): ...", self.out)

    def test_no_bodies(self):
        self.assertNotIn('return "hi"', self.out)
        self.assertNotIn("return [x]", self.out)

    def test_syntax_error_falls_back(self):
        out = gitskim.skim_python("def broken(:\n  pass\n" * 40)
        self.assertIn("more lines", out)
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m unittest -v`
Expected: ERRORs `no attribute 'skim_python'`

**Step 3: Write minimal implementation**

Replace `# (Tasks 6, 7)` with (Task 7 appends below this):

```python
FALLBACK_LINES = 30


def skim_fallback(text: str, keep: int = FALLBACK_LINES) -> str:
    """Unknown file type: first N lines, then a count of the rest."""
    lines = text.splitlines()
    if len(lines) <= keep:
        return text.rstrip("\n")
    rest = len(lines) - keep
    return "\n".join(lines[:keep]) + f"\n… ({rest} more lines)"


def _first_doc_line(node) -> Optional[str]:
    doc = ast.get_docstring(node)
    return doc.strip().splitlines()[0] if doc else None


def _py_func(node, indent: int) -> list:
    pad = "    " * indent
    out = [f"{pad}@{ast.unparse(d)}" for d in node.decorator_list]
    kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    out.append(f"{pad}{kw} {node.name}({ast.unparse(node.args)}){ret}: ...")
    doc = _first_doc_line(node)
    if doc:
        out.append(f'{pad}    """{doc}"""')
    return out


def _py_class(node) -> list:
    out = [f"@{ast.unparse(d)}" for d in node.decorator_list]
    bases = ", ".join(ast.unparse(b) for b in node.bases)
    out.append(f"class {node.name}({bases}):" if bases else f"class {node.name}:")
    doc = _first_doc_line(node)
    if doc:
        out.append(f'    """{doc}"""')
    members = 0
    for n in node.body:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.append(f"    {n.target.id}: {ast.unparse(n.annotation)}")
            members += 1
    for n in node.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.extend(_py_func(n, 1))
            members += 1
    if members == 0 and not doc:
        out.append("    ...")
    return out


def skim_python(text: str) -> str:
    """Signatures only: docstring, imports, constants, classes, functions."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return skim_fallback(text)
    out: list = []
    doc = _first_doc_line(tree)
    if doc:
        out.append(f'"""{doc}"""')
    imports = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node))
    out.extend(imports)
    body: list = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body.extend(_py_func(node, 0))
            body.append("")
        elif isinstance(node, ast.ClassDef):
            body.extend(_py_class(node))
            body.append("")
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name) and t.id.isupper()]
            if names:
                body.append(f"{' = '.join(names)} = ...")
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id.isupper():
            body.append(f"{node.target.id}: {ast.unparse(node.annotation)} = ...")
    if imports and body:
        out.append("")
    out.extend(body)
    return "\n".join(out).rstrip("\n")
```

Note on `ast.unparse(node.args)`: it renders defaults as `loud: bool=False` (no spaces around `=`). The test expects exactly that.

**Step 4: Run tests to verify they pass**

Run: `python3 -m unittest -v`
Expected: all `ok`. If `test_class_with_bases_fields_and_methods` fails on the `greet` line, print `self.out` and adjust the expected string to what `ast.unparse` produces on this Python version; do not change the implementation.

**Step 5: Commit**

```bash
git add gitskim.py tests/test_gitskim.py
git commit -m "feat: Python signature skimmer via ast"
```

---

## Task 7: Regex skimmer, Markdown skimmer, skimmer registry

**Files:**
- Modify: `gitskim.py` (section `Skimmers`, append after `skim_python`)
- Test: `tests/test_gitskim.py`

**Step 1: Write the failing tests**

```python
TS_SAMPLE = """import { Foo } from './foo';
export const VERSION = '1.0';

export interface User {
  id: number;
  name: string;
}

export class UserService extends Base {
  private cache = new Map();

  constructor(private http: HttpClient) {}

  async getUser(id: number): Promise<User> {
    if (this.cache.has(id)) {
      return this.cache.get(id);
    }
    return this.http.get(`/users/${id}`);
  }
}

export function helper(x: number) {
  return x * 2;
}
"""

JAVA_SAMPLE = """package com.example;

import java.util.List;

@Service
public class OrderService {
    private final OrderRepo repo;

    @Transactional
    public Order create(OrderRequest req) {
        if (req == null) {
            throw new IllegalArgumentException();
        }
        return repo.save(new Order(req));
    }
}
"""


class TestSkimRegex(unittest.TestCase):
    def test_typescript_keeps_declarations_drops_bodies(self):
        out = gitskim.skim_regex(TS_SAMPLE)
        self.assertIn("import { Foo } from './foo';", out)
        self.assertIn("export const VERSION = '1.0';", out)
        self.assertIn("export interface User { ... }", out)
        self.assertIn("export class UserService extends Base { ... }", out)
        self.assertIn("  async getUser(id: number): Promise<User> { ... }", out)
        self.assertIn("export function helper(x: number) { ... }", out)
        self.assertNotIn("this.cache.get", out)
        self.assertNotIn("if (this.cache", out)

    def test_java_keeps_annotations_and_methods(self):
        out = gitskim.skim_regex(JAVA_SAMPLE)
        self.assertIn("package com.example;", out)
        self.assertIn("@Service", out)
        self.assertIn("public class OrderService { ... }", out)
        self.assertIn("    @Transactional", out)
        self.assertIn("    public Order create(OrderRequest req) { ... }", out)
        self.assertNotIn("throw new", out)

    def test_caps_output(self):
        many = "\n".join(f"export const C{i} = {i};" for i in range(500))
        out = gitskim.skim_regex(many, max_lines=50)
        self.assertEqual(out.count("export const"), 50)
        self.assertIn("more signature lines", out)


class TestSkimMarkdown(unittest.TestCase):
    def test_headings_only(self):
        out = gitskim.skim_markdown("# Title\n\ntext\n\n## Sub\nmore\n### Deep\n")
        self.assertEqual(out, "# Title\n## Sub\n### Deep")

    def test_no_headings_falls_back(self):
        out = gitskim.skim_markdown("just text\n")
        self.assertEqual(out, "just text")


class TestRegistry(unittest.TestCase):
    def test_skimmer_for_ext(self):
        self.assertIs(gitskim.skimmer_for("a/b.py"), gitskim.skim_python)
        self.assertIs(gitskim.skimmer_for("x.ts"), gitskim.skim_regex)
        self.assertIs(gitskim.skimmer_for("x.md"), gitskim.skim_markdown)
        self.assertIs(gitskim.skimmer_for("x.unknownext"), gitskim.skim_fallback)

    def test_lang_for(self):
        self.assertEqual(gitskim.lang_for("a.py"), "python")
        self.assertEqual(gitskim.lang_for("Dockerfile"), "dockerfile")
        self.assertEqual(gitskim.lang_for("x.weird"), "")
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m unittest -v`
Expected: ERRORs `no attribute 'skim_regex'`

**Step 3: Write minimal implementation**

Append to the `Skimmers` section:

```python
# Lines that start a declaration in C-like / Go / Rust / Java / C# / TS code.
_MODIFIERS = r"(?:export\s+|default\s+|pub(?:\([^)]*\))?\s+|public\s+|private\s+|protected\s+|internal\s+|static\s+|async\s+|abstract\s+|final\s+|override\s+|readonly\s+|unsafe\s+|extern\s+|declare\s+)*"
_KEYWORDS = r"(?:import|from|package|using|namespace|module|function|class|interface|type|enum|struct|union|impl|trait|fn|func|def|const|let|var|record|use|mod|extends|implements)\b"
SIG_RE = re.compile(r"^\s{0,4}" + _MODIFIERS + _KEYWORDS)
ANNOTATION_RE = re.compile(r"^\s*@\w+")
CONTROL_RE = re.compile(r"^\s*(?:if|else|for|while|do|switch|case|try|catch|finally|return|with|match|loop|defer|go)\b")


def skim_regex(text: str, max_lines: int = 200) -> str:
    """Heuristic skimmer for brace languages: declarations and annotations, no bodies.

    Keeps lines matching SIG_RE, annotations, and any line at indent <= 4 that
    opens a block ('{') and is not a control statement. A trailing '{' becomes '{ ... }'.
    """
    out = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#", "*", "/*")):
            continue
        indent = len(line) - len(line.lstrip())
        opens_block = stripped.endswith("{") and indent <= 4 and not CONTROL_RE.match(line)
        if SIG_RE.match(line) or ANNOTATION_RE.match(line) or opens_block:
            if stripped.endswith("{"):
                line = line[: line.rfind("{")].rstrip() + " { ... }"
            out.append(line)
    if len(out) > max_lines:
        rest = len(out) - max_lines
        out = out[:max_lines] + [f"… ({rest} more signature lines)"]
    return "\n".join(out)


HEADING_RE = re.compile(r"^#{1,6}\s")


def skim_markdown(text: str) -> str:
    heads = [l.rstrip() for l in text.splitlines() if HEADING_RE.match(l)]
    return "\n".join(heads) if heads else skim_fallback(text)


LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".cs": "csharp", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".hpp": "cpp", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".scala": "scala", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".md": "markdown", ".yml": "yaml", ".yaml": "yaml", ".toml": "toml",
    ".json": "json", ".xml": "xml", ".html": "html", ".css": "css", ".scss": "scss",
    ".sql": "sql", ".tf": "hcl", ".ini": "ini", ".cfg": "ini", ".txt": "",
}
LANG_BY_NAME = {"dockerfile": "dockerfile", "makefile": "makefile", "jenkinsfile": "groovy"}

REGEX_EXTS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
              ".kt", ".cs", ".c", ".h", ".cpp", ".hpp", ".swift", ".scala", ".php"}

SKIMMERS: dict = {".py": skim_python, ".md": skim_markdown}
SKIMMERS.update({ext: skim_regex for ext in REGEX_EXTS})


def skimmer_for(rel: str) -> Callable[[str], str]:
    return SKIMMERS.get(Path(rel).suffix.lower(), skim_fallback)


def lang_for(rel: str) -> str:
    p = Path(rel)
    return LANG_BY_EXT.get(p.suffix.lower()) or LANG_BY_NAME.get(p.name.lower(), "")
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m unittest -v`
Expected: all `ok`. The TS `constructor(private http: HttpClient) {}` line ends with `}` not `{`, so it is dropped; that is acceptable.

**Step 5: Commit**

```bash
git add gitskim.py tests/test_gitskim.py
git commit -m "feat: regex and markdown skimmers, skimmer registry, language tags"
```

---

## Task 8: Liquibase / SQL changelog skimmer

**Files:**
- Modify: `gitskim.py` (section `Database changelogs`)
- Test: `tests/test_gitskim.py`

**Step 1: Write the failing tests**

```python
LB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog">
  <changeSet id="1" author="vf">
    <comment>users</comment>
    <createTable tableName="users">
      <column name="id" type="uuid"><constraints primaryKey="true" nullable="false"/></column>
      <column name="email" type="varchar(255)"><constraints nullable="false" unique="true"/></column>
    </createTable>
    <createIndex indexName="ix_users_email" tableName="users">
      <column name="email"/>
    </createIndex>
  </changeSet>
  <changeSet id="2" author="vf">
    <addColumn tableName="users"><column name="age" type="int"/></addColumn>
    <addForeignKeyConstraint baseTableName="orders" baseColumnNames="user_id"
        referencedTableName="users" referencedColumnNames="id" constraintName="fk_o_u"/>
    <renameColumn tableName="users" oldColumnName="age" newColumnName="years"/>
    <dropColumn tableName="users" columnName="years"/>
    <sql>UPDATE users SET x = 1</sql>
    <dropTable tableName="legacy"/>
  </changeSet>
</databaseChangeLog>
"""

LB_SQL = """--liquibase formatted sql

--changeset vf:1
CREATE TABLE users (
    id uuid PRIMARY KEY,
    email varchar(255) NOT NULL
);
INSERT INTO users VALUES ('x');

--changeset vf:2
ALTER TABLE users ADD COLUMN age int;
CREATE UNIQUE INDEX ix_users_email ON users(email);
"""

LB_YAML = """databaseChangeLog:
  - changeSet:
      id: 1
      author: vf
      changes:
        - createTable:
            tableName: users
            columns:
              - column:
                  name: id
                  type: uuid
              - column:
                  name: email
                  type: varchar(255)
  - changeSet:
      id: 2
      author: vf
      changes:
        - addColumn:
            tableName: users
            columns:
              - column:
                  name: age
                  type: int
"""


class TestLiquibase(unittest.TestCase):
    def test_is_db_changelog(self):
        self.assertTrue(gitskim.is_db_changelog("src/main/resources/db/changelog/001.xml"))
        self.assertTrue(gitskim.is_db_changelog("db/migration/V1__init.sql"))
        self.assertTrue(gitskim.is_db_changelog("liquibase/master.yaml"))
        self.assertFalse(gitskim.is_db_changelog("src/app.py"))
        self.assertFalse(gitskim.is_db_changelog("docs/changelog.md"))

    def test_xml(self):
        lines = gitskim.skim_db_changelog("db/changelog/a.xml", LB_XML)
        self.assertEqual(lines[0],
            "- 1/vf: createTable users(id uuid PK NOT NULL, email varchar(255) NOT NULL UNIQUE); "
            "createIndex ix_users_email on users(email)")
        self.assertEqual(lines[1],
            "- 2/vf: addColumn users(age int); FK orders.user_id -> users.id; "
            "renameColumn users.age -> years; dropColumn users.years; other: sql; dropTable legacy")

    def test_xml_parse_error_returns_empty(self):
        self.assertEqual(gitskim.skim_db_changelog("db/changelog/a.xml", "<broken"), [])

    def test_sql(self):
        lines = gitskim.skim_db_changelog("db/changelog/a.sql", LB_SQL)
        self.assertEqual(lines, [
            "- vf:1: CREATE TABLE users ( id uuid PRIMARY KEY, email varchar(255) NOT NULL )",
            "- vf:2: ALTER TABLE users ADD COLUMN age int; CREATE UNIQUE INDEX ix_users_email ON users(email)",
        ])

    def test_yaml(self):
        lines = gitskim.skim_db_changelog("db/changelog/a.yaml", LB_YAML)
        self.assertEqual(lines, [
            "- 1/vf: createTable users(id uuid, email varchar(255))",
            "- 2/vf: addColumn users(age int)",
        ])
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m unittest -v`
Expected: ERRORs `no attribute 'is_db_changelog'`

**Step 3: Write minimal implementation**

Replace `# (Task 8)` with:

```python
DB_PATH_RE = re.compile(r"(changelog|liquibase|migration|flyway)", re.I)
DB_EXTS = (".xml", ".yaml", ".yml", ".sql")


def is_db_changelog(rel: str) -> bool:
    return rel.lower().endswith(DB_EXTS) and bool(DB_PATH_RE.search(rel))


def _tag(el) -> str:
    return el.tag.split("}", 1)[-1]


def _xml_col(col) -> str:
    s = f"{col.get('name')} {col.get('type', '')}".strip()
    for c in col:
        if _tag(c) == "constraints":
            if c.get("primaryKey") == "true":
                s += " PK"
            if c.get("nullable") == "false":
                s += " NOT NULL"
            if c.get("unique") == "true":
                s += " UNIQUE"
            if c.get("referencedTableName"):
                s += f" FK->{c.get('referencedTableName')}"
    return s


def _xml_cols(el) -> str:
    return ", ".join(_xml_col(c) for c in el if _tag(c) == "column")


_XML_SKIP = {"comment", "preConditions", "rollback", "validCheckSum"}


def _xml_op(ch) -> Optional[str]:
    t = _tag(ch)
    g = ch.get
    if t in _XML_SKIP:
        return None
    if t == "createTable":
        return f"createTable {g('tableName')}({_xml_cols(ch)})"
    if t == "addColumn":
        return f"addColumn {g('tableName')}({_xml_cols(ch)})"
    if t == "dropColumn":
        return f"dropColumn {g('tableName')}.{g('columnName')}"
    if t == "renameColumn":
        return f"renameColumn {g('tableName')}.{g('oldColumnName')} -> {g('newColumnName')}"
    if t == "addForeignKeyConstraint":
        return f"FK {g('baseTableName')}.{g('baseColumnNames')} -> {g('referencedTableName')}.{g('referencedColumnNames')}"
    if t == "createIndex":
        cols = ", ".join(c.get("name", "") for c in ch if _tag(c) == "column")
        return f"createIndex {g('indexName')} on {g('tableName')}({cols})"
    if t == "dropTable":
        return f"dropTable {g('tableName')}"
    return f"other: {t}"


def skim_liquibase_xml(text: str) -> list:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out = []
    for cs in root.iter():
        if _tag(cs) != "changeSet":
            continue
        ops = [op for op in (_xml_op(ch) for ch in cs) if op]
        out.append(f"- {cs.get('id', '?')}/{cs.get('author', '?')}: " + "; ".join(ops))
    return out


SQL_CHANGESET_RE = re.compile(r"^\s*--\s*changeset\s+(\S+)", re.I)
SQL_DDL_RE = re.compile(r"^(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|VIEW)|ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX))\b", re.I)


def skim_liquibase_sql(text: str, max_len: int = 200) -> list:
    """One line per --changeset with its DDL statements, whitespace squashed."""
    out = []
    current = "?"
    ops: list = []
    buf: list = []

    def flush_changeset():
        if ops:
            out.append(f"- {current}: " + "; ".join(ops))

    for line in text.splitlines():
        m = SQL_CHANGESET_RE.match(line)
        if m:
            flush_changeset()
            current, ops, buf = m.group(1), [], []
            continue
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if ";" in line:
            stmt = re.sub(r"\s+", " ", " ".join(buf)).strip().rstrip(";").strip()
            buf = []
            if SQL_DDL_RE.match(stmt):
                ops.append(stmt[:max_len] + ("…" if len(stmt) > max_len else ""))
    flush_changeset()
    return out


_YAML_OPS = ("createTable", "addColumn", "dropColumn", "renameColumn", "addForeignKeyConstraint", "createIndex", "dropTable")


def skim_liquibase_yaml(text: str) -> list:
    """Very rough YAML changelog reader: changeSet id/author, op, tableName, columns."""
    out = []
    cs: Optional[dict] = None

    def flush():
        if cs:
            ops = []
            for op in cs["ops"]:
                cols = ", ".join(c.strip() for c in op["cols"])
                ops.append(f"{op['name']} {op.get('table', '?')}({cols})" if op["cols"] else f"{op['name']} {op.get('table', '?')}")
            out.append(f"- {cs.get('id', '?')}/{cs.get('author', '?')}: " + "; ".join(ops))

    for raw in text.splitlines():
        s = raw.strip().lstrip("- ").strip()
        if s.startswith("changeSet:"):
            flush()
            cs = {"ops": []}
            continue
        if cs is None:
            continue
        key, _, val = s.partition(":")
        val = val.strip()
        if key == "id":
            cs["id"] = val
        elif key == "author":
            cs["author"] = val
        elif key in _YAML_OPS:
            cs["ops"].append({"name": key, "cols": []})
        elif key == "tableName" and cs["ops"]:
            cs["ops"][-1]["table"] = val
        elif key == "name" and cs["ops"]:
            cs["ops"][-1]["cols"].append(val)
        elif key == "type" and cs["ops"] and cs["ops"][-1]["cols"]:
            cs["ops"][-1]["cols"][-1] += f" {val}"
    flush()
    return out


def skim_db_changelog(rel: str, text: str) -> list:
    ext = Path(rel).suffix.lower()
    if ext == ".xml":
        return skim_liquibase_xml(text)
    if ext == ".sql":
        return skim_liquibase_sql(text)
    return skim_liquibase_yaml(text)
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m unittest -v`
Expected: all `ok`

**Step 5: Commit**

```bash
git add gitskim.py tests/test_gitskim.py
git commit -m "feat: Liquibase XML/SQL/YAML changelog skimmer"
```

---

## Task 9: Directory tree rendering

**Files:**
- Modify: `gitskim.py` (section `Rendering`)
- Test: `tests/test_gitskim.py`

**Step 1: Write the failing test**

```python
class TestRenderTree(unittest.TestCase):
    def test_tree_with_notes(self):
        entries = [
            gitskim.FileEntry("src/app.py", 10),
            gitskim.FileEntry("src/big.bin", 300 * 1024, status="too_large"),
            gitskim.FileEntry("README.md", 5),
            gitskim.FileEntry("img.dat", 3, status="binary"),
            gitskim.FileEntry(".env", 3, status="secret"),
        ]
        out = gitskim.render_tree(entries)
        self.assertEqual(out.splitlines(), [
            "├── src/",
            "│   ├── app.py",
            "│   └── big.bin  (skipped, 300 KB)",
            "├── .env  ⚠ skipped (possible secret)",
            "├── img.dat  (binary)",
            "└── README.md",
        ])
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest -v`
Expected: ERROR `no attribute 'render_tree'`

**Step 3: Write minimal implementation**

Replace `# (Tasks 9, 10)` with (Task 10 appends below):

```python
def _tree_note(e: FileEntry) -> str:
    if e.status == "too_large":
        return f"  (skipped, {max(1, e.size // 1024)} KB)"
    if e.status == "binary":
        return "  (binary)"
    if e.status == "secret":
        return "  ⚠ skipped (possible secret)"
    if e.status == "error":
        return "  (unreadable)"
    return ""


def render_tree(entries: list) -> str:
    """ASCII tree. Directories first, then files, both case-insensitively sorted."""
    root: dict = {}
    for e in entries:
        node = root
        parts = e.path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part + "/", {})
        node[parts[-1]] = e
    lines: list = []

    def walk(node: dict, prefix: str) -> None:
        items = sorted(node.items(), key=lambda kv: (not kv[0].endswith("/"), kv[0].lower()))
        for i, (name, val) in enumerate(items):
            last = i == len(items) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{name}{_tree_note(val) if isinstance(val, FileEntry) else ''}")
            if isinstance(val, dict):
                walk(val, prefix + ("    " if last else "│   "))

    walk(root, "")
    return "\n".join(lines)
```

**Step 4: Run test to verify it passes**

Run: `python3 -m unittest -v`
Expected: all `ok`

**Step 5: Commit**

```bash
git add gitskim.py tests/test_gitskim.py
git commit -m "feat: ASCII directory tree with skip notes"
```

---

## Task 10: `process_file`, `render`, `main` wiring (end to end)

**Files:**
- Modify: `gitskim.py` (sections `Rendering` and `CLI`)
- Test: `tests/test_gitskim.py`

**Step 1: Write the failing tests**

```python
class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({
            "README.md": "# Demo\n\nSome text.\n\n```python\nprint(1)\n```\n",
            "src/app.py": "import os\n\ndef main() -> int:\n    return 0\n",
            "src/web.ts": "export function f() {\n  return 1;\n}\n",
            "db/changelog/001.xml": LB_XML,
            ".env": "SECRET=abc",
            "config.py": "pw = " + '"' + "x" * 12 + '"' + "\nAPI_" + 'KEY = "' + "a" * 20 + '"\n',
            "notes.txt": "\n".join(f"line {i}" for i in range(50)),
        }, extra_commits={"src/app.py": "import os\n\ndef main() -> int:\n    return 1\n"})

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(self.repo), "--stdout", *args],
            capture_output=True, text=True,
        )

    def test_skim_mode_output(self):
        res = self.run_cli()
        self.assertEqual(res.returncode, 0, res.stderr)
        out = res.stdout
        self.assertTrue(out.startswith(f"# {self.repo.name}\n"))
        self.assertIn("mode: skim", out)
        self.assertIn("## Structure", out)
        self.assertIn("## Largest files", out)
        self.assertIn("## Database schema", out)
        self.assertIn("- 1/vf: createTable users(", out)
        self.assertIn("## Files", out)
        # ranking: app.py has 2 commits, comes first in Files
        self.assertLess(out.index("### src/app.py · 2 commits"), out.index("### README.md"))
        # README full even in skim mode, with 4-backtick fence because it contains ```
        self.assertIn("Some text.", out)
        self.assertIn("````markdown", out)
        # python skimmed
        self.assertIn("def main() -> int: ...", out)
        self.assertNotIn("return 1", out)
        # ts skimmed
        self.assertIn("export function f() { ... }", out)
        # fallback truncation
        self.assertIn("… (20 more lines)", out)
        # secrets
        self.assertIn(".env  ⚠ skipped (possible secret)", out)
        self.assertIn("config.py  ⚠ skipped (possible secret)", out)
        self.assertNotIn("### .env", out)
        self.assertNotIn("### config.py", out)
        self.assertIn("possible secret", res.stderr)

    def test_full_mode(self):
        res = self.run_cli("--full")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("mode: full", res.stdout)
        self.assertIn("return 1", res.stdout)

    def test_no_secret_scan_includes_config(self):
        res = self.run_cli("--no-secret-scan")
        self.assertIn("### config.py", res.stdout)

    def test_output_file(self):
        out_path = Path(tempfile.mkdtemp()) / "out.md"
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(self.repo), "-o", str(out_path)],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(out_path.exists())
        self.assertIn(str(out_path), res.stderr)

    def test_not_a_repo_exits_1(self):
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), tempfile.mkdtemp()],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("not a git repository", res.stderr)
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m unittest -v`
Expected: FAIL (output is empty; `main` does nothing yet)

**Step 3: Write minimal implementation**

Append to the `Rendering` section:

```python
def process_file(repo: Path, e: FileEntry, opts: Options) -> None:
    """Fill content/tokens/lang/db_schema for one 'ok' entry. Mutates e."""
    if e.status != "ok":
        return
    if opts.secret_scan and matches_any(e.path, SECRET_PATHS):
        e.status, e.note = "secret", "sensitive filename"
        return
    text = (repo / e.path).read_text(encoding="utf-8", errors="replace")
    if opts.secret_scan:
        hit = find_secret(text)
        if hit:
            e.status, e.note = "secret", hit
            return
    e.lang = lang_for(e.path)
    if is_db_changelog(e.path):
        e.db_schema = skim_db_changelog(e.path, text)
    if opts.full or Path(e.path).name.lower() == "readme.md":
        e.content = text.rstrip("\n")
    else:
        e.content = skimmer_for(e.path)(text)
    e.tokens = estimate_tokens(e.content)


def _fence(content: str) -> str:
    return "````" if "```" in content else "```"


def render(name: str, branch: str, commit: str, entries: list, opts: Options,
           diff: str = "", log: str = "") -> str:
    included = [e for e in entries if e.status == "ok"]
    tree = render_tree(entries)
    total = sum(e.tokens for e in included) + estimate_tokens(tree)
    today = _dt.date.today().isoformat()
    mode = "full" if opts.full else "skim"
    out = [
        f"# {name}",
        f"Branch {branch} @ {commit} · {today} · {len(included)}/{len(entries)} files · "
        f"~{fmt_tokens(total)} tokens (est.) · mode: {mode}",
        "",
        "## Structure", "```", tree, "```", "",
    ]
    if included:
        out += ["## Largest files", "", "| File | Commits | ~Tokens |", "|---|---:|---:|"]
        for e in sorted(included, key=lambda e: -e.tokens)[:10]:
            out.append(f"| {e.path} | {e.commits} | {fmt_tokens(e.tokens)} |")
        out.append("")
    db = [e for e in included if e.db_schema]
    if db:
        out.append("## Database schema")
        out.append("")
        for e in db:
            out += [f"### {e.path}", *e.db_schema, ""]
    out.append("## Files")
    out.append("")
    for e in included:
        f = _fence(e.content)
        out += [f"### {e.path} · {e.commits} commits · ~{fmt_tokens(e.tokens)} tokens",
                f"{f}{e.lang}", e.content, f, ""]
    if log or diff:
        out.append("## Recent changes")
        out.append("")
        if log:
            out += ["### git log", "```", log.rstrip("\n"), "```", ""]
        if diff:
            out += ["### git diff (working tree + staged)", "```diff", diff.rstrip("\n"), "```", ""]
    return "\n".join(out).rstrip("\n") + "\n"
```

Replace the body of `main` in the `CLI` section:

```python
def repo_meta(repo: Path) -> tuple:
    try:
        branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        commit = run_git(repo, "rev-parse", "--short", "HEAD").strip()
    except GitskimError:
        branch, commit = "-", "-"
    return branch, commit


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    opts = Options(
        full=args.full, include=args.include, exclude=args.exclude,
        max_size_kb=args.max_size, default_ignore=not args.no_default_ignore,
        untracked=args.untracked, sort=args.sort, secret_scan=not args.no_secret_scan,
    )
    tmp = None
    try:
        repo, tmp, name = resolve_source(args.source)
        entries = collect_files(repo, opts)
        rank_files(repo, entries, opts.sort)
        for e in entries:
            try:
                process_file(repo, e, opts)
            except OSError as ex:
                e.status, e.note = "error", str(ex)
                warn(f"cannot read {e.path}: {ex}")
        for e in entries:
            if e.status == "secret":
                warn(f"skipped {e.path}: possible secret ({e.note})")
        branch, commit = repo_meta(repo)
        diff = log = ""
        if args.diff:
            diff = run_git(repo, "diff") + run_git(repo, "diff", "--cached")
        if args.log:
            log = run_git(repo, "log", f"-n{args.log}", "--oneline", "--no-decorate")
        md = render(name, branch, commit, entries, opts, diff=diff, log=log)
        if args.stdout:
            sys.stdout.write(md)
        else:
            out_path = Path(args.output)
            out_path.write_text(md, encoding="utf-8")
            ok = sum(1 for e in entries if e.status == "ok")
            warn(f"wrote {out_path} ({ok} files, ~{fmt_tokens(estimate_tokens(md))} tokens est.)")
        return 0
    except GitskimError as ex:
        warn(str(ex))
        return 1
    finally:
        cleanup(tmp)
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m unittest -v`
Expected: all `ok`. Then smoke-test on the project itself:

```bash
python3 gitskim.py . --stdout | head -40
```
Expected: header with `mode: skim`, tree, Largest files table, `### gitskim.py` with signatures. `tests/test_gitskim.py` must NOT be marked as a secret. If it is, a test literal leaks a pattern; fix the test literal (concatenate), not the scanner.

**Step 5: Commit**

```bash
git add gitskim.py tests/test_gitskim.py
git commit -m "feat: process files, render SKIM.md, wire CLI end to end"
```

---

## Task 11: `--diff` and `--log`

**Files:**
- Test: `tests/test_gitskim.py` (implementation already landed in Task 10; this task proves it)

**Step 1: Write the failing test**

```python
class TestDiffAndLog(unittest.TestCase):
    def test_log_and_diff_sections(self):
        repo = make_repo({"a.py": "x = 1\n"}, extra_commits={"a.py": "x = 2\n"})
        (repo / "a.py").write_text("x = 3\n")
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(repo), "--stdout", "--log", "5", "--diff"],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("## Recent changes", res.stdout)
        self.assertIn("### git log", res.stdout)
        self.assertIn("touch a.py", res.stdout)
        self.assertIn("### git diff", res.stdout)
        self.assertIn("+x = 3", res.stdout)

    def test_no_section_without_flags(self):
        repo = make_repo({"a.py": "x = 1\n"})
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(repo), "--stdout"],
            capture_output=True, text=True,
        )
        self.assertNotIn("## Recent changes", res.stdout)
```

**Step 2: Run test**

Run: `python3 -m unittest -v`
Expected: PASS already (implemented in Task 10). If it fails, fix `main`/`render`, not the test.

**Step 3: Commit**

```bash
git add tests/test_gitskim.py
git commit -m "test: cover --log and --diff sections"
```

---

## Task 12: README, dogfooding example, push

**Files:**
- Create: `README.md`
- Create: `examples/SKIM-self.md`
- Modify: `.gitignore` (keep `/SKIM.md` ignored; `examples/` is tracked)

**Step 1: Write README.md**

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
| `--no-default-ignore` | | keep lockfiles, minified assets, images |
| `--untracked` | off | include untracked, non-ignored files |
| `--sort changes\|path` | `changes` | file order |
| `--diff` | off | append `git diff` + staged diff |
| `--log N` | `0` | append last N commits |
| `--no-secret-scan` | | disable secret heuristics |

Requires Python ≥ 3.9 and `git` on PATH. The input must be a git repository (local path or clone URL).

## Output

    # repo-name
    Branch main @ 3f2a9c1 · 2026-10-01 · 42/48 files · ~12.3k tokens (est.) · mode: skim

    ## Structure        ASCII tree, skipped files annotated
    ## Largest files    top 10 by estimated tokens
    ## Database schema  only when changelogs/migrations are found
    ## Files            one section per file, most-changed first
    ## Recent changes   only with --diff / --log

See [`examples/SKIM-self.md`](examples/SKIM-self.md) for gitskim applied to itself.

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

## Development

    python3 -m unittest -v

MIT License.
```

**Step 2: Generate the dogfooding example**

```bash
mkdir -p examples
python3 gitskim.py . -o examples/SKIM-self.md --exclude "examples/*"
head -30 examples/SKIM-self.md
```
Expected: header, tree with `gitskim.py`, `README.md`, `tests/`, `docs/`; no secret warnings on stderr.

**Step 3: Run the full suite one last time**

Run: `python3 -m unittest -v`
Expected: all `ok`

**Step 4: Commit and push**

```bash
git add README.md examples/SKIM-self.md
git commit -m "docs: README with usage, comparison, limitations; dogfooding example"
git push
```

---

## Done criteria

- `python3 -m unittest -v` green.
- `python3 gitskim.py . --stdout` runs on the gitskim repo without warnings.
- `python3 gitskim.py https://github.com/vfichtner/gitskim --stdout | head` works (needs network).
- README reflects the actual flags (`python3 gitskim.py --help`).
