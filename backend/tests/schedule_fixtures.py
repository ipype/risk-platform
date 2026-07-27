"""Builders for schedule test data.

Two kinds: a real XER text writer (so the parser is tested against the actual grammar
rather than a mock) and canonical-model constructors (so the DCMA checks can be driven
to exact percentages without hand-writing a hundred activity rows).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.schedule.model import (
    Activity,
    ActivityStatus,
    ActivityType,
    ConstraintType,
    Relationship,
    RelationshipType,
    Schedule,
    WorkCalendar,
    WorkingDuration,
)

_OLE_EPOCH = date(1899, 12, 30)

DEFAULT_CALENDAR = WorkCalendar(
    id="CAL-1", name="Standard 5x8", hours_per_day=8.0, is_default=True
)


# --------------------------------------------------------------------------- #
# XER text
# --------------------------------------------------------------------------- #


def ole_serial(day: date) -> int:
    return (day - _OLE_EPOCH).days


def calendar_blob(
    workday_numbers: set[int] | None = None, holidays: list[date] | None = None
) -> str:
    """Build a P6 ``clndr_data`` blob. Day numbers are P6's 1=Sunday..7=Saturday."""
    workday_numbers = workday_numbers if workday_numbers is not None else {2, 3, 4, 5, 6}
    days = "".join(
        f"(0||{n}()({'(0||0(s|08:00|f|17:00)())' if n in workday_numbers else ''}))"
        for n in range(1, 8)
    )
    exceptions = "".join(f"(0||0(d|{ole_serial(d)})())" for d in (holidays or []))
    return f"(0||CalendarData()((0||DaysOfWeek()({days}))(0||Exceptions()({exceptions}))))"


def xer_table(name: str, rows: list[dict[str, str]]) -> str:
    if not rows:
        return f"%T\t{name}\n"
    fields = list(rows[0])
    lines = [f"%T\t{name}", "%F\t" + "\t".join(fields)]
    lines += ["%R\t" + "\t".join(str(row.get(f, "")) for f in fields) for row in rows]
    return "\n".join(lines) + "\n"


def xer_document(tables: list[tuple[str, list[dict[str, str]]]], code_page: str = "CP1252") -> bytes:
    header = (
        "ERMHDR\t19.12\t2026-07-26\tProject\tadmin\tAdmin\t"
        f"dbxDatabaseNoName\tProject\t{code_page}\n"
    )
    body = "".join(xer_table(name, rows) for name, rows in tables)
    return (header + body + "%E\n").encode("cp1252")


def simple_xer(
    *,
    extra_projects: bool = False,
    holidays: list[date] | None = None,
    code_page: str = "CP1252",
) -> bytes:
    """A small but structurally complete two-week pipeline schedule.

    Four activities: a start milestone, two tasks (one complete, one in progress) and a
    finish milestone, wired FS with one SS relationship carrying a two-day lag.
    """
    projects = [
        {
            "proj_id": "1001",
            "proj_short_name": "PIPE-A",
            "clndr_id": "CAL-1",
            "last_recalc_date": "2026-06-01 08:00",
            "plan_start_date": "2026-05-04 08:00",
            "plan_end_date": "2026-07-10 17:00",
        }
    ]
    tasks = [
        {
            "task_id": "T1",
            "proj_id": "1001",
            "wbs_id": "W1",
            "clndr_id": "CAL-1",
            "task_code": "A1000",
            "task_name": "Notice to proceed",
            "task_type": "TT_Mile",
            "status_code": "TK_Complete",
            "target_drtn_hr_cnt": "0",
            "remain_drtn_hr_cnt": "0",
            "total_float_hr_cnt": "0",
            "free_float_hr_cnt": "0",
            "early_start_date": "2026-05-04 08:00",
            "early_end_date": "2026-05-04 08:00",
            "late_start_date": "2026-05-04 08:00",
            "late_end_date": "2026-05-04 08:00",
            "target_start_date": "2026-05-04 08:00",
            "target_end_date": "2026-05-04 08:00",
            "act_start_date": "2026-05-04 08:00",
            "act_end_date": "2026-05-04 08:00",
            "cstr_type": "",
            "cstr_date": "",
            "cstr_type2": "",
            "cstr_date2": "",
            "driving_path_flag": "Y",
        },
        {
            "task_id": "T2",
            "proj_id": "1001",
            "wbs_id": "W1",
            "clndr_id": "CAL-1",
            "task_code": "A1010",
            "task_name": "Site clearance",
            "task_type": "TT_Task",
            "status_code": "TK_Complete",
            "target_drtn_hr_cnt": "80",
            "remain_drtn_hr_cnt": "0",
            "total_float_hr_cnt": "0",
            "free_float_hr_cnt": "0",
            "early_start_date": "2026-05-04 08:00",
            "early_end_date": "2026-05-15 17:00",
            "late_start_date": "2026-05-04 08:00",
            "late_end_date": "2026-05-15 17:00",
            "target_start_date": "2026-05-04 08:00",
            "target_end_date": "2026-05-15 17:00",
            "act_start_date": "2026-05-04 08:00",
            "act_end_date": "2026-05-15 17:00",
            "cstr_type": "",
            "cstr_date": "",
            "cstr_type2": "",
            "cstr_date2": "",
            "driving_path_flag": "Y",
        },
        {
            "task_id": "T3",
            "proj_id": "1001",
            "wbs_id": "W1",
            "clndr_id": "CAL-2",
            "task_code": "A1020",
            "task_name": "Pipe laying",
            "task_type": "TT_Task",
            "status_code": "TK_Active",
            "target_drtn_hr_cnt": "240",
            "remain_drtn_hr_cnt": "120",
            "total_float_hr_cnt": "-40",
            "free_float_hr_cnt": "0",
            "early_start_date": "2026-05-18 08:00",
            "early_end_date": "2026-07-03 17:00",
            "late_start_date": "2026-05-18 08:00",
            "late_end_date": "2026-06-26 17:00",
            "target_start_date": "2026-05-18 08:00",
            "target_end_date": "2026-06-19 17:00",
            "act_start_date": "2026-05-18 08:00",
            "act_end_date": "",
            "cstr_type": "CS_MSO",
            "cstr_date": "2026-05-18 08:00",
            "cstr_type2": "",
            "cstr_date2": "",
            "driving_path_flag": "Y",
        },
        {
            "task_id": "T4",
            "proj_id": "1001",
            "wbs_id": "W1",
            "clndr_id": "CAL-1",
            "task_code": "A1030",
            "task_name": "Mechanical completion",
            "task_type": "TT_FinMile",
            "status_code": "TK_NotStart",
            "target_drtn_hr_cnt": "0",
            "remain_drtn_hr_cnt": "0",
            "total_float_hr_cnt": "-40",
            "free_float_hr_cnt": "0",
            "early_start_date": "2026-07-03 17:00",
            "early_end_date": "2026-07-03 17:00",
            "late_start_date": "2026-06-26 17:00",
            "late_end_date": "2026-06-26 17:00",
            "target_start_date": "2026-06-19 17:00",
            "target_end_date": "2026-06-19 17:00",
            "act_start_date": "",
            "act_end_date": "",
            "cstr_type": "",
            "cstr_date": "",
            "cstr_type2": "",
            "cstr_date2": "",
            "driving_path_flag": "Y",
        },
    ]
    preds = [
        {
            "task_pred_id": "R1",
            "task_id": "T2",
            "pred_task_id": "T1",
            "proj_id": "1001",
            "pred_proj_id": "1001",
            "pred_type": "PR_FS",
            "lag_hr_cnt": "0",
        },
        {
            "task_pred_id": "R2",
            "task_id": "T3",
            "pred_task_id": "T2",
            "proj_id": "1001",
            "pred_proj_id": "1001",
            "pred_type": "PR_SS",
            "lag_hr_cnt": "16",
        },
        {
            "task_pred_id": "R3",
            "task_id": "T4",
            "pred_task_id": "T3",
            "proj_id": "1001",
            "pred_proj_id": "1001",
            "pred_type": "PR_FS",
            "lag_hr_cnt": "-8",
        },
        {
            # points at an activity in another project — must be dropped with a warning
            "task_pred_id": "R4",
            "task_id": "T3",
            "pred_task_id": "X9",
            "proj_id": "1001",
            "pred_proj_id": "2002",
            "pred_type": "PR_FS",
            "lag_hr_cnt": "0",
        },
    ]

    if extra_projects:
        projects.append(
            {
                "proj_id": "2002",
                "proj_short_name": "PIPE-B",
                "clndr_id": "CAL-1",
                "last_recalc_date": "2026-06-01 08:00",
                "plan_start_date": "2026-05-04 08:00",
                "plan_end_date": "2026-09-30 17:00",
            }
        )
        tasks.append({**tasks[1], "task_id": "X9", "proj_id": "2002", "task_code": "B1000"})

    return xer_document(
        [
            ("PROJECT", projects),
            (
                "CALENDAR",
                [
                    {
                        "clndr_id": "CAL-1",
                        "clndr_name": "Standard 5x8",
                        "day_hr_cnt": "8",
                        "default_flag": "Y",
                        "clndr_data": calendar_blob(holidays=holidays),
                    },
                    {
                        "clndr_id": "CAL-2",
                        "clndr_name": "Six-day 10h",
                        "day_hr_cnt": "10",
                        "default_flag": "N",
                        "clndr_data": calendar_blob(workday_numbers={2, 3, 4, 5, 6, 7}),
                    },
                ],
            ),
            (
                "PROJWBS",
                [
                    {
                        "wbs_id": "W0",
                        "proj_id": "1001",
                        "wbs_short_name": "PIPE-A",
                        "wbs_name": "Pipeline A — Phase 1",
                        "parent_wbs_id": "",
                        "proj_node_flag": "Y",
                    },
                    {
                        "wbs_id": "W1",
                        "proj_id": "1001",
                        "wbs_short_name": "CIV",
                        "wbs_name": "Civil works",
                        "parent_wbs_id": "W0",
                        "proj_node_flag": "N",
                    },
                ],
            ),
            ("TASK", tasks),
            ("TASKPRED", preds),
            (
                "TASKRSRC",
                [
                    {
                        "taskrsrc_id": "TR1",
                        "task_id": "T3",
                        "proj_id": "1001",
                        "rsrc_id": "R100",
                        "target_cost": "125000.50",
                    }
                ],
            ),
        ],
        code_page=code_page,
    )


# --------------------------------------------------------------------------- #
# canonical model
# --------------------------------------------------------------------------- #


def days(value: float, calendar_id: str = "CAL-1") -> WorkingDuration:
    return WorkingDuration(days=value, calendar_id=calendar_id)


def make_activity(
    index: int,
    *,
    status: ActivityStatus = ActivityStatus.NOT_STARTED,
    type: ActivityType = ActivityType.TASK,
    total_float: float | None = 5.0,
    remaining_duration: float | None = 10.0,
    constraint: ConstraintType = ConstraintType.NONE,
    early_start: datetime | None = None,
    early_finish: datetime | None = None,
    baseline_finish: datetime | None = None,
    actual_start: datetime | None = None,
    actual_finish: datetime | None = None,
    has_resource_assignment: bool = False,
) -> Activity:
    base = datetime(2026, 6, 1, 8, 0)
    return Activity(
        id=f"T{index}",
        code=f"A{1000 + index * 10}",
        name=f"Activity {index}",
        calendar_id="CAL-1",
        type=type,
        status=status,
        original_duration=days(remaining_duration) if remaining_duration is not None else None,
        remaining_duration=days(remaining_duration) if remaining_duration is not None else None,
        total_float=days(total_float) if total_float is not None else None,
        free_float=days(0),
        early_start=early_start if early_start is not None else base + timedelta(days=index),
        early_finish=early_finish
        if early_finish is not None
        else base + timedelta(days=index + 10),
        baseline_finish=baseline_finish,
        actual_start=actual_start,
        actual_finish=actual_finish,
        constraint_type=constraint,
        has_resource_assignment=has_resource_assignment,
    )


def chain(activities: list[Activity]) -> list[Relationship]:
    """Wire activities into a single FS chain so logic checks pass by default."""
    return [
        Relationship(
            id=f"R{i}",
            predecessor_id=activities[i].id,
            successor_id=activities[i + 1].id,
            type=RelationshipType.FS,
            lag=days(0),
        )
        for i in range(len(activities) - 1)
    ]


def make_schedule(
    activities: list[Activity],
    relationships: list[Relationship] | None = None,
    *,
    data_date: datetime | None = datetime(2026, 6, 1, 8, 0),
    must_finish_by: datetime | None = None,
) -> Schedule:
    return Schedule(
        project_id="1001",
        project_name="Test project",
        data_date=data_date,
        must_finish_by=must_finish_by,
        calendars=(DEFAULT_CALENDAR,),
        activities=tuple(activities),
        relationships=tuple(relationships if relationships is not None else chain(activities)),
    )
