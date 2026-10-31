from vigilant.markdown_docs import extract_sections


def sections_of(text):
    return extract_sections("docs/guide.md", text.encode())


def identifiers(section):
    return [
        (r.identifier, r.line, r.context, r.is_explicit) for r in section.references
    ]


def test_content_is_associated_with_the_correct_heading():
    sections = sections_of(
        "# Users\n\nCreate users here.\n\n# Billing\nCharge them here.\n\n"
    )

    assert [
        (s.path, s.heading, s.start_line, s.end_line, s.content) for s in sections
    ] == [
        ("docs/guide.md", "Users", 1, 3, "# Users\n\nCreate users here."),
        ("docs/guide.md", "Billing", 5, 6, "# Billing\nCharge them here."),
    ]


def test_nested_headings_build_a_hierarchy_and_siblings_replace_each_other():
    sections = sections_of(
        "# API\n## Users\n### Create\ntext\n### Delete\ntext\n## Billing\n# Other\n"
    )

    assert [s.hierarchy for s in sections] == [
        ("API",),
        ("API", "Users"),
        ("API", "Users", "Create"),
        ("API", "Users", "Delete"),
        ("API", "Billing"),
        ("Other",),
    ]


def test_skipped_heading_levels_keep_the_nearest_parent():
    sections = sections_of("# Top\n### Deep\n## Middle\n")

    assert [s.hierarchy for s in sections] == [
        ("Top",),
        ("Top", "Deep"),
        ("Top", "Middle"),
    ]


def test_document_without_headings_is_one_section():
    sections = sections_of("\n\nJust some text.\nMore text.\n\n")

    assert len(sections) == 1
    assert sections[0].heading is None
    assert sections[0].hierarchy == ()
    assert (sections[0].start_line, sections[0].end_line) == (3, 4)


def test_text_before_the_first_heading_is_its_own_section():
    sections = sections_of("Intro\n\n# Title\nBody\n")

    assert [(s.heading, s.start_line, s.end_line) for s in sections] == [
        (None, 1, 1),
        ("Title", 3, 4),
    ]


def test_empty_and_blank_files_have_no_sections():
    assert sections_of("") == []
    assert sections_of("\n  \n\n") == []


def test_heading_without_content_is_kept():
    sections = sections_of("# Empty\n")

    assert [(s.heading, s.start_line, s.end_line) for s in sections] == [
        ("Empty", 1, 1)
    ]


def test_closing_hashes_and_indentation_in_headings():
    sections = sections_of("   ## Title ##\ntext\n")

    assert sections[0].heading == "Title"


def test_lines_that_are_not_headings():
    sections = sections_of("#hashtag\n    # indented code\n####### seven\n")

    assert len(sections) == 1
    assert sections[0].heading is None


def test_hash_comment_inside_a_code_block_is_not_a_heading():
    sections = sections_of("# Title\n```python\n# comment\nrun()\n```\n# Next\n")

    assert [s.heading for s in sections] == ["Title", "Next"]
    assert ("comment", 3, "code_block", False) in identifiers(sections[0])
    assert ("run", 4, "code_block", True) in identifiers(sections[0])


def test_triple_backticks_inside_a_line_do_not_open_a_code_block():
    sections = sections_of("```inline``` code\n# Heading\n")

    assert [s.heading for s in sections] == [None, "Heading"]
    assert ("inline", 1, "inline_code", True) in identifiers(sections[0])


def test_fence_closes_only_with_a_matching_marker():
    sections = sections_of("````\n```\n# not heading\n````\n# Heading\n")

    assert [s.heading for s in sections] == [None, "Heading"]


def test_unclosed_fence_extends_to_the_end_of_the_file():
    sections = sections_of("~~~\n# not heading\n")

    assert [s.heading for s in sections] == [None]


def test_inline_code_identifiers_and_explicit_forms():
    section = sections_of("Use `create_user` or `Service.run()` then plain_word.\n")[0]

    assert ("create_user", 1, "inline_code", True) in identifiers(section)
    assert ("Service.run", 1, "inline_code", True) in identifiers(section)
    assert ("plain_word", 1, "prose", False) in identifiers(section)


def test_arguments_in_inline_code_are_not_explicit_references():
    section = sections_of("Call `create_user(name, role)` now.\n")[0]

    assert ("create_user", 1, "inline_code", True) in identifiers(section)
    assert ("name", 1, "inline_code", False) in identifiers(section)


def test_heading_identifiers_use_the_heading_context():
    section = sections_of("## The `create_user` function\n")[0]

    assert ("create_user", 1, "heading", True) in identifiers(section)
    assert section.references[0].evidence == "## The `create_user` function"


def test_reference_line_numbers_are_absolute_and_evidence_is_the_source_line():
    sections = sections_of("# A\n\ntext\n\n# B\n\n  See `target_name` here.\n")

    reference = next(r for r in sections[1].references if r.identifier == "target_name")
    assert reference.line == 7
    assert reference.evidence == "See `target_name` here."


def test_crlf_line_endings_and_byte_order_mark():
    sections = extract_sections("a.md", "﻿# Title\r\nBody\r\n".encode())

    assert sections[0].heading == "Title"
    assert sections[0].content == "# Title\nBody"


def test_invalid_utf8_does_not_raise_and_keeps_line_numbers():
    sections = extract_sections("a.md", b"\xff bad\n# Title\n")

    assert [(s.heading, s.start_line) for s in sections] == [(None, 1), ("Title", 2)]
