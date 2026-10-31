import subprocess

import pytest


class GitRepo:
    def __init__(self, path):
        self.path = path
        self._git("init", "--quiet")

    def commit(self, files=None, delete=()):
        """Write files, delete paths and return the new commit's hash."""
        for name, content in (files or {}).items():
            file_path = self.path / name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)
        for name in delete:
            (self.path / name).unlink()
        self._git("add", "--all")
        self._git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "--allow-empty",
            "--no-gpg-sign",
            "-m",
            "test commit",
        )
        return self._git("rev-parse", "HEAD").strip()

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.path, check=True, capture_output=True, text=True
        ).stdout


@pytest.fixture
def git_repo(tmp_path):
    return GitRepo(tmp_path)
