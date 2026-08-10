"""Structured records for escalation and fallback decisions (EGPT-3.5).

Epic 4 consumes these, so the payload is a plain dict of JSON-safe scalars rather than a log
string that a downstream parser would have to reverse-engineer. The record is attached to the
``logging.LogRecord`` as an extra field so a structured handler can pick it up, while the
default handler still prints a readable one-line message.

Nothing here imports a model, a provider or an SDK: an event is a description of a decision
that has already been made, and building one must stay as cheap as the decision itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from egpt.utils.log import log_info

__all__ = [
    "ESCALATION_LOG_FIELD",
    "EscalationEvent",
    "log_escalation_event",
]

#: Key under which the event dict is attached to the ``LogRecord``. A downstream structured
#: handler reads ``getattr(record, ESCALATION_LOG_FIELD)``; ``caplog`` reads it the same way.
ESCALATION_LOG_FIELD = "egpt_escalation"


@dataclass(frozen=True)
class EscalationEvent:
    """One escalation decision, tenant-attributed.

    Frozen for the same reason ``ModelResolveRequest`` is: an event is a fact about a decision
    already taken, and a consumer that could edit it would be editing history.

    ``escalated_from`` and ``target`` are provider *names*, not model ids: the decision this
    record describes is "which provider serves this request", and the model id is a
    consequence of it. ``model_id`` carries the consequence when it is known.
    """

    reason: str
    escalated_from: str
    target: str
    escalation_level: int
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    model_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe payload. Every value is a str, int or None."""
        return {
            "event": "model_escalation",
            "reason": self.reason,
            "escalated_from": self.escalated_from,
            "target": self.target,
            "escalation_level": self.escalation_level,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "model_id": self.model_id,
        }


def log_escalation_event(event: EscalationEvent) -> None:
    """Emit exactly one log record for an escalation.

    ``log_info`` rather than ``log_debug``: an escalation moves a request from the in-VPC tier
    to a metered frontier one, which an operator needs to see without turning debug on. The
    structured payload rides along in ``extra`` so a JSON handler gets the fields and the
    default handler still gets a sentence.
    """
    log_info(
        "Model escalation: %s -> %s (%s)" % (event.escalated_from, event.target, event.reason),
        extra={ESCALATION_LOG_FIELD: event.to_dict()},
    )
