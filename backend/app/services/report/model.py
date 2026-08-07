"""The document model every renderer reads, and the formatting they share.

A section builder does not emit HTML, or a worksheet, or a paragraph of prose. It emits
*blocks* — a table, a set of labelled values, a callout — and the renderers decide what
those look like. That is the whole reason this file exists: the moment a builder writes a
formatted number, the workbook and the printed page start disagreeing about what the P80
was, and the disagreement shows up in front of a client rather than in a test.

Numbers therefore travel unformatted. :class:`Cell` carries the value; ``format_value``
turns it into text for HTML, and ``excel_number_format`` hands Excel the pattern so the
same figure stays a number a reviewer can re-sum in the cell next to it. A ``display``
string on a cell overrides both, for the cases where the value genuinely is text.

Blocks are a closed set and deliberately a small one. Adding a chart kind is a day's work
in three renderers; adding a table is free.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Align",
    "Block",
    "Callout",
    "Cell",
    "Column",
    "Document",
    "Format",
    "KeyValue",
    "KeyValues",
    "MatrixBand",
    "MatrixBlock",
    "MatrixCell",
    "MatrixLevel",
    "Paragraph",
    "Section",
    "Table",
    "excel_number_format",
    "format_value",
    "text_cell",
    "value_cell",
]

Align = Literal["left", "right", "center"]

#: How a column's numbers are read. ``days`` and ``currency`` are distinct even though
#: both are floats, because a report that prints a delay with a currency symbol is a
#: report nobody trusts the rest of.
Format = Literal["text", "int", "currency", "days", "pct", "ratio", "date"]

Tone = Literal["info", "warning", "method"]


class Cell(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: float | int | str | None = None
    #: Overrides formatting. Set only when the cell genuinely is text.
    display: str | None = None
    #: Hex fill, for band colours. Never the sole carrier of meaning — the band name is
    #: always in a neighbouring cell, because a colour-blind reader and a monochrome
    #: printer are both real.
    color: str | None = None
    emphasis: bool = False


def value_cell(value: float | int | None, *, color: str | None = None,
               emphasis: bool = False) -> Cell:
    return Cell(value=value, color=color, emphasis=emphasis)


def text_cell(text: str | None, *, color: str | None = None,
              emphasis: bool = False) -> Cell:
    return Cell(value=None, display=text or "", color=color, emphasis=emphasis)


class Column(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    align: Align = "left"
    format: Format = "text"
    width: int = 18


class Block(BaseModel):
    model_config = ConfigDict(frozen=True)


class Paragraph(Block):
    kind: Literal["paragraph"] = "paragraph"
    text: str


class KeyValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    value: str
    note: str | None = None


class KeyValues(Block):
    kind: Literal["keyvalues"] = "keyvalues"
    caption: str | None = None
    items: tuple[KeyValue, ...] = ()


class Table(Block):
    kind: Literal["table"] = "table"
    caption: str | None = None
    columns: tuple[Column, ...] = ()
    rows: tuple[tuple[Cell, ...], ...] = ()
    #: Printed under the table. Where an approximation gets declared on the face of the
    #: numbers rather than in a docstring nobody reading the report will ever open.
    note: str | None = None
    empty_text: str = "Nothing to report."


class Callout(Block):
    kind: Literal["callout"] = "callout"
    #: ``method`` is not decoration. It marks the statements that exist to stop a reader
    #: doing the arithmetic the wrong way — the additive-percentile trap above all.
    tone: Tone = "info"
    title: str
    text: str


class MatrixLevel(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: int
    label: str


class MatrixCell(BaseModel):
    model_config = ConfigDict(frozen=True)

    probability: int
    impact: int
    score: int
    band: str | None = None
    color: str = "#ffffff"
    count: int = 0
    codes: tuple[str, ...] = ()


class MatrixBand(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    color: str
    min_score: int
    max_score: int


class MatrixBlock(Block):
    kind: Literal["matrix"] = "matrix"
    caption: str | None = None
    #: Highest probability first — the way a matrix is read.
    probability_levels: tuple[MatrixLevel, ...] = ()
    impact_levels: tuple[MatrixLevel, ...] = ()
    cells: tuple[MatrixCell, ...] = ()
    bands: tuple[MatrixBand, ...] = ()
    note: str | None = None

    def cell(self, probability: int, impact: int) -> MatrixCell | None:
        for item in self.cells:
            if item.probability == probability and item.impact == impact:
                return item
        return None


AnyBlock = Annotated[
    Union[Paragraph, KeyValues, Table, Callout, MatrixBlock],
    Field(discriminator="kind"),
]


class Section(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    blocks: tuple[AnyBlock, ...] = ()


class Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    subtitle: str = ""
    generated_on: date
    prepared_by: str = ""
    currency: str = ""
    sections: tuple[Section, ...] = ()


# ------------------------------------------------------------------------- formatting


def _thousands(value: float, places: int = 0) -> str:
    return f"{value:,.{places}f}"


def format_value(value: object, fmt: Format = "text", currency: str = "") -> str:
    """One number, one string, wherever it is printed.

    ``None`` is an em dash rather than an empty cell or a zero. A missing duration
    sensitivity and a measured zero are different findings and the report says which.
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ", timespec="minutes") if isinstance(
            value, datetime
        ) else value.isoformat()
    if isinstance(value, str):
        return value
    if not isinstance(value, (int, float)):
        return str(value)

    number = float(value)
    if fmt == "currency":
        return f"{currency}{_thousands(number)}" if currency else _thousands(number)
    if fmt == "days":
        return f"{_thousands(number, 1)} d"
    if fmt == "pct":
        return f"{number * 100:.1f}%"
    if fmt == "ratio":
        return f"{number:.2f}"
    if fmt == "int":
        return _thousands(number)
    if number == int(number) and abs(number) < 1e15:
        return _thousands(number)
    return _thousands(number, 2)


def excel_number_format(fmt: Format, currency: str = "") -> str | None:
    """The pattern Excel gets, so the figure stays a number in the cell."""
    if fmt == "currency":
        prefix = f'"{currency}"' if currency else ""
        return f"{prefix}#,##0"
    if fmt == "days":
        return '#,##0.0" d"'
    if fmt == "pct":
        return "0.0%"
    if fmt == "ratio":
        return "0.00"
    if fmt == "int":
        return "#,##0"
    return None
