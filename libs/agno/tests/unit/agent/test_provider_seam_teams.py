"""The provider seam on the Team path (EGPT-3.1).

A Team is not a lesser case of the seam. ``team/_init.py`` carries its own copy of the default
model path, and it assigns ``member.model = team.model`` for members that declare none -- which
makes those members reach ``set_default_model`` with a non-None model and short-circuit at its
guard. So attaching the seam to ``Agent.set_default_model`` alone left two live paths to the
frontier default:

* the team's own model, which serves the prompts carrying user input and member output
* every member that inherited it

In Mode A -- whose entire premise is that traffic stays inside the VPC -- either one is an
egress leak, and it is the mainline path rather than an edge case: a team with declaratively
created members is the ordinary way to build one.

These tests pin both halves, plus the property that makes the fix safe: with no provider
configured, teams resolve exactly as they did before Epic 3.
"""

import pytest

from egpt.agent import Agent
from egpt.models.base import Model
from egpt.providers import (
    ModelCapabilities,
    ModelResolveRequest,
    ProviderMode,
    get_default_model_provider,
    set_default_model_provider,
)
from egpt.team import Team


@pytest.fixture(autouse=True)
def restore_default_model_provider():
    """The default provider is a module global; a leak would change later tests' resolution."""
    saved = get_default_model_provider()
    try:
        yield
    finally:
        set_default_model_provider(saved)


class _StubModel(Model):
    """A concrete Model that talks to nothing. Never invoked -- only assigned and compared."""

    def invoke(self, *args, **kwargs):
        raise NotImplementedError

    async def ainvoke(self, *args, **kwargs):
        raise NotImplementedError

    def invoke_stream(self, *args, **kwargs):
        raise NotImplementedError

    async def ainvoke_stream(self, *args, **kwargs):
        raise NotImplementedError

    def _parse_provider_response(self, *args, **kwargs):
        raise NotImplementedError

    def _parse_provider_response_delta(self, *args, **kwargs):
        raise NotImplementedError


class _RecordingProvider:
    """Satisfies the Protocol structurally; records what it was asked."""

    name = "recording"

    def __init__(self, model: Model) -> None:
        self._model = model
        self.requests: list = []

    def resolve(self, request: ModelResolveRequest) -> Model:
        self.requests.append(request)
        return self._model

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(modes=(ProviderMode.LOCAL,), provider_keys=("recording",))


@pytest.fixture
def provider_model() -> Model:
    return _StubModel(id="in-vpc-slm", provider="stub")


def test_a_team_resolves_through_the_seam(provider_model):
    """The blocking case: a Team resolving its own default bypassed Epic 3 entirely."""
    set_default_model_provider(_RecordingProvider(provider_model))

    team = Team(name="T", members=[Agent(name="Researcher")])
    team.initialize_team()

    assert team.model is provider_model


def test_members_resolve_through_the_seam(provider_model):
    """Inheritance assigns a non-None model, which short-circuits the member's own guard.

    So a member had to be resolved *before* inheriting, or it silently ran on whatever the team
    resolved -- including the frontier default.
    """
    set_default_model_provider(_RecordingProvider(provider_model))

    team = Team(name="T", members=[Agent(name="Researcher"), Agent(name="Writer")])
    team.initialize_team()

    assert [m.model for m in team.members] == [provider_model, provider_model]


def test_a_member_provider_outranks_team_inheritance(provider_model):
    """A member carrying its own provider must not be overwritten by the team's model.

    This is the point of resolving before inheriting: per-member routing (its own adapter, its
    own tenant endpoint) is exactly what a team-wide assignment would erase.
    """
    member_model = _StubModel(id="member-specific", provider="stub")
    set_default_model_provider(_RecordingProvider(provider_model))

    member = Agent(name="Specialist")
    member.model_provider = _RecordingProvider(member_model)
    team = Team(name="T", members=[member])
    team.initialize_team()

    assert team.model is provider_model
    assert member.model is member_model


def test_an_explicit_member_model_is_untouched(provider_model):
    """User intent still outranks configuration, on the team path as on the agent path."""
    explicit = _StubModel(id="explicit", provider="stub")
    set_default_model_provider(_RecordingProvider(provider_model))

    member = Agent(name="Pinned", model=explicit)
    team = Team(name="T", members=[member])
    team.initialize_team()

    assert member.model is explicit


def test_the_provider_sees_a_real_team_id(provider_model):
    """``Team.id`` defaults to None and ``set_id`` fills it, so resolving before ``set_id``
    made the per-component routing signal always None for a declaratively created team."""
    provider = _RecordingProvider(provider_model)
    set_default_model_provider(provider)

    Team(name="T", members=[Agent(name="R")]).initialize_team()

    assert provider.requests, "the provider was never consulted"
    assert all(r.agent_id is not None for r in provider.requests)


def test_without_a_provider_teams_resolve_as_before(monkeypatch):
    """The safety property: no provider configured means the pre-Epic-3 path, unchanged."""
    from egpt.team import _init as team_init

    calls = []
    sentinel = _StubModel(id="default-path", provider="stub")

    def fake_get_model(value):
        calls.append(value)
        return value if isinstance(value, Model) else sentinel

    monkeypatch.setattr(team_init, "get_model", fake_get_model)
    set_default_model_provider(None)

    team = Team(name="T", members=[Agent(name="R")])
    team.initialize_team()

    assert "openai-responses:gpt-5.4" in calls
    assert team.model is sentinel
