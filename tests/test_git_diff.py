import pytest

from vigilant.git_diff import (
    FileChange,
    GitError,
    changed_python_files,
    markdown_files,
)


def test_added_file_has_only_new_source(git_repo):
    base = git_repo.commit()
    target = git_repo.commit({"app.py": "x = 1\n"})

    assert changed_python_files(git_repo.path, base, target) == [
        FileChange("app.py", "added", None, b"x = 1\n")
    ]


def test_modified_file_has_both_sources(git_repo):
    base = git_repo.commit({"pkg/app.py": "x = 1\n"})
    target = git_repo.commit({"pkg/app.py": "x = 2\n"})

    assert changed_python_files(git_repo.path, base, target) == [
        FileChange("pkg/app.py", "modified", b"x = 1\n", b"x = 2\n")
    ]


def test_deleted_file_has_only_old_source(git_repo):
    base = git_repo.commit({"app.py": "x = 1\n"})
    target = git_repo.commit(delete=["app.py"])

    assert changed_python_files(git_repo.path, base, target) == [
        FileChange("app.py", "deleted", b"x = 1\n", None)
    ]


def test_renamed_file_is_reported_as_deleted_and_added(git_repo):
    base = git_repo.commit({"old.py": "x = 1\n"})
    target = git_repo.commit({"new.py": "x = 1\n"}, delete=["old.py"])

    changes = changed_python_files(git_repo.path, base, target)

    assert [(change.path, change.status) for change in changes] == [
        ("new.py", "added"),
        ("old.py", "deleted"),
    ]


def test_non_python_files_are_ignored(git_repo):
    base = git_repo.commit({"README.md": "old\n", "data.pyc.txt": "old\n"})
    target = git_repo.commit({"README.md": "new\n", "data.pyc.txt": "new\n"})

    assert changed_python_files(git_repo.path, base, target) == []


def test_identical_revisions_have_no_changes(git_repo):
    commit = git_repo.commit({"app.py": "x = 1\n"})

    assert changed_python_files(git_repo.path, commit, commit) == []


def test_files_are_sorted_and_found_from_a_subdirectory(git_repo):
    base = git_repo.commit({"sub/keep.txt": "x\n"})
    target = git_repo.commit(
        {"z.py": "z = 1\n", "a.py": "a = 1\n", "sub/m.py": "m = 1\n"}
    )

    changes = changed_python_files(git_repo.path / "sub", base, target)

    assert [change.path for change in changes] == ["a.py", "sub/m.py", "z.py"]


def test_path_with_spaces_and_unicode(git_repo):
    base = git_repo.commit()
    target = git_repo.commit({"my dir/módulo.py": "x = 1\n"})

    changes = changed_python_files(git_repo.path, base, target)

    assert [change.path for change in changes] == ["my dir/módulo.py"]
    assert changes[0].new_source == b"x = 1\n"


def test_invalid_revision_raises_clear_error(git_repo):
    commit = git_repo.commit()

    with pytest.raises(GitError, match="unknown revision: no-such-branch"):
        changed_python_files(git_repo.path, commit, "no-such-branch")


def test_revision_is_never_interpreted_as_a_git_option(git_repo):
    commit = git_repo.commit()
    output_file = git_repo.path / "injected"

    for revision in (f"--output={output_file}", "--all", "--default=HEAD"):
        with pytest.raises(GitError, match="unknown revision"):
            changed_python_files(git_repo.path, revision, commit)

    assert not output_file.exists()


def test_directory_outside_a_repository_raises_git_error(tmp_path):
    with pytest.raises(GitError, match="not a git repository"):
        changed_python_files(tmp_path, "HEAD~1", "HEAD")


def test_markdown_files_are_read_from_the_requested_revision(git_repo):
    old = git_repo.commit(
        {"README.md": "old\n", "docs/guide.markdown": "guide\n", "app.py": "x = 1\n"}
    )
    git_repo.commit({"README.md": "new\n", "docs/extra.MD": "extra\n"})

    assert markdown_files(git_repo.path, old) == [
        ("README.md", b"old\n"),
        ("docs/guide.markdown", b"guide\n"),
    ]
    assert markdown_files(git_repo.path, "HEAD") == [
        ("README.md", b"new\n"),
        ("docs/extra.MD", b"extra\n"),
        ("docs/guide.markdown", b"guide\n"),
    ]


def test_markdown_files_ignore_uncommitted_changes_and_symlinks(git_repo):
    (git_repo.path / "link.md").symlink_to("target.txt")
    git_repo.commit({"README.md": "committed\n", "target.txt": "x\n"})
    (git_repo.path / "README.md").write_text("uncommitted\n")

    assert markdown_files(git_repo.path, "HEAD") == [("README.md", b"committed\n")]


def test_markdown_files_invalid_revision_raises_clear_error(git_repo):
    git_repo.commit()

    with pytest.raises(GitError, match="unknown revision: missing"):
        markdown_files(git_repo.path, "missing")
