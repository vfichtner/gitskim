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

URL_RE = re.compile(r"^(https?://|git@|ssh://|git://|file://)")


def run_git(repo: Path, *args: str) -> str:
    """Run a git command in repo and return stdout. Raises GitskimError on failure."""
    try:
        res = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    except FileNotFoundError:
        raise GitskimError("git not found on PATH") from None
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", "replace").strip()
        raise GitskimError(f"git {' '.join(args)} failed: {err}") from None
    # surrogateescape: never raise on odd bytes in paths; keeps \r etc. intact.
    return res.stdout.decode("utf-8", "surrogateescape")


def repo_name_from_url(url: str) -> str:
    """Last path segment of a git URL without .git, e.g. git@host:user/repo.git -> repo."""
    return re.split(r"[/:]", url.rstrip("/"))[-1].removesuffix(".git")


def resolve_source(arg: str) -> tuple:
    """Return (repo_path, tempdir_or_None, repo_name).

    URLs are shallow-cloned into a tempdir the caller must remove via cleanup().
    """
    if shutil.which("git") is None:
        raise GitskimError("git not found on PATH")

    if URL_RE.match(arg) or arg.endswith(".git"):
        tmp = Path(tempfile.mkdtemp(prefix="gitskim-"))
        dest = tmp / "repo"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", arg, str(dest)],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            cleanup(tmp)
            raise GitskimError(f"clone failed: {e.stderr.strip()}") from None
        return dest, tmp, repo_name_from_url(arg)

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


# ── File collection ───────────────────────────────────────────────────────────

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


def matches_any(rel: str, patterns: list, casefold: bool = False) -> bool:
    """fnmatch against the full relative path and the basename.

    casefold=True compares lowercased path/name against lowercased patterns
    (used for the built-in ignore list so LOGO.PNG is ignored like logo.png).
    """
    name = rel.rsplit("/", 1)[-1]
    if casefold:
        rel, name = rel.lower(), name.lower()
        return any(
            fnmatch.fnmatchcase(rel, p.lower()) or fnmatch.fnmatchcase(name, p.lower())
            for p in patterns
        )
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p) for p in patterns)


def in_ignored_dir(rel: str) -> bool:
    return bool(set(rel.split("/")[:-1]) & IGNORE_DIRS)


def is_binary(path: Path) -> bool:
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)


def collect_files(repo: Path, opts: Options) -> list:
    """List candidate files via git ls-files and apply the filter chain.

    Order: --exclude always wins; an explicit --include match bypasses the
    built-in ignore list (explicit beats default); otherwise the built-in
    ignore list applies. Filtered-out files are dropped. Too-large, binary and
    unreadable files are kept with a status so they still appear in the tree.
    """
    args = ["ls-files", "-z"]
    if opts.untracked:
        args += ["--cached", "--others", "--exclude-standard"]
    out = run_git(repo, *args)
    entries = []
    for rel in sorted(set(filter(None, out.split("\0")))):   # set: unmerged entries repeat
        if opts.exclude and matches_any(rel, opts.exclude):
            continue
        if opts.include:
            if not matches_any(rel, opts.include):
                continue
        elif opts.default_ignore and (
            in_ignored_dir(rel) or matches_any(rel, DEFAULT_IGNORE, casefold=True)
        ):
            continue
        p = repo / rel
        if not p.is_file():          # submodule dirs, deleted-but-indexed files
            continue
        entry = FileEntry(path=rel, size=0)
        try:
            entry.size = p.stat().st_size
            if entry.size > opts.max_size_kb * 1024:
                entry.status = "too_large"
            elif is_binary(p):
                entry.status = "binary"
        except OSError as ex:
            entry.status, entry.note = "error", str(ex)
        entries.append(entry)
    return entries


# ── Ranking ───────────────────────────────────────────────────────────────────

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


# ── Tokens & secrets ──────────────────────────────────────────────────────────

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
        # \w* prefix so db_password / MY_SECRET match; a leading \b alone would miss them
        r"(?i)\b\w*(api[_-]?key|secret|password|passwd|token)\w*\s*[=:]\s*[\"'][^\"'\s]{8,}[\"']")),
]


def find_secret(text: str) -> Optional[str]:
    """Return a short label for the first secret-looking pattern found, else None."""
    for label, pat in SECRET_PATTERNS:
        if pat.search(text):
            return label
    return None


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
