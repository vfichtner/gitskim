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
