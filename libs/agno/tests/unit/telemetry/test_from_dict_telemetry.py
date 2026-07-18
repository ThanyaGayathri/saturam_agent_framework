"""from_dict / storage must default telemetry to off and round-trip opt-in."""

from egpt.agent.agent import Agent
from egpt.team.team import Team
from egpt.workflow.workflow import Workflow


def _clear_telemetry_env(monkeypatch) -> None:
    monkeypatch.delenv("EGPT_TELEMETRY", raising=False)
    monkeypatch.delenv("AGNO_TELEMETRY", raising=False)


def test_agent_from_dict_telemetry_default_off():
    agent = Agent.from_dict({"id": "a1", "name": "Agent"})
    assert agent.telemetry is False


def test_agent_from_dict_telemetry_explicit_true():
    agent = Agent.from_dict({"id": "a1", "name": "Agent", "telemetry": True})
    assert agent.telemetry is True


def test_agent_to_dict_from_dict_preserves_telemetry_true(monkeypatch):
    """Explicit telemetry=True must survive to_dict → from_dict (not omitted)."""
    _clear_telemetry_env(monkeypatch)
    agent = Agent(id="a1", name="Agent", telemetry=True)
    data = agent.to_dict()
    assert data.get("telemetry") is True
    restored = Agent.from_dict(data)
    assert restored.telemetry is True


def test_agent_to_dict_from_dict_preserves_telemetry_false():
    agent = Agent(id="a1", name="Agent", telemetry=False)
    data = agent.to_dict()
    assert data.get("telemetry") is False
    restored = Agent.from_dict(data)
    assert restored.telemetry is False


def test_team_from_dict_telemetry_default_off():
    team = Team.from_dict({"id": "t1", "name": "Team", "members": []})
    assert team.telemetry is False


def test_team_from_dict_telemetry_explicit_true():
    team = Team.from_dict({"id": "t1", "name": "Team", "members": [], "telemetry": True})
    assert team.telemetry is True


def test_team_to_dict_from_dict_preserves_telemetry_true(monkeypatch):
    """Explicit telemetry=True must survive to_dict → from_dict (not omitted)."""
    _clear_telemetry_env(monkeypatch)
    team = Team(id="t1", name="Team", members=[], telemetry=True)
    data = team.to_dict()
    assert data.get("telemetry") is True
    restored = Team.from_dict(data)
    assert restored.telemetry is True


def test_workflow_from_dict_telemetry_default_off():
    workflow = Workflow.from_dict({"id": "w1", "name": "Workflow"})
    assert workflow.telemetry is False


def test_workflow_from_dict_telemetry_explicit_true(monkeypatch):
    _clear_telemetry_env(monkeypatch)
    workflow = Workflow.from_dict({"id": "w1", "name": "Workflow", "telemetry": True})
    assert workflow.telemetry is True


def test_workflow_to_dict_from_dict_preserves_telemetry_true(monkeypatch):
    _clear_telemetry_env(monkeypatch)
    workflow = Workflow(id="w1", name="Workflow", telemetry=True)
    data = workflow.to_dict()
    assert data.get("telemetry") is True
    restored = Workflow.from_dict(data)
    assert restored.telemetry is True
