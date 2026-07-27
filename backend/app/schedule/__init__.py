"""Schedule ingestion and quality assessment.

Pure domain code — no database, no network, no filesystem. Persistence and HTTP routes
sit above this package so that a parse is reproducible from the stored source bytes alone.
"""

from app.schedule.dcma import CheckStatus, DcmaCheck, DcmaReport, DcmaThresholds, run_dcma
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
    WorkingDuration,
)
from app.schedule.parsers import (
    list_projects,
    parse_schedule,
    parser_for,
    supported_formats,
)

__all__ = [
    "Activity",
    "ActivityStatus",
    "ActivityType",
    "CheckStatus",
    "ConstraintType",
    "DcmaCheck",
    "DcmaReport",
    "DcmaThresholds",
    "Relationship",
    "RelationshipType",
    "Schedule",
    "WbsNode",
    "WorkCalendar",
    "WorkingDuration",
    "list_projects",
    "parse_schedule",
    "parser_for",
    "run_dcma",
    "supported_formats",
]
