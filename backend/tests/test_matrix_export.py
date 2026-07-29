"""Placement, grid and SVG tests. Pure functions only — no DB, no app fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from app.models.matrix import DEFAULT_CONFIG
from app.services.matrix_export import (
    OVERALL,
    Placement,
    band_for_score,
    build_grid,
    grid_to_svg,
    lens_label,
    placement_for,
    valid_lens,
)


@dataclass
class FakeRisk:
    risk_code: str = "ENV-030-0001"
    title: str = "Contaminated soil discovered during excavation"
    status: str = "Open"
    owner: str | None = "A. Analyst"
    probability: int | None = 4
    impact: int | None = 5
    impact_scores: dict | None = None
    target_probability: int | None = 2
    target_impact: int | None = 3
    target_impact_scores: dict | None = None


def test_overall_lens_uses_stored_worst_case_impact():
    risk = FakeRisk(impact_scores={"COST": 3, "SCHED": 5, "SAFE": 2}, impact=5)
    p = placement_for(risk, DEFAULT_CONFIG)
    assert (p.probability, p.impact, p.score) == (4, 5, 20)
    assert p.band == "Very high"
    assert p.placed


def test_area_lens_reads_that_area_not_the_worst_case():
    risk = FakeRisk(impact_scores={"COST": 3, "SCHED": 5}, impact=5)
    p = placement_for(risk, DEFAULT_CONFIG, lens="COST")
    assert p.impact == 3
    assert p.score == 12
    assert p.band == "High"


def test_area_lens_unscored_area_is_unplaced_not_zero():
    risk = FakeRisk(impact_scores={"COST": 3}, impact=3)
    p = placement_for(risk, DEFAULT_CONFIG, lens="SAFE")
    assert p.impact is None
    assert p.score is None
    assert not p.placed


def test_target_basis_uses_residual_scores():
    risk = FakeRisk(
        probability=5,
        impact=5,
        impact_scores={"COST": 5},
        target_probability=2,
        target_impact=2,
        target_impact_scores={"COST": 2},
    )
    current = placement_for(risk, DEFAULT_CONFIG, lens="COST", basis="current")
    residual = placement_for(risk, DEFAULT_CONFIG, lens="COST", basis="target")
    assert current.score == 25
    assert residual.score == 4
    assert residual.band == "Low"


def test_missing_probability_is_unplaced():
    p = placement_for(FakeRisk(probability=None), DEFAULT_CONFIG)
    assert not p.placed


def test_band_for_score_boundaries():
    bands = DEFAULT_CONFIG["bands"]
    assert band_for_score(4, bands)["name"] == "Low"
    assert band_for_score(5, bands)["name"] == "Medium"
    assert band_for_score(9, bands)["name"] == "Medium"
    assert band_for_score(10, bands)["name"] == "High"
    assert band_for_score(25, bands)["name"] == "Very high"
    assert band_for_score(None, bands) is None
    assert band_for_score(99, bands) is None


def test_valid_lens_falls_back_to_overall():
    assert valid_lens("COST", DEFAULT_CONFIG) == "COST"
    assert valid_lens("NOPE", DEFAULT_CONFIG) == OVERALL
    assert valid_lens(None, DEFAULT_CONFIG) == OVERALL
    assert lens_label(OVERALL, DEFAULT_CONFIG).startswith("Overall")
    assert lens_label("SAFE", DEFAULT_CONFIG) == "Safety"


def _placement(code: str, p: int | None, i: int | None) -> Placement:
    score = p * i if p is not None and i is not None else None
    return Placement(
        code=code,
        title=code,
        probability=p,
        impact=i,
        score=score,
        band=None,
        owner=None,
        status="Open",
    )


def test_grid_buckets_and_counts():
    grid = build_grid(
        [
            _placement("A-001-0001", 5, 5),
            _placement("A-001-0002", 5, 5),
            _placement("A-001-0003", 1, 1),
            _placement("A-001-0004", None, 3),
        ],
        DEFAULT_CONFIG,
    )
    assert len(grid.cells) == 25
    assert grid.cell(5, 5).count == 2
    assert grid.cell(1, 1).count == 1
    assert grid.cell(3, 3).count == 0
    assert len(grid.placed) == 3
    assert [p.code for p in grid.unplaced] == ["A-001-0004"]
    assert grid.total == 4


def test_grid_rows_read_high_probability_first():
    grid = build_grid([], DEFAULT_CONFIG)
    assert [r["level"] for r in grid.rows] == [5, 4, 3, 2, 1]
    assert [c["level"] for c in grid.columns] == [1, 2, 3, 4, 5]


def test_off_scale_risk_is_reported_not_dropped():
    """A 3x3 config must not silently swallow a risk scored 5x5 under an older scheme."""
    small = {
        "name": "3x3",
        "probability_levels": [{"level": n, "label": f"P{n}"} for n in (1, 2, 3)],
        "impact_levels": [{"level": n, "label": f"I{n}"} for n in (1, 2, 3)],
        "impact_areas": [],
        "bands": [{"name": "Low", "min_score": 1, "max_score": 9, "color": "#c0dd97"}],
    }
    grid = build_grid([_placement("A-001-0009", 5, 5)], small)
    assert grid.placed == []
    assert len(grid.unplaced) == 1
    assert grid.unplaced[0].off_scale is True
    assert grid.total == 1


def test_cell_placements_are_sorted_by_code():
    grid = build_grid(
        [_placement("Z-001-0001", 2, 2), _placement("A-001-0001", 2, 2)], DEFAULT_CONFIG
    )
    assert [p.code for p in grid.cell(2, 2).placements] == ["A-001-0001", "Z-001-0001"]


def test_svg_is_wellformed_and_carries_the_data():
    grid = build_grid(
        [_placement("ENV-030-0001", 4, 5), _placement("CST-010-0002", 1, 2)],
        DEFAULT_CONFIG,
        lens="COST",
        basis="target",
    )
    svg = grid_to_svg(grid, "Northgate Interchange", generated_on=date(2026, 7, 28))

    import xml.etree.ElementTree as ET

    ET.fromstring(svg)  # raises if malformed

    assert svg.startswith("<svg")
    assert "ENV-030-0001" in svg
    assert "Northgate Interchange" in svg
    assert "Residual (post-mitigation)" in svg
    assert "Impact — Cost" in svg
    assert "2 of 2 risks placed" in svg
    assert "generated 2026-07-28" in svg


def test_svg_escapes_markup_in_titles():
    grid = build_grid([], DEFAULT_CONFIG)
    svg = grid_to_svg(grid, '<script>alert("x")</script> & co')
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    import xml.etree.ElementTree as ET

    ET.fromstring(svg)


def test_svg_counts_only_mode_omits_codes():
    grid = build_grid([_placement("ENV-030-0001", 3, 3)], DEFAULT_CONFIG)
    svg = grid_to_svg(grid, "P", show_codes=False)
    assert "ENV-030-0001" not in svg
    assert ">1</text>" in svg


def test_svg_truncates_long_cells_with_a_more_marker():
    codes = [_placement(f"ENV-030-{n:04d}", 3, 3) for n in range(1, 8)]
    svg = grid_to_svg(build_grid(codes, DEFAULT_CONFIG), "P", max_codes=4)
    assert "+3 more" in svg
    assert "ENV-030-0005" not in svg


def test_empty_register_still_renders():
    svg = grid_to_svg(build_grid([], DEFAULT_CONFIG), "Empty project")
    assert "0 of 0 risks placed" in svg
    import xml.etree.ElementTree as ET

    ET.fromstring(svg)


@pytest.mark.parametrize("basis", ["current", "target"])
def test_grid_carries_its_basis_through(basis):
    grid = build_grid([], DEFAULT_CONFIG, basis=basis)
    assert grid.basis == basis
