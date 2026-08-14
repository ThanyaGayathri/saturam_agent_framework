"""Registration and discovery seam: resolve any integration by ``(kind, name)``.

ADR 0001 forbids core, services and orchestration from importing a concrete adapter, but
they still have to *build* one -- a DB row naming ``model: "openai:gpt-4"`` must become a
``Model`` without ``services/factory`` importing ``egpt.integrations.models.openai``. This
module is that seam. It supersedes the per-kind construction tables that EGPT-1.4
through 1.8 left behind (``_PROVIDERS``, ``_DB_BACKENDS``, ``TOOLKIT_CLASSES``, the
embedder tuple, and the vector-db table that never existed).

Two registration modes, one table:

*In-tree providers are declared, not decorated.* The rows under ``_declarations/`` are
``(module, attr)`` **strings**, imported lazily on first touch of their kind. Enumerating
all 58 model providers imports none of them. This is load-bearing: the base install ships
no SDKs, so a decorator-based registry -- which must import a module to learn its name --
would make ``import egpt`` require every optional dependency at once. The guards in
``tests/unit/registry/test_discovery_laziness.py`` pin this mechanically.

*Out-of-tree providers use Python entry points*, which are lazy by nature -- an
``EntryPoint`` is a string until something calls ``load()``. A third-party package adds a
provider by declaring one, with no edit to this repository::

    [project.entry-points."egpt.models"]
    acme = "acme_egpt:AcmeModel"

``@register(kind, name)`` exists as a third mode for user and runtime code. It is
deliberately **not** used in-tree: decorating a class means importing it, which is exactly
what the declaration rows avoid. Code that registers its own class has already imported it,
so nothing is lost there.

``declarations(kind)`` and ``entries(kind)`` are not interchangeable. ``entries`` is the
runtime view and includes entry-point registrations; ``declarations`` is the in-tree view
that the architectural layer-guards read. A guard asserting "every row points at
``egpt.integrations.``" must read ``declarations``, or installing any third-party plugin
would fail the test suite.
"""

import importlib
import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

from egpt.utils.log import log_warning

# Every integration kind resolvable through this seam. Readers are deliberately absent:
# ReaderFactory keeps its own map (EGPT-1.9 scope decision), so core still names reader
# modules in one other place.
KINDS: Tuple[str, ...] = ("model", "db", "vectordb", "vectorstore", "graphdb", "tool", "embedder")

# Kind -> entry-point group suffix. The group name is derived, never hand-written, so the
# published strings cannot drift apart. Suffixes are plural because an entry-point
# group names a collection.
_GROUP_SUFFIX: Dict[str, str] = {
    "model": "models",
    "db": "dbs",
    "vectordb": "vectordbs",
    "vectorstore": "vectorstores",
    "graphdb": "graphdbs",
    "tool": "tools",
    "embedder": "embedders",
}

# Registration sources, in precedence order: a built-in declaration always wins over an
# entry point, and runtime registration is the caller's own code so it may displace either.
SOURCE_BUILTIN = "builtin"
SOURCE_ENTRY_POINT = "entry_point"
SOURCE_RUNTIME = "runtime"


def entry_point_group(kind: str) -> str:
    """The Python entry-point group a third-party package declares to add a ``kind``.

    These strings are a public contract -- renaming one silently orphans every plugin
    that already ships it -- so they are pinned by ``test_entry_point_discovery.py``.
    """
    _check_kind(kind)
    return "egpt." + _GROUP_SUFFIX[kind]


@dataclass(frozen=True, eq=False)
class Registration:
    """One resolvable integration.

    Either ``module``/``attr`` (the lazy form: nothing is imported until ``load``) or
    ``obj`` (an already-loaded class or factory, which only runtime registration produces).

    ``eq=False`` because ``obj`` may be an unhashable or expensively-comparable object;
    identity comparison is all this table needs.
    """

    kind: str
    name: str
    module: Optional[str] = None
    attr: Optional[str] = None
    obj: Optional[Any] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    source: str = SOURCE_BUILTIN
    origin: Optional[str] = None

    def __post_init__(self) -> None:
        if self.obj is None and not self.module:
            raise ValueError(f"Registration {self.kind}/{self.name} needs either 'obj' or 'module'.")
        # Freeze the metadata so a caller holding a Registration cannot mutate the table.
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def load(self) -> Any:
        """Import the target module and return the class or factory.

        The only ``import_module`` call in this module. An ``ImportError`` from a missing
        optional SDK propagates unwrapped: the integration's own message names the package
        to install, and replacing it with a generic failure would discard that.
        """
        if self.obj is not None:
            return self.obj
        target: Any = importlib.import_module(str(self.module))
        for part in str(self.attr).split("."):
            target = getattr(target, part)
        return target


_LOCK = threading.RLock()

# kind -> {name -> Registration}. Inner dicts are treated as immutable: a writer builds a
# new dict and rebinds, so readers never need the lock (dict rebinding is atomic under the
# GIL) and `resolve` -- which sits on the request-time hydration path -- never serializes.
_TABLE: Dict[str, Dict[str, Registration]] = {kind: {} for kind in KINDS}

# Kinds whose declaration module has been imported and whose entry points have been merged.
_READY: Set[str] = set()

# Kinds currently mid-bootstrap, so a declaration module that calls back into this module
# (via register) does not recurse into its own import.
_BOOTSTRAPPING: Set[str] = set()

# Bumped on every mutation of a kind. Consumers that derive indices from the table (see
# egpt/models/utils.py) key their cache on this, so an entry-point provider registered
# after those indices were first built does not stay invisible to them.
_VERSION: Dict[str, int] = {kind: 0 for kind in KINDS}

# Memoized result of the one entry_points() scan. None until the first scan runs.
_ENTRY_POINTS: Optional[Dict[str, List[Any]]] = None


def _check_kind(kind: str) -> None:
    if kind not in _TABLE:
        raise ValueError(f"Unknown integration kind '{kind}'. Known kinds: {', '.join(KINDS)}.")


def _all_entry_points() -> Dict[str, List[Any]]:
    """Every entry point in every group, scanned once per process.

    One scan sliced per kind rather than one scan per kind: ``entry_points()`` walks the metadata
    of every installed distribution regardless of which group is asked for.

    A broken third-party ``.dist-info`` must not take down model resolution for everybody,
    so any failure degrades to "no entry points" plus a warning.
    """
    global _ENTRY_POINTS
    if _ENTRY_POINTS is not None:
        return _ENTRY_POINTS

    groups = [entry_point_group(kind) for kind in KINDS]
    found: Dict[str, List[Any]] = {group: [] for group in groups}
    try:
        from importlib.metadata import entry_points

        # Typed as Any because the return shape is version-dependent: 3.10+ gives an
        # EntryPoints with .select(), 3.9 a plain dict of group -> entry points. mypy sees
        # only the interpreter it runs on, so the other branch is always an attribute error
        # to it -- and this package supports both.
        discovered: Any = entry_points()
        if hasattr(discovered, "select"):  # Python 3.10+
            for group in groups:
                found[group] = list(discovered.select(group=group))
        else:  # Python 3.9
            for group in groups:
                found[group] = list(discovered.get(group, []))
    except Exception as exc:
        log_warning(f"Could not scan entry points for integrations: {exc}. Only built-in integrations are available.")
        found = {group: [] for group in groups}

    _ENTRY_POINTS = found
    return found


def _entry_point_origin(entry_point: Any) -> str:
    """The distribution that shipped an entry point, for collision messages.

    ``EntryPoint.dist`` only exists on 3.10+, and even there it is None for entry points
    built by hand (which the hermetic tests do).
    """
    dist = getattr(entry_point, "dist", None)
    if dist is None:
        return "<unknown distribution>"
    metadata = getattr(dist, "metadata", None)
    if metadata is not None and hasattr(metadata, "get"):
        name = metadata.get("Name")
        if name:
            return str(name)
    return str(getattr(dist, "name", None) or "<unknown distribution>")


def _merge_entry_points(kind: str, table: Dict[str, Registration]) -> None:
    """Fold a kind's entry points into ``table``, which the caller owns and may mutate.

    Sorted by ``(origin, name)`` before merging because ``entry_points()`` iterates in
    ``sys.path`` order: without the sort, which of two colliding plugins wins would depend
    on install order and could differ between a developer's machine and CI.
    """
    group = entry_point_group(kind)
    candidates = []
    for entry_point in _all_entry_points().get(group, []):
        candidates.append((_entry_point_origin(entry_point), entry_point.name, entry_point))
    candidates.sort(key=lambda item: (item[0], item[1]))

    for origin, name, entry_point in candidates:
        existing = table.get(name)
        if existing is not None:
            if existing.source == SOURCE_BUILTIN:
                # Refuse rather than raise. The colliding entry point arrives from a
                # distribution the user may not control, and turning an unrelated
                # `pip install` into a hard failure of every model lookup would surface
                # the problem nowhere near its cause.
                log_warning(
                    f"Ignoring {kind} '{name}' from '{origin}': that name is a built-in integration. "
                    f"Register it under a different name to make it resolvable."
                )
            else:
                log_warning(
                    f"Ignoring {kind} '{name}' from '{origin}': already registered by "
                    f"'{existing.origin or existing.source}'."
                )
            continue

        value = getattr(entry_point, "value", "")
        if ":" not in value:
            # Skip only this row: one malformed declaration should not cost a distribution
            # its other, well-formed providers.
            log_warning(
                f"Ignoring {kind} '{name}' from '{origin}': entry point value '{value}' is not '<module>:<attribute>'."
            )
            continue

        module, _, attr = value.partition(":")
        table[name] = Registration(
            kind=kind,
            name=name,
            module=module.strip(),
            attr=attr.strip(),
            source=SOURCE_ENTRY_POINT,
            origin=origin,
        )


def _ensure_ready(kind: str) -> Dict[str, Registration]:
    """Import a kind's declaration module and merge its entry points, once."""
    _check_kind(kind)
    if kind in _READY:
        return _TABLE[kind]

    with _LOCK:
        if kind in _READY or kind in _BOOTSTRAPPING:
            return _TABLE[kind]
        _BOOTSTRAPPING.add(kind)
        try:
            try:
                module = importlib.import_module(f"egpt.registry._declarations.{_GROUP_SUFFIX[kind]}")
                # Call an explicit entry point rather than relying on the import itself to
                # have registered anything. Module bodies run once per process, so a
                # side-effect-based bootstrap can never be repeated -- and anything that
                # clears the table (a test fixture, `_reset_for_tests`) would leave the kind
                # permanently empty, with no error to say why.
                module.register_all()
            except ImportError as exc:  # pragma: no cover - a packaging fault, not a user error
                log_warning(f"Could not load built-in {kind} declarations: {exc}")
            table = dict(_TABLE[kind])
            _merge_entry_points(kind, table)
            _TABLE[kind] = table
            _VERSION[kind] += 1
            _READY.add(kind)
        finally:
            _BOOTSTRAPPING.discard(kind)
    return _TABLE[kind]


def register(
    kind: str,
    name: str,
    *,
    module: Optional[str] = None,
    attr: Optional[str] = None,
    obj: Optional[Any] = None,
    override: bool = False,
    source: str = SOURCE_RUNTIME,
    **metadata: Any,
) -> Any:
    """Register an integration, or return a decorator that registers the class it wraps.

    Used directly for the lazy form::

        register("model", "acme", module="acme_egpt.model", attr="AcmeModel")

    or as a decorator, which is the mode intended for user and runtime code::

        @register("model", "acme")
        class AcmeModel(Model): ...

    Raises:
        ValueError: ``name`` is already registered for ``kind`` and ``override`` is False.
            Unlike an entry-point collision (which is refused with a warning, because the
            user may not control the offending distribution), this is the caller's own
            code, so failing loudly is both safe and actionable.
    """
    _check_kind(kind)

    def _add(target: Optional[Any], target_module: Optional[str], target_attr: Optional[str]) -> None:
        registration = Registration(
            kind=kind,
            name=name,
            module=target_module,
            attr=target_attr,
            obj=target,
            metadata=metadata,
            source=source,
        )
        with _LOCK:
            table = dict(_TABLE[kind])
            if name in table and not override:
                raise ValueError(
                    f"{kind} '{name}' is already registered by "
                    f"'{table[name].origin or table[name].source}'. Pass override=True to replace it."
                )
            table[name] = registration
            _TABLE[kind] = table
            _VERSION[kind] += 1

    if obj is None and module is None:

        def decorator(target: Any) -> Any:
            # module/attr are recorded for introspection only -- `obj` is the load path,
            # since a decorated class is already imported by definition.
            _add(target, getattr(target, "__module__", None), attr or getattr(target, "__name__", None))
            return target

        return decorator

    _add(obj, module, attr)
    return obj


def unregister(kind: str, name: str) -> bool:
    """Remove a registration. Returns whether there was one to remove."""
    _check_kind(kind)
    with _LOCK:
        table = dict(_TABLE[kind])
        removed = table.pop(name, None)
        if removed is None:
            return False
        _TABLE[kind] = table
        _VERSION[kind] += 1
        return True


def get(kind: str, name: str) -> Optional[Registration]:
    """The ``Registration`` for ``name``, or None. Imports nothing."""
    return _ensure_ready(kind).get(name)


def entries(kind: str) -> Mapping[str, Registration]:
    """Every registration for ``kind``, **including** out-of-tree entry points.

    The runtime view. Use ``declarations`` in architectural guards -- see the module
    docstring for why the distinction matters.
    """
    return MappingProxyType(dict(_ensure_ready(kind)))


def declarations(kind: str) -> Mapping[str, Registration]:
    """Only the integrations this repository ships, ignoring entry points.

    The layer-guards read this. A guard asserting every row points into
    ``egpt.integrations.`` would fail on any machine with a third-party plugin installed
    if it read ``entries`` instead.
    """
    return MappingProxyType({name: reg for name, reg in _ensure_ready(kind).items() if reg.source == SOURCE_BUILTIN})


def names(kind: str) -> Tuple[str, ...]:
    """Every registered name for ``kind``, sorted. Imports no integration module."""
    return tuple(sorted(_ensure_ready(kind)))


def version(kind: str) -> int:
    """A counter bumped on every mutation of ``kind``'s table.

    Consumers deriving indices from the table key their cache on this so a provider
    registered after those indices were built does not stay invisible to them.
    """
    _check_kind(kind)
    return _VERSION[kind]


def resolve(kind: str, name: str) -> Any:
    """Import and return the class or factory registered under ``(kind, name)``.

    Raises:
        ValueError: ``name`` is not registered. Every seam this replaced already raised
            ValueError for an unknown name, so call sites keep their contract.
        ImportError: the integration's optional dependency is not installed. Propagated
            unwrapped so the message still names the package to install.
    """
    registration = _ensure_ready(kind).get(name)
    if registration is None:
        known = ", ".join(sorted(_ensure_ready(kind))) or "<none>"
        raise ValueError(f"Unknown {kind} '{name}'. Registered {kind}s: {known}.")
    # Deliberately outside the lock: holding a lock across an import is the classic
    # deadlock against the import machinery, and RLock does not help across threads.
    return registration.load()


def construct(kind: str, name: str, **config: Any) -> Any:
    """Build the integration registered under ``(kind, name)``, forwarding ``config``.

    Sync-only by design: this builds an object and has nothing to await, so an async
    variant would be dead weight (the same rationale as ``BaseDb.clone_with_table_overrides``).
    """
    return resolve(kind, name)(**config)


def _kind_constructor(kind: str) -> Callable[..., Any]:
    def _construct(name: str, **config: Any) -> Any:
        return construct(kind, name, **config)

    return _construct


# ADR 0001's construct_* vocabulary, as thin wrappers so there is exactly one implementation.
construct_model = _kind_constructor("model")
construct_db = _kind_constructor("db")
construct_vector_db = _kind_constructor("vectordb")
construct_vector_store = _kind_constructor("vectorstore")
construct_graph_db = _kind_constructor("graphdb")
construct_tool = _kind_constructor("tool")
construct_embedder = _kind_constructor("embedder")


def _reset_for_tests() -> None:
    """Drop all discovery state. For test fixtures only."""
    global _ENTRY_POINTS
    with _LOCK:
        for kind in KINDS:
            _TABLE[kind] = {}
            _VERSION[kind] += 1
        _READY.clear()
        _BOOTSTRAPPING.clear()
        _ENTRY_POINTS = None
