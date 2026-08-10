"""The capability policy re-check at hydration (EGPT-2.3).

The scenario every test here is built around is the one the write-path check cannot cover: a
config that was **already stored** -- written before the policy existed, or while a grant was in
place that has since been revoked. The mock db returns such a row directly, which is exactly how
a tightened policy or an out-of-band write looks from the hydration path.

Enforcement is asserted at each of the four entry points that read a stored config, because they
are genuinely separate code paths and three of them were bypasses before this ticket.
"""

from unittest.mock import MagicMock

import pytest

from egpt.agent.agent import Agent, get_agent_by_id, get_agents
from egpt.db.base import BaseDb
from egpt.registry import Registry
from egpt.registry.policy import CapabilityPolicy, PolicyViolationError

# A definition that was legitimately stored while `sql` was granted.
STORED_CONFIG = {"model": "openai:gpt-5.5", "tools": [{"name": "run_sql_query"}]}

REVOKED = CapabilityPolicy.from_dict({"tenants": {"acme": {"models": ["openai"], "tools": ["*"]}}})
GRANTED = CapabilityPolicy.from_dict(
    {"tenants": {"acme": {"models": ["openai"], "tools": ["*"], "granted_privileged": ["sql"]}}}
)


@pytest.fixture
def mock_db():
    """A db holding one stored agent config, tagged with the tenant that wrote it."""
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"
    db.get_config = MagicMock(
        return_value={
            "component_id": "agent-1",
            "config": dict(STORED_CONFIG),
            "tenant_id": "acme",
            "version": 1,
            "stage": "published",
        }
    )
    db.get_component = MagicMock(
        return_value={"component_id": "agent-1", "component_type": "agent", "current_version": 1}
    )
    db.list_components = MagicMock(return_value=([{"component_id": "agent-1", "current_version": 1}], 1))
    return db


class TestGetAgentById:
    def test_a_config_that_is_no_longer_permitted_fails_to_hydrate(self, mock_db):
        """The ticket's hydration case: it passed at write, the policy tightened, it now fails."""
        with pytest.raises(PolicyViolationError) as excinfo:
            get_agent_by_id(db=mock_db, id="agent-1", registry=Registry(capability_policy=REVOKED))

        error = excinfo.value
        assert error.component_id == "agent-1"
        assert error.tenant_id == "acme"
        assert "sql" in str(error)

    def test_it_raises_rather_than_returning_none(self, mock_db):
        """None would render as 404 -- "no such agent" rather than "you may not use this one"."""
        with pytest.raises(PolicyViolationError):
            get_agent_by_id(db=mock_db, id="agent-1", registry=Registry(capability_policy=REVOKED))

    def test_a_still_granted_config_hydrates(self, mock_db):
        agent = get_agent_by_id(db=mock_db, id="agent-1", registry=Registry(capability_policy=GRANTED))
        assert agent is not None
        assert agent.id == "agent-1"

    def test_no_policy_configured_hydrates_exactly_as_before(self, mock_db, monkeypatch):
        monkeypatch.delenv("EGPT_CAPABILITY_POLICY_FILE", raising=False)
        monkeypatch.delenv("AGNO_CAPABILITY_POLICY_FILE", raising=False)
        assert get_agent_by_id(db=mock_db, id="agent-1", registry=Registry()) is not None

    def test_the_tenant_comes_off_the_row_already_fetched(self, mock_db):
        """No extra db round trip: get_config already returns the tenant_id column."""
        with pytest.raises(PolicyViolationError):
            get_agent_by_id(db=mock_db, id="agent-1", registry=Registry(capability_policy=REVOKED))
        mock_db.get_component.assert_not_called()


class TestGetAgentsListing:
    def test_a_violating_agent_is_omitted_rather_than_failing_the_page(self, mock_db, caplog):
        with caplog.at_level("WARNING"):
            agents = get_agents(db=mock_db, registry=Registry(capability_policy=REVOKED))

        assert agents == []
        assert "Omitting agent from listing" in caplog.text

    def test_a_permitted_agent_is_listed(self, mock_db):
        assert len(get_agents(db=mock_db, registry=Registry(capability_policy=GRANTED))) == 1

    def test_one_poisoned_row_does_not_empty_the_rest_of_the_page(self, mock_db):
        """The listing must degrade per-row, not wholesale."""
        mock_db.list_components = MagicMock(return_value=([{"component_id": "bad"}, {"component_id": "good"}], 2))

        def config_for(component_id, **_):
            tools = [{"name": "run_sql_query"}] if component_id == "bad" else []
            return {"config": {"model": "openai:gpt-5.5", "tools": tools}, "tenant_id": "acme"}

        mock_db.get_config = MagicMock(side_effect=lambda component_id, **kw: config_for(component_id))

        agents = get_agents(db=mock_db, registry=Registry(capability_policy=REVOKED))
        assert [a.id for a in agents] == ["good"]


class TestAgentLoad:
    """`Agent.load` does not go through get_agent_by_id, so it needs its own gate."""

    def test_load_refuses_a_no_longer_permitted_config(self, mock_db):
        with pytest.raises(PolicyViolationError):
            Agent.load("agent-1", db=mock_db, registry=Registry(capability_policy=REVOKED))

    def test_load_accepts_a_granted_config(self, mock_db):
        assert Agent.load("agent-1", db=mock_db, registry=Registry(capability_policy=GRANTED)) is not None


class TestStudioToolsPath:
    """StudioTools deliberately bypasses get_agent_by_id, so it was an open bypass."""

    def test_the_studio_loader_is_gated_too(self, mock_db):
        from egpt.tools.studio import StudioTools

        studio = StudioTools(db=mock_db, registry=Registry(capability_policy=REVOKED))
        with pytest.raises(PolicyViolationError):
            studio._load_agent_from_db("agent-1")

    def test_the_studio_loader_accepts_a_granted_config(self, mock_db):
        from egpt.tools.studio import StudioTools

        studio = StudioTools(db=mock_db, registry=Registry(capability_policy=GRANTED))
        assert studio._load_agent_from_db("agent-1") is not None
