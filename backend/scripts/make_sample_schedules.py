#!/usr/bin/env python3
"""Generate sample P6 XER files for exercising schedule ingestion by hand.

    python scripts/make_sample_schedules.py [output_dir]

Produces four files:

* ``sample-clean.xer``      — a healthy 26-activity project. Gate passes.
* ``sample-problems.xer``   — the same project with realistic defects. Gate blocks.
* ``sample-multi.xer``      — two projects in one export, to exercise the 409 path.
* ``sample-nodates.xer``    — no data date, so the date-dependent checks must abstain
                              rather than quietly pass.

These are written to be *read* as well as uploaded: the defects in the problems file are
each labelled in the activity name, so a report can be checked against the intent.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

CAL_STD = "CAL-STD"
CAL_6D = "CAL-6D"
DATA_DATE = datetime(2026, 6, 1, 8, 0)

_STD_WORKDAYS = {0, 1, 2, 3, 4}
_SIX_DAY = {0, 1, 2, 3, 4, 5}


# --------------------------------------------------------------------------- #
# XER writing
# --------------------------------------------------------------------------- #


def _calendar_blob(workday_numbers: set[int]) -> str:
    """P6 ``clndr_data``. Day numbers are P6's own 1=Sunday..7=Saturday."""
    days = "".join(
        f"(0||{n}()({'(0||0(s|08:00|f|17:00)())' if n in workday_numbers else ''}))"
        for n in range(1, 8)
    )
    return f"(0||CalendarData()((0||DaysOfWeek()({days}))(0||Exceptions()())))"


def _table(name: str, rows: list[dict]) -> str:
    if not rows:
        return ""
    fields = list(rows[0])
    lines = [f"%T\t{name}", "%F\t" + "\t".join(fields)]
    for row in rows:
        lines.append("%R\t" + "\t".join(_cell(row.get(f)) for f in fields))
    return "\n".join(lines) + "\n"


def _cell(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def write_xer(path: Path, tables: list[tuple[str, list[dict]]]) -> None:
    header = (
        "ERMHDR\t19.12\t2026-07-26\tProject\tadmin\tAdmin\t"
        "dbxDatabaseNoName\tProject\tCP1252\n"
    )
    body = "".join(_table(name, rows) for name, rows in tables)
    path.write_bytes((header + body + "%E\n").encode("cp1252"))


# --------------------------------------------------------------------------- #
# date helpers
# --------------------------------------------------------------------------- #


def add_workdays(start: datetime, days: float, workdays: set[int] = _STD_WORKDAYS) -> datetime:
    """Step by whole working days on the given week pattern. Negative steps backwards."""
    cursor = start
    remaining = int(round(abs(days)))
    step = timedelta(days=1 if days >= 0 else -1)
    while remaining > 0:
        cursor += step
        if cursor.weekday() in workdays:
            remaining -= 1
    return cursor


# --------------------------------------------------------------------------- #
# project construction
# --------------------------------------------------------------------------- #


class Builder:
    def __init__(self, project_id: str, name: str, short_name: str) -> None:
        self.project_id = project_id
        self.name = name
        self.short_name = short_name
        self.tasks: list[dict] = []
        self.preds: list[dict] = []
        self.resources: list[dict] = []
        self._seq = 0

    def activity(
        self,
        code: str,
        name: str,
        *,
        duration_days: float,
        start: datetime,
        calendar: str = CAL_STD,
        wbs: str = "W-ENG",
        task_type: str = "TT_Task",
        status: str = "TK_NotStart",
        total_float_days: float = 0.0,
        constraint: str = "",
        constraint_date: datetime | None = None,
        baseline_shift_days: float = 0.0,
        resourced: bool = True,
        cost: float = 0.0,
        early_start_override: datetime | None = None,
    ) -> str:
        self._seq += 1
        task_id = f"{self.project_id}-T{self._seq:03d}"
        workdays = _SIX_DAY if calendar == CAL_6D else _STD_WORKDAYS
        hours_per_day = 10.0 if calendar == CAL_6D else 8.0

        finish = add_workdays(start, duration_days, workdays)
        early_start = early_start_override or start

        complete = status == "TK_Complete"
        active = status == "TK_Active"

        actual_start = start if (complete or active) else None
        actual_finish = finish if complete else None
        remaining = 0.0 if complete else (duration_days / 2 if active else duration_days)

        baseline_start = start - timedelta(days=baseline_shift_days)
        baseline_finish = finish - timedelta(days=baseline_shift_days)

        self.tasks.append(
            {
                "task_id": task_id,
                "proj_id": self.project_id,
                "wbs_id": wbs,
                "clndr_id": calendar,
                "task_code": code,
                "task_name": name,
                "task_type": task_type,
                "status_code": status,
                "target_drtn_hr_cnt": duration_days * hours_per_day,
                "remain_drtn_hr_cnt": remaining * hours_per_day,
                "total_float_hr_cnt": total_float_days * hours_per_day,
                "free_float_hr_cnt": 0,
                "early_start_date": early_start,
                "early_end_date": finish,
                "late_start_date": add_workdays(early_start, total_float_days, workdays),
                "late_end_date": add_workdays(finish, total_float_days, workdays),
                "target_start_date": baseline_start,
                "target_end_date": baseline_finish,
                "act_start_date": actual_start,
                "act_end_date": actual_finish,
                "cstr_type": constraint,
                "cstr_date": constraint_date,
                "cstr_type2": "",
                "cstr_date2": None,
                "driving_path_flag": "Y" if total_float_days == 0 else "N",
            }
        )

        if resourced and task_type not in ("TT_Mile", "TT_FinMile"):
            self.resources.append(
                {
                    "taskrsrc_id": f"TR-{task_id}",
                    "task_id": task_id,
                    "proj_id": self.project_id,
                    "rsrc_id": "R-CREW",
                    "target_cost": cost or duration_days * 12500,
                }
            )
        return task_id

    def link(
        self,
        predecessor: str,
        successor: str,
        *,
        kind: str = "PR_FS",
        lag_days: float = 0.0,
    ) -> None:
        self.preds.append(
            {
                "task_pred_id": f"R-{len(self.preds) + 1:03d}-{self.project_id}",
                "task_id": successor,
                "pred_task_id": predecessor,
                "proj_id": self.project_id,
                "pred_proj_id": self.project_id,
                "pred_type": kind,
                "lag_hr_cnt": lag_days * 8,
            }
        )

    def project_row(self, must_finish_by: datetime | None) -> dict:
        return {
            "proj_id": self.project_id,
            "proj_short_name": self.short_name,
            "clndr_id": CAL_STD,
            "last_recalc_date": DATA_DATE,
            "plan_start_date": datetime(2026, 3, 2, 8, 0),
            "plan_end_date": must_finish_by,
        }

    def wbs_rows(self) -> list[dict]:
        rows = [
            {
                "wbs_id": f"W-ROOT-{self.project_id}",
                "proj_id": self.project_id,
                "wbs_short_name": self.short_name,
                "wbs_name": self.name,
                "parent_wbs_id": "",
                "proj_node_flag": "Y",
            }
        ]
        for code, label in (
            ("W-ENG", "Engineering"),
            ("W-PRO", "Procurement"),
            ("W-CON", "Construction"),
            ("W-COM", "Commissioning"),
        ):
            rows.append(
                {
                    "wbs_id": code,
                    "proj_id": self.project_id,
                    "wbs_short_name": code,
                    "wbs_name": label,
                    "parent_wbs_id": f"W-ROOT-{self.project_id}",
                    "proj_node_flag": "N",
                }
            )
        return rows


def _calendar_rows() -> list[dict]:
    return [
        {
            "clndr_id": CAL_STD,
            "clndr_name": "Standard 5 day / 8 hour",
            "day_hr_cnt": 8,
            "default_flag": "Y",
            "clndr_data": _calendar_blob({2, 3, 4, 5, 6}),
        },
        {
            "clndr_id": CAL_6D,
            "clndr_name": "Construction 6 day / 10 hour",
            "day_hr_cnt": 10,
            "default_flag": "N",
            "clndr_data": _calendar_blob({2, 3, 4, 5, 6, 7}),
        },
    ]


def build_pipeline(project_id: str, short_name: str, name: str, *, healthy: bool) -> Builder:
    """A 26-activity EPC project. ``healthy=False`` injects realistic defects."""
    b = Builder(project_id, name, short_name)

    # Anchor the whole thing to the data date rather than a fixed calendar start. A
    # healthy schedule has its in-progress work straddling the data date: completed work
    # behind it, future work ahead. Placing it by hand is how the "clean" sample ended up
    # with an in-progress activity baselined to have already finished, which correctly
    # trips checks 9, 11 and 14.
    completed_workdays = 4 * 10 + 2 * 8  # engineering, then two procurement activities
    active_overlap = 4  # working days of the in-progress activity already elapsed
    start = add_workdays(DATA_DATE, -(completed_workdays + active_overlap))
    chain: list[str] = []

    kickoff = b.activity(
        "MS-1000", "Notice to proceed", duration_days=0, start=start,
        task_type="TT_Mile", status="TK_Complete", resourced=False,
    )
    chain.append(kickoff)

    # --- engineering: complete, finished on baseline -------------------------
    cursor = start
    for i, label in enumerate(
        ["Basis of design", "Process flow diagrams", "P&ID development", "Detailed design"]
    ):
        task = b.activity(
            f"ENG-{1010 + i * 10}", label, duration_days=10, start=cursor,
            wbs="W-ENG", status="TK_Complete",
        )
        chain.append(task)
        cursor = add_workdays(cursor, 10)

    # --- procurement: two done, one straddling the data date ------------------
    for i, label in enumerate(["Long-lead enquiry", "Vendor evaluation"]):
        task = b.activity(
            f"PRO-{2010 + i * 10}", label, duration_days=8, start=cursor,
            wbs="W-PRO", status="TK_Complete",
        )
        chain.append(task)
        cursor = add_workdays(cursor, 8)

    # started four days ago, finishes four days from now — the shape of live work
    in_progress = b.activity(
        "PRO-2030", "Purchase orders", duration_days=8, start=cursor,
        wbs="W-PRO", status="TK_Active",
    )
    chain.append(in_progress)
    cursor = add_workdays(cursor, 8)

    # --- construction --------------------------------------------------------
    for i, label in enumerate(
        [
            "Site establishment",
            "Earthworks and trenching",
            "Pipe stringing",
            "Welding and NDT",
            "Lowering-in and backfill",
            "Tie-ins",
            "Hydrotest",
        ]
    ):
        task = b.activity(
            f"CON-{3010 + i * 10}", label, duration_days=12, start=cursor,
            calendar=CAL_6D, wbs="W-CON",
        )
        chain.append(task)
        cursor = add_workdays(cursor, 12, _SIX_DAY)

    # --- commissioning -------------------------------------------------------
    for i, label in enumerate(["Pre-commissioning", "Dry commissioning", "Wet commissioning"]):
        task = b.activity(
            f"COM-{4010 + i * 10}", label, duration_days=6, start=cursor, wbs="W-COM"
        )
        chain.append(task)
        cursor = add_workdays(cursor, 6)

    handover = b.activity(
        "MS-9000", "Mechanical completion", duration_days=0, start=cursor,
        task_type="TT_FinMile", wbs="W-COM", resourced=False,
    )
    chain.append(handover)

    for previous, following in zip(chain, chain[1:]):
        b.link(previous, following)

    # a couple of legitimate parallel side activities, hung off the chain properly
    permits = b.activity(
        "ENG-1900", "Permitting and approvals", duration_days=15, start=start,
        wbs="W-ENG", status="TK_Complete", total_float_days=20,
    )
    b.link(kickoff, permits)
    b.link(permits, chain[5])

    survey = b.activity(
        "CON-3900", "Route survey", duration_days=5, start=DATA_DATE,
        wbs="W-CON", total_float_days=12,
    )
    b.link(chain[7], survey, kind="PR_SS", lag_days=2)
    b.link(survey, chain[9])

    if healthy:
        return b

    # ---------------------------------------------------------------------- #
    # defects, each labelled so a report can be checked against the intent
    # ---------------------------------------------------------------------- #

    # check 1 — no predecessor and no successor at all
    b.activity(
        "DEF-8010", "DEFECT check1 orphan no logic", duration_days=10,
        start=DATA_DATE, wbs="W-CON", total_float_days=10,
    )
    b.activity(
        "DEF-8020", "DEFECT check1 orphan second", duration_days=10,
        start=DATA_DATE, wbs="W-CON", total_float_days=10,
    )

    # check 7 — negative float, and check 9 — future work forecast in the past
    stale = b.activity(
        "DEF-8030", "DEFECT check7 negative float and check9 stale forecast",
        duration_days=10, start=DATA_DATE, wbs="W-CON",
        total_float_days=-8,
        early_start_override=DATA_DATE - timedelta(days=21),
    )
    b.link(chain[9], stale)
    b.link(stale, chain[11])

    # check 5 — a mandatory date that overrides network logic
    forced = b.activity(
        "DEF-8040", "DEFECT check5 mandatory start constraint", duration_days=8,
        start=DATA_DATE, wbs="W-CON", constraint="CS_MANDSTART",
        constraint_date=DATA_DATE, total_float_days=-4,
    )
    b.link(chain[10], forced)
    b.link(forced, chain[12])

    # check 6 — float far beyond two months, and check 8 — a 90 day bar
    b_long = b.activity(
        "DEF-8050", "DEFECT check6 high float and check8 long duration",
        duration_days=90, start=DATA_DATE, wbs="W-CON", total_float_days=120,
    )
    b.link(chain[8], b_long)
    b.link(b_long, chain[13])

    # check 11 and 14 — baselined to finish before the data date, still not started
    missed = b.activity(
        "DEF-8060", "DEFECT check11 missed task never started", duration_days=10,
        start=DATA_DATE, wbs="W-PRO", baseline_shift_days=120, total_float_days=5,
    )
    b.link(chain[6], missed)
    b.link(missed, chain[10])

    # checks 2, 3, 4 — a lead, a long lag, and non-FS logic
    b.link(chain[12], chain[14], kind="PR_FS", lag_days=-5)
    b.link(chain[13], chain[15], kind="PR_FS", lag_days=10)
    b.link(chain[14], chain[16], kind="PR_SS")
    b.link(chain[15], chain[17], kind="PR_FF")

    return b


def tables_for(builders: list[Builder], must_finish_by: datetime | None) -> list:
    projects, wbs, tasks, preds, resources = [], [], [], [], []
    for builder in builders:
        projects.append(builder.project_row(must_finish_by))
        wbs.extend(builder.wbs_rows())
        tasks.extend(builder.tasks)
        preds.extend(builder.preds)
        resources.extend(builder.resources)
    return [
        ("PROJECT", projects),
        ("CALENDAR", _calendar_rows()),
        ("PROJWBS", wbs),
        ("TASK", tasks),
        ("TASKPRED", preds),
        ("TASKRSRC", resources),
    ]


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    finish_by = datetime(2027, 3, 1, 17, 0)

    clean = build_pipeline("4001", "NORTHFIELD", "Northfield Pipeline — Phase 2", healthy=True)
    write_xer(out_dir / "sample-clean.xer", tables_for([clean], finish_by))

    broken = build_pipeline("4002", "SOUTHGATE", "Southgate Pipeline — Phase 1", healthy=False)
    write_xer(out_dir / "sample-problems.xer", tables_for([broken], finish_by))

    multi_a = build_pipeline("5001", "ALPHA", "Alpha Compressor Station", healthy=True)
    multi_b = build_pipeline("5002", "BRAVO", "Bravo Metering Skid", healthy=True)
    write_xer(out_dir / "sample-multi.xer", tables_for([multi_a, multi_b], finish_by))

    # no data date: the date-dependent checks must abstain, not pass
    no_dates = build_pipeline("6001", "DELTA", "Delta Tank Farm", healthy=True)
    tables = tables_for([no_dates], None)
    tables[0][1][0]["last_recalc_date"] = None
    write_xer(out_dir / "sample-nodates.xer", tables)

    for path in sorted(out_dir.glob("sample-*.xer")):
        print(f"{path.name:>24}  {path.stat().st_size:>7,} bytes")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "sample_schedules"))
