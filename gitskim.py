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
    return any(fnmatch.fnmatchcase(rel, p) or fnmatch.fnmatchcase(name, p) for p in patterns)


def in_ignored_dir(rel: str) -> bool:
    return bool(set(rel.split("/")[:-1]) & IGNORE_DIRS)


def is_binary(path: Path) -> bool:
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)


def collect_files(repo: Path, opts: Options) -> list:
    """List candidate files via git ls-files and apply the filter chain.

    Order: --exclude always wins. IGNORE_DIRS (node_modules, .venv, dist, ...)
    apply whenever the built-in ignore is on, even under --include; only
    --no-default-ignore disables them. An explicit --include match bypasses the
    DEFAULT_IGNORE file patterns (lockfiles, images, ...); otherwise those
    apply too. Filtered-out files are dropped. Too-large, binary and unreadable
    files are kept with a status so they still appear in the tree.
    """
    args = ["ls-files", "-z"]
    if opts.untracked:
        args += ["--cached", "--others", "--exclude-standard"]
    out = run_git(repo, *args)
    entries = []
    for rel in sorted(set(filter(None, out.split("\0")))):   # set: unmerged entries repeat
        if opts.exclude and matches_any(rel, opts.exclude):
            continue
        if opts.default_ignore and in_ignored_dir(rel):
            continue
        if opts.include:
            if not matches_any(rel, opts.include):
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


def skim_liquibase_sql(text: str, max_len: int = 200) -> list:
    """One line per --changeset with its DDL statements, whitespace squashed."""
    out = []
    current = "?"
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
        return skim_liquibase_sql(text)
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
    return "````" if "```" in content else "```"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


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
        if not e.content.strip():         # empty files: tree + counts only, no empty code block
            continue
        f = _fence(e.content)
        out += [f"### {e.path} · {_plural(e.commits, 'commit')} · ~{fmt_tokens(e.tokens)} tokens",
                f"{f}{e.lang}", e.content, f, ""]
    if log or diff:
        out.append("## Recent changes")
        out.append("")
        if log:
            out += ["### git log", "```", log.rstrip("\n"), "```", ""]
        if diff:
            out += ["### git diff (working tree + staged)", "```diff", diff.rstrip("\n"), "```", ""]
    return "\n".join(out).rstrip("\n") + "\n"


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


if __name__ == "__main__":
    sys.exit(main())
