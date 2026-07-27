"""Primavera P6 ``.xer`` reader — pure Python, no JVM.

The XER format is a tab-delimited table dump. Each table is announced by a ``%T`` line,
its columns by ``%F``, and every subsequent ``%R`` line is a row. That is the entire
grammar, which is why this is worth doing directly rather than dragging a JRE into the
API image for it.

Scope note: ``.mpp`` genuinely does need MPXJ (it is a compiled OLE compound document,
not a text format). It lives behind the same :class:`~app.schedule.parsers.base.ScheduleParser`
protocol in ``mpp.py`` and can be switched on without touching anything downstream.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.core.errors import (
    AmbiguousProjectError,
    MalformedScheduleFile,
    ProjectNotFound,
)
from app.schedule.model import (
    Activity,
    ActivityStatus,
    ActivityType,
    ConstraintType,
    Relationship,
    RelationshipType,
    Schedule,
    WbsNode,
    WorkCalendar,
)

# P6 serialises exception dates as days since this epoch (the Excel/OLE convention).
_OLE_EPOCH = date(1899, 12, 30)

# P6 numbers weekdays 1=Sunday..7=Saturday; Python uses 0=Monday..6=Sunday.
_P6_DAY_TO_PYTHON = {1: 6, 2: 0, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5}

_ACTIVITY_TYPES = {
    "TT_Task": ActivityType.TASK,
    "TT_Rsrc": ActivityType.RESOURCE_DEPENDENT,
    "TT_LOE": ActivityType.LEVEL_OF_EFFORT,
    "TT_Mile": ActivityType.START_MILESTONE,
    "TT_FinMile": ActivityType.FINISH_MILESTONE,
    "TT_WBS": ActivityType.WBS_SUMMARY,
}

_STATUSES = {
    "TK_NotStart": ActivityStatus.NOT_STARTED,
    "TK_Active": ActivityStatus.IN_PROGRESS,
    "TK_Complete": ActivityStatus.COMPLETED,
}

_RELATIONSHIP_TYPES = {
    "PR_FS": RelationshipType.FS,
    "PR_SS": RelationshipType.SS,
    "PR_FF": RelationshipType.FF,
    "PR_SF": RelationshipType.SF,
}

_CONSTRAINTS = {
    "": ConstraintType.NONE,
    "CS_ALAP": ConstraintType.AS_LATE_AS_POSSIBLE,
    "CS_MSO": ConstraintType.START_ON,
    "CS_MSOA": ConstraintType.START_ON_OR_AFTER,
    "CS_MSOB": ConstraintType.START_ON_OR_BEFORE,
    "CS_MEO": ConstraintType.FINISH_ON,
    "CS_MEOA": ConstraintType.FINISH_ON_OR_AFTER,
    "CS_MEOB": ConstraintType.FINISH_ON_OR_BEFORE,
    "CS_MANDSTART": ConstraintType.MANDATORY_START,
    "CS_MANDFIN": ConstraintType.MANDATORY_FINISH,
}

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


# --------------------------------------------------------------------------- #
# low-level readers
# --------------------------------------------------------------------------- #


def _decode(data: bytes) -> tuple[str, list[str]]:
    """Decode XER bytes, honouring the code page declared in the ``ERMHDR`` line."""
    warnings: list[str] = []
    head = data[:512].decode("latin-1", errors="replace")
    first_line = head.split("\n", 1)[0]

    encoding = "cp1252"
    upper = first_line.upper()
    if "UTF-8" in upper or "UTF8" in upper:
        encoding = "utf-8"
    elif "1252" in upper or "ASCII" in upper or "ANSI" in upper:
        encoding = "cp1252"

    try:
        return data.decode(encoding), warnings
    except UnicodeDecodeError:
        warnings.append(
            f"File declared encoding {encoding!r} but does not decode cleanly; "
            "unreadable characters were replaced. Activity names may be corrupted."
        )
        return data.decode(encoding, errors="replace"), warnings


def _read_tables(text: str) -> dict[str, list[dict[str, str]]]:
    """Split the XER body into ``{table_name: [row_dict, ...]}``."""
    tables: dict[str, list[dict[str, str]]] = {}
    current: str | None = None
    fields: list[str] = []

    for raw_line in text.splitlines():
        if not raw_line:
            continue
        parts = raw_line.split("\t")
        tag = parts[0]

        if tag == "%T":
            current = parts[1] if len(parts) > 1 else None
            fields = []
            if current:
                tables.setdefault(current, [])
        elif tag == "%F":
            fields = parts[1:]
        elif tag == "%R":
            if current is None or not fields:
                continue
            values = parts[1:]
            # trailing empty columns are frequently omitted by the exporter
            if len(values) < len(fields):
                values += [""] * (len(fields) - len(values))
            tables[current].append(dict(zip(fields, values, strict=False)))
        elif tag == "%E":
            break

    return tables


def _text(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def _number(row: dict[str, str], key: str) -> float | None:
    raw = _text(row, key)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _timestamp(row: dict[str, str], key: str) -> datetime | None:
    raw = _text(row, key)
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _extract_block(text: str, open_index: int) -> str:
    """Return the parenthesis-balanced substring beginning at ``open_index``."""
    depth = 0
    for i in range(open_index, len(text)):
        char = text[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_index : i + 1]
    return text[open_index:]


def _parse_calendar_data(blob: str) -> tuple[frozenset[int], frozenset[date], frozenset[date]]:
    """Pull the workweek and exception dates out of P6's ``clndr_data`` blob.

    The blob is a nested s-expression-ish structure. A weekday with no ``s|`` (start
    time) token has no working hours, so it is not a workday. Exceptions carrying an
    ``s|`` are working exceptions; the rest are holidays.

    Degrades to a standard Monday–Friday week if the blob is missing or unparseable —
    that is a warning, not a failure, because a wrong-but-flagged calendar is far more
    useful than a rejected file.
    """
    workdays: set[int] = set()
    holidays: set[date] = set()
    extra_workdays: set[date] = set()

    week_match = re.search(r"\(0\|\|DaysOfWeek\(\)", blob)
    if week_match:
        week_block = _extract_block(blob, week_match.start())
        for day_match in re.finditer(r"\(0\|\|([1-7])\(\)", week_block):
            day_block = _extract_block(week_block, day_match.start())
            if "s|" in day_block:
                workdays.add(_P6_DAY_TO_PYTHON[int(day_match.group(1))])

    exception_match = re.search(r"\(0\|\|Exceptions\(\)", blob)
    if exception_match:
        exception_block = _extract_block(blob, exception_match.start())
        for entry in re.finditer(r"\(0\|\|\d+\(d\|(\d+)\)", exception_block):
            day = _OLE_EPOCH + timedelta(days=int(entry.group(1)))
            block = _extract_block(exception_block, entry.start())
            if "s|" in block:
                extra_workdays.add(day)
            else:
                holidays.add(day)

    return frozenset(workdays), frozenset(holidays), frozenset(extra_workdays)


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


class XerParser:
    """Reads Primavera P6 ``.xer`` exports into the canonical schedule model."""

    suffixes: tuple[str, ...] = (".xer",)
    format_name: str = "Primavera P6 XER"

    def available(self) -> tuple[bool, str]:
        return True, ""

    # -- discovery -------------------------------------------------------------

    def list_projects(self, data: bytes) -> list[tuple[str, str, int]]:
        text, _ = _decode(data)
        tables = _read_tables(text)
        return self._project_candidates(tables)

    @staticmethod
    def _project_candidates(
        tables: dict[str, list[dict[str, str]]],
    ) -> list[tuple[str, str, int]]:
        counts: dict[str, int] = {}
        for row in tables.get("TASK", []):
            proj_id = _text(row, "proj_id")
            counts[proj_id] = counts.get(proj_id, 0) + 1

        names: dict[str, str] = {}
        for row in tables.get("PROJWBS", []):
            if _text(row, "proj_node_flag") == "Y":
                names[_text(row, "proj_id")] = _text(row, "wbs_name")
        for row in tables.get("PROJECT", []):
            proj_id = _text(row, "proj_id")
            names.setdefault(proj_id, _text(row, "proj_short_name") or proj_id)

        project_ids = [_text(r, "proj_id") for r in tables.get("PROJECT", [])]
        for proj_id in counts:
            if proj_id not in project_ids:
                project_ids.append(proj_id)

        return [
            (pid, names.get(pid, pid), counts.get(pid, 0))
            for pid in project_ids
            if pid
        ]

    # -- parse -----------------------------------------------------------------

    def parse(
        self,
        data: bytes,
        *,
        filename: str | None = None,
        project_id: str | None = None,
    ) -> Schedule:
        text, warnings = _decode(data)
        tables = _read_tables(text)

        if "TASK" not in tables:
            raise MalformedScheduleFile(
                "No TASK table found. This does not look like a P6 XER export."
            )

        candidates = self._project_candidates(tables)
        if not candidates:
            raise MalformedScheduleFile("File contains no projects.")

        if project_id is None:
            with_activities = [c for c in candidates if c[2] > 0]
            if len(with_activities) == 1:
                project_id = with_activities[0][0]
            elif len(candidates) == 1:
                project_id = candidates[0][0]
            else:
                raise AmbiguousProjectError(candidates)
        elif project_id not in {c[0] for c in candidates}:
            raise ProjectNotFound(project_id, [c[0] for c in candidates])

        project_row = next(
            (r for r in tables.get("PROJECT", []) if _text(r, "proj_id") == project_id),
            {},
        )

        calendars, calendar_warnings = self._build_calendars(tables, project_row)
        warnings.extend(calendar_warnings)
        calendar_by_id = {cal.id: cal for cal in calendars}
        default_calendar = next(
            (c for c in calendars if c.is_default), calendars[0] if calendars else None
        )
        if default_calendar is None:
            default_calendar = WorkCalendar(
                id="__implied__", name="Implied 5x8", is_default=True
            )
            calendars = (default_calendar,)
            calendar_by_id = {default_calendar.id: default_calendar}
            warnings.append(
                "No CALENDAR table in the file; assumed a Monday–Friday 8h/day calendar. "
                "Every duration in working days is derived from that assumption."
            )

        wbs = self._build_wbs(tables, project_id)
        resourced, costs = self._build_resource_index(tables, project_id)

        activities, activity_warnings = self._build_activities(
            tables,
            project_id=project_id,
            calendar_by_id=calendar_by_id,
            default_calendar=default_calendar,
            resourced=resourced,
            costs=costs,
        )
        warnings.extend(activity_warnings)

        activity_ids = {a.id for a in activities}
        activity_calendars = {a.id: a.calendar_id for a in activities}
        relationships, relationship_warnings = self._build_relationships(
            tables,
            activity_ids=activity_ids,
            activity_calendars=activity_calendars,
            calendar_by_id=calendar_by_id,
            default_calendar=default_calendar,
        )
        warnings.extend(relationship_warnings)

        project_name = next(
            (
                _text(r, "wbs_name")
                for r in tables.get("PROJWBS", [])
                if _text(r, "proj_id") == project_id and _text(r, "proj_node_flag") == "Y"
            ),
            _text(project_row, "proj_short_name") or project_id,
        )

        data_date = _timestamp(project_row, "last_recalc_date")
        if data_date is None:
            warnings.append(
                "No data date (last_recalc_date) in the file. DCMA checks 9, 11, 13 and "
                "14 cannot be evaluated and will report as not assessed."
            )

        baseline_finishes = [a.baseline_finish for a in activities if a.baseline_finish]
        baseline_finish = max(baseline_finishes) if baseline_finishes else None
        if baseline_finish is not None:
            warnings.append(
                "Baseline finish was derived from the planned dates in this file, not "
                "from a separate baseline export. Upload the baseline XER for a true "
                "BEI and missed-task comparison."
            )

        return Schedule(
            project_id=project_id,
            project_name=project_name,
            data_date=data_date,
            baseline_finish=baseline_finish,
            must_finish_by=_timestamp(project_row, "plan_end_date"),
            source_format=self.format_name,
            source_filename=filename,
            calendars=tuple(calendars),
            wbs=tuple(wbs),
            activities=tuple(activities),
            relationships=tuple(relationships),
            warnings=tuple(warnings),
        )

    # -- builders --------------------------------------------------------------

    @staticmethod
    def _build_calendars(
        tables: dict[str, list[dict[str, str]]],
        project_row: dict[str, str],
    ) -> tuple[tuple[WorkCalendar, ...], list[str]]:
        warnings: list[str] = []
        project_calendar_id = _text(project_row, "clndr_id")
        calendars: list[WorkCalendar] = []

        for row in tables.get("CALENDAR", []):
            calendar_id = _text(row, "clndr_id")
            if not calendar_id:
                continue
            hours_per_day = _number(row, "day_hr_cnt") or 8.0
            workdays, holidays, extra = _parse_calendar_data(_text(row, "clndr_data"))
            if not workdays:
                workdays = frozenset({0, 1, 2, 3, 4})
                warnings.append(
                    f"Calendar {calendar_id} ({_text(row, 'clndr_name') or 'unnamed'}) "
                    "has no readable workweek; assumed Monday–Friday."
                )
            calendars.append(
                WorkCalendar(
                    id=calendar_id,
                    name=_text(row, "clndr_name") or calendar_id,
                    hours_per_day=hours_per_day,
                    workdays=workdays,
                    holidays=holidays,
                    extra_workdays=extra,
                    is_default=(
                        calendar_id == project_calendar_id
                        or _text(row, "default_flag") == "Y"
                    ),
                )
            )

        if calendars and not any(c.is_default for c in calendars):
            first = calendars[0]
            calendars[0] = first.model_copy(update={"is_default": True})
            warnings.append(
                f"No default calendar flagged; using {first.name!r} as the project default."
            )

        return tuple(calendars), warnings

    @staticmethod
    def _build_wbs(
        tables: dict[str, list[dict[str, str]]], project_id: str
    ) -> list[WbsNode]:
        nodes: list[WbsNode] = []
        for row in tables.get("PROJWBS", []):
            if _text(row, "proj_id") != project_id:
                continue
            nodes.append(
                WbsNode(
                    id=_text(row, "wbs_id"),
                    code=_text(row, "wbs_short_name"),
                    name=_text(row, "wbs_name"),
                    parent_id=_text(row, "parent_wbs_id") or None,
                    is_project_node=_text(row, "proj_node_flag") == "Y",
                )
            )
        return nodes

    @staticmethod
    def _build_resource_index(
        tables: dict[str, list[dict[str, str]]], project_id: str
    ) -> tuple[set[str], dict[str, int]]:
        """Which activities carry a resource assignment, and their budgeted cost.

        Cost is stored in minor units (cents) as an integer — never a float. DCMA check
        10 needs the assignment flag; the cost feeds the eventual contingency work.
        """
        resourced: set[str] = set()
        costs: dict[str, int] = {}
        for row in tables.get("TASKRSRC", []):
            if _text(row, "proj_id") not in ("", project_id):
                continue
            task_id = _text(row, "task_id")
            if not task_id:
                continue
            resourced.add(task_id)
            amount = _number(row, "target_cost")
            if amount is not None:
                costs[task_id] = costs.get(task_id, 0) + int(round(amount * 100))
        return resourced, costs

    @staticmethod
    def _build_activities(
        tables: dict[str, list[dict[str, str]]],
        *,
        project_id: str,
        calendar_by_id: dict[str, WorkCalendar],
        default_calendar: WorkCalendar,
        resourced: set[str],
        costs: dict[str, int],
    ) -> tuple[list[Activity], list[str]]:
        warnings: list[str] = []
        missing_calendars: set[str] = set()
        activities: list[Activity] = []

        for row in tables.get("TASK", []):
            if _text(row, "proj_id") != project_id:
                continue

            task_id = _text(row, "task_id")
            calendar_id = _text(row, "clndr_id")
            calendar = calendar_by_id.get(calendar_id)
            if calendar is None:
                if calendar_id:
                    missing_calendars.add(calendar_id)
                calendar = default_calendar
                calendar_id = default_calendar.id

            raw_type = _text(row, "task_type")
            raw_status = _text(row, "status_code")

            activities.append(
                Activity(
                    id=task_id,
                    code=_text(row, "task_code") or task_id,
                    name=_text(row, "task_name"),
                    calendar_id=calendar_id,
                    wbs_id=_text(row, "wbs_id") or None,
                    type=_ACTIVITY_TYPES.get(raw_type, ActivityType.TASK),
                    status=_STATUSES.get(raw_status, ActivityStatus.NOT_STARTED),
                    original_duration=calendar.hours_to_days(
                        _number(row, "target_drtn_hr_cnt")
                    ),
                    remaining_duration=calendar.hours_to_days(
                        _number(row, "remain_drtn_hr_cnt")
                    ),
                    total_float=calendar.hours_to_days(_number(row, "total_float_hr_cnt")),
                    free_float=calendar.hours_to_days(_number(row, "free_float_hr_cnt")),
                    early_start=_timestamp(row, "early_start_date"),
                    early_finish=_timestamp(row, "early_end_date"),
                    late_start=_timestamp(row, "late_start_date"),
                    late_finish=_timestamp(row, "late_end_date"),
                    baseline_start=_timestamp(row, "target_start_date"),
                    baseline_finish=_timestamp(row, "target_end_date"),
                    actual_start=_timestamp(row, "act_start_date"),
                    actual_finish=_timestamp(row, "act_end_date"),
                    constraint_type=_CONSTRAINTS.get(
                        _text(row, "cstr_type"), ConstraintType.NONE
                    ),
                    constraint_date=_timestamp(row, "cstr_date"),
                    secondary_constraint_type=_CONSTRAINTS.get(
                        _text(row, "cstr_type2"), ConstraintType.NONE
                    ),
                    secondary_constraint_date=_timestamp(row, "cstr_date2"),
                    is_critical=_text(row, "driving_path_flag") == "Y",
                    has_resource_assignment=task_id in resourced,
                    budgeted_cost=costs.get(task_id),
                )
            )

        if missing_calendars:
            warnings.append(
                f"{len(missing_calendars)} calendar id(s) referenced by activities are "
                f"absent from the CALENDAR table; those activities fall back to "
                f"{default_calendar.name!r}. Their durations in working days are approximate."
            )

        return activities, warnings

    @staticmethod
    def _build_relationships(
        tables: dict[str, list[dict[str, str]]],
        *,
        activity_ids: set[str],
        activity_calendars: dict[str, str],
        calendar_by_id: dict[str, WorkCalendar],
        default_calendar: WorkCalendar,
    ) -> tuple[list[Relationship], list[str]]:
        warnings: list[str] = []
        external = 0
        relationships: list[Relationship] = []

        for row in tables.get("TASKPRED", []):
            successor_id = _text(row, "task_id")
            predecessor_id = _text(row, "pred_task_id")

            if successor_id not in activity_ids or predecessor_id not in activity_ids:
                external += 1
                continue

            # P6 measures lag on the successor's calendar unless the project says
            # otherwise; that project setting is not carried in the XER, so this is the
            # documented assumption rather than a silent one.
            calendar = calendar_by_id.get(
                activity_calendars.get(successor_id, ""), default_calendar
            )
            relationships.append(
                Relationship(
                    id=_text(row, "task_pred_id")
                    or f"{predecessor_id}->{successor_id}",
                    predecessor_id=predecessor_id,
                    successor_id=successor_id,
                    type=_RELATIONSHIP_TYPES.get(
                        _text(row, "pred_type"), RelationshipType.FS
                    ),
                    lag=calendar.hours_to_days(_number(row, "lag_hr_cnt")),
                )
            )

        if external:
            warnings.append(
                f"Dropped {external} relationship(s) pointing outside this project. "
                "External logic is invisible to the analysis — if these drive the "
                "schedule, export the projects together."
            )

        return relationships, warnings
