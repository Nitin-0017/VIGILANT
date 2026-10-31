from dataclasses import dataclass
from pathlib import PurePosixPath

from vigilant.ast_diff import FunctionChange
from vigilant.markdown_docs import Reference, Section


@dataclass(frozen=True)
class DocMatch:
    source_path: str
    function: str
    doc_path: str
    heading: str | None
    hierarchy: tuple[str, ...]
    section_start_line: int
    section_end_line: int
    line: int
    identifier: str
    reason: str
    evidence: str


def match_documentation(
    changes: list[FunctionChange], sections: list[Section]
) -> list[DocMatch]:
    """Find documentation lines that refer to the changed functions.

    A match shows that documentation may be relevant, not that it is out of date.
    """
    matches = []
    for source_path, function in sorted({(c.path, c.function) for c in changes}):
        for section in sections:
            matches += _section_matches(source_path, function, section)
    return matches


def _section_matches(
    source_path: str, function: str, section: Section
) -> list[DocMatch]:
    module_parts = _module_parts(source_path)
    function_parts = function.replace(".<locals>", "").split(".")

    matches = []
    matched_lines = set()
    for reference in section.references:
        if reference.line in matched_lines:
            continue
        reason = _match_reason(module_parts, function_parts, reference, section)
        if reason:
            matched_lines.add(reference.line)
            matches.append(
                DocMatch(
                    source_path=source_path,
                    function=function,
                    doc_path=section.path,
                    heading=section.heading,
                    hierarchy=section.hierarchy,
                    section_start_line=section.start_line,
                    section_end_line=section.end_line,
                    line=reference.line,
                    identifier=reference.identifier,
                    reason=reason,
                    evidence=reference.evidence,
                )
            )
    return matches


def _module_parts(source_path: str) -> list[str]:
    parts = list(PurePosixPath(source_path).with_suffix("").parts)
    return parts[:-1] if parts[-1] == "__init__" else parts


def _match_reason(
    module_parts: list[str],
    function_parts: list[str],
    reference: Reference,
    section: Section,
) -> str | None:
    identifier_parts = reference.identifier.split(".")
    name = function_parts[-1]
    if identifier_parts[-1] != name:
        return None

    context = reference.context.replace("_", " ")
    qualified_parts = module_parts + function_parts
    if (
        len(identifier_parts) > 1
        and identifier_parts == qualified_parts[-len(identifier_parts) :]
    ):
        return f"qualified name in {context}"

    if not reference.is_explicit and not _is_distinctive(name):
        return None
    if len(function_parts) == 1:
        return f"exact name in {context}" if len(identifier_parts) == 1 else None

    parent = function_parts[-2]
    if any(r.identifier.split(".")[-1] == parent for r in section.references):
        return f"name in {context}, with parent {parent} in the same section"
    return None


def _is_distinctive(name: str) -> bool:
    """Tell apart names like create_user from ordinary words like run."""
    stripped = name.strip("_")
    return "_" in stripped or any(character.isupper() for character in stripped[1:])
