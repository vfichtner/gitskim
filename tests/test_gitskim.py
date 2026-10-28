from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gitskim  # noqa: E402

# Keep a developer's own ~/.config/gitskim/config.ini and env out of the tests
# (subprocesses inherit os.environ).
os.environ["GITSKIM_CONFIG"] = str(Path(tempfile.gettempdir()) / "gitskim-test-no-config.ini")
os.environ.pop("GITSKIM_OUTPUT_DIR", None)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def make_repo(files: dict, extra_commits: dict | None = None, case: unittest.TestCase | None = None) -> Path:
    """Create a temp git repo. files: {relpath: str|bytes}. extra_commits: {relpath: new_content} committed one by one.

    When case is given, the tempdir is removed via case.addCleanup.
    """
    tmp = Path(tempfile.mkdtemp(prefix="gitskim-test-"))
    if case is not None:
        case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
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


class TestResolveSource(unittest.TestCase):
    def test_local_repo_resolves_to_toplevel(self):
        repo = make_repo({"a.txt": "a", "sub/b.txt": "b"}, case=self)
        path, tmp, name = gitskim.resolve_source(str(repo / "sub"))
        self.assertEqual(path, repo.resolve())
        self.assertIsNone(tmp)
        self.assertEqual(name, repo.name)

    def test_non_repo_raises(self):
        plain = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        with self.assertRaises(gitskim.GitskimError):
            gitskim.resolve_source(str(plain))

    def test_missing_dir_raises(self):
        with self.assertRaises(gitskim.GitskimError):
            gitskim.resolve_source("/definitely/not/here")

    def test_local_url_clone(self):
        repo = make_repo({"a.txt": "a"}, case=self)
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


class TestRepoName(unittest.TestCase):
    def test_ssh_scp_style(self):
        self.assertEqual(gitskim.repo_name_from_url("git@github.com:user/repo.git"), "repo")

    def test_https_with_suffix(self):
        self.assertEqual(gitskim.repo_name_from_url("https://x/y/repo.git"), "repo")

    def test_trailing_slash_no_suffix(self):
        self.assertEqual(gitskim.repo_name_from_url("https://x/y/repo/"), "repo")


class TestRunGit(unittest.TestCase):
    def test_run_git_returns_stdout(self):
        repo = make_repo({"a.txt": "a"}, case=self)
        self.assertEqual(gitskim.run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")

    def test_run_git_failure_raises(self):
        repo = make_repo({"a.txt": "a"}, case=self)
        with self.assertRaises(gitskim.GitskimError):
            gitskim.run_git(repo, "rev-parse", "--verify", "nope")


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
            "LOGO.PNG": b"\x89PNG",
            "pkg/node_modules/x.js": "x",
        }, case=self)

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

    def test_include_bypasses_default_ignore(self):
        entries = gitskim.collect_files(self.repo, gitskim.Options(include=["*.png"]))
        self.assertEqual(self.paths(entries), ["assets/logo.png"])

    def test_exclude_beats_include(self):
        entries = gitskim.collect_files(self.repo, gitskim.Options(include=["*.txt"], exclude=["big.txt"]))
        self.assertEqual(self.paths(entries), ["notes.txt"])

    def test_default_ignore_is_case_insensitive(self):
        entries = gitskim.collect_files(self.repo, gitskim.Options())
        self.assertNotIn("LOGO.PNG", self.paths(entries))

    def test_nested_ignored_dir(self):
        entries = gitskim.collect_files(self.repo, gitskim.Options())
        self.assertNotIn("pkg/node_modules/x.js", self.paths(entries))
        kept = gitskim.collect_files(self.repo, gitskim.Options(default_ignore=False))
        self.assertIn("pkg/node_modules/x.js", self.paths(kept))

    def test_include_keeps_ignore_dirs_active(self):
        inc = gitskim.collect_files(self.repo, gitskim.Options(include=["*.js"]))
        self.assertEqual(self.paths(inc), [])          # dist/ and node_modules/ stay ignored
        raw = gitskim.collect_files(self.repo, gitskim.Options(include=["*.js"], default_ignore=False))
        self.assertEqual(self.paths(raw), ["dist/bundle.js", "pkg/node_modules/x.js"])

    def test_untracked_files_only_with_flag(self):
        (self.repo / "new.txt").write_text("new")
        default = gitskim.collect_files(self.repo, gitskim.Options())
        self.assertNotIn("new.txt", self.paths(default))
        with_flag = gitskim.collect_files(self.repo, gitskim.Options(untracked=True))
        self.assertIn("new.txt", self.paths(with_flag))
        self.assertIn("src/app.py", self.paths(with_flag))


class TestRankFiles(unittest.TestCase):
    @staticmethod
    def touch(repo, rel, content):
        (repo / rel).write_text(content, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"touch {rel}")

    def test_sorted_by_commit_count_then_path(self):
        repo = make_repo({"a.txt": "1", "b.txt": "1", "c.txt": "1"}, case=self)
        self.touch(repo, "c.txt", "2")
        self.touch(repo, "c.txt", "3")
        self.touch(repo, "b.txt", "2")
        entries = gitskim.collect_files(repo, gitskim.Options())
        gitskim.rank_files(repo, entries, "changes")
        self.assertEqual([e.path for e in entries], ["c.txt", "b.txt", "a.txt"])
        self.assertEqual([e.commits for e in entries], [3, 2, 1])

    def test_non_ascii_path_counts(self):
        repo = make_repo({"café.txt": "1", "plain.txt": "1"}, case=self)
        self.touch(repo, "café.txt", "2")
        entries = gitskim.collect_files(repo, gitskim.Options())
        gitskim.rank_files(repo, entries, "changes")
        self.assertEqual([e.path for e in entries], ["café.txt", "plain.txt"])
        self.assertEqual(entries[0].commits, 2)

    def test_sort_path(self):
        repo = make_repo({"b.txt": "1", "a.txt": "1"}, extra_commits={"b.txt": "2"}, case=self)
        entries = gitskim.collect_files(repo, gitskim.Options())
        gitskim.rank_files(repo, entries, "path")
        self.assertEqual([e.path for e in entries], ["a.txt", "b.txt"])


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
        for text in [
            'db_' + 'password = "' + "s3cr3tpassw0rd" + '"',
            'access_' + 'token: "' + "abcd1234efgh" + '"',
            'SECRET_' + 'KEY = "' + "dj4ng0-s3cr3t-k3y" + '"',
        ]:
            self.assertEqual(gitskim.find_secret(text), "credential assignment", text)

    def test_generic_assignment_ignores_lookalikes_and_placeholders(self):
        for text in [
            'tokenizer = "bert-base-uncased"',
            'TOKEN_HEADER = "Authorization"',
            'api_key_file = "config/keys.json"',
            'password: "${DB_PASSWORD}"',
            'API_KEY="your-api-key-here"',
            'secret: "<redacted>"',
        ]:
            self.assertIsNone(gitskim.find_secret(text), text)

    def test_detects_vendor_tokens(self):
        cases = [
            ("GitHub token", "ghp_" + "A1b2C3d4" * 4 + "wxyz"),
            ("Slack token", "xoxb-" + "1234567890-abcdefghij"),
            ("Google API key", "AIza" + "SyD" + "x" * 32),
            ("JWT", "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "abcDEF123_-xyz789"),
            ("Stripe live key", "sk_live_" + "a1B2" * 6),
        ]
        for label, token in cases:
            self.assertEqual(gitskim.find_secret(f"x = '{token}'"), label, label)

    def test_clean_text_passes(self):
        self.assertIsNone(gitskim.find_secret("def main():\n    return 42\n"))
        self.assertIsNone(gitskim.find_secret('password = os.environ["DB_PASSWORD"]'))

    def test_secret_paths(self):
        self.assertTrue(gitskim.matches_any(".env", gitskim.SECRET_PATHS))
        self.assertTrue(gitskim.matches_any("config/.env.local", gitskim.SECRET_PATHS))
        self.assertTrue(gitskim.matches_any("certs/server.pem", gitskim.SECRET_PATHS))
        self.assertFalse(gitskim.matches_any("src/env.py", gitskim.SECRET_PATHS))

    def test_is_secret_path_exempts_templates(self):
        self.assertTrue(gitskim.is_secret_path(".env.local"))
        self.assertTrue(gitskim.is_secret_path("certs/server.pem"))
        for rel in [".env.example", ".env.sample", ".env.template", ".env.dist", "certs/server.pem.example"]:
            self.assertFalse(gitskim.is_secret_path(rel), rel)


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
    tags: list = field(default_factory=list)

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
        self.assertIn("    age: int = 0", self.out)
        self.assertIn("    tags: list = field(default_factory=list)", self.out)
        # with a docstring the def line is a real header (no "..."), docstring indented below
        self.assertIn('    def greet(self, loud: bool=False) -> str:\n        """Say hi."""', self.out)
        self.assertNotIn("-> str: ...", self.out)
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

    def test_nested_class(self):
        out = gitskim.skim_python('class Model:\n    class Meta:\n        ordering = ["x"]\n')
        self.assertIn("class Model:\n    class Meta:\n        ...", out)

    def test_long_default_truncated(self):
        src = "class C:\n    x: dict = {" + ", ".join(f'"k{i}": {i}' for i in range(20)) + "}\n"
        line = next(l for l in gitskim.skim_python(src).splitlines() if l.startswith("    x: dict = "))
        self.assertTrue(line.endswith("…"))
        self.assertEqual(len(line), len("    x: dict = ") + 60 + 1)

    def test_module_constant_annotation_keeps_ellipsis(self):
        self.assertIn("LIMIT: int = ...", gitskim.skim_python("LIMIT: int = 5\n"))

    def test_bom_is_stripped(self):
        self.assertEqual(gitskim.skim_python("\ufeffimport os\n"), "import os")


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


GO_SAMPLE = """package server

import (
\t"fmt"
\t"net/http"
)

type Server struct {
\taddr string
}

func (s *Server) Start() error {
\tif err := http.ListenAndServe(s.addr, nil); err != nil {
\t\treturn fmt.Errorf("listen: %w", err)
\t}
\treturn nil
}
"""

RUST_SAMPLE = """use std::collections::HashMap;

#[derive(Debug)]
pub struct Cache<T> {
    map: HashMap<String, T>,
}

impl<T> Default for Cache<T> {
    pub fn new() -> Self {
        let map = HashMap::new();
        Self { map }
    }
}
"""


class TestSkimRegex(unittest.TestCase):
    def test_let_const_var_only_at_top_level(self):
        out = gitskim.skim_regex("fn main() {\n    let m = HashMap::new();\n}\n")
        self.assertEqual(out, "fn main() { ... }")
        self.assertIn("const MAX: u32 = 3;", gitskim.skim_regex("const MAX: u32 = 3;\n"))

    def test_closing_brace_lines_skipped_and_allman_attached(self):
        self.assertEqual(gitskim.skim_regex("func f() {\n\tif x {\n\t} else {\n\t}\n}\n"), "func f() { ... }")
        self.assertEqual(gitskim.skim_regex("public void Run()\n{\n}\n"), "public void Run() { ... }")
        # Allman after a kept declaration line: no double brace, nothing attached to unrelated lines
        self.assertEqual(gitskim.skim_regex("import foo;\npublic class A\n{\n}\n"), "import foo;\npublic class A { ... }")
        self.assertEqual(gitskim.skim_regex("import foo;\nif (x)\n{\n}\n"), "import foo;")

    def test_preprocessor_and_attributes_kept_comments_dropped(self):
        out = gitskim.skim_regex("#include <stdio.h>\n#define MAX 3\nint main() {\n  return 0;\n}\n")
        self.assertIn("#include <stdio.h>", out)
        self.assertIn("#define MAX 3", out)
        self.assertIn("int main() { ... }", out)
        self.assertIn("#[derive(Debug)]", gitskim.skim_regex("#[derive(Debug)]\npub struct A;\n"))
        self.assertNotIn("comment", gitskim.skim_regex("# a comment\nfunction f() {}\n"))

    def test_kotlin_declarations_and_go_select(self):
        out = gitskim.skim_regex("data class User(val id: Int)\nfun helper(x: Int) = x * 2\nobject Err : Base()\n")
        for sig in ["data class User(val id: Int)", "fun helper(x: Int) = x * 2", "object Err : Base()"]:
            self.assertIn(sig, out)
        self.assertNotIn("select", gitskim.skim_regex("func f() {\n\tselect {\n\tcase <-c:\n\t}\n}\n"))

    def test_go_fixture(self):
        out = gitskim.skim_regex(GO_SAMPLE)
        for sig in ["package server", "type Server struct { ... }", "func (s *Server) Start() error { ... }"]:
            self.assertIn(sig, out)
        for body in ["if err", "return", "addr string"]:
            self.assertNotIn(body, out)

    def test_rust_fixture(self):
        out = gitskim.skim_regex(RUST_SAMPLE)
        for sig in ["use std::collections::HashMap;", "#[derive(Debug)]", "pub struct Cache<T> { ... }",
                    "impl<T> Default for Cache<T> { ... }", "    pub fn new() -> Self { ... }"]:
            self.assertIn(sig, out)
        for body in ["let map", "Self { map }", "map: HashMap"]:
            self.assertNotIn(body, out)

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

    def test_headings_inside_fences_ignored(self):
        text = "# Real\n\n```python\n# not a heading\n## nor this\n```\n\n~~~\n# tilde fenced\n~~~\n\n## Also real\n"
        self.assertEqual(gitskim.skim_markdown(text), "# Real\n## Also real")


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

    def test_sql_without_changeset_markers_uses_file_stem(self):
        text = "CREATE TABLE users (id int);\nINSERT INTO users VALUES (1);\nCREATE INDEX ix ON users(id);\n"
        self.assertEqual(gitskim.skim_db_changelog("db/migration/V1__init.sql", text),
                         ["- V1__init: CREATE TABLE users (id int); CREATE INDEX ix ON users(id)"])

    def test_sql_statement_without_semicolon(self):
        text = "--changeset vf:1\nCREATE TABLE t (id int)\n--changeset vf:2\nCREATE TABLE u (id int)\n"
        self.assertEqual(gitskim.skim_db_changelog("db/changelog/a.sql", text),
                         ["- vf:1: CREATE TABLE t (id int)", "- vf:2: CREATE TABLE u (id int)"])

    def test_xml_master_includes(self):
        master = ('<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog">\n'
                  '  <include file="db/changelog/001.xml"/>\n'
                  '  <includeAll path="db/changelog/releases/"/>\n'
                  '</databaseChangeLog>\n')
        self.assertEqual(gitskim.skim_db_changelog("db/changelog/master.xml", master),
                         ["- include db/changelog/001.xml", "- include db/changelog/releases/"])

    def test_xml_generic_op_with_table_attrs(self):
        xml = ('<databaseChangeLog><changeSet id="3" author="vf">'
               '<addNotNullConstraint tableName="users" columnName="email"/>'
               '<sql>UPDATE users SET x = 1</sql>'
               '<addUniqueConstraint tableName="users" columnNames="a, b"/>'
               '</changeSet></databaseChangeLog>')
        self.assertEqual(gitskim.skim_db_changelog("db/changelog/a.xml", xml),
                         ["- 3/vf: addNotNullConstraint users.email; other: sql; addUniqueConstraint users.a, b"])

    def test_yaml_unknown_op(self):
        text = ("databaseChangeLog:\n  - changeSet:\n      id: 9\n      author: vf\n      changes:\n"
                "        - addNotNullConstraint:\n            tableName: users\n            columnName: email\n")
        self.assertEqual(gitskim.skim_db_changelog("db/changelog/a.yaml", text), ["- 9/vf: other: addNotNullConstraint users"])

    def test_yaml(self):
        lines = gitskim.skim_db_changelog("db/changelog/a.yaml", LB_YAML)
        self.assertEqual(lines, [
            "- 1/vf: createTable users(id uuid, email varchar(255))",
            "- 2/vf: addColumn users(age int)",
        ])


class TestRender(unittest.TestCase):
    def test_empty_file_has_no_files_entry(self):
        entries = [
            gitskim.FileEntry("pkg/__init__.py", 0, commits=1, lang="python", content=""),
            gitskim.FileEntry("pkg/a.py", 10, commits=1, lang="python", content="def f(): ...", tokens=3),
        ]
        out = gitskim.render("demo", "main", "abc1234", entries, gitskim.Options())
        self.assertNotIn("### pkg/__init__.py", out)
        self.assertIn("### pkg/a.py · 1 commit · ~3 tokens", out)
        self.assertIn("2/2 files", out)                 # still counted
        self.assertIn("├── __init__.py", out)           # still in the tree

    def test_fence_longer_than_longest_backtick_run(self):
        self.assertEqual(gitskim._fence("plain"), "```")
        self.assertEqual(gitskim._fence("a ``` b"), "````")
        self.assertEqual(gitskim._fence("a ``` b ```` c"), "`````")

    def test_commit_plural(self):
        entries = [gitskim.FileEntry("a.py", 1, commits=2, content="x = 1", tokens=1)]
        self.assertIn("### a.py · 2 commits", gitskim.render("d", "main", "abc", entries, gitskim.Options()))

    def test_tree_note_megabytes(self):
        big = gitskim.FileEntry("x.bin", 3 * 1024 * 1024 + 200 * 1024, status="too_large")
        self.assertEqual(gitskim._tree_note(big), "  (skipped, 3.2 MB)")
        self.assertEqual(gitskim._tree_note(gitskim.FileEntry("y.bin", 300 * 1024, status="too_large")), "  (skipped, 300 KB)")


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
        }, extra_commits={"src/app.py": "import os\n\ndef main() -> int:\n    return 1\n"}, case=self)

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
        out_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        out_path = out_dir / "out.md"
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(self.repo), "-o", str(out_path)],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(out_path.exists())
        self.assertIn(str(out_path), res.stderr)
        self.assertEqual([p.name for p in out_dir.iterdir()], ["out.md"])

    def test_default_output_name_in_cwd(self):
        cwd = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(self.repo)],
            capture_output=True, text=True, cwd=cwd,
            env={k: v for k, v in os.environ.items() if k != "GITSKIM_OUTPUT_DIR"},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        files = [p.name for p in cwd.iterdir()]
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0], rf"^{re.escape(self.repo.name)}_\d{{4}}-\d{{2}}-\d{{2}}_\d{{4}}\.md$")
        self.assertIn(files[0], res.stderr)

    def test_default_output_name_format(self):
        now = gitskim._dt.datetime(2026, 10, 12, 14, 32, 59)
        self.assertEqual(gitskim.default_output_name("gitskim", now), "gitskim_2026-10-12_1432.md")

    def test_output_dir(self):
        out_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        target = out_dir / "nested"
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(self.repo), "--output-dir", str(target)],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        files = [p.name for p in target.iterdir()]
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0], rf"^{re.escape(self.repo.name)}_\d{{4}}-\d{{2}}-\d{{2}}_\d{{4}}\.md$")

    def test_output_dir_from_env_with_filename(self):
        out_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        env = {**os.environ, "GITSKIM_OUTPUT_DIR": str(out_dir)}
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(self.repo), "-o", "ctx.md"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((out_dir / "ctx.md").exists())

    def test_negative_max_size_rejected(self):
        res = self.run_cli("--max-size", "-1")
        self.assertEqual(res.returncode, 2)
        self.assertIn("max-size", res.stderr)

    def test_not_a_repo_exits_1(self):
        plain = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), plain],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn("not a git repository", res.stderr)


class TestMultiRepo(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="gitskim-ws-"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        for rel, extra in (("api", {"app.py": "def g() -> int:\n    return 2\n"}), ("team/web", None), ("legacy", None)):
            repo = make_repo({"app.py": "def g() -> int:\n    return 1\n", "README.md": f"# {rel}\n"}, extra, case=self)
            dest = self.ws / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(repo), str(dest))
        (self.ws / "notes").mkdir()                             # plain dir, not a repo
        (self.ws / "node_modules" / "pkg").mkdir(parents=True)
        git(self.ws / "node_modules" / "pkg", "init", "-q")      # ignored dir: not scanned

    def run_cli(self, *args, source=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(source or self.ws), "--stdout", *args],
            capture_output=True, text=True,
        )

    def test_find_repos(self):
        self.assertEqual(gitskim.find_repos(self.ws), ["api", "legacy", "team/web"])
        self.assertEqual(gitskim.find_repos(self.ws, max_depth=1), ["api", "legacy"])

    def test_combined_output(self):
        res = self.run_cli("--log", "5")
        self.assertEqual(res.returncode, 0, res.stderr)
        out = res.stdout
        self.assertTrue(out.startswith(f"# {self.ws.name}\n3 repositories · "))
        self.assertIn("## Repositories", out)
        self.assertIn("| team/web | main |", out)
        self.assertIn("### team/web/app.py", out)
        self.assertIn("### git log · api", out)
        # ranking spans repos: api/app.py has 2 commits
        self.assertLess(out.index("### api/app.py · 2 commits"), out.index("### legacy/app.py"))
        self.assertNotIn("node_modules", out)

    def test_progress_messages_and_quiet(self):
        res = self.run_cli()
        self.assertIn("[1/3] api: 2 files", res.stderr)
        self.assertIn("done (", res.stderr)
        res = self.run_cli("-q")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stderr, "")

    def test_exclude_drops_whole_repo(self):
        res = self.run_cli("--exclude", "legacy/*", "--log", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("2 repositories", res.stdout)
        self.assertNotIn("legacy", res.stdout)

    def test_multi_flag_on_repo(self):
        res = self.run_cli("--multi", source=self.ws / "api")
        self.assertEqual(res.returncode, 1)
        self.assertIn("no git repositories found", res.stderr)


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="gitskim-ws-"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        for rel in ("api", "team/web", "other/web"):
            repo = make_repo({"app.py": "def g() -> int:\n    return 1\n", "data.csv": "a,b\n",
                              "generated/x.py": "X = 1\n"}, case=self)
            dest = self.ws / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(repo), str(dest))
        self.out = self.ws.parent / (self.ws.name + "-out")
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        self.cfg = self.ws.parent / (self.ws.name + ".ini")
        self.addCleanup(lambda: self.cfg.unlink(missing_ok=True))
        self.cfg.write_text(
            f"[gitskim]\nworkspace = {self.ws}\noutput_dir = {self.out}\n"
            "exclude =\n    *.csv\nignore_dirs = generated\nmax_size = 50\n", encoding="utf-8")

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), "--config", str(self.cfg), *args],
            capture_output=True, text=True, cwd=self.ws,
        )

    def test_load_config(self):
        cfg = gitskim.load_config(str(self.cfg))
        self.assertEqual(cfg.workspace, self.ws)
        self.assertEqual(cfg.exclude, ["*.csv"])
        self.assertEqual(cfg.ignore_dirs, ["generated"])
        self.assertEqual(cfg.max_size, 50)

    def test_missing_default_config_is_fine_missing_explicit_is_not(self):
        self.assertIsNone(gitskim.load_config(None).workspace)
        with self.assertRaises(gitskim.GitskimError):
            gitskim.load_config(str(self.ws / "nope.ini"))

    def test_invalid_number(self):
        self.cfg.write_text("[gitskim]\nmax_size = big\n", encoding="utf-8")
        with self.assertRaises(gitskim.GitskimError):
            gitskim.load_config(str(self.cfg))

    def test_resolve_project(self):
        projects = ["api", "other/web", "team/web"]
        self.assertEqual(gitskim.resolve_project("api", projects), "api")
        self.assertEqual(gitskim.resolve_project("team/web", projects), "team/web")
        self.assertIsNone(gitskim.resolve_project("nope", projects))
        with self.assertRaises(gitskim.GitskimError):
            gitskim.resolve_project("web", projects)

    def test_project_by_name_writes_to_output_dir(self):
        res = self.run_cli("api")
        self.assertEqual(res.returncode, 0, res.stderr)
        files = list(self.out.iterdir())
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.startswith("api_"))
        md = files[0].read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# api\n"))
        self.assertIn("### app.py", md)
        self.assertNotIn("data.csv", md)            # config exclude
        self.assertNotIn("generated", md)           # config ignore_dirs

    def test_cli_exclude_adds_to_config(self):
        res = self.run_cli("api", "--stdout", "--exclude", "app.py")
        self.assertNotIn("app.py", res.stdout)
        self.assertNotIn("data.csv", res.stdout)

    def test_several_projects_and_whole_workspace(self):
        res = self.run_cli("api", "team/web", "--stdout")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("2 repositories", res.stdout)
        self.assertIn("### team/web/app.py", res.stdout)
        res = self.run_cli("--stdout")
        self.assertIn("3 repositories", res.stdout)

    def test_list_and_unknown_project(self):
        res = self.run_cli("--list")
        self.assertEqual(res.stdout.split(), ["api", "other/web", "team/web"])
        res = self.run_cli("nope")
        self.assertEqual(res.returncode, 1)
        self.assertIn("unknown project 'nope'", res.stderr)
        self.assertIn("available: api", res.stderr)

    def test_init_config(self):
        target = self.ws / "cfg" / "config.ini"
        res = subprocess.run([sys.executable, str(ROOT / "gitskim.py"), "--init-config", "--config", str(target)],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIsNone(gitskim.load_config(str(target)).max_size)    # template parses
        res = subprocess.run([sys.executable, str(ROOT / "gitskim.py"), "--init-config", "--config", str(target)],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)


class TestDiffAndLog(unittest.TestCase):
    def test_log_and_diff_sections(self):
        repo = make_repo({"a.py": "x = 1\n"}, extra_commits={"a.py": "x = 2\n"}, case=self)
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
        repo = make_repo({"a.py": "x = 1\n"}, case=self)
        res = subprocess.run(
            [sys.executable, str(ROOT / "gitskim.py"), str(repo), "--stdout"],
            capture_output=True, text=True,
        )
        self.assertNotIn("## Recent changes", res.stdout)


if __name__ == "__main__":
    unittest.main()
