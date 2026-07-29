"""Unit tests for the EgptOS composition root (EGPT-1.2).

Covers:
- the control-plane service graph resolves from the container
- a caller-supplied binding survives ``build_os_container`` (test substitution)
- a fake integration can be swapped in without touching production code
- the public ``Agent(model=...)`` API still works with no container involved
"""

from unittest.mock import MagicMock

import pytest

from egpt.agent.agent import Agent
from egpt.container import Container, ContainerError
from egpt.db.sqlite import SqliteDb
from egpt.models.base import Model
from egpt.os import EgptOS
from egpt.os.container import (
    JWT_MIDDLEWARE_KWARGS,
    SCHEDULE_EXECUTOR,
    SCHEDULE_POLLER,
    build_os_container,
)
from egpt.os.service_accounts import ServiceAccountVerifier
from egpt.registry import Registry


def _mock_model():
    """A concrete Model subclass with all abstract methods stubbed."""
    abstract_methods = {
        name: MagicMock() for name in dir(Model) if getattr(getattr(Model, name, None), "__isabstractmethod__", False)
    }
    model = type("MockModel", (Model,), abstract_methods)(id="mock-model")
    model.provider = "mock"
    return model


@pytest.fixture
def agent():
    return Agent(name="Test Agent", model=_mock_model())


@pytest.fixture
def agent_os(agent, tmp_path):
    return EgptOS(
        agents=[agent],
        db=SqliteDb(db_file=str(tmp_path / "test.db")),
        internal_service_token="test-token",
    )


# =============================================================================
# Composition root
# =============================================================================


def test_egpt_os_exposes_a_container(agent_os):
    assert isinstance(agent_os.container, Container)
    for token in (Registry, ServiceAccountVerifier, JWT_MIDDLEWARE_KWARGS, SCHEDULE_EXECUTOR, SCHEDULE_POLLER):
        assert token in agent_os.container


def test_registry_is_resolved_from_the_container(agent_os):
    """EgptOS no longer instantiates Registry directly - it resolves the binding."""
    assert isinstance(agent_os.registry, Registry)
    assert agent_os.registry is agent_os.container.resolve(Registry)


def test_explicit_registry_wins_over_the_container(agent, tmp_path):
    registry = Registry(name="user-supplied")
    agent_os = EgptOS(
        agents=[agent],
        db=SqliteDb(db_file=str(tmp_path / "explicit.db")),
        registry=registry,
    )

    assert agent_os.registry is registry
    # The container must hand out that same registry, not build a second, empty one.
    assert agent_os.container.resolve(Registry) is registry


def test_a_container_cannot_be_shared_between_two_egpt_os_instances(agent, tmp_path):
    """The default factories close over one EgptOS, so sharing would serve stale config."""
    container = Container(name="shared")
    EgptOS(agents=[agent], db=SqliteDb(db_file=str(tmp_path / "first.db")), container=container)

    with pytest.raises(ContainerError, match="already built for a different EgptOS"):
        EgptOS(agents=[agent], db=SqliteDb(db_file=str(tmp_path / "second.db")), container=container)

    # A child is a separate container, so it inherits the bindings without owning them.
    EgptOS(agents=[agent], db=SqliteDb(db_file=str(tmp_path / "third.db")), container=container.child())


def test_child_container_gets_its_own_instance_scoped_services(agent, tmp_path):
    """A child must not inherit the defaults: they close over the *parent's* EgptOS.

    ``child()`` is the documented way to reuse a configured container, so if the
    defaults were inherited the second OS would silently run on the first one's
    registry, verify service accounts against the first one's database, and hand the
    scheduler the first one's internal service token.
    """
    first_db = SqliteDb(db_file=str(tmp_path / "first.db"))
    second_db = SqliteDb(db_file=str(tmp_path / "second.db"))
    shared = Container(name="shared")

    first = EgptOS(agents=[agent], db=first_db, container=shared, internal_service_token="token-one")
    second = EgptOS(agents=[agent], db=second_db, container=shared.child(), internal_service_token="token-two")

    # Each OS gets its own registry, or agents from one leak into the other.
    assert second.container.resolve(Registry) is not first.container.resolve(Registry)

    # The verifier must point at the OS that owns it, not at the parent's database.
    first_verifier = first._get_service_account_verifier()
    second_verifier = second._get_service_account_verifier()
    assert second_verifier is not first_verifier
    assert second_verifier.db is second_db

    # And the scheduler must not carry the first instance's credential.
    pytest.importorskip("httpx")
    assert second.container.resolve(SCHEDULE_EXECUTOR).internal_service_token == "token-two"


def test_child_inherits_custom_bindings_but_not_defaults(agent, tmp_path):
    """Only the six instance-scoped defaults are re-bound; custom bindings still flow down.

    "Inherited" means the binding, not the instance: an ``EgptOS`` child always holds the
    defaults, so it is always the resolution context and a parent's ``bind()`` factory is
    re-run for it. Only ``bind_instance`` keeps one object across both instances.
    """

    class MetricsSink:
        pass

    class Exporter:
        pass

    sink = MetricsSink()
    parent = Container(name="app")
    parent.bind_instance(MetricsSink, sink)
    parent.bind(Exporter, lambda _c: Exporter())
    parent.bind_instance(Registry, FakeRegistry(name="parent-registry"))

    agent_os = EgptOS(agents=[agent], db=SqliteDb(db_file=str(tmp_path / "child.db")), container=parent.child())

    # An instance binding is the same object in the child, so it is genuinely shared.
    assert agent_os.container.resolve(MetricsSink) is sink
    # A factory binding is inherited but rebuilt, so the child gets its own Exporter.
    assert isinstance(agent_os.container.resolve(Exporter), Exporter)
    assert agent_os.container.resolve(Exporter) is not parent.resolve(Exporter)
    # A default is instance-scoped, so the child binds its own rather than the parent's.
    assert type(agent_os.container.resolve(Registry)) is Registry


def test_binding_on_the_child_itself_still_wins(agent, tmp_path):
    """The substitution seam is unchanged -- pre-bind on the container you actually pass."""
    fake = FakeRegistry(name="fake")
    child = Container(name="app").child()
    child.bind_instance(Registry, fake)

    agent_os = EgptOS(agents=[agent], db=SqliteDb(db_file=str(tmp_path / "own.db")), container=child)

    assert agent_os.registry is fake


def test_rebuilding_with_the_same_egpt_os_is_allowed(agent_os):
    """Idempotence must not trip the owner guard."""
    assert build_os_container(agent_os, agent_os.container) is agent_os.container


def test_service_account_verifier_is_resolved_once(agent_os):
    """One verifier per EgptOS, so its caches are shared across the REST and MCP apps."""
    verifier = agent_os._get_service_account_verifier()

    assert isinstance(verifier, ServiceAccountVerifier)
    assert verifier is agent_os._get_service_account_verifier()
    assert verifier is agent_os.container.resolve(ServiceAccountVerifier)
    assert verifier.db is agent_os.db


def test_verifier_override_is_honoured_after_a_first_call(agent_os):
    """The call site must not memoize the resolve, or the container seam goes dead.

    Substituting a verifier is exactly what an auth test needs to do, and it usually
    happens after something has already touched the app.
    """
    real = agent_os._get_service_account_verifier()
    fake = MagicMock(spec=ServiceAccountVerifier)

    with agent_os.container.override(ServiceAccountVerifier, fake):
        assert agent_os._get_service_account_verifier() is fake

    assert agent_os._get_service_account_verifier() is not fake
    assert isinstance(real, ServiceAccountVerifier)


def test_assigned_verifier_wins_over_the_binding(agent_os):
    """The attribute stays an explicit override slot that existing tests assign to."""
    fake = MagicMock(spec=ServiceAccountVerifier)
    agent_os._service_account_verifier = fake

    assert agent_os._get_service_account_verifier() is fake


def test_no_verifier_without_a_db(agent):
    """Whether a verifier exists is policy (needs a db), not a container concern."""
    agent_os = EgptOS(agents=[agent])

    assert agent_os._get_service_account_verifier() is None


def test_jwt_middleware_kwargs_are_recomputed_per_resolve(agent_os):
    """The kwargs derive from mutable auth config, so the binding is transient.

    Caching them would freeze whatever ``authorization`` happened to be the first time
    they were resolved, which is not what the code this replaced did.
    """
    kwargs = agent_os.container.resolve(JWT_MIDDLEWARE_KWARGS)
    assert "verification_keys" in kwargs and "algorithm" in kwargs

    agent_os.authorization = not kwargs["authorization"]

    assert agent_os.container.resolve(JWT_MIDDLEWARE_KWARGS)["authorization"] == agent_os.authorization


# =============================================================================
# Substituting implementations
# =============================================================================


class FakeRegistry(Registry):
    """Stand-in for a substituted internal service."""


def test_caller_supplied_binding_is_not_overwritten(agent, tmp_path):
    """A container passed to EgptOS is a deliberate substitution; defaults must not win."""
    container = Container(name="test")
    fake = FakeRegistry(name="fake")
    container.bind_instance(Registry, fake)

    agent_os = EgptOS(
        agents=[agent],
        db=SqliteDb(db_file=str(tmp_path / "bound.db")),
        container=container,
    )

    assert agent_os.container is container
    assert agent_os.registry is fake


def test_override_substitutes_a_service_without_touching_production_code(agent_os):
    fake = FakeRegistry(name="fake")

    with agent_os.container.override(Registry, fake):
        assert agent_os.container.resolve(Registry) is fake

    assert type(agent_os.container.resolve(Registry)) is Registry


def test_build_os_container_is_idempotent(agent_os):
    """Re-running the composition root keeps existing bindings (and their singletons)."""
    registry = agent_os.container.resolve(Registry)

    build_os_container(agent_os, agent_os.container)

    assert agent_os.container.resolve(Registry) is registry


# =============================================================================
# Scheduler slice (optional dependency)
# =============================================================================


def test_scheduler_graph_resolves_with_the_shared_executor(agent_os):
    pytest.importorskip("croniter", reason="scheduler services require egpt[scheduler]")

    executor = agent_os.container.resolve(SCHEDULE_EXECUTOR)
    poller = agent_os.container.resolve(SCHEDULE_POLLER)

    assert executor is agent_os.container.resolve(SCHEDULE_EXECUTOR)  # singleton
    assert poller.executor is executor
    assert poller.db is agent_os.db
    assert executor.internal_service_token == "test-token"
    # The poller owns a polling task, so each resolve is a fresh one.
    assert agent_os.container.resolve(SCHEDULE_POLLER) is not poller


def test_stub_executor_reaches_the_poller(agent_os):
    """Substituting an integration in the container is enough - no production edits."""
    pytest.importorskip("croniter", reason="scheduler services require egpt[scheduler]")

    stub = MagicMock(name="StubScheduleExecutor")

    with agent_os.container.override(SCHEDULE_EXECUTOR, stub):
        poller = agent_os.container.resolve(SCHEDULE_POLLER)
        assert poller.executor is stub


# =============================================================================
# Public API is unchanged
# =============================================================================


def test_agent_constructs_without_a_container():
    """The ergonomic API is untouched: no container is involved in building an Agent."""
    model = _mock_model()
    agent = Agent(name="Direct", model=model)

    assert agent.model is model
    assert not hasattr(agent, "container")


def test_agent_runs_without_a_container():
    from egpt.models.response import ModelResponse

    model = _mock_model()
    model.response = MagicMock(return_value=ModelResponse(content="hello"))
    agent = Agent(name="Direct", model=model)

    response = agent.run("hi")

    assert response.content == "hello"
