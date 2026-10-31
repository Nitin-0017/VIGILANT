import pytest

from vigilant.ast_diff import (
    ChangeType,
    FunctionChange,
    UnparsableSource,
    changed_functions,
    function_changes,
)
from vigilant.git_diff import FileChange


def compare(old, new):
    old_source = None if old is None else old.encode()
    new_source = None if new is None else new.encode()
    return function_changes(FileChange("app.py", "modified", old_source, new_source))


def summary(old, new):
    return [(c.function, c.change_type, c.detail) for c in compare(old, new)]


def test_changed_default_reports_both_signatures_and_locations():
    old = 'def create_user(name, role="user"):\n    pass\n'
    new = '\n\ndef create_user(name, role="viewer"):\n    pass\n'

    assert compare(old, new) == [
        FunctionChange(
            path="app.py",
            function="create_user",
            change_type=ChangeType.DEFAULT_CHANGED,
            detail="role: 'user' -> 'viewer'",
            old_signature="def create_user(name, role='user')",
            new_signature="def create_user(name, role='viewer')",
            old_line=1,
            new_line=3,
        )
    ]


def test_added_function():
    changes = compare("def a(): pass\n", "def a(): pass\ndef b(x): pass\n")

    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.FUNCTION_ADDED
    assert changes[0].function == "b"
    assert changes[0].old_signature is None
    assert changes[0].new_signature == "def b(x)"
    assert changes[0].new_line == 2


def test_removed_function():
    changes = compare("def a(): pass\ndef b(x): pass\n", "def a(): pass\n")

    assert len(changes) == 1
    assert changes[0].change_type == ChangeType.FUNCTION_REMOVED
    assert changes[0].function == "b"
    assert changes[0].new_signature is None
    assert changes[0].old_line == 2


def test_new_file_reports_every_function_as_added():
    assert summary(None, "def a(): pass\ndef b(): pass\n") == [
        ("a", ChangeType.FUNCTION_ADDED, "a"),
        ("b", ChangeType.FUNCTION_ADDED, "b"),
    ]


def test_deleted_file_reports_every_function_as_removed():
    assert summary("def a(): pass\n", None) == [("a", ChangeType.FUNCTION_REMOVED, "a")]


def test_added_parameter():
    assert summary("def f(a): pass\n", "def f(a, b=1): pass\n") == [
        ("f", ChangeType.PARAMETER_ADDED, "b")
    ]


def test_removed_parameter():
    assert summary("def f(a, b): pass\n", "def f(a): pass\n") == [
        ("f", ChangeType.PARAMETER_REMOVED, "b")
    ]


def test_parameter_added_at_the_start_is_not_a_rename():
    assert summary("def f(a): pass\n", "def f(b, a): pass\n") == [
        ("f", ChangeType.PARAMETER_ADDED, "b")
    ]


def test_renamed_parameter():
    assert summary("def f(a, b, c): pass\n", "def f(a, x, c): pass\n") == [
        ("f", ChangeType.PARAMETER_RENAMED, "b -> x")
    ]


def test_renamed_parameter_with_changed_default_reports_both():
    assert summary("def f(a=1): pass\n", "def f(b=2): pass\n") == [
        ("f", ChangeType.PARAMETER_RENAMED, "a -> b"),
        ("f", ChangeType.DEFAULT_CHANGED, "b: 1 -> 2"),
    ]


def test_replacement_with_a_different_kind_is_not_a_rename():
    assert summary("def f(a): pass\n", "def f(*, b): pass\n") == [
        ("f", ChangeType.PARAMETER_REMOVED, "a"),
        ("f", ChangeType.PARAMETER_ADDED, "b"),
    ]


def test_default_added_and_removed():
    assert summary("def f(a, b=1): pass\n", "def f(a=2, b=None): pass\n") == [
        ("f", ChangeType.DEFAULT_CHANGED, "a: (none) -> 2"),
        ("f", ChangeType.DEFAULT_CHANGED, "b: 1 -> None"),
    ]
    assert summary("def f(a=1): pass\n", "def f(a): pass\n") == [
        ("f", ChangeType.DEFAULT_CHANGED, "a: 1 -> (none)")
    ]


def test_keyword_only_default_changed():
    assert summary("def f(*, a=1, b): pass\n", "def f(*, a=2, b): pass\n") == [
        ("f", ChangeType.DEFAULT_CHANGED, "a: 1 -> 2")
    ]


def test_return_annotation_changed():
    assert summary("def f() -> int: pass\n", "def f() -> str: pass\n") == [
        ("f", ChangeType.RETURN_ANNOTATION_CHANGED, "int -> str")
    ]
    assert summary("def f(): pass\n", "def f() -> str: pass\n") == [
        ("f", ChangeType.RETURN_ANNOTATION_CHANGED, "(none) -> str")
    ]


def test_async_changed():
    assert summary("def f(): pass\n", "async def f(): pass\n") == [
        ("f", ChangeType.ASYNC_CHANGED, "def -> async def")
    ]
    assert compare("def f(): pass\n", "async def f(): pass\n")[0].new_signature == (
        "async def f()"
    )


def test_parameter_annotation_kind_and_order_are_signature_changes():
    assert summary("def f(a: int): pass\n", "def f(a: str): pass\n") == [
        ("f", ChangeType.SIGNATURE_CHANGED, "a annotation: int -> str")
    ]
    assert summary("def f(a): pass\n", "def f(*, a): pass\n") == [
        (
            "f",
            ChangeType.SIGNATURE_CHANGED,
            "a kind: positional-or-keyword -> keyword-only",
        )
    ]
    assert summary("def f(a, b): pass\n", "def f(b, a): pass\n") == [
        ("f", ChangeType.SIGNATURE_CHANGED, "parameter order changed")
    ]


def test_variadic_parameters_are_tracked():
    assert summary("def f(*args): pass\n", "def f(*args, **kwargs): pass\n") == [
        ("f", ChangeType.PARAMETER_ADDED, "kwargs")
    ]


def test_class_methods_use_qualified_names():
    old = "class Service:\n    def run(self, a): pass\n"
    new = "class Service:\n    def run(self, a, b): pass\n"

    assert summary(old, new) == [("Service.run", ChangeType.PARAMETER_ADDED, "b")]


def test_nested_functions_use_qualified_names():
    old = "def outer():\n    def inner(a): pass\n"
    new = "def outer():\n    def inner(a=1): pass\n"

    assert summary(old, new) == [
        ("outer.<locals>.inner", ChangeType.DEFAULT_CHANGED, "a: (none) -> 1")
    ]


def test_function_inside_conditional_is_detected():
    old = "if True:\n    def f(a): pass\n"
    new = "if True:\n    def f(b): pass\n"

    assert summary(old, new) == [("f", ChangeType.PARAMETER_RENAMED, "a -> b")]


def test_body_changes_and_moves_are_not_reported():
    old = "def f(a):\n    return a\n\ndef g(): pass\n"
    new = "def g(): pass\n\n# comment\ndef f(a):\n    log(a)\n    return a + 1\n"

    assert compare(old, new) == []


def test_formatting_changes_are_not_reported():
    old = 'def f(a,b = "x"): pass\n'
    new = "def f(\n    a,\n    b='x',\n): pass\n"

    assert compare(old, new) == []


def test_each_property_definition_is_compared_separately():
    old = (
        "class C:\n"
        "    @property\n    def x(self): pass\n"
        "    @x.setter\n    def x(self, value): pass\n"
    )
    new = (
        "class C:\n"
        "    @property\n    def x(self): pass\n"
        "    @x.setter\n    def x(self, new_value): pass\n"
    )

    assert summary(old, new) == [
        ("C.x", ChangeType.PARAMETER_RENAMED, "value -> new_value")
    ]


def test_new_definition_does_not_misalign_existing_ones_with_the_same_name():
    old = "def f(a): pass\ndef f(a, b): pass\n"
    new = "def f(c): pass\ndef f(a): pass\ndef f(a, b): pass\n"

    changes = compare(old, new)

    assert [(c.change_type, c.new_signature, c.new_line) for c in changes] == [
        (ChangeType.FUNCTION_ADDED, "def f(c)", 1)
    ]


def test_invalid_syntax_raises_and_names_the_revision():
    with pytest.raises(UnparsableSource, match="target revision"):
        compare("def f(): pass\n", "def f(:\n")
    with pytest.raises(UnparsableSource, match="base revision"):
        compare("def f(:\n", "def f(): pass\n")


def test_non_utf8_source_raises_unparsable():
    file = FileChange("app.py", "added", None, b"x = '\xff'\n")

    with pytest.raises(UnparsableSource):
        function_changes(file)


def test_declared_source_encoding_is_honoured():
    source = "# -*- coding: latin-1 -*-\ndef f(a='é'): pass\n".encode("latin-1")
    file = FileChange("app.py", "added", None, source)

    assert function_changes(file)[0].new_signature == "def f(a='é')"


def test_analysis_never_executes_the_source(tmp_path):
    marker = tmp_path / "executed"
    source = f"open({str(marker)!r}, 'w')\ndef f(a=open({str(marker)!r}, 'w')): pass\n"

    compare(None, source)

    assert not marker.exists()


def test_scanned_code_warnings_are_not_emitted(recwarn):
    compare(None, "def f(a='\\d'): pass\n")

    assert len(recwarn) == 0


def changed(old, new):
    old_source = None if old is None else old.encode()
    new_source = None if new is None else new.encode()
    return changed_functions(FileChange("app.py", "modified", old_source, new_source))


def test_body_change_is_reported_with_both_versions_of_the_code():
    result = changed("def f():\n    return 1\n", "\n\ndef f():\n    return 2\n")

    assert len(result) == 1
    assert result[0].function == "f"
    assert result[0].changes[0].change_type == ChangeType.BODY_CHANGED
    assert result[0].old_code == "def f():\n    return 1"
    assert result[0].new_code == "def f():\n    return 2"
    assert (result[0].old_line, result[0].new_line) == (1, 3)


def test_signature_change_keeps_its_structural_details():
    result = changed("def f(a=1):\n    pass\n", "def f(a=2):\n    pass\n")

    assert [c.change_type for c in result[0].changes] == [ChangeType.DEFAULT_CHANGED]


def test_added_and_removed_functions_have_one_side_of_code():
    added = changed("", "def f():\n    pass\n")[0]
    removed = changed("def f():\n    pass\n", "")[0]

    assert added.old_code is None and added.new_code == "def f():\n    pass"
    assert removed.new_code is None and removed.old_code == "def f():\n    pass"


def test_formatting_comments_and_moves_are_not_reported():
    old = "def f(a,b):\n    return a+b\n\ndef g(): pass\n"
    new = "def g(): pass\n\n# Add.\ndef f(a, b):\n    return a + b\n"

    assert changed(old, new) == []


def test_docstring_change_is_reported():
    result = changed('def f():\n    "old"\n', 'def f():\n    "new"\n')

    assert result[0].changes[0].change_type == ChangeType.BODY_CHANGED


def test_nested_change_reports_only_the_inner_function():
    old = "def outer():\n    def inner():\n        return 1\n    return inner\n"
    new = "def outer():\n    def inner():\n        return 2\n    return inner\n"

    result = changed(old, new)

    assert [r.function for r in result] == ["outer.<locals>.inner"]
    assert result[0].new_code == "def inner():\n    return 2"


def test_method_source_is_dedented_and_includes_decorators():
    old = "class C:\n    @staticmethod\n    def m():\n        return 1\n"
    new = "class C:\n    @staticmethod\n    def m():\n        return 2\n"

    result = changed(old, new)

    assert result[0].function == "C.m"
    assert result[0].new_code == "@staticmethod\ndef m():\n    return 2"


def test_same_named_definitions_pair_after_identical_ones_cancel():
    old = "def f(a):\n    return 1\ndef f(a, b):\n    return 2\n"
    new = (
        "def f(c):\n    return 3\ndef f(a):\n    return 1\ndef f(a, b):\n    return 2\n"
    )

    result = changed(old, new)

    assert len(result) == 1
    assert result[0].old_code is None
    assert result[0].new_code == "def f(c):\n    return 3"


def test_declared_encoding_is_used_for_source_text():
    source = "# -*- coding: latin-1 -*-\ndef f():\n    return 'é'\n".encode("latin-1")

    result = changed_functions(FileChange("a.py", "added", None, source))

    assert "é" in result[0].new_code


def test_function_changes_still_ignore_body_only_changes():
    assert compare("def f():\n    return 1\n", "def f():\n    return 2\n") == []


def test_changed_function_records_its_full_line_range_in_both_versions():
    old = "x = 1\n\n\n@decorator\ndef f():\n    return 1\n"
    new = "x = 1\ny = 2\n\n\n@decorator\ndef f():\n    a = 2\n    return a\n"

    result = changed(old, new)[0]

    assert (result.old_first_line, result.old_line, result.old_end_line) == (4, 5, 6)
    assert (result.new_first_line, result.new_line, result.new_end_line) == (5, 6, 8)
