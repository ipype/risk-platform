"""The model seam.

Everything in this package is about *making a call*. Nothing in it knows what a risk is,
what a proposal is, or what the platform intends to do with the answer — that lives in
``app/agents/``, which is pure, and ``app/services/``, which touches the database. The
split is the same one ``app/sim/`` and ``services/sim_execute.py`` already draw, for the
same reason: the part that can fail because a network is down should not also be the part
that decides what a good answer looks like.
"""

from app.llm.registry import PROVIDERS, get_provider
from app.llm.types import ASSISTANT, SYSTEM, USER, Completion, Message, Provider

__all__ = [
    "ASSISTANT",
    "Completion",
    "Message",
    "PROVIDERS",
    "Provider",
    "SYSTEM",
    "USER",
    "get_provider",
]
