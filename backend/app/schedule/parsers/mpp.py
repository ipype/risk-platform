"""MS Project ``.mpp`` reader — MPXJ via JPype, off by default.

``.mpp`` is a compiled OLE compound document, not a text format, so there is no
pure-Python route to it. MPXJ handles it, but MPXJ is Java: enabling this parser means
a JRE in the API image (roughly +180 MB) and a JPype bridge that must be initialised
once per process and is not fork-safe.

That is a deployment decision, not a parsing decision, and ``BACKLOG.md`` still lists the
deployment target as open. So the parser is registered — the API can say precisely why a
``.mpp`` was rejected instead of calling it unsupported — but it does not import ``jpype``
unless someone sets ``SCHEDULE_ENABLE_MPXJ=1``.

To turn it on later:

1. ``JAVA_HOME`` + a JRE in ``backend/Dockerfile``
2. ``mpxj`` and ``JPype1`` in ``requirements.txt``
3. ``SCHEDULE_ENABLE_MPXJ=1``
4. implement :meth:`MppParser._parse_with_mpxj`

Nothing downstream changes: the canonical model is the contract.
"""

from __future__ import annotations

import os
from importlib.util import find_spec

from app.core.errors import ParserUnavailable
from app.schedule.model import Schedule

_ENABLE_FLAG = "SCHEDULE_ENABLE_MPXJ"


class MppParser:
    """Placeholder for the MPXJ-backed reader.

    Deliberately fails loudly and specifically. A parser that silently returns an empty
    schedule is worse than no parser at all — it would sail through the DCMA gate with
    zero offenders and produce a confident simulation of nothing.
    """

    suffixes: tuple[str, ...] = (".mpp",)
    format_name: str = "MS Project MPP (MPXJ)"

    def available(self) -> tuple[bool, str]:
        if os.getenv(_ENABLE_FLAG, "").strip().lower() not in ("1", "true", "yes"):
            return False, (
                "MPXJ is disabled in this deployment. .mpp requires a JRE in the API "
                f"image; set {_ENABLE_FLAG}=1 once that is in place. Export the schedule "
                "as .xer or Project XML in the meantime."
            )
        if find_spec("jpype") is None or find_spec("mpxj") is None:
            return False, (
                f"{_ENABLE_FLAG} is set but the 'mpxj' and 'JPype1' packages are not "
                "installed in this image."
            )
        return True, ""

    def _unavailable(self) -> ParserUnavailable:
        return ParserUnavailable(".mpp", self.available()[1])

    def list_projects(self, data: bytes) -> list[tuple[str, str, int]]:
        ok, _ = self.available()
        if not ok:
            raise self._unavailable()
        return self._parse_with_mpxj(data, project_id=None, filename=None)[1]

    def parse(
        self,
        data: bytes,
        *,
        filename: str | None = None,
        project_id: str | None = None,
    ) -> Schedule:
        ok, _ = self.available()
        if not ok:
            raise self._unavailable()
        return self._parse_with_mpxj(data, project_id=project_id, filename=filename)[0]

    def _parse_with_mpxj(
        self,
        data: bytes,
        *,
        project_id: str | None,
        filename: str | None,
    ) -> tuple[Schedule, list[tuple[str, str, int]]]:
        raise NotImplementedError(
            "MPXJ ingestion is not implemented yet. See the module docstring for the "
            "four steps required to enable it."
        )
