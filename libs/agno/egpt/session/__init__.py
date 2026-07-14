from typing import Union

from egpt.session.agent import AgentSession
from egpt.session.summary import SessionSummaryManager
from egpt.session.team import TeamSession
from egpt.session.workflow import WorkflowSession

Session = Union[AgentSession, TeamSession, WorkflowSession]

__all__ = ["AgentSession", "TeamSession", "WorkflowSession", "Session", "SessionSummaryManager"]
