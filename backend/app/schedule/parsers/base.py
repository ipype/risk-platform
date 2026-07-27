"""The parser seam.

Everything about *how* a schedule file is read lives behind this protocol. Downstream
code depends on :class:`~app.schedule.model.Schedule` and nothing else, which is what
keeps the MPXJ-vs-pure-Python question a reversible implementation detail instead of an
architectural commitment.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.schedule.model import Schedule


class ProjectSummary(Protocol):
    """Structural type for the lightweight project listing returned before a full parse."""

    id: str
    name: str
    activity_count: int


@runtime_checkable
class ScheduleParser(Protocol):
    """Reads one schedule file format into the canonical model.

    Implementations must be pure: bytes in, :class:`Schedule` out. No database access, no
    network, no filesystem writes. That keeps them property-testable and makes a parse
    reproducible from the stored source file alone.
    """

    #: Lowercase file suffixes this parser claims, including the dot.
    suffixes: tuple[str, ...]

    #: Human-readable name of the source tool, used in ``Schedule.source_format``.
    format_name: str

    def available(self) -> tuple[bool, str]:
        """Whether this parser can run here.

        Returns ``(True, "")`` when usable, or ``(False, reason)`` when the format is
        understood but its runtime dependency is absent — a missing JRE, for instance.
        Kept separate from suffix matching so the API can tell a user "we know what a
        ``.mpp`` is, this deployment just cannot open one" rather than "unsupported".
        """
        ...

    def list_projects(self, data: bytes) -> list[tuple[str, str, int]]:
        """Return ``(project_id, project_name, activity_count)`` for each project in the file.

        Export files routinely carry several projects plus their baselines. The caller
        uses this to disambiguate before committing to a parse.
        """
        ...

    def parse(
        self,
        data: bytes,
        *,
        filename: str | None = None,
        project_id: str | None = None,
    ) -> Schedule:
        """Parse ``data`` into the canonical model.

        Raises :class:`~app.core.errors.AmbiguousProjectError` when the file holds more
        than one project and ``project_id`` is not given. Guessing is not an option: the
        wrong project produces a complete and entirely wrong analysis.
        """
        ...
