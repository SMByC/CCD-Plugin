import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "Git and Bash are required")
class ReleaseTaskTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.remote = self.root / "origin.git"
        self.env = {
            **os.environ,
            "HOME": str(self.root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.git("init", "-q")
        self.git("config", "user.name", "Release task test")
        self.git("config", "user.email", "release-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.metadata = self.repo / "metadata.txt"
        self.metadata.write_text("[general]\nversion=26.10\n", encoding="utf-8")
        self.code = self.repo / "plugin.py"
        self.code.write_text("# Committed code\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "Version 26.10")
        self.git("init", "-q", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        tasks = json.loads((PROJECT_ROOT / ".zed" / "tasks.json").read_text(encoding="utf-8"))
        self.command = next(task["command"] for task in tasks if task["label"] == "Release a new version in GitHub")

    def git(self, *arguments):
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout

    def run_task(self, version):
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", self.command],
            input=f"{version}\n",
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def assert_no_tags(self):
        self.assertEqual(self.git("tag", "--list"), "")
        self.assertEqual(self.git(f"--git-dir={self.remote}", "tag", "--list"), "")

    def test_dirty_tracked_changes_cannot_create_or_push_a_tag(self):
        self.code.write_text("# Uncommitted fix\n", encoding="utf-8")
        for staged in (False, True):
            with self.subTest(staged=staged):
                if staged:
                    self.git("add", "plugin.py")
                result = self.run_task("26.10")

                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("Commit or stash", result.stderr)
                self.assert_no_tags()

    def test_clean_matching_commit_is_tagged_and_pushed_with_untracked_files_present(self):
        (self.repo / "local-notes.txt").write_text("Not part of the release\n", encoding="utf-8")

        result = self.run_task("26.10")

        self.assertEqual(result.returncode, 0, result.stderr)
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("rev-parse", "refs/tags/26.10"), head)
        self.assertEqual(self.git(f"--git-dir={self.remote}", "rev-parse", "refs/tags/26.10"), head)
        self.assertIn("version=26.10", self.git(f"--git-dir={self.remote}", "show", "26.10:metadata.txt"))

    def test_requested_version_must_match_the_committed_metadata(self):
        result = self.run_task("26.11")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match", result.stderr)
        self.assert_no_tags()


if __name__ == "__main__":
    unittest.main()
