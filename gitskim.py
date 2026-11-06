#!/usr/bin/env python3
"""gitskim – skim a git repository into one compact Markdown file for LLM context.

Single file, standard library only. Usage: python3 gitskim.py <path-or-url> [-o FILE]  (default: <repo>_<YYYY-MM-DD_HHMM>.md)
"""
from __future__ import annotations

import argparse
import ast
import configparser
import datetime as _dt
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
    ignore_dirs: list = field(default_factory=list)   # extra directory names, from the config file
    anonymized: bool = False                          # only labels the header; the text is replaced after render()


@dataclass
class FileEntry:
    path: str                      # repo-relative, posix separators
    size: int
    status: str = "ok"             # ok | too_large | binary | secret | error
    note: str = ""
    commits: int = 0
    tokens: int = 0
    lang: str = ""
    content: str = ""              # what goes into the output file (skimmed or full)
    db_schema: list = field(default_factory=list)


def warn(msg: str) -> None:
    print(f"gitskim: {msg}", file=sys.stderr)


QUIET = False                 # -q: progress messages off, warnings and errors stay
PROGRESS_INTERVAL = 2.0       # seconds between "still working" lines on big repos


def info(msg: str) -> None:
    if not QUIET:
        warn(msg)


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
        info(f"cloning {arg} …")
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


def find_repos(root: Path, max_depth: int = 3, skip_dirs: tuple = ()) -> list:
    """Git repositories below root (not root itself), up to max_depth levels deep.

    Does not descend into a found repo, hidden directories or IGNORE_DIRS.
    Returns sorted root-relative posix paths, e.g. ["api", "team/web"].
    """
    found = []
    root = root.resolve()
    for dirpath, dirnames, _ in os.walk(root):
        d = Path(dirpath)
        depth = len(d.relative_to(root).parts)
        if depth and (d / ".git").exists():      # .git is a file in worktrees/submodules
            found.append(d.relative_to(root).as_posix())
            dirnames[:] = []
            continue
        if depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [n for n in dirnames if not n.startswith(".") and n not in IGNORE_DIRS and n not in skip_dirs]
    return sorted(found)


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
    return any(fnmatch.fnmatchcase(rel, p) or fnmatch.fnmatchcase(name, p) for p in patterns)


def _user_match(rel: str, prefix: str, patterns: list) -> bool:
    return matches_any(rel, patterns) or bool(prefix and matches_any(prefix + rel, patterns))


def in_ignored_dir(rel: str) -> bool:
    return bool(set(rel.split("/")[:-1]) & IGNORE_DIRS)


def is_binary(path: Path) -> bool:
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)


def collect_files(repo: Path, opts: Options, prefix: str = "") -> list:
    """List candidate files via git ls-files and apply the filter chain.

    Order: --exclude always wins. IGNORE_DIRS (node_modules, .venv, dist, ...)
    apply whenever the built-in ignore is on, even under --include; only
    --no-default-ignore disables them. An explicit --include match bypasses the
    DEFAULT_IGNORE file patterns (lockfiles, images, ...); otherwise those
    apply too. Filtered-out files are dropped. Too-large, binary and unreadable
    files are kept with a status so they still appear in the tree.

    prefix (multi-repo mode, e.g. "api/") lets --include/--exclude also match
    the workspace-relative path, so --exclude "legacy/*" drops a whole repo.
    """
    args = ["ls-files", "-z"]
    if opts.untracked:
        args += ["--cached", "--others", "--exclude-standard"]
    out = run_git(repo, *args)
    entries = []
    for rel in sorted(set(filter(None, out.split("\0")))):   # set: unmerged entries repeat
        if opts.exclude and _user_match(rel, prefix, opts.exclude):
            continue
        if opts.ignore_dirs and set(rel.split("/")[:-1]) & set(opts.ignore_dirs):
            continue
        if opts.default_ignore and in_ignored_dir(rel):
            continue
        if opts.include:
            if not _user_match(rel, prefix, opts.include):
                continue
        elif opts.default_ignore and matches_any(rel, DEFAULT_IGNORE, casefold=True):
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
            # quotepath=off: otherwise non-ASCII paths come back as "caf\303\251.txt"
            out = run_git(repo, "-c", "core.quotepath=off", "log", "--name-only", "--format=", f"-n{max_commits}")
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
SECRET_PATH_EXCEPTIONS = [".env.example", ".env.sample", ".env.template", ".env.dist", "*.example", "*.sample", "*.template"]


def is_secret_path(rel: str) -> bool:
    """Secret-looking filename, unless it is an example/template variant."""
    return matches_any(rel, SECRET_PATHS) and not matches_any(rel, SECRET_PATH_EXCEPTIONS)


SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("Stripe live key", re.compile(r"\bsk_live_[0-9a-zA-Z]{20,}\b")),
    ("credential assignment", re.compile(
        # \w* prefix so db_password / ACCESS_TOKEN match; no suffix so tokenizer / TOKEN_HEADER
        # / api_key_file don't. secret[_-]?key is listed because SECRET_KEY would otherwise be
        # missed (the keyword must directly precede '=' or ':'). Lookaheads skip placeholders.
        r"(?i)\b\w*(api[_-]?key|secret[_-]?key|secret|password|passwd|token)\s*[=:]\s*[\"']"
        r"(?!\$\{|\{\{|<|%\()"
        r"(?![^\"'\s]*(?:your|example|changeme|change-me|xxx|dummy|placeholder|redacted))"
        r"[^\"'\s]{8,}[\"']")),
]


def find_secret(text: str) -> Optional[str]:
    """Return a short label for the first secret-looking pattern found, else None."""
    for label, pat in SECRET_PATTERNS:
        if pat.search(text):
            return label
    return None


# ── Skimmers ──────────────────────────────────────────────────────────────────

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
    head = f"{pad}{kw} {node.name}({ast.unparse(node.args)}){ret}:"
    doc = _first_doc_line(node)
    if doc:
        out += [head, f'{pad}    """{doc}"""']
    else:
        out.append(head + " ...")
    return out


MAX_DEFAULT_LEN = 60


def _py_value(node) -> str:
    """Unparsed default/value, truncated so giant literals don't bloat the skim."""
    v = ast.unparse(node)
    return v if len(v) <= MAX_DEFAULT_LEN else v[:MAX_DEFAULT_LEN] + "…"


def _py_class(node, indent: int = 0) -> list:
    pad = "    " * indent
    out = [f"{pad}@{ast.unparse(d)}" for d in node.decorator_list]
    bases = ", ".join(ast.unparse(b) for b in node.bases)
    out.append(f"{pad}class {node.name}({bases}):" if bases else f"{pad}class {node.name}:")
    doc = _first_doc_line(node)
    if doc:
        out.append(f'{pad}    """{doc}"""')
    members = 0
    for n in node.body:                       # fields (with defaults)
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            line = f"{pad}    {n.target.id}: {ast.unparse(n.annotation)}"
            if n.value is not None:
                line += f" = {_py_value(n.value)}"
            out.append(line)
            members += 1
    for n in node.body:                       # nested classes (Meta, Config, ...)
        if isinstance(n, ast.ClassDef):
            out.extend(_py_class(n, indent + 1))
            members += 1
    for n in node.body:                       # methods
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.extend(_py_func(n, indent + 1))
            members += 1
    if members == 0 and not doc:
        out.append(f"{pad}    ...")
    return out


def skim_python(text: str) -> str:
    """Signatures only: docstring, imports, constants, classes, functions."""
    text = text.lstrip("\ufeff")
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


# Lines that start a declaration in C-like / Go / Rust / Java / C# / Kotlin / Swift / TS code.
_MODIFIERS = (r"(?:export\s+|default\s+|pub(?:\([^)]*\))?\s+|public\s+|private\s+|protected\s+|internal\s+|"
              r"static\s+|async\s+|abstract\s+|final\s+|override\s+|readonly\s+|unsafe\s+|extern\s+|declare\s+|"
              r"data\s+|sealed\s+|open\s+|suspend\s+|inline\s+|operator\s+)*")
_KEYWORDS = (r"(?:import|from|package|using|namespace|module|function|class|interface|enum|struct|union|impl|"
             r"trait|fn|func|def|record|extends|implements|fun|object|protocol|extension|companion)\b")
# Declarations that are only interesting at column 0; indented they are locals.
_TOP_ONLY = r"(?:const|let|var|val|use|mod|type)\b"
SIG_RE = re.compile(r"^\s{0,4}" + _MODIFIERS + _KEYWORDS)
TOP_RE = re.compile(r"^" + _MODIFIERS + _TOP_ONLY)
ANNOTATION_RE = re.compile(r"^\s*@\w+")
PREPROC_RE = re.compile(r"^\s{0,4}#(?:\[|\s*(?:include|define|pragma|if|ifdef|ifndef|endif|region))")
CONTROL_RE = re.compile(r"^\s*(?:if|else|for|while|do|switch|case|try|catch|finally|return|with|match|loop|"
                        r"defer|go|select|guard|elif|elseif|when)\b")


def skim_regex(text: str, max_lines: int = 200) -> str:
    """Heuristic skimmer for brace languages: declarations and annotations, no bodies.

    Keeps lines matching SIG_RE / TOP_RE, annotations, preprocessor and attribute
    lines, and any line at indent <= 4 that opens a block ('{') and is not a
    control statement. A trailing '{' becomes '{ ... }'. Lines starting with '}'
    are never kept; a bare '{' (Allman style) attaches to the previous line.
    """
    out: list = []
    prev: Optional[str] = None        # previous significant source line
    prev_kept = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "*", "/*")):
            continue
        if stripped.startswith("#") and not PREPROC_RE.match(line):
            continue
        if stripped.startswith("}"):          # closing braces, "} else {", "} catch (e) {"
            prev, prev_kept = None, False
            continue
        expanded = raw.expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip())
        if stripped == "{":                   # Allman: the opener is the previous line
            if prev is not None and indent <= 4 and not CONTROL_RE.match(prev):
                if not prev_kept:
                    out.append(prev + " { ... }")
                elif not out[-1].endswith("{ ... }"):
                    out[-1] += " { ... }"
            prev, prev_kept = None, False
            continue
        opens_block = stripped.endswith("{") and indent <= 4 and not CONTROL_RE.match(line)
        keep = bool(TOP_RE.match(line) or SIG_RE.match(line) or ANNOTATION_RE.match(line)
                    or PREPROC_RE.match(line) or opens_block)
        if keep:
            if stripped.endswith("{"):
                line = line[: line.rfind("{")].rstrip() + " { ... }"
            out.append(line)
        prev, prev_kept = line, keep
    if len(out) > max_lines:
        rest = len(out) - max_lines
        out = out[:max_lines] + [f"… ({rest} more signature lines)"]
    return "\n".join(out)


HEADING_RE = re.compile(r"^#{1,6}\s")
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


def skim_markdown(text: str) -> str:
    """Headings only; '#' lines inside ``` / ~~~ fences are code, not headings."""
    heads = []
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and HEADING_RE.match(line):
            heads.append(line.rstrip())
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


# ── Database changelogs ───────────────────────────────────────────────────────

DB_PATH_RE = re.compile(r"(changelog|liquibase|migration|flyway)", re.I)
DB_EXTS = (".xml", ".yaml", ".yml", ".sql")


def is_db_changelog(rel: str) -> bool:
    return rel.lower().endswith(DB_EXTS) and bool(DB_PATH_RE.search(rel))


def _tag(el) -> str:
    return el.tag.split("}", 1)[-1]


def _xml_col(col) -> str:
    s = f"{col.get('name', '?')} {col.get('type', '')}".strip()
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
    tbl = g("tableName") or g("baseTableName") or g("oldTableName") or ""
    col = g("columnName") or g("columnNames") or ""
    if tbl:
        return f"{t} {tbl}{'.' + col if col else ''}"
    return f"other: {t}"


def skim_liquibase_xml(text: str) -> list:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out = []
    for cs in root.iter():
        t = _tag(cs)
        if t in ("include", "includeAll"):      # master changelog
            out.append(f"- include {cs.get('file') or cs.get('path') or '?'}")
        elif t == "changeSet":
            ops = [op for op in (_xml_op(ch) for ch in cs) if op]
            out.append(f"- {cs.get('id', '?')}/{cs.get('author', '?')}: " + "; ".join(ops))
    return out


SQL_CHANGESET_RE = re.compile(r"^\s*--\s*changeset\s+(\S+)", re.I)
SQL_DDL_RE = re.compile(r"^(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|VIEW)|ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX))\b", re.I)


def skim_liquibase_sql(text: str, max_len: int = 200, default_label: str = "?") -> list:
    """One line per --changeset with its DDL statements, whitespace squashed.

    Statements before any --changeset marker (e.g. Flyway files) are labelled default_label.
    """
    out = []
    current = default_label
    ops: list = []
    buf: list = []

    def flush_stmt():                 # also called without a trailing ';' (boundary / EOF)
        nonlocal buf
        stmt = re.sub(r"\s+", " ", " ".join(buf)).strip().rstrip(";").strip()
        buf = []
        if stmt and SQL_DDL_RE.match(stmt):
            ops.append(stmt[:max_len] + ("…" if len(stmt) > max_len else ""))

    def flush_changeset():
        flush_stmt()
        if ops:
            out.append(f"- {current}: " + "; ".join(ops))

    for line in text.splitlines():
        m = SQL_CHANGESET_RE.match(line)
        if m:
            flush_changeset()
            current, ops = m.group(1), []
            continue
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if ";" in line:
            flush_stmt()
    flush_changeset()
    return out


_YAML_OPS = ("createTable", "addColumn", "dropColumn", "renameColumn", "addForeignKeyConstraint", "createIndex", "dropTable")
_YAML_STRUCTURAL = {"changes", "columns", "column", "constraints", "preConditions", "rollback", "validCheckSum"}


def skim_liquibase_yaml(text: str) -> list:
    """Very rough YAML changelog reader: changeSet id/author, op, tableName, columns."""
    out = []
    cs: Optional[dict] = None

    def flush():
        if cs:
            ops = []
            for op in cs["ops"]:
                cols = ", ".join(c.strip() for c in op["cols"])
                head = f"{op['name']} {op['table']}" if op.get("table") else op["name"]
                ops.append(f"{head}({cols})" if op["cols"] else head)
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
        elif not val and key[:1].islower() and key.isidentifier() and key not in _YAML_STRUCTURAL:
            cs["ops"].append({"name": f"other: {key}", "cols": []})    # unknown change type
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
        return skim_liquibase_sql(text, default_label=Path(rel).stem)
    return skim_liquibase_yaml(text)


# ── Rendering ─────────────────────────────────────────────────────────────────

def _tree_note(e: FileEntry) -> str:
    if e.status == "too_large":
        kb = e.size / 1024
        return f"  (skipped, {kb / 1024:.1f} MB)" if kb >= 1024 else f"  (skipped, {max(1, int(kb))} KB)"
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


def process_file(repo: Path, e: FileEntry, opts: Options) -> None:
    """Fill content/tokens/lang/db_schema for one 'ok' entry. Mutates e."""
    if e.status != "ok":
        return
    if opts.secret_scan and is_secret_path(e.path):
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
    """Backtick fence one longer than the longest backtick run in content (min 3)."""
    longest = max((len(m) for m in re.findall(r"`+", content)), default=0)
    return "`" * max(3, longest + 1)


def _plural(n: int, word: str, plural: str = "") -> str:
    return f"{n} {word}" if n == 1 else f"{n} {plural or word + 's'}"


def _labelled(x) -> list:
    """diff/log are a plain string (one repo) or [(repo_name, text), ...] (multi-repo)."""
    if isinstance(x, str):
        return [("", x)] if x else []
    return [(label, text) for label, text in x if text]


def render(name: str, branch: str, commit: str, entries: list, opts: Options,
           diff="", log="", repos: Optional[list] = None) -> str:
    """repos: [(name, branch, commit), ...] in multi-repo mode; branch/commit are then ignored."""
    included = [e for e in entries if e.status == "ok"]
    tree = render_tree(entries)
    total = sum(e.tokens for e in included) + estimate_tokens(tree)
    today = _dt.date.today().isoformat()
    mode = ("full" if opts.full else "skim") + (" · anonymized" if opts.anonymized else "")
    where = _plural(len(repos), "repository", "repositories") if repos else f"Branch {branch} @ {commit}"
    out = [
        f"# {name}",
        f"{where} · {today} · {len(included)}/{len(entries)} files · "
        f"~{fmt_tokens(total)} tokens (est.) · mode: {mode}",
        "",
    ]
    if repos:
        out += ["## Repositories", "", "| Repository | Branch | Commit | Files | ~Tokens |", "|---|---|---|---:|---:|"]
        for rname, rbranch, rcommit in repos:
            mine = [e for e in included if e.path.startswith(rname + "/")]
            out.append(f"| {rname} | {rbranch} | {rcommit} | {len(mine)} | {fmt_tokens(sum(e.tokens for e in mine))} |")
        out.append("")
    out += ["## Structure", "```", tree, "```", ""]
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
        if not e.content.strip():         # empty files: tree + counts only, no empty code block
            continue
        f = _fence(e.content)
        out += [f"### {e.path} · {_plural(e.commits, 'commit')} · ~{fmt_tokens(e.tokens)} tokens",
                f"{f}{e.lang}", e.content, f, ""]
    logs, diffs = _labelled(log), _labelled(diff)
    if logs or diffs:
        out.append("## Recent changes")
        out.append("")
        for label, text in logs:
            out += [f"### git log{' · ' + label if label else ''}", "```", text.rstrip("\n"), "```", ""]
        for label, text in diffs:
            out += [f"### git diff (working tree + staged){' · ' + label if label else ''}",
                    "```diff", text.rstrip("\n"), "```", ""]
    return "\n".join(out).rstrip("\n") + "\n"


# ── Anonymization ─────────────────────────────────────────────────────────────
#
# Replaces company, customer, people, host and project names with stable, typed
# pseudonyms (Acme -> Company1) over the rendered Markdown. The mapping is kept in
# a local JSON file so the same name always gets the same pseudonym and an LLM
# answer can be translated back with --deanonymize.

ANON_PROMPT = """\
You get a Markdown summary of a code repository. It will later be sent to an
external LLM. Find everything that identifies the company, its customers or
partners, people, internal infrastructure or internal projects: company and
customer names (also inside package names such as com.acme.*), product and
code names, names of people (also in comments, @author tags, TODOs), internal
domains and host names. Ignore open-source libraries, frameworks, programming
languages and general technical or business terms. The section "Already
covered" lists what gitskim replaces on its own; do not repeat it.

Answer with exactly two parts:

1. An INI block that starts with [anonymize] and has these keys:
   terms   = one entry per line, indented, as "Name" or "Name = kind";
             kind is one word such as company, customer, person, project,
             product (default: company). Use the spelling found in the text;
             gitskim also matches other cases and CamelCase/package forms.
   domains = internal domains, one per line (e.g. acme.internal); hosts below
             them are replaced as well.
   keep    = words gitskim must not replace even though they look like names.
   enabled = true
2. A Markdown table: Term | Kind | Reason | Example location. Mark uncertain
   cases with "?".

Only list what actually appears in the text. Do not write an anonymized version
of the document."""

ANON_KEYS = {"enabled", "confirmed", "terms", "domains", "keep", "map"}


def _commented(text: str, prefix: str = "#   ") -> str:
    return "\n".join((prefix + line).rstrip() for line in text.splitlines())


ANON_SECTION = f"""\
[anonymize]
# Replaces company, customer, people, host and project names with stable
# pseudonyms (Acme -> Company1) when you run `gitskim --anonymize`.
# Git authors, their e-mails, the repo/remote names, all e-mail addresses
# and IPv4 addresses are detected automatically; everything else goes below.
#
# First-time setup:
#   1. gitskim --anon-prompt <project>   writes the prompt below plus a normal
#      (NOT anonymized) skim into one file.
#   2. Give that file to an INTERNAL LLM only, never to an external one.
#   3. Paste the [anonymize] block it returns over this section and read its
#      check list.
#
# The prompt (also contained in the --anon-prompt file):
#
{_commented(ANON_PROMPT)}

# enabled = true: anonymize every run (turn off once with --no-anonymize).
enabled = false

# One per line, "Name" or "Name = kind" (kind: company, customer, person,
# project, product, ...; default company).
terms =

# Internal domains; hosts below them are replaced too (api.acme.internal).
domains =

# Words that must never be replaced.
keep =

# confirmed = true   # run with automatic detection only, even if terms is empty
# map = ~/.config/gitskim/anon-map.json   # pseudonym mapping (contains originals!)
"""

ANON_BOOTSTRAP = f"""\
# gitskim configuration, created by `gitskim --anonymize`.
# `gitskim --init-config --config OTHER_FILE` shows all [gitskim] options.

[gitskim]

{ANON_SECTION}"""


@dataclass
class AnonConfig:
    present: bool = False              # the config file has an [anonymize] section
    enabled: bool = False
    confirmed: bool = False
    terms: list = field(default_factory=list)      # [(term, kind), ...]
    domains: list = field(default_factory=list)
    keep: list = field(default_factory=list)
    map: Optional[str] = None


def _config_bool(value: str, key: str, p: Path) -> bool:
    v = value.strip().lower()
    if v in ("1", "yes", "true", "on"):
        return True
    if v in ("", "0", "no", "false", "off"):
        return False
    raise GitskimError(f"invalid config {p}: {key} must be true or false")


def parse_anon_section(sec, p: Path) -> AnonConfig:
    for key in sec:
        if key not in ANON_KEYS:
            warn(f"{p}: unknown key '{key}' in [anonymize] ignored")
    ac = AnonConfig(present=True)
    ac.enabled = _config_bool(sec.get("enabled", ""), "enabled", p)
    ac.confirmed = _config_bool(sec.get("confirmed", ""), "confirmed", p)
    for item in _config_list(sec.get("terms", "")):
        term, _, kind = item.partition("=")
        term, kind = term.strip(), (kind.strip().lower() or "company")
        if not re.fullmatch(r"[a-z]+", kind):
            raise GitskimError(f"invalid config {p}: kind of '{term}' must be one word (a-z), got '{kind}'")
        if term:
            ac.terms.append((term, kind))
    ac.domains = [d.strip().strip(".").lower() for d in _config_list(sec.get("domains", "")) if d.strip(". ")]
    ac.keep = _config_list(sec.get("keep", ""))
    ac.map = sec.get("map", "").strip() or None
    return ac


def ensure_anon_section(p: Path) -> str:
    """Create the config with an [anonymize] section, or append the section. Returns 'created' or 'appended'."""
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(ANON_BOOTSTRAP, encoding="utf-8")
        return "created"
    old = p.read_text(encoding="utf-8")
    with p.open("a", encoding="utf-8") as f:
        f.write(("" if old.endswith("\n") else "\n") + "\n" + ANON_SECTION)
    return "appended"


# Auto-detected names (repo names, author name parts, remote owners) are skipped
# when they are this short or a common word, or the replacement would wreck the code.
GENERIC_WORDS = set("""
admin api app apps backend base bot build cli client cloud code common config core
data demo deploy dev docs example frontend gateway infra internal lib libs main
master mobile monorepo platform playground project root scripts sdk server service
services shared site src test tests tool tools user utils web website worker
""".split())
PUBLIC_GIT_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "dev.azure.com",
                    "ssh.dev.azure.com", "sr.ht", "git.sr.ht", "gitee.com"}
BOT_NAME_RE = re.compile(r"\[bot\]$|^(github|dependabot|renovate|snyk-bot|gitlab)\b", re.I)

EMAIL_RE = re.compile(r"(?<![\w.%+-])([A-Za-z0-9._%+-]+)@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![\w-])")
IPV4_RE = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w]|\.\d)")
EXEMPT_EMAIL_LOCAL = {"git", "noreply", "no-reply"}
MIN_TERM_LEN = 3


def _exempt_domain(domain: str) -> bool:
    d = domain.lower()
    return (d in ("localhost", "example.com", "example.org", "example.net")
            or d.endswith((".example", ".test", ".invalid", ".localhost", ".example.com")))


def _exempt_ip(octets: tuple) -> bool:
    a, b, c, d = octets
    return (a == 127 or octets in ((0, 0, 0, 0), (255, 255, 255, 255))
            or (a, b, c) in ((192, 0, 2), (198, 51, 100), (203, 0, 113)))


def _auto_ok(word: str) -> bool:
    return len(word) >= 4 and word.lower() not in GENERIC_WORDS and not word.isdigit()


def _match_case(src: str, repl: str) -> str:
    if src.isupper() and sum(c.isalpha() for c in src) > 1:
        return repl.upper()
    if src.islower():
        return repl.lower()
    return repl


def _bounded(text: str, s: int, e: int) -> bool:
    """Match at text[s:e] starts and ends at a word, camelCase or separator boundary.

    A digit right after the match is no boundary: acme2 -> company12 would be ambiguous
    (and Company1 must not match inside Company10); the leak check reports such spots.
    """
    if s > 0:
        p, c = text[s - 1], text[s]
        if p.isalnum() and not (c.isupper() and (p.islower() or p.isdigit())):
            return False
    if e < len(text):
        n, last = text[e], text[e - 1]
        if n.isdigit():
            return False
        if n.isalnum() and not (n.isupper() and not last.isupper()):
            return False
    return True


def default_map_path(cfg_path: Optional[Path]) -> Path:
    return (cfg_path or default_config_path()).parent / "anon-map.json"


class AnonMap:
    """Stable original <-> pseudonym mapping, persisted as JSON (mode 0600)."""

    def __init__(self, path: Path):
        self.path = path
        self.entries = []                    # [{"original", "kind", "pseudonym"}]
        self.by_orig, self.by_pseudo, self.changed = {}, {}, False
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for ent in data.get("entries", []):
                    self._index(ent)
            except (ValueError, OSError, AttributeError, KeyError, TypeError) as ex:
                raise GitskimError(f"cannot read mapping {path}: {ex}") from None

    def _index(self, ent: dict) -> None:
        self.entries.append(ent)
        self.by_orig.setdefault(ent["original"].lower(), ent)
        self.by_pseudo.setdefault(ent["pseudonym"].lower(), ent)

    def _new_pseudonym(self, kind: str) -> str:
        n = 1                     # first free number; name parts share their person's pseudonym
        while True:
            if kind == "domain":
                p = f"domain{n}.example"
            elif kind == "email":
                p = f"user{n}@example.com"
            elif kind == "ip":
                p = f"10.0.{n // 250}.{n % 250 + 1}"
            else:
                p = f"{kind.capitalize()}{n}"
            if p.lower() not in self.by_pseudo:
                return p
            n += 1

    def get(self, original: str, kind: str, pseudonym: Optional[str] = None) -> dict:
        ent = self.by_orig.get(original.lower())
        if ent is None:
            ent = {"original": original, "kind": kind, "pseudonym": pseudonym or self._new_pseudonym(kind)}
            self._index(ent)
            self.changed = True
        return ent

    def save(self) -> None:
        if not self.changed:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": self.entries}, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        self.changed = False


def _remote_parts(url: str) -> tuple:
    """(host, owner, repo) of a git remote URL; empty strings where unknown."""
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)(?::\d+)?[:/](.+?)(?:\.git)?/?$", url.strip())
    if not m:
        return "", "", ""
    host, path = m.group(1).lower(), m.group(2).strip("/").split("/")
    return host, (path[0] if len(path) > 1 else ""), path[-1]


def _base_domain(host: str) -> str:
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3:   # acme.co.uk
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


@dataclass
class AutoTerms:
    persons: dict = field(default_factory=dict)    # name -> [emails]
    emails: list = field(default_factory=list)     # author e-mails without a usable name
    domains: list = field(default_factory=list)
    orgs: list = field(default_factory=list)
    projects: list = field(default_factory=list)

    def add(self, attr: str, value: str) -> None:
        lst = getattr(self, attr)
        if value and value.lower() not in (x.lower() for x in lst):
            lst.append(value)


def collect_auto_terms(repos: list) -> AutoTerms:
    """repos: [(path, label), ...]. Git authors, remotes and repo/folder names."""
    auto = AutoTerms()
    for repo, label in repos:
        for part in re.split(r"[/+]", label) + [repo.name]:
            if _auto_ok(part):
                auto.add("projects", part)
        try:
            log = run_git(repo, "log", "-n2000", "--format=%an%x00%ae%x00%cn%x00%ce")
        except GitskimError:
            log = ""
        for line in log.splitlines():
            f = line.split("\0")
            for name, email in ((f[0], f[1]), (f[2], f[3])) if len(f) == 4 else ():
                name, email = name.strip(), email.strip().lower()
                if BOT_NAME_RE.search(name) or (email and _exempt_domain(email.rpartition("@")[2])):
                    continue
                if any(_auto_ok(w) for w in name.split()):
                    emails = auto.persons.setdefault(name, [])
                    if email and email not in emails:
                        emails.append(email)
                elif email:
                    auto.add("emails", email)
        try:
            remotes = run_git(repo, "config", "--get-regexp", r"^remote\..*\.url$")
        except GitskimError:
            remotes = ""
        for line in remotes.splitlines():
            host, owner, name = _remote_parts(line.partition(" ")[2])
            if host and host not in PUBLIC_GIT_HOSTS and "." in host:
                auto.add("domains", _base_domain(host))
            if _auto_ok(owner):
                auto.add("orgs", owner)
            if _auto_ok(name):
                auto.add("projects", name)
    return auto


class Anonymizer:
    def __init__(self, amap: AnonMap, keep: list = ()):
        self.map = amap
        self.keep = {k.lower() for k in keep}
        self.terms = {}                   # lower(original) -> entry, matched in text this run
        self.used = {}                    # pseudonym -> kind, replaced this run
        self._rx = None

    def add_term(self, original: str, kind: str, pseudonym: Optional[str] = None) -> Optional[dict]:
        original = original.strip()
        if len(original) < MIN_TERM_LEN or original.lower() in self.keep:
            return None
        ent = self.map.get(original, kind, pseudonym)
        self.terms[original.lower()] = ent
        self._rx = None
        return ent

    def add_config(self, ac: AnonConfig) -> None:
        for term, kind in ac.terms:
            if len(term) < MIN_TERM_LEN:
                warn(f"anonymize: term '{term}' ignored (shorter than {MIN_TERM_LEN} characters)")
            ent = self.add_term(term, kind)
            if ent and kind == "person":
                self._name_parts(term, ent["pseudonym"])
        for d in ac.domains:
            self.add_term(d, "domain")

    def add_auto(self, auto: AutoTerms) -> None:
        for name, emails in auto.persons.items():
            ent = self.add_term(name, "person")
            if ent is None:
                continue
            p = ent["pseudonym"]
            self._name_parts(name, p)
            for email in emails:
                self.map.get(email, "email", f"{p.lower()}@example.com")
                local = email.partition("@")[0].rpartition("+")[2]     # 123+login@users.noreply.github.com
                if _auto_ok(local) and local not in EXEMPT_EMAIL_LOCAL:
                    self.add_term(local, "person", p)
        for email in auto.emails:
            self.map.get(email, "email")
        for d in auto.domains:
            self.add_term(d, "domain")
        for o in auto.orgs:
            self.add_term(o, "org")
        for pr in auto.projects:
            self.add_term(pr, "project")

    def _name_parts(self, name: str, pseudonym: str) -> None:
        """'Max Mustermann' -> 'Mustermann' alone gets the same pseudonym (parts shorter than 4 are skipped)."""
        words = name.split()
        if len(words) > 1:
            for w in words:
                if _auto_ok(w) and w.lower() not in self.terms:
                    self.add_term(w, "person", pseudonym)

    def _use(self, ent: dict) -> str:
        self.used[ent["pseudonym"]] = ent["kind"]
        return ent["pseudonym"]

    def _email(self, m) -> str:
        full, local, domain = m.group(0), m.group(1), m.group(2)
        if (_exempt_domain(domain) or local.lower() in EXEMPT_EMAIL_LOCAL
                or full.lower() in self.keep or full.lower() in self.map.by_pseudo):
            return full
        ent = self.map.get(full, "email")
        self.terms.setdefault(full.lower(), ent)
        return _match_case(full, self._use(ent))

    def _ip(self, m) -> str:
        octets = tuple(int(x) for x in m.groups())
        if max(octets) > 255 or _exempt_ip(octets) or m.group(0) in self.map.by_pseudo or m.group(0) in self.keep:
            return m.group(0)
        ent = self.map.get(m.group(0), "ip")
        self.terms.setdefault(m.group(0), ent)
        return self._use(ent)

    def _term(self, m) -> str:
        if not _bounded(m.string, m.start(), m.end()):
            return m.group(0)
        ent = self.terms.get(m.group(0).lower())
        return _match_case(m.group(0), self._use(ent)) if ent else m.group(0)

    def apply(self, text: str) -> str:
        text = EMAIL_RE.sub(self._email, text)
        text = IPV4_RE.sub(self._ip, text)
        words = [t for t, ent in self.terms.items() if ent["kind"] not in ("email", "ip")]
        if words:
            if self._rx is None:
                alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
                self._rx = re.compile(f"(?:{alts})", re.IGNORECASE)
            text = self._rx.sub(self._term, text)
        return text

    def leaks(self, text: str) -> list:
        """[(original, count, first_line)] for originals that still appear somewhere (any case, substring)."""
        low, out = text.lower(), []
        for key, ent in self.terms.items():
            n = low.count(key)
            if n:
                out.append((ent["original"], n, low[:low.index(key)].count("\n") + 1))
        return sorted(out, key=lambda x: x[2])

    def summary(self) -> str:
        counts = Counter(self.used.values())
        parts = []
        for kind, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            plural = kind[:-1] + "ies" if kind.endswith("y") else kind + "s"
            parts.append(f"{n} {kind if n == 1 else plural}")
        return ", ".join(parts) or "nothing to replace"


def deanonymize(text: str, amap: AnonMap) -> str:
    if not amap.by_pseudo:
        return text
    alts = "|".join(re.escape(p) for p in sorted(amap.by_pseudo, key=len, reverse=True))
    rx = re.compile(f"(?:{alts})", re.IGNORECASE)

    def repl(m):
        if not _bounded(m.string, m.start(), m.end()) or m.string[m.end():m.end() + 1] == "@":
            return m.group(0)
        return _match_case(m.group(0), amap.by_pseudo[m.group(0).lower()]["original"])

    return rx.sub(repl, text)


def anon_prompt_bundle(name: str, auto: AutoTerms, ac: AnonConfig, skim: str) -> str:
    def items(xs):
        return ", ".join(xs) or "(none)"
    persons = [f"{n} <{', '.join(e)}>" if e else n for n, e in auto.persons.items()]
    out = [
        f"# Anonymization analysis: {name}",
        "",
        "> Contains original, NOT anonymized data. Give this file to an internal LLM only.",
        "",
        "## Task",
        "",
        ANON_PROMPT,
        "",
        "## Already covered",
        "",
        "gitskim replaces these on its own, together with every e-mail address and IPv4 address in the text:",
        "",
        f"- People (git authors): {items(persons + auto.emails)}",
        f"- Domains (from git remotes): {items(auto.domains)}",
        f"- Organisations (remote owners): {items(auto.orgs)}",
        f"- Projects (repo and folder names): {items(auto.projects)}",
        "",
    ]
    if ac.terms or ac.domains or ac.keep:
        out += ["## Current [anonymize] config (extend it, keep these entries)", "", "```ini", "[anonymize]"]
        out += ["terms ="] + [f"    {t}" + (f" = {k}" if k != "company" else "") for t, k in ac.terms]
        out += ["domains ="] + [f"    {d}" for d in ac.domains]
        out += ["keep ="] + [f"    {k}" for k in ac.keep]
        out += ["```", ""]
    out += ["## Repository skim", "", "---", "", skim]
    return "\n".join(out).rstrip("\n") + "\n"


# ── CLI ───────────────────────────────────────────────────────────────────────


def non_negative_int(value: str) -> int:
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return n


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gitskim",
        description="Skim a git repository into one compact Markdown file for LLM context.",
    )
    p.add_argument("source", nargs="*",
                   help="project name(s) from the configured workspace, a local path or a git URL; "
                        "a folder that is not a repo is scanned for repos (default: the whole workspace)")
    p.add_argument("--config", metavar="FILE", help="config file (default: $GITSKIM_CONFIG or ~/.config/gitskim/config.ini)")
    p.add_argument("--init-config", action="store_true", help="write a commented config template and exit")
    p.add_argument("--list", action="store_true", help="list the projects in the configured workspace and exit")
    p.add_argument("-o", "--output", help="output file (default: <repo>_<YYYY-MM-DD_HHMM>.md)")
    p.add_argument("--output-dir", metavar="DIR",
                   help="write into DIR, created if missing (default: $GITSKIM_OUTPUT_DIR, then output_dir from the config)")
    p.add_argument("--stdout", action="store_true", help="write to stdout instead of a file")
    p.add_argument("--full", action="store_true", help="full file contents instead of signatures")
    p.add_argument("--include", action="append", default=[], metavar="GLOB", help="only files matching GLOB (repeatable)")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip files matching GLOB (repeatable)")
    p.add_argument("--max-size", type=non_negative_int, metavar="KB", help="skip files larger than KB (default: 100)")
    p.add_argument("--no-default-ignore", action="store_true", help="disable built-in ignore list")
    p.add_argument("--untracked", action="store_true", help="include untracked (non-ignored) files")
    p.add_argument("--sort", choices=["changes", "path"], default="changes", help="file order (default: changes)")
    p.add_argument("--diff", action="store_true", help="append working-tree and staged diff")
    p.add_argument("--log", type=int, default=0, metavar="N", help="append last N commits")
    p.add_argument("--no-secret-scan", action="store_true", help="disable secret heuristics")
    p.add_argument("--anonymize", action="store_true",
                   help="replace company, people, host and project names with stable pseudonyms (see [anonymize] in the config)")
    p.add_argument("--no-anonymize", action="store_true", help="do not anonymize, even if the config enables it")
    p.add_argument("--anon-prompt", action="store_true",
                   help="write an LLM prompt plus the NOT anonymized skim, to build the [anonymize] config with an internal LLM")
    p.add_argument("--deanonymize", nargs="?", const="-", metavar="FILE",
                   help="translate pseudonyms in FILE (default: stdin) back to the originals, print to stdout")
    p.add_argument("--anon-map", metavar="FILE", help="pseudonym mapping file (default: anon-map.json next to the config)")
    p.add_argument("--multi", action="store_true", help="treat source as a folder of repos even if it is a repo itself")
    p.add_argument("--scan-depth", type=non_negative_int, metavar="N",
                   help="how deep to look for repos in multi-repo mode (default: 3)")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress messages (warnings and errors are still shown)")
    p.add_argument("--version", action="version", version=f"gitskim {__version__}")
    return p


LARGE_FILE_TOKENS = 8000


# ── Config file ───────────────────────────────────────────────────────────────

CONFIG_TEMPLATE = """\
# gitskim configuration. Command-line flags win over these values;
# --exclude adds to the list below instead of replacing it.

[gitskim]
# Folder that holds your git projects. `gitskim api` then skims <workspace>/api,
# `gitskim api web` skims both into one file, plain `gitskim` the whole workspace.
# Projects are found up to scan_depth levels deep and can be named by their
# path below the workspace (team/web) or, if unique, by folder name (web).
workspace = ~/code

# Where output files go (overridden by --output-dir and $GITSKIM_OUTPUT_DIR).
output_dir = ~/skims

# File globs that are never included, one per line (or comma-separated).
# Matched against the path inside the repo, the path below the workspace
# (api/src/...) and the bare file name.
exclude =
    *.log
    *.csv

# Directory names that are skipped wherever they appear, in addition to
# the built-in ones (node_modules, dist, build, .venv, ...).
ignore_dirs =
    generated

# max_size = 100
# scan_depth = 3

""" + ANON_SECTION

CONFIG_KEYS = {"workspace", "output_dir", "exclude", "ignore_dirs", "max_size", "scan_depth"}


@dataclass
class Config:
    path: Optional[Path] = None
    workspace: Optional[Path] = None
    output_dir: Optional[str] = None
    exclude: list = field(default_factory=list)
    ignore_dirs: list = field(default_factory=list)
    max_size: Optional[int] = None
    scan_depth: Optional[int] = None
    anon: AnonConfig = field(default_factory=AnonConfig)


def default_config_path() -> Path:
    if os.environ.get("GITSKIM_CONFIG"):
        return Path(os.environ["GITSKIM_CONFIG"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "gitskim" / "config.ini"


def _config_list(value: str) -> list:
    return [x.strip() for line in value.splitlines() for x in line.split(",") if x.strip()]


def load_config(path: Optional[str]) -> Config:
    """Read the [gitskim] section. A missing default file means no config; a missing --config file is an error."""
    explicit = path is not None
    p = Path(path).expanduser() if explicit else default_config_path()
    if not p.is_file():
        if explicit:
            raise GitskimError(f"config file not found: {p}")
        return Config()
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    try:
        cp.read(p, encoding="utf-8")
    except configparser.Error as ex:
        raise GitskimError(f"invalid config {p}: {ex}") from None
    if not cp.has_section("gitskim"):
        raise GitskimError(f"invalid config {p}: missing [gitskim] section")
    sec = cp["gitskim"]
    for key in sec:
        if key not in CONFIG_KEYS:
            warn(f"{p}: unknown key '{key}' ignored")
    cfg = Config(path=p)
    if sec.get("workspace", "").strip():
        cfg.workspace = Path(sec["workspace"].strip()).expanduser()
    cfg.output_dir = sec.get("output_dir", "").strip() or None
    cfg.exclude = _config_list(sec.get("exclude", ""))
    cfg.ignore_dirs = [d.strip("/") for d in _config_list(sec.get("ignore_dirs", ""))]
    for key in ("max_size", "scan_depth"):
        if sec.get(key, "").strip():
            try:
                value = int(sec[key])
                if value < 0:
                    raise ValueError
            except ValueError:
                raise GitskimError(f"invalid config {p}: {key} must be an integer >= 0") from None
            setattr(cfg, key, value)
    if cp.has_section("anonymize"):
        cfg.anon = parse_anon_section(cp["anonymize"], p)
    return cfg


def init_config(path: Optional[str]) -> Path:
    p = Path(path).expanduser() if path else default_config_path()
    if p.exists():
        raise GitskimError(f"config already exists: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return p


def resolve_project(name: str, projects: list) -> Optional[str]:
    """Match name against workspace-relative repo paths: exact path first, then unique folder name."""
    name = name.strip("/")
    if name in projects:
        return name
    hits = [rel for rel in projects if rel.rsplit("/", 1)[-1] == name]
    if len(hits) > 1:
        raise GitskimError(f"'{name}' is ambiguous in the workspace: {', '.join(hits)}")
    return hits[0] if hits else None


def default_output_name(name: str, now: Optional[_dt.datetime] = None) -> str:
    """<repo>_<YYYY-MM-DD_HHMM>.md: sortable, no colons (safe on Windows)."""
    return f"{name}_{(now or _dt.datetime.now()).strftime('%Y-%m-%d_%H%M')}.md"


def output_path(output: Optional[str], output_dir: Optional[str], name: str) -> Path:
    """-o alone: as given. --output-dir alone: DIR/<default name>. Both: DIR/<-o> (unless -o is absolute)."""
    if not output_dir:
        return Path(output or default_output_name(name))
    d = Path(output_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d / Path(output).expanduser() if output else d / default_output_name(name)


def repo_meta(repo: Path) -> tuple:
    try:
        branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        commit = run_git(repo, "rev-parse", "--short", "HEAD").strip()
    except GitskimError:
        branch, commit = "-", "-"
    return branch, commit


def skim_repo(repo: Path, opts: Options, prefix: str = "", label: str = "") -> list:
    """Collect, rank and process one repo. Entry paths get prefix prepended afterwards."""
    label = label or prefix.rstrip("/") or repo.name
    entries = collect_files(repo, opts, prefix)
    info(f"{label}: {_plural(len(entries), 'file')}")
    rank_files(repo, entries, opts.sort)
    last = time.monotonic()
    for i, e in enumerate(entries, 1):
        if time.monotonic() - last >= PROGRESS_INTERVAL:
            info(f"{label}: {i}/{len(entries)} files …")
            last = time.monotonic()
        try:
            process_file(repo, e, opts)
        except OSError as ex:
            e.status, e.note = "error", str(ex)
            warn(f"cannot read {prefix}{e.path}: {ex}")
    for e in entries:
        e.path = prefix + e.path
    return entries


def recent_changes(repo: Path, args) -> tuple:
    diff = log = ""
    try:                                  # both fail on a repo without commits
        if args.diff:
            diff = run_git(repo, "diff") + run_git(repo, "diff", "--cached")
        if args.log:
            log = run_git(repo, "log", f"-n{args.log}", "--oneline", "--no-decorate")
    except GitskimError:
        pass
    return diff, log


def multi_repo_root(source: str, force: bool) -> Optional[Path]:
    """The folder to scan for repos, or None for normal single-repo mode."""
    if URL_RE.match(source) or source.endswith(".git"):
        return None
    p = Path(source).expanduser()
    if not p.is_dir():
        return None                       # resolve_source reports it
    if force:
        return p
    try:
        run_git(p, "rev-parse", "--show-toplevel")
        return None
    except GitskimError:
        return p


def skim_many(root: Path, rels: list, opts: Options, args) -> tuple:
    """Skim several repos below root into one entry list. Returns (entries, repos, diffs, logs)."""
    entries, repos, diffs, logs = [], [], [], []
    for i, rel in enumerate(rels, 1):
        repo = root / rel
        mine = skim_repo(repo, opts, prefix=rel + "/", label=f"[{i}/{len(rels)}] {rel}")
        if not mine:                  # e.g. --exclude "legacy/*": leave the repo out entirely
            continue
        entries += mine
        repos.append((rel, *repo_meta(repo)))
        diff, log = recent_changes(repo, args)
        diffs.append((rel, diff))
        logs.append((rel, log))
    if opts.sort == "changes":
        entries.sort(key=lambda e: (-e.commits, e.path))
    else:
        entries.sort(key=lambda e: e.path)
    if not repos:
        raise GitskimError("all repositories were filtered out")
    info(f"skimmed {_plural(len(repos), 'repository', 'repositories')}: {', '.join(r[0] for r in repos)}")
    return entries, repos, diffs, logs


def anon_setup_needed(cfg_path: Path, what: str) -> int:
    """Explain the first-time setup and return exit code 2 (no output written)."""
    head = {"created": f"created {cfg_path} with an [anonymize] section",
            "appended": f"added an [anonymize] section to {cfg_path}",
            "empty": f"[anonymize] in {cfg_path} has no terms or domains yet"}[what]
    warn(f"{head}; nothing written. Next steps:\n"
         "  1. gitskim --anon-prompt <project>   (prompt + NOT anonymized skim, for an INTERNAL LLM only)\n"
         f"  2. paste the [anonymize] block the LLM returns into {cfg_path}\n"
         "  3. run --anonymize again (or set confirmed = true to rely on automatic detection only)")
    return 2


def main(argv: Optional[list] = None) -> int:
    global QUIET
    args = build_parser().parse_args(argv)
    QUIET = args.quiet
    started = time.monotonic()
    tmp = None
    try:
        if args.init_config:
            warn(f"wrote {init_config(args.config)}")
            return 0
        if args.anonymize and args.anon_prompt:
            raise GitskimError("--anonymize and --anon-prompt exclude each other")
        if args.anonymize and args.no_anonymize:
            raise GitskimError("--anonymize and --no-anonymize exclude each other")
        cfg_path = Path(args.config).expanduser() if args.config else default_config_path()
        if args.anonymize and not cfg_path.exists():
            return anon_setup_needed(cfg_path, ensure_anon_section(cfg_path))
        cfg = load_config(args.config)
        map_path = Path(args.anon_map or cfg.anon.map or default_map_path(cfg.path or cfg_path)).expanduser()
        if args.deanonymize is not None:
            if not map_path.is_file():
                raise GitskimError(f"no pseudonym mapping at {map_path} (written by --anonymize)")
            try:
                text = sys.stdin.read() if args.deanonymize == "-" else Path(args.deanonymize).read_text(encoding="utf-8")
            except OSError as ex:
                raise GitskimError(f"cannot read {args.deanonymize}: {ex}") from None
            sys.stdout.write(deanonymize(text, AnonMap(map_path)))
            return 0
        anonymize = not args.anon_prompt and (args.anonymize or (cfg.anon.enabled and not args.no_anonymize))
        if anonymize:
            if not cfg.anon.present:
                return anon_setup_needed(cfg_path, ensure_anon_section(cfg_path))
            if not (cfg.anon.terms or cfg.anon.domains or cfg.anon.confirmed):
                return anon_setup_needed(cfg_path, "empty")
        scan_depth = args.scan_depth if args.scan_depth is not None else (cfg.scan_depth if cfg.scan_depth is not None else 3)
        opts = Options(
            full=args.full, include=args.include, exclude=cfg.exclude + args.exclude,
            max_size_kb=args.max_size if args.max_size is not None else (cfg.max_size if cfg.max_size is not None else 100),
            default_ignore=not args.no_default_ignore, untracked=args.untracked, sort=args.sort,
            secret_scan=not args.no_secret_scan, ignore_dirs=cfg.ignore_dirs, anonymized=anonymize,
        )
        output_dir = args.output_dir or os.environ.get("GITSKIM_OUTPUT_DIR") or cfg.output_dir
        if shutil.which("git") is None:
            raise GitskimError("git not found on PATH")

        projects = None
        if cfg.workspace is not None:
            if not cfg.workspace.is_dir():
                raise GitskimError(f"workspace from {cfg.path} is not a directory: {cfg.workspace}")
            projects = find_repos(cfg.workspace, scan_depth, tuple(cfg.ignore_dirs))
        if args.list:
            if projects is None:
                raise GitskimError(f"no workspace configured (see --init-config, config: {default_config_path()})")
            print("\n".join(projects))
            return 0

        # Which repos? Workspace project names, else one path/URL.
        sources = args.source
        if not sources:
            if projects is None:
                raise GitskimError("no source given and no workspace configured (see --init-config)")
            sources_rel = projects
        else:
            sources_rel = [resolve_project(src, projects) for src in sources] if projects else [None] * len(sources)
            if None in sources_rel:
                if len(sources) > 1:
                    bad = [s for s, r in zip(sources, sources_rel) if r is None]
                    raise GitskimError(f"unknown project(s) in workspace {cfg.workspace}: {', '.join(bad)}"
                                       if projects is not None else "several sources need a configured workspace")
                sources_rel = None
        if sources_rel is not None:
            if not sources_rel:
                raise GitskimError(f"no git repositories found in workspace {cfg.workspace}")
            rels = list(dict.fromkeys(sources_rel))          # dedupe, keep order
            if not args.source:
                name = cfg.workspace.resolve().name
            else:
                name = "+".join(r.rsplit("/", 1)[-1] for r in rels)
            scanned = [(cfg.workspace / rel, rel) for rel in rels]
            if len(rels) == 1 and args.source:
                repo = cfg.workspace / rels[0]
                entries = skim_repo(repo, opts)
                branch, commit = repo_meta(repo)
                diffs, logs = recent_changes(repo, args)
                repos = None
            else:
                entries, repos, diffs, logs = skim_many(cfg.workspace, rels, opts, args)
                branch = commit = "-"
        else:
            source = sources[0]
            root = multi_repo_root(source, args.multi)
            if root is not None:
                found = find_repos(root, scan_depth, tuple(cfg.ignore_dirs))
                if not found:
                    what = "no git repositories found below" if args.multi else "not a git repository and no git repositories found below it:"
                    hint = f" (and no such project in workspace {cfg.workspace})" if projects is not None else ""
                    raise GitskimError(f"{what} {source}{hint}")
                name = root.resolve().name
                scanned = [(root / rel, rel) for rel in found]
                entries, repos, diffs, logs = skim_many(root, found, opts, args)
                branch = commit = "-"
            else:
                try:
                    repo, tmp, name = resolve_source(source)
                except GitskimError as ex:
                    if projects is not None and not (URL_RE.match(source) or Path(source).expanduser().exists()):
                        raise GitskimError(f"unknown project '{source}' in workspace {cfg.workspace}; "
                                           f"available: {', '.join(projects) or '(none)'}") from None
                    raise ex
                scanned = [(repo, name)]
                entries = skim_repo(repo, opts, label=name)
                branch, commit = repo_meta(repo)
                diffs, logs = recent_changes(repo, args)
                repos = None
        for e in entries:
            if e.status == "secret":
                warn(f"skipped {e.path}: possible secret ({e.note})")
            elif e.status == "ok" and e.tokens > LARGE_FILE_TOKENS:
                warn(f"{e.path} is large (~{fmt_tokens(e.tokens)} tokens est.)")
        md = render(name, branch, commit, entries, opts, diff=diffs, log=logs, repos=repos)
        if args.anon_prompt or anonymize:
            auto = collect_auto_terms(scanned)
        if args.anon_prompt:
            md = anon_prompt_bundle(name, auto, cfg.anon, md)
            name += "_anon-prompt"
            warn("the --anon-prompt output contains original, NOT anonymized data: give it to an internal LLM only")
        elif anonymize:
            amap = AnonMap(map_path)
            anon = Anonymizer(amap, cfg.anon.keep)
            anon.add_config(cfg.anon)
            anon.add_auto(auto)
            md, name = anon.apply(md), anon.apply(name)
            for original, n, line in anon.leaks(md)[:10]:
                warn(f"anonymize: '{original}' still appears {n}x (first in line {line}); "
                     "add the spelling to terms or check the output")
            try:
                amap.save()
            except OSError as ex:
                raise GitskimError(f"cannot write mapping {map_path}: {ex}") from None
            info(f"anonymized: {anon.summary()} (mapping: {map_path})")
        if args.stdout:
            try:
                sys.stdout.write(md)
                sys.stdout.flush()
            except BrokenPipeError:           # e.g. `| head`: exit quietly
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, sys.stdout.fileno())
                return 0
            info(f"done ({sum(1 for e in entries if e.status == 'ok')} files, "
                 f"~{fmt_tokens(estimate_tokens(md))} tokens est., {time.monotonic() - started:.1f}s)")
        else:
            try:
                out_path = output_path(args.output, output_dir, name)
                out_path.write_text(md, encoding="utf-8")
            except OSError as ex:
                raise GitskimError(f"cannot write output: {ex}") from None
            ok = sum(1 for e in entries if e.status == "ok")
            info(f"wrote {out_path} ({ok} files, ~{fmt_tokens(estimate_tokens(md))} tokens est., "
                 f"{time.monotonic() - started:.1f}s)")
        return 0
    except GitskimError as ex:
        warn(str(ex))
        return 1
    finally:
        cleanup(tmp)


if __name__ == "__main__":
    sys.exit(main())
