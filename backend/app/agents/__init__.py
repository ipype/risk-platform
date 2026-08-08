"""What a generator asks a model, and what it will accept as an answer.

Pure, on the same terms as ``app/sim/`` and ``app/ingest/``: no database, no network, no
logging, no clock, no randomness. It may import ``app.core.errors`` and nothing else from
the application.

The boundary is worth more here than anywhere else in P5. The claims this platform makes
about its AI features — that a suggestion cites evidence it was actually shown, that an
unevidenced suggestion never reaches a reviewer, that a repeat of yesterday's finding is
recognised as one — are all decided by code in this package, and all of it is testable with
a string and a frozen dataclass. The moment prompt construction and response admission need
a session to exercise, they stop being properties anyone verifies and become behaviour
people hope for.
"""

from app.agents import dedupe, qual_eval, risk_id, types

__all__ = ["dedupe", "qual_eval", "risk_id", "types"]
