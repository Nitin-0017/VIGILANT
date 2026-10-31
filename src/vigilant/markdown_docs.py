import re
from dataclasses import dataclass

HEADING = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
FENCE = re.compile(r"[ \t]*(`{3,}(?=[^`]*$)|~{3,})")
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
IDENTIFIER = re.compile(r"(?<![\w.])[^\W\d]\w*(?:\.[^\W\d]\w*)*(?!\w)")
BARE_REFERENCE = re.compile(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*(?:\(.*\))?")


@dataclass(frozen=True)
class Reference:
    identifier: str
    line: int
    context: str
    is_explicit: bool
    evidence: str


@dataclass(frozen=True)
class Section:
    path: str
    heading: str | None
    hierarchy: tuple[str, ...]
    start_line: int
    end_line: int
    content: str
    references: tuple[Reference, ...]


def document_lines(source: bytes) -> list[str]:
    return [
        line.rstrip("\r")
        for line in source.decode("utf-8-sig", errors="replace").split("\n")
    ]


def extract_sections(path: str, source: bytes) -> list[Section]:
    """Split a Markdown file into one section per heading, plus any leading text."""
    lines = document_lines(source)
    sections = []
    open_headings: list[tuple[int, str]] = []
    start = 1
    references: list[Reference] = []
    fence = None

    for number, line in enumerate(lines, start=1):
        if fence:
            if line.strip().rstrip(fence[0]) == "" and line.strip().startswith(fence):
                fence = None
            else:
                references += _identifiers(line, number, "code_block", line.strip())
        elif opening := FENCE.match(line):
            fence = opening[1]
        elif heading := HEADING.fullmatch(line):
            sections.append(
                _section(path, lines, start, number - 1, open_headings, references)
            )
            level, title = len(heading[1]), heading[2] or ""
            open_headings = [h for h in open_headings if h[0] < level]
            open_headings.append((level, title))
            start = number
            references = _text_references(title, number, line, "heading")
        else:
            references += _text_references(line, number, line, "prose")

    sections.append(_section(path, lines, start, len(lines), open_headings, references))
    return [section for section in sections if section is not None]


def _section(
    path: str,
    lines: list[str],
    start: int,
    end: int,
    open_headings: list[tuple[int, str]],
    references: list[Reference],
) -> Section | None:
    while start <= end and not lines[start - 1].strip():
        start += 1
    while end >= start and not lines[end - 1].strip():
        end -= 1
    if start > end:
        return None
    hierarchy = tuple(title for _, title in open_headings)
    return Section(
        path=path,
        heading=hierarchy[-1] if hierarchy else None,
        hierarchy=hierarchy,
        start_line=start,
        end_line=end,
        content="\n".join(lines[start - 1 : end]),
        references=tuple(references),
    )


def _text_references(
    text: str, number: int, line: str, context: str
) -> list[Reference]:
    evidence = line.strip()
    references = []
    position = 0
    for span in INLINE_CODE.finditer(text):
        references += _identifiers(
            text[position : span.start()], number, context, evidence
        )
        code_context = "heading" if context == "heading" else "inline_code"
        code = span[2].strip()
        references += _identifiers(
            code,
            number,
            code_context,
            evidence,
            is_bare_span=BARE_REFERENCE.fullmatch(code) is not None,
        )
        position = span.end()
    return references + _identifiers(text[position:], number, context, evidence)


def _identifiers(
    text: str, number: int, context: str, evidence: str, is_bare_span: bool = False
) -> list[Reference]:
    return [
        Reference(
            identifier=match[0],
            line=number,
            context=context,
            is_explicit=text[match.end() : match.end() + 1] == "("
            or (is_bare_span and match.start() == 0),
            evidence=evidence,
        )
        for match in IDENTIFIER.finditer(text)
    ]
