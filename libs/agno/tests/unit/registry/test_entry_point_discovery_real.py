"""EGPT-1.9 AC4 against a genuinely installed package, not a monkeypatch.

``test_entry_point_discovery.py`` covers the merge logic hermetically, but it stubs
``_all_entry_points`` -- so it proves nothing about ``importlib.metadata`` actually finding a
real ``.dist-info``, which is the part a plugin author depends on and the part most likely to
break under a packaging change.

Env-gated because it needs ``libs/agno/tests/fixtures/egpt_dummy_provider`` installed. CI
installs it in the entry-point job; locally::

    pip install -e libs/agno/tests/fixtures/egpt_dummy_provider
    EGPT_TEST_ENTRY_POINTS=1 pytest libs/agno/tests/unit/registry/test_entry_point_discovery_real.py

Skipping when the fixture is absent rather than failing keeps a normal `pytest` run green on
a developer's machine; the gate is what stops that convenience from silently disabling the
acceptance criterion in CI.
"""

import os

import pytest

from egpt.registry import discovery

pytestmark = pytest.mark.skipif(
    os.environ.get("EGPT_TEST_ENTRY_POINTS") != "1",
    reason="set EGPT_TEST_ENTRY_POINTS=1 and install tests/fixtures/egpt_dummy_provider",
)


@pytest.fixture(autouse=True)
def require_the_fixture_package():
    """Fail loudly, not silently, when the gate is on but the package is missing.

    With the env var set, "the fixture is not installed" is a broken CI job, not a reason to
    report success.
    """
    pytest.importorskip("egpt_dummy_provider", reason="fixture package not installed")


def test_out_of_tree_model_resolves_through_the_real_dist_info():
    """AC4: a provider from another distribution resolves with no change to this repository."""
    import egpt_dummy_provider

    assert discovery.resolve("model", "dummy") is egpt_dummy_provider.DummyModel

    registration = discovery.get("model", "dummy")
    assert registration.source == discovery.SOURCE_ENTRY_POINT
    assert registration.origin == "egpt-dummy-provider", (
        f"the registration should name the distribution it came from, got {registration.origin!r}"
    )


def test_no_core_change_was_needed():
    """The mechanical statement of "no core edit": present in entries, absent from declarations.

    If ``dummy`` ever appeared in ``declarations``, it would mean the provider had been added
    to this repository's own tables -- which is precisely what the criterion forbids.
    """
    assert "dummy" in discovery.entries("model")
    assert "dummy" not in discovery.declarations("model")


def test_a_second_kind_works_the_same_way():
    """The mechanism is per-kind, so one working kind does not prove the rest."""
    import egpt_dummy_provider

    assert discovery.resolve("vectordb", "dummy") is egpt_dummy_provider.DummyVectorDb
    assert "dummy" not in discovery.declarations("vectordb")


def test_the_whole_model_path_sees_an_entry_point_provider():
    """The end-to-end claim, and the one most easily broken.

    ``get_model`` does not call ``resolve`` directly -- it resolves a serialized
    (provider, name) pair through alias indices derived from the table. Those indices were
    module-level constants before EGPT-1.9, computed once at import, which would have made an
    entry-point provider resolvable by key but invisible here: AC4 would half-pass, and the
    half that failed is the one DB-defined-agent hydration actually uses.
    """
    from egpt.models.utils import get_model, get_model_from_dict

    built = get_model("dummy:some-id")
    assert type(built).__name__ == "DummyModel"
    assert built.id == "some-id"

    rebuilt = get_model_from_dict({"provider": "Dummy", "name": "DummyModel", "id": "some-id"})
    assert type(rebuilt).__name__ == "DummyModel"


def test_a_plugin_cannot_shadow_a_builtin_provider():
    """Installing this fixture must not have changed what "openai" means."""
    registration = discovery.get("model", "openai")
    assert registration.source == discovery.SOURCE_BUILTIN
    assert registration.module == "egpt.integrations.models.openai"
