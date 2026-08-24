"""Entry-point discovery: the mechanism that lets a package add an integration from outside.

These are hermetic -- ``_all_entry_points`` is monkeypatched, so nothing is installed and
nothing is written to disk. ``test_entry_point_discovery_real.py`` covers the genuine
``.dist-info`` path that these cannot reach.

What is worth testing here is mostly the failure behaviour. A registry that resolves the
happy path but crashes the whole application because some unrelated distribution shipped a
malformed ``.dist-info`` is worse than no registry, and a registry whose winner depends on
``sys.path`` order gives you a bug that reproduces on one machine and not another.
"""

from importlib.metadata import EntryPoint

import pytest

from egpt.registry import discovery


class _FakeDist:
    def __init__(self, name):
        self.name = name
        self.metadata = {"Name": name}


class _FakeEntryPoint:
    """The four attributes ``_merge_entry_points`` reads. `dist` is what carries the origin.

    Deliberately carries no ``extras`` attribute: extras are parsed from ``value`` by
    ``_declared_extras``, so there is one grammar and one parser, and a double that only sets
    ``value`` stays faithful to the real object without reimplementing stdlib tokenization
    (PR #129 review).
    """

    def __init__(self, name, value, dist_name=None):
        self.name = name
        self.value = value
        self.dist = _FakeDist(dist_name) if dist_name else None
        # No `extras` attribute at all: `_declared_extras` parses the fragment out of `value`,
        # so the double does not have to reimplement stdlib tokenization to stay faithful
        # (PR #129 review). An entry-point-like object without `.extras` is exactly the
        # duck-typed shape this path has to tolerate.


def _install(monkeypatch, kind, entry_points):
    """Publish `entry_points` under `kind`'s group and force a re-merge."""
    group = discovery.entry_point_group(kind)
    monkeypatch.setattr(discovery, "_all_entry_points", lambda: {group: list(entry_points)})
    discovery._READY.discard(kind)


def test_entry_point_group_names_are_stable():
    """These strings are a public contract: a plugin declares one to be discoverable.

    Renaming one silently orphans every package that already ships it, so a rename has to be
    a deliberate edit here rather than a side effect of refactoring the kind names. Compared as a
    whole mapping rather than key by key, so *adding* a kind lands here too -- a new group name is
    the same kind of published string as a renamed one.
    """
    assert {kind: discovery.entry_point_group(kind) for kind in discovery.KINDS} == {
        "model": "egpt.models",
        "db": "egpt.dbs",
        "vectordb": "egpt.vectordbs",
        "vectorstore": "egpt.vectorstores",
        "graphdb": "egpt.graphdbs",
        "tool": "egpt.tools",
        "embedder": "egpt.embedders",
    }


def test_out_of_tree_provider_resolves_and_constructs(monkeypatch):
    """AC4, hermetically: a provider nobody in this repository has heard of is resolvable."""
    _install(monkeypatch, "model", [_FakeEntryPoint("acme", "collections:OrderedDict", "acme-egpt")])

    import collections

    assert discovery.resolve("model", "acme") is collections.OrderedDict
    assert discovery.construct("model", "acme") == collections.OrderedDict()

    registration = discovery.get("model", "acme")
    assert registration.source == discovery.SOURCE_ENTRY_POINT
    assert registration.origin == "acme-egpt"


def test_no_extras_means_no_metadata(monkeypatch):
    """The pre-aicoe-tracker#179 shape, still true for a package that declares nothing extra."""
    _install(monkeypatch, "model", [_FakeEntryPoint("acme", "collections:OrderedDict", "acme-egpt")])

    assert discovery.get("model", "acme").metadata == {}


def test_the_supports_adapters_extra_becomes_registration_metadata(monkeypatch):
    """aicoe-tracker#179: a plugin's ``module:attr [supports_adapters]`` entry point is the only
    way an out-of-tree *package* (as opposed to a runtime ``register()`` call) can declare a
    capability -- the entry-point spec's value string has no other field to carry one."""
    _install(
        monkeypatch,
        "model",
        [_FakeEntryPoint("acme", "collections:OrderedDict [supports_adapters]", "acme-egpt")],
    )

    assert discovery.get("model", "acme").metadata == {"supports_adapters": True}


@pytest.mark.parametrize(
    "fragment",
    [
        "supports_adapters",
        # The spelling a packaging-literate author reaches for: setuptools publishes a declared
        # extra PEP 685-normalized as `Provides-Extra: supports-adapters`, and `importlib`
        # tokenizes this one into TWO extras (`supports`, `adapters`) with no normalization.
        "supports-adapters",
        "Supports_Adapters",
        "SUPPORTS_ADAPTERS",
    ],
)
def test_every_spelling_of_the_extra_declares_the_same_capability(monkeypatch, fragment):
    """Matching the extra literally made every near-miss a silent capability drop (PR #129 review).

    That is the exact shape of aicoe-tracker#165 -- the row still resolves, so the only symptom
    is an adapter quietly ignored at request time -- reintroduced by the fix for it.
    """
    _install(
        monkeypatch,
        "model",
        [_FakeEntryPoint("acme", f"collections:OrderedDict [{fragment}]", "acme-egpt")],
    )

    assert discovery.get("model", "acme").metadata == {"supports_adapters": True}


@pytest.mark.parametrize("fragment", ["supports_adapters, telemetry", "telemetry, supports_adapters"])
def test_the_capability_survives_alongside_other_extras(monkeypatch, fragment):
    """Regression, PR #129 review (Critical): a declaration next to any other extra was dropped.

    The first attempt normalized ``EntryPoint.extras`` by joining *all* tokens into one string,
    so ``[supports_adapters, telemetry]`` compared as ``supports-adapters-telemetry`` and matched
    nothing -- the adapter silently ignored, which is the aicoe-tracker#165 failure this path
    exists to prevent. Order-dependent too: one order warned misleadingly, the other was silent.
    """
    _install(monkeypatch, "model", [_FakeEntryPoint("acme", f"collections:OrderedDict [{fragment}]", "acme-egpt")])

    assert discovery.get("model", "acme").metadata == {"supports_adapters": True}


@pytest.mark.parametrize("fragment", ["supports, adapters", "supports , adapters"])
def test_two_unrelated_extras_do_not_fuse_into_the_capability(monkeypatch, fragment):
    """Regression, PR #129 review (Critical): the inverse of the above -- a false *positive*.

    Joining across extras made ``[supports, adapters]`` -- two distinct extras, neither of them
    the capability -- normalize to ``supports-adapters`` and grant adapter rendering nobody
    declared, so a backend with no multi-LoRA support would send an adapter name as its model id.

    ``.extras`` cannot tell these apart from ``[supports-adapters]``: its ``\\w+`` tokenizer has
    already discarded the comma. That is why the fragment is parsed from ``value``.
    """
    _install(monkeypatch, "model", [_FakeEntryPoint("acme", f"collections:OrderedDict [{fragment}]", "acme-egpt")])

    assert discovery.get("model", "acme").metadata == {}


@pytest.mark.parametrize("value", ["collections:[supports_adapters]", "collections [ns:OrderedDict]"])
def test_a_colon_only_inside_the_extras_fragment_is_not_a_valid_target(monkeypatch, value, caplog):
    """Regression, PR #129 review: the guard tested the raw value, the parse used the stripped one.

    Such a row passed ``":" in value``, parsed to an empty ``attr``, registered, claimed the name
    against any later well-formed registration, and failed at request time inside
    ``Registration.load()`` rather than being skipped here.
    """
    _install(monkeypatch, "model", [_FakeEntryPoint("acme", value, "acme-egpt")])

    assert discovery.get("model", "acme") is None
    assert "is not '<module>:<attribute>'" in caplog.text


def test_a_near_miss_extra_warns_instead_of_failing_silently(monkeypatch, caplog):
    """A typo must not be indistinguishable from declaring nothing."""
    _install(
        monkeypatch,
        "model",
        [_FakeEntryPoint("acme", "collections:OrderedDict [supports_adapter]", "acme-egpt")],
    )

    assert discovery.get("model", "acme").metadata == {}
    assert "supports_adapters" in caplog.text


def test_an_unrelated_extra_is_not_mistaken_for_the_capability(monkeypatch):
    """Normalizing must not turn any extra into an opt-in."""
    _install(
        monkeypatch,
        "model",
        [_FakeEntryPoint("acme", "collections:OrderedDict [telemetry]", "acme-egpt")],
    )

    assert discovery.get("model", "acme").metadata == {}


def test_an_entry_point_object_without_an_extras_attribute_still_declares(monkeypatch):
    """The reason extras are parsed from ``value`` rather than read off ``.extras``.

    ``.extras`` is unusable for this on two counts. Its ``\\w+`` tokenizer discards the comma, so
    it cannot distinguish ``[supports-adapters]`` from ``[supports, adapters]`` -- the two
    Critical bugs the parametrized tests above pin. And it returned ``re.Match`` objects rather
    than strings on CPython 3.9.0-3.9.10 and 3.10.0-3.10.2 (``re.findall`` from 3.9.11/3.10.3),
    so a membership test against it was silently always-False on interpreters ``pyproject.toml``
    declares supported and CI cannot see, because the matrix pins ``"3.10"`` to a patched release.

    Parsing ``value`` sidesteps both, and makes the duck-typed case work for free: entry-point
    objects here are only required to carry ``name``/``value``/``dist``, so an object with no
    ``extras`` attribute at all must still have its declaration honoured (PR #129 review).
    """
    entry_point = _FakeEntryPoint("acme", "collections:OrderedDict [supports_adapters]", "acme-egpt")
    assert not hasattr(entry_point, "extras"), "the double must not grow an extras attribute"

    _install(monkeypatch, "model", [entry_point])

    assert discovery.get("model", "acme").metadata == {"supports_adapters": True}


def test_out_of_tree_registration_is_invisible_to_declarations(monkeypatch):
    """The distinction the layer-guards depend on.

    Guards assert things like "every row points into egpt.integrations." -- true of what this
    repository ships, and no business of a third party's package. If they read ``entries``
    instead of ``declarations``, installing any plugin would fail the test suite.
    """
    _install(monkeypatch, "model", [_FakeEntryPoint("acme", "collections:OrderedDict", "acme-egpt")])

    assert "acme" in discovery.entries("model")
    assert "acme" not in discovery.declarations("model")
    assert "openai" in discovery.declarations("model")


def test_builtin_wins_over_a_colliding_entry_point(monkeypatch, caplog):
    """A plugin cannot hijack a built-in name, and cannot take the application down trying.

    Refused rather than raised: the collision arrives from a distribution the user may not
    control, so turning an unrelated `pip install` into a hard failure of every model lookup
    would surface the problem nowhere near its cause. The warning names the distribution so
    it is still findable.
    """
    _install(monkeypatch, "model", [_FakeEntryPoint("openai", "collections:OrderedDict", "impostor")])
    caplog.clear()

    registration = discovery.get("model", "openai")
    assert registration.source == discovery.SOURCE_BUILTIN
    assert registration.module == "egpt.integrations.models.openai"
    assert any("impostor" in record.message for record in caplog.records), (
        "the collision must name the distribution that caused it: " + "; ".join(r.message for r in caplog.records)
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_colliding_plugins_pick_the_same_winner_in_any_order(monkeypatch, reverse):
    """`entry_points()` iterates in sys.path order, which is not stable across environments.

    Without an explicit sort, which of two plugins claiming one name wins would depend on
    install order -- a bug that reproduces on CI and not locally, or the other way round.
    """
    candidates = [
        _FakeEntryPoint("shared", "collections:OrderedDict", "zebra-plugin"),
        _FakeEntryPoint("shared", "collections:Counter", "alpha-plugin"),
    ]
    _install(monkeypatch, "model", reversed(candidates) if reverse else candidates)

    registration = discovery.get("model", "shared")
    assert registration.origin == "alpha-plugin", "the winner must not depend on iteration order"


def test_malformed_entry_point_does_not_cost_its_neighbours(monkeypatch, caplog):
    """One bad row is skipped; the rest of that distribution's providers still work."""
    _install(
        monkeypatch,
        "model",
        [
            _FakeEntryPoint("broken", "no-colon-here", "acme-egpt"),
            _FakeEntryPoint("intact", "collections:OrderedDict", "acme-egpt"),
        ],
    )
    caplog.clear()

    assert discovery.get("model", "broken") is None
    assert discovery.get("model", "intact") is not None
    assert any("broken" in record.message for record in caplog.records)


def test_a_broken_environment_degrades_instead_of_exploding(monkeypatch, caplog):
    """A corrupt `.dist-info` from some unrelated package must not stop models resolving.

    The scan reaches into the metadata of every installed distribution, so it is exposed to
    the state of packages this framework has nothing to do with. Failing closed there would
    make an unrelated broken install look like a framework outage.
    """

    def _explode():
        raise RuntimeError("corrupt .dist-info")

    monkeypatch.setattr(discovery, "_ENTRY_POINTS", None)
    monkeypatch.setattr("importlib.metadata.entry_points", _explode)
    discovery._READY.discard("model")
    caplog.clear()

    # The failure is absorbed and reported, not raised...
    found = discovery._all_entry_points()
    assert found == {discovery.entry_point_group(kind): [] for kind in discovery.KINDS}
    assert any("entry points" in record.message for record in caplog.records)

    # ...and the built-ins, which need no scan at all, are unaffected.
    assert discovery.get("model", "openai") is not None
    assert discovery.resolve("db", "in_memory").__name__ == "InMemoryDb"


def test_python_39_entry_points_shape_is_handled(monkeypatch):
    """CI runs 3.10 and 3.12, but the package supports 3.9, whose API differs.

    On 3.9 ``entry_points()`` returns a plain dict with no ``select``. Without this the 3.9
    branch would ship untested, which for a package declaring ``>=3.9`` is a real hole.
    """

    class _LegacyMapping(dict):
        """3.9's return value: a dict of group -> entry points, and no `select`."""

    legacy = _LegacyMapping({"egpt.models": [_FakeEntryPoint("acme", "collections:OrderedDict", "acme-egpt")]})
    assert not hasattr(legacy, "select"), "the fixture must not accidentally expose the 3.10+ API"

    monkeypatch.setattr(discovery, "_ENTRY_POINTS", None)
    monkeypatch.setattr("importlib.metadata.entry_points", lambda: legacy)

    found = discovery._all_entry_points()
    assert [entry_point.name for entry_point in found["egpt.models"]] == ["acme"]
    assert found[discovery.entry_point_group("db")] == []


def test_a_real_entry_point_object_is_understood(monkeypatch):
    """The stubs above are duck-typed; check the seam against the genuine class too."""
    _install(
        monkeypatch, "vectordb", [EntryPoint(name="acme", value="collections:OrderedDict", group="egpt.vectordbs")]
    )

    import collections

    assert discovery.resolve("vectordb", "acme") is collections.OrderedDict


def test_every_kind_has_a_group_and_unknown_kinds_are_rejected():
    for kind in discovery.KINDS:
        assert discovery.entry_point_group(kind).startswith("egpt.")
    with pytest.raises(ValueError, match="Unknown integration kind"):
        discovery.entry_point_group("reader")
