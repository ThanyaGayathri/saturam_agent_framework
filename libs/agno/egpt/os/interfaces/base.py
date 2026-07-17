from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

from fastapi import APIRouter

from egpt.agent import Agent, RemoteAgent
from egpt.team import RemoteTeam, Team
from egpt.workflow import RemoteWorkflow, Workflow


class BaseInterface(ABC):
    type: str
    version: str = "1.0"
    agent: Optional[Union[Agent, RemoteAgent]] = None
    team: Optional[Union[Team, RemoteTeam]] = None
    workflow: Optional[Union[Workflow, RemoteWorkflow]] = None

    prefix: str
    tags: List[str]

    # Whether this interface verifies the authenticity of its own inbound requests
    # (e.g. Slack/Telegram/WhatsApp verify a signing secret in their routers). When
    # True, EgptOS excludes the interface's route prefix from the central auth layer,
    # since the interface authenticates callers itself. Interfaces that do NOT
    # self-authenticate (the default) stay behind AuthMiddleware and require a valid
    # EgptOS credential once authentication is enabled -- see
    # EgptOS._add_auth_middleware.
    authenticates_own_requests: bool = False

    router: APIRouter

    @abstractmethod
    def get_router(self, use_async: bool = True, **kwargs) -> APIRouter:
        pass

    def get_scope_mappings(self) -> Dict[str, List[str]]:
        """RBAC scope requirements for this interface's routes, keyed by "METHOD /path".

        An interface that executes agents/teams/workflows MUST override this so its routes
        are authorization-gated. Returning ``{}`` (the default) leaves every route unmapped,
        which the RBAC layer treats as *allow* -- so a non-self-authenticating interface that
        runs entities but returns no mappings is reachable by any authenticated token
        regardless of scopes. EgptOS merges these at startup (see ``_add_auth_middleware``),
        so entries reflect the interface's *actual* mount prefix.
        """
        return {}
