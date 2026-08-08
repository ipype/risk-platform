"""The pure extractors, exercised against real files built in-process.

Every test here runs without a database, a session or an app, which is the point of
keeping ``app/ingest/`` pure: bytes go in, a list of chunks comes out, and the expectation
can be written down. What is under test is mostly one rule — a chunk never spans a locator
boundary — plus the three format-specific decisions that rule forced: tables before prose
in a PDF, body order in Word, and cached values in a workbook.
"""

from __future__ import annotations

import pytest

from app.core.errors import (
    DocumentHasNoText,
    DocumentUnreadable,
    UnsupportedDocumentFormat,
)
from app.ingest import extract
from app.ingest.plain import extract_text
from app.ingest.types import (
    HARD_MAX_CHARS,
    PROSE,
    TABLE_ROW,
    row_text,
    split_oversized,
)
from tests.document_fixtures import (
    ruled_table_pdf,
    scanned_pdf,
    text_pdf,
    word_document,
    workbook,
)


class TestRegistry:
    def test_an_unknown_suffix_names_what_is_supported(self) -> None:
        with pytest.raises(UnsupportedDocumentFormat) as caught:
            extract(b"anything", filename="drawing.dwg")
        assert ".pdf" in str(caught.value)
        assert ".docx" in str(caught.value)

    def test_a_file_with_no_suffix_is_refused(self) -> None:
        with pytest.raises(UnsupportedDocumentFormat):
            extract(b"anything", filename="README")

    def test_schedules_are_not_document_sources(self) -> None:
        """``.xer`` is parsed into activities; the evidence service reads those directly.

        Routing it through here would produce prose chunks of data the platform already
        holds relationally, in a form that answers questions prose cannot.
        """
        with pytest.raises(UnsupportedDocumentFormat):
            extract(b"%T\tPROJECT", filename="master.xer")

    def test_the_suffix_decides_and_not_the_bytes(self) -> None:
        """A .docx and a .xlsx are both Zip archives; only the suffix separates them."""
        data = workbook({"Costs": [["Item", "Value"], ["Pipe", 10]]})
        with pytest.raises(DocumentUnreadable):
            extract(data, filename="mislabelled.docx")


class TestPdf:
    def test_prose_carries_a_page_and_a_bbox(self) -> None:
        data = text_pdf([["Permit validity", "The consent is valid for ninety days."]])
        chunks = extract(data, filename="permit.pdf").chunks
        assert chunks
        assert all(c.kind == PROSE for c in chunks)
        assert chunks[0].locator["page"] == 1
        x0, top, x1, bottom = chunks[0].locator["bbox"]
        assert x1 > x0 and bottom > top

    def test_a_chunk_never_spans_a_page(self) -> None:
        """The rule the whole design rests on: one chunk, one renderable highlight."""
        data = text_pdf([["Page one text."], ["Page two text."]])
        extraction = extract(data, filename="two.pdf")
        assert extraction.page_count == 2
        pages = {c.locator["page"] for c in extraction.chunks}
        assert pages == {1, 2}
        for chunk in extraction.chunks:
            assert "Page one" not in chunk.text or "Page two" not in chunk.text

    def test_a_scan_is_refused_rather_than_stored_empty(self) -> None:
        with pytest.raises(DocumentHasNoText) as caught:
            extract(scanned_pdf(2), filename="scan.pdf")
        assert "OCR" in str(caught.value)

    def test_corrupt_bytes_raise_unreadable(self) -> None:
        with pytest.raises(DocumentUnreadable):
            extract(b"%PDF-1.4\nnot really a pdf", filename="broken.pdf")

    def test_a_ruled_table_becomes_rows(self) -> None:
        data = ruled_table_pdf(
            ["Consent register", "Two consents are outstanding."],
            [
                ["Consent", "Days", "Owner"],
                ["Env permit", "90", "Ops"],
                ["Crossing", "45", "Eng"],
            ],
        )
        extraction = extract(data, filename="register.pdf")
        rows = [c for c in extraction.chunks if c.kind == TABLE_ROW]
        assert len(rows) == 2
        assert "Consent: Env permit" in rows[0].text
        assert "Days: 90" in rows[0].text
        assert rows[0].locator["page"] == 1
        assert rows[0].locator["row"] == 1
        assert len(rows[0].locator["bbox"]) == 4

    def test_table_text_is_not_also_emitted_as_prose(self) -> None:
        """Prose-first-tables-second would emit every cell twice, and the shredded copy
        retrieves better than the good one because it is longer."""
        data = ruled_table_pdf(
            ["Consent register"],
            [["Consent", "Days"], ["Env permit", "90"], ["Crossing", "45"]],
        )
        extraction = extract(data, filename="register.pdf")
        prose = " ".join(c.text for c in extraction.chunks if c.kind == PROSE)
        assert "Consent register" in prose
        assert "Env permit" not in prose
        assert "90" not in prose

    def test_ordinals_are_a_gapless_sequence(self) -> None:
        data = text_pdf([["One."], ["Two."], ["Three."]])
        chunks = extract(data, filename="three.pdf").chunks
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))


class TestWord:
    def test_headings_become_a_section_path(self) -> None:
        data = word_document(
            [
                ("heading1", "Consents"),
                ("heading2", "Validity"),
                ("body", "The consent is valid for ninety days from issue."),
            ]
        )
        chunks = extract(data, filename="spec.docx").chunks
        body = [c for c in chunks if "ninety days" in c.text]
        assert body
        assert body[0].section == "Consents › Validity"

    def test_a_sibling_heading_pops_the_deeper_one(self) -> None:
        data = word_document(
            [
                ("heading1", "Consents"),
                ("heading2", "Validity"),
                ("body", "First."),
                ("heading2", "Renewal"),
                ("body", "Second."),
            ]
        )
        chunks = extract(data, filename="spec.docx").chunks
        second = [c for c in chunks if c.text == "Second."]
        assert second[0].section == "Consents › Renewal"

    def test_a_chunk_never_spans_a_heading(self) -> None:
        data = word_document(
            [
                ("heading1", "Alpha"),
                ("body", "Under alpha."),
                ("heading1", "Bravo"),
                ("body", "Under bravo."),
            ]
        )
        for chunk in extract(data, filename="spec.docx").chunks:
            assert not ("Under alpha" in chunk.text and "Under bravo" in chunk.text)

    def test_a_table_keeps_its_position_in_the_body(self) -> None:
        """python-docx exposes paragraphs and tables as separate sequences; reading them
        in turn would put every table at the end regardless of where it sat."""
        data = word_document(
            [
                ("body", "Before the table."),
                ("table", [["Item", "Days"], ["Permit", "90"]]),
                ("body", "After the table."),
            ]
        )
        chunks = extract(data, filename="spec.docx").chunks
        kinds = [c.kind for c in chunks]
        row_at = kinds.index(TABLE_ROW)
        assert any("Before" in c.text for c in chunks[:row_at])
        assert any("After" in c.text for c in chunks[row_at:])

    def test_a_table_row_carries_its_headers(self) -> None:
        data = word_document(
            [("table", [["Item", "Days"], ["Permit", "90"]])]
        )
        rows = [c for c in extract(data, filename="s.docx").chunks if c.kind == TABLE_ROW]
        assert rows[0].text == "Item: Permit | Days: 90"

    def test_an_empty_document_is_refused(self) -> None:
        with pytest.raises(DocumentHasNoText):
            extract(word_document([]), filename="empty.docx")


class TestWorkbook:
    def test_each_row_becomes_one_labelled_chunk(self) -> None:
        data = workbook(
            {"Costs": [["Package", "Value"], ["Civils", 2000000], ["Mechanical", 750000]]}
        )
        chunks = extract(data, filename="estimate.xlsx").chunks
        assert len(chunks) == 2
        assert chunks[0].text == "Package: Civils | Value: 2000000"
        assert chunks[0].locator["sheet"] == "Costs"
        assert chunks[0].locator["cells"] == "A2:B2"
        assert chunks[0].section == "Costs"

    def test_integers_do_not_come_back_as_floats(self) -> None:
        """openpyxl reads a whole number as a float; ``90.0`` is noise in every excerpt."""
        data = workbook({"S": [["Consent", "Days"], ["Env permit", 90]]})
        text = extract(data, filename="x.xlsx").chunks[0].text
        assert "Days: 90" in text
        assert "90.0" not in text

    def test_a_single_column_sheet_has_no_header_row_to_find(self) -> None:
        """The heuristic needs two non-empty cells. One column means every row is data,
        which is the right answer — and the warning says the labels are missing."""
        extraction = extract(workbook({"S": [["Days"], [90]]}), filename="x.xlsx")
        assert [c.text for c in extraction.chunks] == ["Days", "90"]
        assert any("column headers" in w for w in extraction.warnings)

    def test_a_title_block_is_not_mistaken_for_headers(self) -> None:
        """Estimating workbooks routinely open with a title and blank rows."""
        data = workbook(
            {
                "Costs": [
                    ["Capital estimate rev C"],
                    [],
                    ["Package", "Value"],
                    ["Civils", 12],
                ]
            }
        )
        chunks = extract(data, filename="e.xlsx").chunks
        assert any("Package: Civils" in c.text for c in chunks)

    def test_an_uncomputed_formula_is_not_evidence(self) -> None:
        """``data_only=True`` — the opposite of the convention for the build workbook.

        ``=SUM(B2:B3)`` cited under a cost risk tells a reviewer nothing and matches no
        query. The trade is that an uncalculated file reads as empty, which is why the
        extractor warns instead of failing silently.
        """
        data = workbook({"Costs": [["P", "V"], ["A", 1], ["B", 2]]}, with_formula=True)
        text = " ".join(c.text for c in extract(data, filename="e.xlsx").chunks)
        assert "SUM" not in text

    def test_multiple_sheets_are_kept_apart(self) -> None:
        data = workbook(
            {
                "Costs": [["P", "V"], ["Civils", 1]],
                "Risks": [["Ref", "Title"], ["R1", "Permit delay"]],
            }
        )
        chunks = extract(data, filename="e.xlsx").chunks
        assert {c.locator["sheet"] for c in chunks} == {"Costs", "Risks"}

    def test_an_empty_sheet_warns_rather_than_disappearing(self) -> None:
        data = workbook({"Costs": [["P", "V"], ["Civils", 1]], "Notes": []})
        extraction = extract(data, filename="e.xlsx")
        assert any("Notes" in w for w in extraction.warnings)

    def test_a_workbook_with_nothing_in_it_is_refused(self) -> None:
        with pytest.raises(DocumentHasNoText):
            extract(workbook({"Sheet1": []}), filename="empty.xlsx")


class TestPlainText:
    def test_markdown_headings_become_a_section_path(self) -> None:
        chunks = extract_text(
            "# Consents\n## Validity\nThe consent lasts ninety days.\n"
        ).chunks
        body = [c for c in chunks if "ninety days" in c.text]
        assert body[0].section == "Consents › Validity"

    def test_chunks_carry_line_numbers(self) -> None:
        chunks = extract_text("First line.\nSecond line.\n").chunks
        assert chunks[0].locator["line_start"] == 1
        assert chunks[0].locator["line_end"] >= 1

    def test_a_txt_upload_takes_the_same_path(self) -> None:
        chunks = extract(b"Plain content here.", filename="notes.txt").chunks
        assert chunks[0].text == "Plain content here."

    def test_undecodable_bytes_do_not_lose_the_document(self) -> None:
        """A handful of mangled characters is still evidence."""
        chunks = extract("Ninety days\udcff".encode("utf-8", "surrogatepass"), filename="a.txt").chunks
        assert "Ninety days" in chunks[0].text

    def test_empty_text_is_refused(self) -> None:
        with pytest.raises(DocumentHasNoText):
            extract_text("   \n\n  ")


class TestChunkingHelpers:
    def test_a_row_without_headers_still_reads(self) -> None:
        assert row_text([], ["Civils", "12"]) == "Civils | 12"

    def test_blank_cells_are_dropped_not_labelled(self) -> None:
        assert row_text(["A", "B", "C"], ["x", "", "z"]) == "A: x | C: z"

    def test_an_oversized_run_splits_on_a_sentence(self) -> None:
        text = ("The consent is valid. " * 200).strip()
        parts = split_oversized(text)
        assert len(parts) > 1
        assert all(len(p) <= HARD_MAX_CHARS for p in parts)
        assert all(not p.endswith("valid") for p in parts)

    def test_text_under_the_limit_is_left_alone(self) -> None:
        assert split_oversized("Short.") == ["Short."]

    def test_a_run_with_no_sentence_breaks_still_splits(self) -> None:
        text = "word " * 1000
        parts = split_oversized(text.strip())
        assert all(len(p) <= HARD_MAX_CHARS for p in parts)
        # Split on whitespace, never mid-word: a cut word makes the excerpt unreadable at
        # exactly the moment a reviewer is deciding whether to trust it.
        assert all(p.startswith("word") and p.endswith("word") for p in parts)
