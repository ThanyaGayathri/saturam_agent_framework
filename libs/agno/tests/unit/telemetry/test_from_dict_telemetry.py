"""from_dict / storage must default telemetry to off."""

from egpt.agent.agent import Agent
from egpt.team.team import Team
from egpt.workflow.workflow import Workflow


def test_agent_from_dict_telemetry_default_off():
    agent = Agent.from_dict({"id": "a1", "name": "Agent"})
    assert agent.telemetry is False


def test_agent_from_dict_telemetry_explicit_true():
    agent = Agent.from_dict({"id": "a1", "name": "Agent", "telemetry": True})
    assert agent.telemetry is True


def test_team_from_dict_telemetry_default_off():
    team = Team.from_dict({"id": "t1", "name": "Team", "members": []})
    assert team.telemetry is False


def test_team_from_dict_telemetry_explicit_true():
    team = Team.from_dict({"id": "t1", "name": "Team", "members": [], "telemetry": True})
    assert team.telemetry is True


def test_workflow_from_dict_telemetry_default_off():
    workflow = Workflow.from_dict({"id": "w1", "name": "Workflow"})
    assert workflow.telemetry is False


def test_workflow_from_dict_telemetry_explicit_true():
    workflow = Workflow.from_dict({"id": "w1", "name": "Workflow", "telemetry": True})
    assert workflow.telemetry is True
