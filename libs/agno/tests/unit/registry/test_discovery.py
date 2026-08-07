"""Core semantics of the registration/discovery seam."""

import threading

import pytest

from egpt.registry import discovery
from egpt.registry.discovery import Registration


def test_builtin_rows_are_present_for_every_kind():
    """Non-vacuity anchor for this whole file: the declarations actually loaded."""
    counts = {kind: len(discovery.declarations(kind)) for kind in discovery.KINDS}
    assert counts["model"] >= 50, counts
    assert counts["db"] >= 13, counts
    assert counts["vectordb"] >= 18, counts
    assert counts["tool"] >= 1, counts
    assert counts["embedder"] >= 1, counts


def test_bootstrap_repopulates_after_the_table_is_cleared():
    """Regression: bootstrapping must not depend on a module body having run.

    Module bodies execute once per process. A bootstrap that relied on the import's side
    effects worked exactly once -- and anything clearing the table (this package's own
    fixture, ``_reset_for_tests``) left the kind permanently empty, resolving nothing, with
    no error to say why. ``register_all()`` is what makes the bootstrap repeatable.
    """
    assert discovery.get("model", "openai") is not None

    discovery._reset_for_tests()
    assert discovery._TABLE["model"] == {}, "the reset must really empty the table"

    # The declarations module is already in sys.modules, so re-importing it is a no-op.
    assert discovery.get("model", "openai") is not None, (
        "the registry did not repopulate; bootstrap is relying on import side effects again"
    )
    assert len(discovery.names("model")) >= 50


def test_kinds_are_separate_namespaces():
    """Five keys are shared between the db and vectordb kinds and mean different classes."""
    shared = set(discovery.names("db")) & set(discovery.names("vectordb"))
    assert {"clickhouse", "redis", "singlestore", "surrealdb"} <= shared

    for name in shared:
        db_row = discovery.get("db", name)
        vectordb_row = discovery.get("vectordb", name)
        assert db_row.module != vectordb_row.module, (
            f"'{name}' resolves to the same module under both kinds; the (kind, name) key is doing no work"
        )


def test_resolve_reports_an_unknown_name_as_a_value_error():
    with pytest.raises(ValueError, match="Unknown model 'nope'"):
        discovery.resolve("model", "nope")


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="Unknown integration kind"):
        discovery.resolve("reader", "pdf")


def test_runtime_registration_and_the_decorator_form():
    discovery.register("model", "acme", module="collections", attr="OrderedDict")
    import collections

    assert discovery.resolve("model", "acme") is collections.OrderedDict

    @discovery.register("tool", "acme-tool")
    class AcmeTool:
        pass

    assert discovery.resolve("tool", "acme-tool") is AcmeTool
    assert discovery.get("tool", "acme-tool").source == discovery.SOURCE_RUNTIME


def test_runtime_collision_raises_unless_overridden():
    """The caller's own code, so failing loudly is safe and actionable.

    Contrast with an entry-point collision, which is refused with a warning because it
    arrives from a distribution the user may not control.
    """
    discovery.register("model", "acme", module="collections", attr="OrderedDict")

    with pytest.raises(ValueError, match="already registered"):
        discovery.register("model", "acme", module="collections", attr="Counter")

    discovery.register("model", "acme", module="collections", attr="Counter", override=True)
    import collections

    assert discovery.resolve("model", "acme") is collections.Counter


def test_unregister_reports_whether_it_removed_anything():
    discovery.register("model", "acme", module="collections", attr="OrderedDict")
    assert discovery.unregister("model", "acme") is True
    assert discovery.unregister("model", "acme") is False
    assert discovery.get("model", "acme") is None


def test_version_changes_on_mutation_so_derived_indices_can_cache():
    """`egpt/models/utils.py` keys its alias indices on this; a static version would go stale."""
    before = discovery.version("model")
    discovery.register("model", "acme", module="collections", attr="OrderedDict")
    assert discovery.version("model") > before

    after_register = discovery.version("model")
    discovery.unregister("model", "acme")
    assert discovery.version("model") > after_register


def test_registration_metadata_cannot_be_mutated_through_the_table():
    """A caller holding a Registration must not be able to edit the shared table."""
    registration = discovery.get("db", "postgres")
    with pytest.raises(TypeError):
        registration.metadata["reconstructable"] = False


def test_a_registration_needs_something_to_load():
    with pytest.raises(ValueError, match="needs either 'obj' or 'module'"):
        Registration(kind="model", name="empty")


def test_entries_and_declarations_return_snapshots():
    """Callers iterate these while other threads register; a live view would raise."""
    snapshot = discovery.entries("model")
    discovery.register("model", "acme", module="collections", attr="OrderedDict")
    assert "acme" not in snapshot
    assert "acme" in discovery.entries("model")


def test_concurrent_resolution_during_registration_is_safe():
    """`resolve` sits on the request-time hydration path, so it must not need the lock.

    Reads are unlocked against a copy-on-write table. If a writer ever mutated the live dict
    instead of rebinding a new one, this would surface as a RuntimeError from a reader
    iterating a dict that changed size.
    """
    errors = []
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                discovery.resolve("db", "in_memory")
                discovery.names("model")
        except Exception as exc:  # pragma: no cover - the failure this test exists to catch
            errors.append(exc)

    def writer():
        try:
            for index in range(200):
                discovery.register("model", f"acme-{index}", module="collections", attr="OrderedDict")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for thread in readers:
        thread.start()
    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    writer_thread.join(timeout=60)
    stop.set()
    for thread in readers:
        thread.join(timeout=60)

    assert not errors, f"concurrent access raised: {errors}"
    assert len(discovery.names("model")) >= 250
