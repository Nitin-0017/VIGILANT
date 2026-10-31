import pytest

from vigilant.git_diff import (
    FileChange,
    GitError,
    changed_python_files,
    diff_line_ranges,
    excluded_python_paths,
    markdown_files,
    read_file,
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


def test_diff_line_ranges_separate_inserted_deleted_and_modified_lines(git_repo):
    old = "a\nb\nc\nd\ne\nf\n"
    new = "a\nNEW\nb\nd\nE\nf\n"
    base = git_repo.commit({"x.py": old})
    head = git_repo.commit({"x.py": new})

    old_ranges, new_ranges = diff_line_ranges(git_repo.path, base, head, "x.py")

    assert old_ranges == [(3, 3), (5, 5)]
    assert new_ranges == [(2, 2), (5, 5)]


def test_diff_line_ranges_for_added_and_deleted_files(git_repo):
    base = git_repo.commit({"gone.py": "a\nb\n"})
    head = git_repo.commit({"new.py": "a\nb\nc\n"}, delete=["gone.py"])

    assert diff_line_ranges(git_repo.path, base, head, "new.py") == ([], [(1, 3)])
    assert diff_line_ranges(git_repo.path, base, head, "gone.py") == ([(1, 2)], [])


def test_diff_line_ranges_ignore_file_content_that_looks_like_a_hunk_header(git_repo):
    base = git_repo.commit({"x.py": "a\n"})
    head = git_repo.commit({"x.py": "a\n@@ -90,9 +90,9 @@\n"})

    assert diff_line_ranges(git_repo.path, base, head, "x.py") == ([], [(2, 2)])


def test_diff_line_ranges_treat_special_characters_in_paths_literally(git_repo):
    base = git_repo.commit({"a[1]*.py": "a\n", "a1b.py": "a\n"})
    head = git_repo.commit({"a[1]*.py": "b\n", "a1b.py": "b\nc\n"})

    assert diff_line_ranges(git_repo.path, base, head, "a[1]*.py") == (
        [(1, 1)],
        [(1, 1)],
    )


def test_code_paths_select_directories_and_files(git_repo):
    base = git_repo.commit({"src/a.py": "1\n", "src/b.py": "1\n", "lib/c.py": "1\n"})
    head = git_repo.commit({"src/a.py": "2\n", "src/b.py": "2\n", "lib/c.py": "2\n"})

    def selected(*paths):
        files = changed_python_files(git_repo.path, base, head, paths)
        return [file.path for file in files]

    assert selected("src") == ["src/a.py", "src/b.py"]
    assert selected("src/") == ["src/a.py", "src/b.py"]
    assert selected("src/a.py", "lib") == ["lib/c.py", "src/a.py"]
    assert selected(".") == ["lib/c.py", "src/a.py", "src/b.py"]
    assert selected("missing") == []
    assert excluded_python_paths(git_repo.path, base, head, ("src",)) == ["lib/c.py"]
    assert excluded_python_paths(git_repo.path, base, head, ()) == []


@pytest.mark.parametrize("path", ["../other", "/etc", "src/../../x", ""])
def test_code_paths_outside_the_repository_are_rejected(git_repo, path):
    commit = git_repo.commit()

    with pytest.raises(GitError, match="must be inside the repository"):
        changed_python_files(git_repo.path, commit, commit, (path,))


def test_read_file_reads_the_requested_commit_and_reports_missing_files(git_repo):
    old = git_repo.commit({"a.md": "old\n"})
    new = git_repo.commit({"a.md": "new\n"})

    assert read_file(git_repo.path, old, "a.md") == b"old\n"
    assert read_file(git_repo.path, new, "a.md") == b"new\n"
    with pytest.raises(GitError):
        read_file(git_repo.path, new, "absent.md")
