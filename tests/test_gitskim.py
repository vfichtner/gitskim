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


if __name__ == "__main__":
    unittest.main()
