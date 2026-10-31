import ast
import copy
import io
import re
import textwrap
import tokenize
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from itertools import zip_longest

from vigilant.git_diff import FileChange


class UnparsableSource(Exception):
    pass


class ChangeType(StrEnum):
    FUNCTION_ADDED = "function_added"
    FUNCTION_REMOVED = "function_removed"
    PARAMETER_ADDED = "parameter_added"
    PARAMETER_REMOVED = "parameter_removed"
    PARAMETER_RENAMED = "parameter_renamed"
    DEFAULT_CHANGED = "default_changed"
    RETURN_ANNOTATION_CHANGED = "return_annotation_changed"
    ASYNC_CHANGED = "async_changed"
    SIGNATURE_CHANGED = "signature_changed"
    BODY_CHANGED = "body_changed"


@dataclass(frozen=True)
class FunctionChange:
    path: str
    function: str
    change_type: ChangeType
    detail: str
    old_signature: str | None
    new_signature: str | None
    old_line: int | None
    new_line: int | None


@dataclass(frozen=True)
class ChangedFunction:
    path: str
    function: str
    changes: tuple[FunctionChange, ...]
    old_code: str | None
    new_code: str | None
    old_line: int | None
    new_line: int | None
    old_first_line: int | None = None
    old_end_line: int | None = None
    new_first_line: int | None = None
    new_end_line: int | None = None


@dataclass(frozen=True)
class Parameter:
    name: str
    kind: str
    default: str | None
    annotation: str | None


@dataclass(frozen=True)
class Function:
    signature: str
    parameters: tuple[Parameter, ...]
    returns: str | None
    is_async: bool
    line: int = field(compare=False)
    source: str = field(compare=False)
    fingerprint: str = field(compare=False)
    first_line: int = field(compare=False)
    end_line: int = field(compare=False)


def function_changes(file: FileChange) -> list[FunctionChange]:
    """Compare the function signatures in both versions of a file, sorted by name."""
    old_functions = _parse_functions(file.old_source, "base")
    new_functions = _parse_functions(file.new_source, "target")

    changes = []
    for name in sorted(old_functions.keys() | new_functions.keys()):
        changes += _compare_definitions(
            file.path, name, old_functions.get(name, []), new_functions.get(name, [])
        )
    return changes


def changed_functions(file: FileChange) -> list[ChangedFunction]:
    """List functions whose signature or body differs, with both versions' code."""
    old_functions = _parse_functions(file.old_source, "base")
    new_functions = _parse_functions(file.new_source, "target")

    changed = []
    for name in sorted(old_functions.keys() | new_functions.keys()):
        pairs = _unmatched_pairs(
            old_functions.get(name, []),
            new_functions.get(name, []),
            lambda old, new: old == new and old.fingerprint == new.fingerprint,
        )
        for old, new in pairs:
            changes = _changes_between(file.path, name, old, new) or [
                FunctionChange(
                    file.path,
                    name,
                    ChangeType.BODY_CHANGED,
                    "function body changed",
                    old.signature,
                    new.signature,
                    old.line,
                    new.line,
                )
            ]
            changed.append(
                ChangedFunction(
                    file.path,
                    name,
                    tuple(changes),
                    old.source if old else None,
                    new.source if new else None,
                    old.line if old else None,
                    new.line if new else None,
                    old.first_line if old else None,
                    old.end_line if old else None,
                    new.first_line if new else None,
                    new.end_line if new else None,
                )
            )
    return changed


def _parse_functions(source: bytes | None, revision: str) -> dict[str, list[Function]]:
    if source is None:
        return {}
    functions: dict[str, list[Function]] = {}
    try:
        # Scanned code may use deprecated syntax; that is not VIGILANT's to report.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            lines = source_lines(source)
            _collect_functions(ast.parse(source), "", functions, lines)
    except (SyntaxError, ValueError, RecursionError) as error:
        raise UnparsableSource(
            f"invalid Python in {revision} revision: {error}"
        ) from error
    return functions


def source_lines(source: bytes) -> list[str]:
    encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
    return re.split(r"\r\n|\r|\n", source.decode(encoding, errors="replace"))


def _collect_functions(
    node: ast.AST,
    prefix: str,
    functions: dict[str, list[Function]],
    lines: list[str],
) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            name = f"{prefix}{child.name}"
            functions.setdefault(name, []).append(_function(child, lines))
            _collect_functions(child, f"{name}.<locals>.", functions, lines)
        elif isinstance(child, ast.ClassDef):
            _collect_functions(child, f"{prefix}{child.name}.", functions, lines)
        else:
            _collect_functions(child, prefix, functions, lines)


def _function(
    node: ast.FunctionDef | ast.AsyncFunctionDef, lines: list[str]
) -> Function:
    is_async = isinstance(node, ast.AsyncFunctionDef)
    returns = _source_text(node.returns)
    signature = (
        f"{'async ' if is_async else ''}def {node.name}({ast.unparse(node.args)})"
    )
    if returns is not None:
        signature += f" -> {returns}"
    first_line = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return Function(
        signature,
        _parameters(node.args),
        returns,
        is_async,
        node.lineno,
        textwrap.dedent("\n".join(lines[first_line - 1 : node.end_lineno])),
        _fingerprint(node),
        first_line,
        node.end_lineno,
    )


class _NestedFunctionRemover(ast.NodeTransformer):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None


def _fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Describe a function's own code, so a nested change only reports the inner one."""
    own_code = copy.deepcopy(node)
    _NestedFunctionRemover().generic_visit(own_code)
    return ast.dump(own_code)


def _parameters(args: ast.arguments) -> tuple[Parameter, ...]:
    positional = [(arg, "positional-only") for arg in args.posonlyargs]
    positional += [(arg, "positional-or-keyword") for arg in args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + args.defaults

    parameters = [
        _parameter(arg, kind, default)
        for (arg, kind), default in zip(positional, defaults)
    ]
    if args.vararg:
        parameters.append(_parameter(args.vararg, "var-positional"))
    parameters += [
        _parameter(arg, "keyword-only", default)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults)
    ]
    if args.kwarg:
        parameters.append(_parameter(args.kwarg, "var-keyword"))
    return tuple(parameters)


def _parameter(arg: ast.arg, kind: str, default: ast.expr | None = None) -> Parameter:
    return Parameter(arg.arg, kind, _source_text(default), _source_text(arg.annotation))


def _source_text(node: ast.expr | None) -> str | None:
    return None if node is None else ast.unparse(node)


def _compare_definitions(
    path: str,
    name: str,
    old_definitions: list[Function],
    new_definitions: list[Function],
) -> list[FunctionChange]:
    changes = []
    pairs = _unmatched_pairs(
        old_definitions, new_definitions, lambda old, new: old == new
    )
    for old, new in pairs:
        changes += _changes_between(path, name, old, new)
    return changes


def _unmatched_pairs(
    old_definitions: list[Function],
    new_definitions: list[Function],
    same: Callable[[Function, Function], bool],
) -> list[tuple[Function | None, Function | None]]:
    # A name can have several definitions, such as a property getter and setter.
    # Identical ones cancel out so that the rest pair up with their closest match.
    unmatched_new = list(new_definitions)
    unmatched_old = []
    for definition in old_definitions:
        match = next((new for new in unmatched_new if same(definition, new)), None)
        if match is None:
            unmatched_old.append(definition)
        else:
            unmatched_new.remove(match)
    return list(zip_longest(unmatched_old, unmatched_new))


def _changes_between(
    path: str, name: str, old: Function | None, new: Function | None
) -> list[FunctionChange]:
    if old is None:
        differences = [(ChangeType.FUNCTION_ADDED, name)]
    elif new is None:
        differences = [(ChangeType.FUNCTION_REMOVED, name)]
    else:
        differences = _signature_differences(old, new)
    return [
        FunctionChange(
            path,
            name,
            change_type,
            detail,
            old.signature if old else None,
            new.signature if new else None,
            old.line if old else None,
            new.line if new else None,
        )
        for change_type, detail in differences
    ]


def _signature_differences(
    old: Function, new: Function
) -> list[tuple[ChangeType, str]]:
    differences = []
    if old.is_async != new.is_async:
        kinds = {True: "async def", False: "def"}
        differences.append(
            (
                ChangeType.ASYNC_CHANGED,
                f"{kinds[old.is_async]} -> {kinds[new.is_async]}",
            )
        )
    differences += _parameter_differences(old.parameters, new.parameters)
    if old.returns != new.returns:
        differences.append(
            (
                ChangeType.RETURN_ANNOTATION_CHANGED,
                _transition(old.returns, new.returns),
            )
        )
    return differences


def _parameter_differences(
    old_parameters: tuple[Parameter, ...], new_parameters: tuple[Parameter, ...]
) -> list[tuple[ChangeType, str]]:
    old_names = [parameter.name for parameter in old_parameters]
    new_names = [parameter.name for parameter in new_parameters]
    new_by_name = {parameter.name: parameter for parameter in new_parameters}

    differences = []
    matched = [
        (parameter, new_by_name[parameter.name])
        for parameter in old_parameters
        if parameter.name in new_by_name
    ]

    # A name that disappears where another appears, with the same kind, is a rename.
    matcher = SequenceMatcher(None, old_names, new_names, autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed = [
            parameter
            for parameter in old_parameters[old_start:old_end]
            if parameter.name not in new_names
        ]
        added = [
            parameter
            for parameter in new_parameters[new_start:new_end]
            if parameter.name not in old_names
        ]
        for old, new in zip_longest(removed, added):
            if old and new and old.kind == new.kind:
                differences.append(
                    (ChangeType.PARAMETER_RENAMED, f"{old.name} -> {new.name}")
                )
                matched.append((old, new))
                continue
            if old:
                differences.append((ChangeType.PARAMETER_REMOVED, old.name))
            if new:
                differences.append((ChangeType.PARAMETER_ADDED, new.name))

    if [name for name in old_names if name in new_names] != [
        name for name in new_names if name in old_names
    ]:
        differences.append((ChangeType.SIGNATURE_CHANGED, "parameter order changed"))

    for old, new in matched:
        if old.default != new.default:
            differences.append(
                (
                    ChangeType.DEFAULT_CHANGED,
                    f"{new.name}: {_transition(old.default, new.default)}",
                )
            )
        if old.annotation != new.annotation:
            differences.append(
                (
                    ChangeType.SIGNATURE_CHANGED,
                    f"{new.name} annotation: {_transition(old.annotation, new.annotation)}",
                )
            )
        if old.kind != new.kind:
            differences.append(
                (
                    ChangeType.SIGNATURE_CHANGED,
                    f"{new.name} kind: {old.kind} -> {new.kind}",
                )
            )
    return differences


def _transition(old: str | None, new: str | None) -> str:
    return f"{old or '(none)'} -> {new or '(none)'}"
