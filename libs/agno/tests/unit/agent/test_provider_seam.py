"""The provider seam in agent initialization (EGPT-3.1).

``set_default_model`` is the one place Epic 3 attaches to. Three properties have to hold, and
the first two are the ones that make this change safe to ship ahead of #64-#67:

* With no provider configured, nothing changes -- ``get_model("openai-responses:gpt-5.4")`` is
  still what runs, including its ImportError handling.
* An explicit ``Agent(model=...)`` is untouched. The seam sits *inside* the existing
  ``if agent.model is None`` guard, so user intent never reaches a provider at all.
* With a provider configured and no explicit model, the provider decides and the default path
  does not run.

The seam is attached to ``set_default_model`` (called from ``initialize_agent``, per
run/serve) rather than ``get_models`` (called from ``Agent.__init__``) on purpose: the former
is a resolver, the latter a normalizer, and resolving at construction time would fire before
a serving layer has had a chance to configure anything.
"""

import pytest

from egpt.agent import Agent
from egpt.agent import _init as agent_init
from egpt.models.base import Model
from egpt.providers import (
    ModelCapabilities,
    ModelResolveRequest,
    ProviderMode,
    get_default_model_provider,
    set_default_model_provider,
)


@pytest.fixture(autouse=True)
def restore_default_model_provider():
    """The default provider is a module global.

    tests/unit/providers/ has this in its conftest, but this file lives under tests/unit/agent/
    and nothing there restores it -- a leak would silently change how every later agent test
    resolves its model.
    """
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
    return _StubModel(id="provider-chosen", provider="stub")


@pytest.fixture
def default_model_lookups(monkeypatch):
    """Record every ``get_model`` call ``_init`` makes, and never touch the network.

    Patches the symbol imported *into* ``egpt.agent._init``, matching
    ``tests/unit/test_default_model_import_errors.py``: patching ``egpt.models.utils.get_model``
    would not rebind the name ``_init`` already holds.

    A ``Model`` argument is returned unchanged, because that is what the real ``get_model``
    does and because ``Agent.__init__`` runs it over ``agent.model`` via ``get_models``. A
    stub that always minted a new model would silently replace an explicit ``Agent(model=...)``
    during construction and make the "explicit model wins" test assert the wrong thing.

    Yields the list of *string* keys, so ``== []`` means "the default path did not run".
    """
    keys: list = []

    def _record(model=None, *args, **kwargs):
        if isinstance(model, Model):
            return model
        keys.append(model)
        return _StubModel(id="default-model", provider="openai")

    monkeypatch.setattr(agent_init, "get_model", _record)
    return keys


# --- an explicit model always wins --------------------------------------------------------


def test_explicit_model_is_untouched_by_a_provider(provider_model, default_model_lookups):
    """The existing `if agent.model is None` guard short-circuits before the seam is reached."""
    explicit = _StubModel(id="user-chosen", provider="openai")
    provider = _RecordingProvider(provider_model)
    set_default_model_provider(provider)

    agent = Agent(model=explicit)
    agent_init.set_default_model(agent)

    assert agent.model is explicit
    assert provider.requests == [], "an explicit Agent(model=...) must never reach the provider"


# --- no provider: byte-identical to pre-EGPT-3.1 -----------------------------------------


def test_without_a_provider_the_default_path_runs(default_model_lookups):
    agent = Agent()
    agent.model = None  # Agent() has no model; belt-and-braces against a future constructor default
    agent_init.set_default_model(agent)

    assert default_model_lookups == ["openai-responses:gpt-5.4"]
    assert agent.model is not None
    assert agent.model.id == "default-model"


def test_without_a_provider_the_import_error_guidance_is_unchanged(monkeypatch):
    """The same assertion tests/unit/test_default_model_import_errors.py makes, from this side."""

    def _raise(*_args, **_kwargs):
        raise ImportError("`openai` not installed. Please install using `pip install openai`")

    monkeypatch.setattr(agent_init, "get_model", _raise)

    with pytest.raises(ImportError, match="Please provide a `model` or install `openai`"):
        agent_init.set_default_model(Agent())


# --- provider configured: it decides ------------------------------------------------------


def test_a_default_provider_resolves_the_model(provider_model, default_model_lookups):
    provider = _RecordingProvider(provider_model)
    set_default_model_provider(provider)

    agent = Agent()
    agent_init.set_default_model(agent)

    assert agent.model is provider_model
    assert default_model_lookups == [], "the default path must not run when a provider answered"


def test_the_provider_sees_the_agent_id_and_no_pinned_model(provider_model, default_model_lookups):
    provider = _RecordingProvider(provider_model)
    set_default_model_provider(provider)

    agent = Agent(id="agent-42")
    agent_init.set_default_model(agent)

    (request,) = provider.requests
    assert request.agent_id == "agent-42"
    assert request.role == "model"
    # agent.model was None, so there is no user intent for the provider to honor.
    assert request.requested is None


def test_a_per_agent_provider_wins_over_the_default(provider_model, default_model_lookups):
    """EGPT-3.4 adds `Agent(model_provider=...)`; the seam already prefers it."""
    default_provider = _RecordingProvider(_StubModel(id="from-default", provider="stub"))
    set_default_model_provider(default_provider)

    agent = Agent()
    agent.model_provider = _RecordingProvider(provider_model)  # type: ignore[attr-defined]
    agent_init.set_default_model(agent)

    assert agent.model is provider_model
    assert default_provider.requests == []


def test_initialize_agent_goes_through_the_seam(provider_model, default_model_lookups):
    """set_default_model is the first line of initialize_agent, which runs per run/serve."""
    set_default_model_provider(_RecordingProvider(provider_model))

    agent = Agent()
    agent_init.initialize_agent(agent)

    assert agent.model is provider_model


def test_the_seam_resolves_once_per_agent_object(provider_model, default_model_lookups):
    """Documented limitation: initialize_agent is idempotent by guard.

    Per-request routing therefore needs `resolve_run_model` (EGPT-3.5, #67) or the
    AgentFactory path, which builds a fresh Agent per request. If this ever starts failing
    because a second call re-resolves, the escalation design in #67 needs re-reading, not a
    quick fix here.
    """
    provider = _RecordingProvider(provider_model)
    set_default_model_provider(provider)

    agent = Agent()
    agent_init.set_default_model(agent)
    agent_init.set_default_model(agent)

    assert len(provider.requests) == 1


def test_construction_does_not_reach_the_provider(provider_model, default_model_lookups):
    """get_models() runs in Agent.__init__ and is a normalizer, not a resolver."""
    provider = _RecordingProvider(provider_model)
    set_default_model_provider(provider)

    agent = Agent()

    assert provider.requests == []
    assert agent.model is None


# --- the uncalled per-run hook ------------------------------------------------------------


def test_resolve_run_model_returns_none_without_a_provider():
    assert agent_init.resolve_run_model(Agent(), ModelResolveRequest()) is None


def test_resolve_run_model_passes_the_request_through(provider_model):
    """Unwired in EGPT-3.1 by design; #67 calls it from _run.py."""
    provider = _RecordingProvider(provider_model)
    set_default_model_provider(provider)

    request = ModelResolveRequest(escalation_level=1, escalation_reason="tool_count")
    resolved = agent_init.resolve_run_model(Agent(), request)

    assert resolved is provider_model
    assert provider.requests == [request]


def test_resolve_run_model_is_not_wired_into_the_run_path():
    """Non-vacuity for the claim above: if #67 wires it, this test is the one to delete."""
    from pathlib import Path

    run_path = Path(agent_init.__file__).parent / "_run.py"
    assert "resolve_run_model" not in run_path.read_text(encoding="utf-8")
