"""Pure risk-matrix construction: placement, grid, SVG rendering.

No DB, no network, no logging. Objects in, structures out. The placement rule — which cell
a risk lands in for a given lens and basis — lives here and nowhere else, so the screen, the
workbook and the SVG can never disagree about where a risk sits.

Vocabulary:
  lens   which impact area the matrix is drawn against. ``OVERALL`` uses the stored
         worst-case impact; anything else reads that area's score directly.
  basis  ``current`` (pre-mitigation) or ``target`` (residual, post-mitigation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Literal, Sequence

OVERALL = "__overall__"

Basis = Literal["current", "target"]

BASIS_LABEL: dict[str, str] = {
    "current": "Current (pre-mitigation)",
    "target": "Residual (post-mitigation)",
}


# --------------------------------------------------------------------------- placement


@dataclass(frozen=True)
class Placement:
    """Where one risk sits on one matrix. ``probability``/``impact`` are None when the
    risk has not been scored on the selected lens and basis."""

    code: str
    title: str
    probability: int | None
    impact: int | None
    score: int | None
    band: str | None
    owner: str | None
    status: str
    category: str | None = None
    off_scale: bool = False

    @property
    def placed(self) -> bool:
        return self.probability is not None and self.impact is not None and not self.off_scale


def band_for_score(score: int | None, bands: Sequence[dict]) -> dict | None:
    """The band whose range contains ``score``. None when unscored or outside every band."""
    if score is None:
        return None
    for band in bands:
        if band["min_score"] <= score <= band["max_score"]:
            return band
    return None


def lens_label(lens: str, config: dict) -> str:
    if lens == OVERALL:
        return "Overall (worst area)"
    for area in config.get("impact_areas", []):
        if area["code"] == lens:
            return str(area["name"])
    return lens


def basis_label(basis: str) -> str:
    return BASIS_LABEL.get(basis, basis)


def valid_lens(lens: str | None, config: dict) -> str:
    """Fall back to the overall lens rather than silently drawing an empty matrix."""
    if not lens or lens == OVERALL:
        return OVERALL
    codes = {area["code"] for area in config.get("impact_areas", [])}
    return lens if lens in codes else OVERALL


def placement_for(
    risk: Any,
    config: dict,
    lens: str = OVERALL,
    basis: str = "current",
    category: str | None = None,
) -> Placement:
    """Resolve one risk to one cell. ``risk`` is any object carrying the register fields."""
    if basis == "target":
        probability = risk.target_probability
        scores = risk.target_impact_scores or {}
        overall = risk.target_impact
    else:
        probability = risk.probability
        scores = risk.impact_scores or {}
        overall = risk.impact

    if lens == OVERALL:
        impact = overall
    else:
        raw = scores.get(lens)
        impact = raw if isinstance(raw, int) else None

    score: int | None = None
    if probability is not None and impact is not None:
        score = probability * impact
    band = band_for_score(score, config.get("bands", []))

    return Placement(
        code=risk.risk_code,
        title=risk.title,
        probability=probability,
        impact=impact,
        score=score,
        band=band["name"] if band else None,
        owner=risk.owner,
        status=risk.status,
        category=category,
    )


# ------------------------------------------------------------------------------- grid


@dataclass
class Cell:
    probability: int
    impact: int
    score: int
    band: str | None
    color: str
    placements: list[Placement] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.placements)


@dataclass
class Grid:
    """A fully resolved matrix: every configured cell, plus what could not be placed."""

    probability_levels: list[dict]
    impact_levels: list[dict]
    cells: dict[tuple[int, int], Cell]
    placed: list[Placement]
    unplaced: list[Placement]
    lens: str
    lens_label: str
    basis: str
    config_name: str

    @property
    def rows(self) -> list[dict]:
        """Probability levels, highest first — the way a matrix is read."""
        return sorted(self.probability_levels, key=lambda level: level["level"], reverse=True)

    @property
    def columns(self) -> list[dict]:
        """Impact levels, lowest first."""
        return sorted(self.impact_levels, key=lambda level: level["level"])

    @property
    def total(self) -> int:
        return len(self.placed) + len(self.unplaced)

    def cell(self, probability: int, impact: int) -> Cell | None:
        return self.cells.get((probability, impact))


def build_grid(
    placements: Iterable[Placement],
    config: dict,
    lens: str = OVERALL,
    basis: str = "current",
) -> Grid:
    """Bucket placements into the configured cells.

    A risk scored against a level the active config no longer defines is *not* dropped —
    it is reported as unplaced and flagged ``off_scale``, because a silently vanishing risk
    is worse than an ugly one.
    """
    probability_levels = list(config.get("probability_levels", []))
    impact_levels = list(config.get("impact_levels", []))
    bands = config.get("bands", [])

    cells: dict[tuple[int, int], Cell] = {}
    for prob in probability_levels:
        for imp in impact_levels:
            score = prob["level"] * imp["level"]
            band = band_for_score(score, bands)
            cells[(prob["level"], imp["level"])] = Cell(
                probability=prob["level"],
                impact=imp["level"],
                score=score,
                band=band["name"] if band else None,
                color=band["color"] if band else "#ffffff",
            )

    placed: list[Placement] = []
    unplaced: list[Placement] = []
    for item in placements:
        if item.probability is None or item.impact is None:
            unplaced.append(item)
            continue
        cell = cells.get((item.probability, item.impact))
        if cell is None:
            unplaced.append(
                Placement(
                    code=item.code,
                    title=item.title,
                    probability=item.probability,
                    impact=item.impact,
                    score=item.score,
                    band=item.band,
                    owner=item.owner,
                    status=item.status,
                    category=item.category,
                    off_scale=True,
                )
            )
            continue
        cell.placements.append(item)
        placed.append(item)

    for cell in cells.values():
        cell.placements.sort(key=lambda p: p.code)
    placed.sort(key=lambda p: p.code)
    unplaced.sort(key=lambda p: p.code)

    return Grid(
        probability_levels=probability_levels,
        impact_levels=impact_levels,
        cells=cells,
        placed=placed,
        unplaced=unplaced,
        lens=lens,
        lens_label=lens_label(lens, config),
        basis=basis,
        config_name=str(config.get("name", "")),
    )


# -------------------------------------------------------------------------------- SVG

_MARGIN = 24
_TITLE_H = 58
_AXIS_TITLE_W = 26
_ROW_HEAD_W = 168
_CELL_W = 132
_CELL_H = 92
_COL_HEAD_H = 46
_AXIS_TITLE_H = 22
_LEGEND_H = 34
_FOOTER_H = 20

_INK = "#1f2937"
_MUTED = "#6b7280"
_FONT = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
)


def _esc(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _wrap(text: str, width: int, max_lines: int = 2) -> list[str]:
    """Greedy word wrap, ellipsing whatever will not fit in ``max_lines``."""
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = [""]
    for word in words:
        candidate = f"{lines[-1]} {word}".strip()
        if not lines[-1] or len(candidate) <= width:
            lines[-1] = candidate
        elif len(lines) < max_lines:
            lines.append(word)
        else:
            lines[-1] = lines[-1][: max(1, width - 1)].rstrip() + "…"
            return lines
    return lines


def grid_to_svg(
    grid: Grid,
    project_title: str = "Risk matrix",
    show_codes: bool = True,
    max_codes: int = 4,
    generated_on: date | None = None,
) -> str:
    """Render a grid as a standalone SVG — no fonts, images or scripts required.

    Deliberately dependency-free: it drops into a report, a slide, or a browser, and prints
    without a screenshot.
    """
    rows = grid.rows
    columns = grid.columns
    n_rows = max(len(rows), 1)
    n_cols = max(len(columns), 1)

    grid_x = _MARGIN + _AXIS_TITLE_W + _ROW_HEAD_W
    grid_y = _MARGIN + _TITLE_H
    grid_w = n_cols * _CELL_W
    grid_h = n_rows * _CELL_H

    width = grid_x + grid_w + _MARGIN
    height = grid_y + grid_h + _COL_HEAD_H + _AXIS_TITLE_H + _LEGEND_H + _FOOTER_H + _MARGIN

    parts: list[str] = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" font-family="{_FONT}" role="img" '
            f'aria-label="{_esc(project_title)} — {_esc(grid.lens_label)}">'
        ),
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        (
            f'<text x="{_MARGIN}" y="{_MARGIN + 20}" font-size="19" font-weight="700" '
            f'fill="{_INK}">{_esc(project_title)}</text>'
        ),
        (
            f'<text x="{_MARGIN}" y="{_MARGIN + 41}" font-size="12.5" fill="{_MUTED}">'
            f"{_esc(grid.lens_label)} · {_esc(basis_label(grid.basis))}"
            f"{' · ' + _esc(grid.config_name) if grid.config_name else ''}</text>"
        ),
    ]

    # Y axis title
    y_mid = grid_y + grid_h / 2
    parts.append(
        f'<text transform="translate({_MARGIN + 12},{y_mid}) rotate(-90)" text-anchor="middle" '
        f'font-size="12" font-weight="600" fill="{_MUTED}">Probability</text>'
    )

    for r, prob in enumerate(rows):
        y = grid_y + r * _CELL_H
        label_lines = _wrap(str(prob["label"]), 20, 2)
        base_y = y + _CELL_H / 2 - (len(label_lines) - 1) * 8 + 1
        parts.append(
            f'<text x="{grid_x - 12}" y="{base_y}" text-anchor="end" font-size="12.5" '
            f'font-weight="600" fill="{_INK}">'
            + "".join(
                f'<tspan x="{grid_x - 12}" dy="{0 if i == 0 else 16}">{_esc(line)}</tspan>'
                for i, line in enumerate(label_lines)
            )
            + "</text>"
        )
        parts.append(
            f'<text x="{grid_x - 12}" y="{base_y + 16 * len(label_lines)}" text-anchor="end" '
            f'font-size="10.5" fill="{_MUTED}">P{prob["level"]}</text>'
        )

        for c, imp in enumerate(columns):
            x = grid_x + c * _CELL_W
            cell = grid.cell(prob["level"], imp["level"])
            color = cell.color if cell else "#ffffff"
            count = cell.count if cell else 0
            parts.append(
                f'<rect x="{x}" y="{y}" width="{_CELL_W}" height="{_CELL_H}" fill="{_esc(color)}" '
                f'stroke="#ffffff" stroke-width="2"/>'
            )
            if count == 0:
                continue
            cx = x + _CELL_W / 2
            if not show_codes:
                parts.append(
                    f'<text x="{cx}" y="{y + _CELL_H / 2 + 8}" text-anchor="middle" '
                    f'font-size="22" font-weight="700" fill="{_INK}">{count}</text>'
                )
                continue

            shown = cell.placements[:max_codes] if cell else []
            extra = count - len(shown)
            lines = [p.code for p in shown]
            if extra > 0:
                lines.append(f"+{extra} more")
            start = y + _CELL_H / 2 - (len(lines) - 1) * 7 - 4
            parts.append(
                f'<text x="{x + _CELL_W - 7}" y="{y + 15}" text-anchor="end" font-size="11" '
                f'font-weight="700" fill="{_INK}" opacity="0.65">{count}</text>'
            )
            parts.append(
                f'<text x="{cx}" y="{start}" text-anchor="middle" font-size="10.5" fill="{_INK}">'
                + "".join(
                    f'<tspan x="{cx}" dy="{0 if i == 0 else 14}">{_esc(line)}</tspan>'
                    for i, line in enumerate(lines)
                )
                + "</text>"
            )

    # column headers
    head_y = grid_y + grid_h
    for c, imp in enumerate(columns):
        x = grid_x + c * _CELL_W
        label_lines = _wrap(str(imp["label"]), 16, 2)
        parts.append(
            f'<text x="{x + _CELL_W / 2}" y="{head_y + 17}" text-anchor="middle" font-size="12.5" '
            f'font-weight="600" fill="{_INK}">'
            + "".join(
                f'<tspan x="{x + _CELL_W / 2}" dy="{0 if i == 0 else 14}">{_esc(line)}</tspan>'
                for i, line in enumerate(label_lines)
            )
            + "</text>"
        )
        parts.append(
            f'<text x="{x + _CELL_W / 2}" y="{head_y + 17 + 14 * len(label_lines)}" '
            f'text-anchor="middle" font-size="10.5" fill="{_MUTED}">I{imp["level"]}</text>'
        )

    axis_y = head_y + _COL_HEAD_H + 14
    parts.append(
        f'<text x="{grid_x + grid_w / 2}" y="{axis_y}" text-anchor="middle" font-size="12" '
        f'font-weight="600" fill="{_MUTED}">Impact — {_esc(grid.lens_label)}</text>'
    )

    # legend
    legend_y = axis_y + 22
    lx = grid_x
    seen: list[tuple[str, str]] = []
    for prob in rows:
        for imp in columns:
            cell = grid.cell(prob["level"], imp["level"])
            if cell and cell.band and (cell.band, cell.color) not in seen:
                seen.append((cell.band, cell.color))
    for name, color in seen:
        parts.append(
            f'<rect x="{lx}" y="{legend_y - 10}" width="13" height="13" rx="2" '
            f'fill="{_esc(color)}" stroke="#d1d5db"/>'
        )
        parts.append(
            f'<text x="{lx + 19}" y="{legend_y}" font-size="11.5" fill="{_INK}">{_esc(name)}</text>'
        )
        lx += 26 + int(len(name) * 6.6)

    stamp = (generated_on or date.today()).isoformat()
    footer = (
        f"{len(grid.placed)} of {grid.total} risks placed"
        + (f" · {len(grid.unplaced)} not scored on this view" if grid.unplaced else "")
        + f" · generated {stamp}"
    )
    parts.append(
        f'<text x="{_MARGIN}" y="{height - _MARGIN + 6}" font-size="11" fill="{_MUTED}">'
        f"{_esc(footer)}</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)
