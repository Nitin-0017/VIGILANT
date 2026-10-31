from vigilant.ast_diff import ChangeType, FunctionChange, function_changes
from vigilant.doc_matching import match_documentation
from vigilant.git_diff import changed_python_files
from vigilant.markdown_docs import extract_sections


def change(function, path="src/app/users.py"):
    return FunctionChange(
        path=path,
        function=function,
        change_type=ChangeType.DEFAULT_CHANGED,
        detail="role: 'user' -> 'viewer'",
        old_signature="def old()",
        new_signature="def new()",
        old_line=1,
        new_line=1,
    )


def match(function, markdown, path="src/app/users.py"):
    sections = extract_sections("docs/guide.md", markdown.encode())
    return match_documentation([change(function, path)], sections)


def reasons(function, markdown, path="src/app/users.py"):
    return [m.reason for m in match(function, markdown, path)]


def test_match_reports_location_reason_and_evidence():
    results = match(
        "create_user", "# API\n\n## Users\n\nIntro.\n\nCall `create_user` first.\n"
    )

    assert len(results) == 1
    result = results[0]
    assert result.source_path == "src/app/users.py"
    assert result.function == "create_user"
    assert result.doc_path == "docs/guide.md"
    assert result.heading == "Users"
    assert result.hierarchy == ("API", "Users")
    assert (result.section_start_line, result.section_end_line) == (3, 7)
    assert result.line == 7
    assert result.identifier == "create_user"
    assert result.reason == "exact name in inline code"
    assert result.evidence == "Call `create_user` first."


def test_distinctive_name_matches_in_every_context():
    assert reasons("create_user", "# create_user\n") == ["exact name in heading"]
    assert reasons("create_user", "Call create_user first.\n") == [
        "exact name in prose"
    ]
    assert reasons("create_user", "```\nx = create_user\n```\n") == [
        "exact name in code block"
    ]
    assert reasons("createUser", "Call createUser first.\n") == ["exact name in prose"]


def test_common_word_in_plain_text_is_not_a_match():
    markdown = "# How to run\nFirst run the tests.\n```bash\ndocker run image\n```\n"

    assert match("run", markdown) == []


def test_common_word_matches_when_written_as_code_or_a_call():
    assert reasons("run", "Use `run` here.\n") == ["exact name in inline code"]
    assert reasons("run", "Use `run()` here.\n") == ["exact name in inline code"]
    assert reasons("run", "Use run() here.\n") == ["exact name in prose"]
    assert reasons("run", "```\nresult = run(1)\n```\n") == ["exact name in code block"]


def test_common_word_used_as_an_argument_in_inline_code_is_not_a_match():
    assert match("name", "Call `create_user(name)` now.\n") == []


def test_matching_is_case_sensitive_and_whole_word():
    assert (
        match("create_user", "CREATE_USER and create_users and my_create_user\n") == []
    )


def test_qualified_function_name_matches_module_path():
    assert reasons("create_user", "Use `users.create_user`.\n") == [
        "qualified name in inline code"
    ]
    assert reasons("create_user", "Use `app.users.create_user`.\n") == [
        "qualified name in inline code"
    ]


def test_same_name_from_an_unrelated_module_is_not_a_match():
    assert match("get", "Call `requests.get()` to fetch.\n") == []
    assert match("create_user", "Use `other.create_user`.\n") == []


def test_package_init_function_matches_the_package_name():
    assert reasons(
        "create_user", "Use `users.create_user`.\n", path="src/users/__init__.py"
    ) == ["qualified name in inline code"]


def test_qualified_method_matches_common_name():
    assert reasons("Service.run", "Call Service.run to start.\n") == [
        "qualified name in prose"
    ]
    assert reasons("Service.run", "Call `users.Service.run`.\n") == [
        "qualified name in inline code"
    ]


def test_bare_method_name_needs_its_class_in_the_same_section():
    with_class = "# The `Service` class\nCall `run` to start.\n"
    without_class = "# Service\nIntro.\n# Other\nCall `run` to start.\n"

    assert reasons("Service.run", with_class) == [
        "name in inline code, with parent Service in the same section"
    ]
    assert match("Service.run", without_class) == []


def test_method_called_through_an_instance_matches_with_its_class():
    markdown = "```python\nclient = Service()\nclient.run(timeout=5)\n```\n"

    results = match("Service.run", markdown)

    assert [(m.identifier, m.line) for m in results] == [("client.run", 3)]


def test_nested_function_uses_parent_without_locals_marker():
    assert reasons("outer.<locals>.inner", "Use `outer.inner`.\n") == [
        "qualified name in inline code"
    ]
    assert match("outer.<locals>.inner", "Use `inner` here.\n") == []


def test_unrelated_documentation_has_no_matches():
    assert match("create_user", "# Billing\nCharge `invoice` monthly.\n") == []


def test_function_without_documentation_and_empty_docs():
    assert match_documentation([change("create_user")], []) == []
    assert match_documentation([], extract_sections("a.md", b"`create_user`\n")) == []


def test_multiple_references_are_all_reported_with_correct_lines():
    results = match(
        "create_user",
        "# Users\n`create_user` one\n\nAgain create_user\n# Other\nSee create_user()\n",
    )

    assert [(m.heading, m.line) for m in results] == [
        ("Users", 2),
        ("Users", 4),
        ("Other", 6),
    ]


def test_repeated_reference_on_one_line_is_reported_once():
    assert len(match("create_user", "`create_user` and create_user\n")) == 1


def test_multiple_documents_are_matched_in_order():
    sections = extract_sections("a.md", b"`create_user`\n")
    sections += extract_sections("b.md", b"nothing here\n")
    sections += extract_sections("c.md", b"create_user\n")

    results = match_documentation([change("create_user")], sections)

    assert [m.doc_path for m in results] == ["a.md", "c.md"]


def test_several_changes_to_one_function_produce_one_set_of_matches():
    sections = extract_sections("a.md", b"`create_user`\n")

    results = match_documentation(
        [change("create_user"), change("create_user")], sections
    )

    assert len(results) == 1


def test_same_function_name_in_different_files_is_reported_for_each():
    sections = extract_sections("a.md", b"`create_user`\n")

    results = match_documentation(
        [change("create_user", "b.py"), change("create_user", "a.py")], sections
    )

    assert [m.source_path for m in results] == ["a.py", "b.py"]


def test_results_from_real_git_analysis_are_matched(git_repo):
    base = git_repo.commit({"users.py": 'def create_user(name, role="user"): pass\n'})
    target = git_repo.commit(
        {"users.py": 'def create_user(name, role="viewer"): pass\n'}
    )
    file = changed_python_files(git_repo.path, base, target)[0]
    sections = extract_sections("README.md", b"# Users\nCall `create_user`.\n")

    results = match_documentation(function_changes(file), sections)

    assert [(m.function, m.doc_path, m.line) for m in results] == [
        ("create_user", "README.md", 2)
    ]
