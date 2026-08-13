"""Portable import/export of component definitions (EGPT-2.8).

A stored definition is a row, and a row does not travel. This module turns one into a
**bundle** -- a self-contained JSON document holding the component, every component it
depends on, and the links between them -- and turns a bundle back into rows in a target
tenant. Seeding a new tenant from templates is the same operation: a template file *is* a
bundle, so there is one format, one parser and one validator rather than two that drift.

Three things about this module are load-bearing and read like arbitrary choices without the
reason:

* **It is core, so it never names ``Agent`` / ``Team`` / ``Workflow``.** ADR 0001 forbids
  core -> orchestration, and ``tests/unit/registry/test_schema_layering.py`` enforces it in a
  clean interpreter. But an imported definition must clear the same EGPT-2.2 bar as an API
  write, and that validator needs the component classes. So the validator arrives *injected*
  as a ``ConfigValidator``, exactly the way ``ComponentSpec.hydrate`` closes the same gap in
  ``egpt.registry.schema``. ``egpt.components.io`` is the layer that binds it.
* **Export redacts by default.** ``PostgresDb.to_dict`` emits ``db_url`` and ``Agent.to_dict``
  embeds it, so a raw config can carry a DSN with a password. A "portable JSON file" that
  quietly contains production credentials is a worse outcome than one that needs a ``--db-id``
  on the way in. See ``redact_config``.
* **A bundle is always dependency-closed.** A team whose members are missing does not degrade
  into a smaller team -- ``Team._hydrate_from_graph`` refuses to build a partial one. Carrying
  the closure is what makes "import produces a hydratable definition" true rather than true
  only when the target happens to already hold the members.

What deliberately does **not** travel: version history, ``current_version``, the audit trail,
and the tenant. Versions renumber in the target, the audit trail is the target's own record of
what happened there, and a definition takes the tenant it is imported into. Carrying any of
them would only add envelope fields that import has to ignore.
"""

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Set, Tuple, Union

from egpt.db.base import ComponentType
from egpt.db.component_scope import TenantScopeRequiredError
from egpt.registry.schema import COMPONENT_TYPES, SCHEMA_VERSION, migrate, stamp_schema_version
from egpt.utils.log import log_error, log_warning

__all__ = [
    "EXPORT_FORMAT",
    "EXPORT_FORMAT_VERSION",
    "REDACTED",
    "ComponentEntry",
    "ComponentIOError",
    "ComponentIdConflictError",
    "ComponentSoftDeletedError",
    "ConfigValidator",
    "ImportResult",
    "configs_equal",
    "dump_document",
    "export_component",
    "import_document",
    "parse_document",
    "redact_config",
    "rewrite_component_ids",
]

EXPORT_FORMAT = "egpt.component-bundle"
EXPORT_FORMAT_VERSION = 1

#: Stand-in for a value the generic secret scan removed. The structural ``db`` rules below
#: *drop* their keys instead: a sentinel ``db_url`` would be handed to a driver and produce a
#: confusing connection error, where an absent one produces a clean "unresolvable db".
REDACTED = "__redacted__"

#: The link fields that mean the same thing in any database. ``child_version`` is excluded on
#: purpose -- it is the one field that is meaningless across databases, and import re-resolves it.
_PORTABLE_LINK_FIELDS = ("link_kind", "link_key", "child_component_id", "position", "meta")

#: Connection fields dropped from a config's ``db`` block on export. Covers `PostgresDb`
#: (`db_url`), `SqliteDb` (`db_file`, `db_url`) and `ClickhouseDb` (`host`, `password`), plus
#: the shape any future SQLAlchemy backend will serialize.
#:
#: What survives is the part that lets the config still resolve: ``id``, ``type``,
#: ``db_schema``, ``table_prefix`` and the per-table overrides -- exactly what
#: ``resolve_db_from_config`` needs to find a registered instance by id.
_DB_CONNECTION_KEYS = frozenset(
    {"db_url", "db_file", "connection_string", "uri", "host", "port", "user", "username", "password"}
)

#: Separator-anchored, and plural-tolerant: `api_keys` is the natural shape for a dict of
#: per-provider credentials, and requiring a separator after the singular let every plural
#: through. What stops `max_tokens` from matching is not the pattern but the value-type gate in
#: ``_redact_tree`` -- a credential is never an int, so numbers are never redacted whatever
#: their key is called. That split is deliberate: trying to express "not max_tokens" in the
#: regex is how the plurals got excluded in the first place.
_SECRET_KEY_RE = re.compile(
    r"(^|[_\-.])"
    r"(api[_-]?keys?|secrets?|passwords?|passwds?|tokens?|credentials?"
    r"|private[_-]?keys?|access[_-]?keys?|authorization)"
    r"([_\-.]|$)",
    re.IGNORECASE,
)

#: Values that can carry a credential. A number or a boolean cannot, so `max_tokens: 4096` and
#: `num_history_runs: 3` survive regardless of what the key is called; a dict or list *can*
#: (``api_keys: {"openai": "sk-..."}``) and is redacted whole, because its inner keys are
#: provider names that no key pattern would catch.
_REDACTABLE_TYPES = (str, dict, list)

#: A URL carrying userinfo -- `postgresql+psycopg://ai:hunter2@host/ai` -- wherever it is parked.
#: The structural rules above cover the `db` block; this catches the same string under a key we
#: do not know about, which is the plausible shape of the next leak (an MCP or toolkit config).
#:
#: Searched rather than anchored: a DSN pasted mid-sentence into a description or a note is the
#: same secret as one that is the whole value, and free-text fields are exactly where that
#: happens.
_DSN_WITH_USERINFO_RE = re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s@]*:[^@/\s]+@", re.IGNORECASE)

#: Envelope fields that travel beside the config and can carry the same secrets. ``description``
#: and ``notes`` are free text an operator writes, which is precisely where a connection string
#: gets pasted while debugging.
_SCANNED_PAYLOAD_FIELDS = ("name", "description", "metadata", "notes", "label")

_CONFLICT_POLICIES = ("error", "skip", "new_version")
_DELETED_POLICIES = ("error", "skip")
_STAGES = ("draft", "published")


class ComponentIOError(ValueError):
    """A bundle could not be produced, parsed, or applied.

    Subclasses ``ValueError`` so the components router's existing ``except ValueError`` arm
    turns it into a 400 even before anything is taught to recognise it specifically.

    ``results`` carries the components already written when the failure happened. It is empty
    for everything raised before the write phase -- which is almost everything -- and non-empty
    only when a write partway through the bundle failed. There is no cross-component
    transaction in ``BaseDb`` to roll that back, so the honest alternative to reporting it is
    leaving the caller to discover it, which is worse.
    """

    def __init__(self, *args: Any, results: Optional[Sequence["ImportResult"]] = None) -> None:
        super().__init__(*args)
        self.results: List["ImportResult"] = list(results or [])


class ComponentIdConflictError(ComponentIOError):
    """The target database already holds this component id.

    ``component_id`` is the global primary key, not a per-tenant one, so this fires whether the
    holder is the caller's tenant or another one -- and the message is identical either way.
    That is the same doctrine the components router applies to cross-tenant id collisions: the
    collision is observable regardless, so naming the owner would only hand a prober a cleaner
    ownership oracle without hiding anything.
    """

    def __init__(self, component_id: str) -> None:
        super().__init__(f"Component {component_id} already exists")
        self.component_id = component_id


class ComponentSoftDeletedError(ComponentIOError):
    """The id is held by a soft-deleted component, which nothing can import over.

    Detected at write time rather than in the pre-pass, because no read API can see such a row:
    ``get_component`` and ``list_configs`` both filter ``deleted_at IS NULL``, yet the row still
    holds the primary key, so the create is what surfaces it. There is no undelete in ``BaseDb``,
    so the only honest resolutions are a hard delete or a different id.
    """

    def __init__(self, component_id: str) -> None:
        super().__init__(
            f"Component {component_id} already exists as a soft-deleted component. "
            f"Hard-delete it (delete_component(hard_delete=True)) or import under a different id."
        )
        self.component_id = component_id


@dataclass(frozen=True)
class ComponentEntry:
    """One component inside a bundle: the component row, its config, and its outgoing links."""

    component_id: str
    component_type: str
    config: Dict[str, Any]
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    stage: str = "draft"
    label: Optional[str] = None
    notes: Optional[str] = None
    links: List[Dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "component_id": self.component_id,
            "component_type": self.component_type,
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata,
            "stage": self.stage,
            "label": self.label,
            "notes": self.notes,
            "config": self.config,
            "links": self.links,
        }


@dataclass(frozen=True)
class ImportResult:
    """What import did to one component.

    ``unchanged`` means no write at all -- not even an audit row. That is the assertion the
    idempotency test leans on, since EGPT-2.6 guarantees no successful mutation commits without
    one.
    """

    component_id: str
    component_type: str
    action: str
    version: Optional[int] = None
    reason: Optional[str] = None


class ConfigValidator(Protocol):
    """What core needs from the layer that owns the component classes.

    The same injection ``ComponentSpec`` uses, and for the same reason: the EGPT-2.2 validator
    has to build an ``Agent`` to dry-run a config, and core may not import one. ``None`` skips
    validation entirely, which is only appropriate for a caller that has already validated.
    """

    def __call__(
        self,
        config: Dict[str, Any],
        component_type: str,
        *,
        component_id: Optional[str] = None,
        links: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]: ...


# ---------------------------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------------------------


def redact_config(config: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Strip credentials from a config, returning the copy and the paths that were removed.

    Two layers. The structural one drops ``_DB_CONNECTION_KEYS`` from the ``db`` block, which is
    the only *proven* secret vector in a config today: ``Model.to_dict`` emits
    ``{name, id, provider}`` and ``Function.to_dict`` uses an explicit include-list, so neither
    can carry one. The generic one is a recursive scan for credential-shaped keys and DSNs, and
    exists because the next leak will come from something not yet written -- an MCP server block,
    a toolkit that grows a ``to_dict``.

    The config is never mutated; the returned paths are relative to it (``db.db_url``).
    """
    redacted: List[str] = []
    working = _redact_db_block(config, redacted)
    working = _redact_tree(working, "", redacted)
    return working, redacted


def _redact_db_block(config: Dict[str, Any], redacted: List[str]) -> Dict[str, Any]:
    db_block = config.get("db")
    if not isinstance(db_block, dict):
        return copy.deepcopy(config)

    kept: Dict[str, Any] = {}
    for key, value in db_block.items():
        if key in _DB_CONNECTION_KEYS and value is not None:
            redacted.append(f"db.{key}")
            continue
        kept[key] = value

    result = copy.deepcopy(config)
    result["db"] = copy.deepcopy(kept)
    return result


def _redact_tree(value: Any, path: str, redacted: List[str]) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and isinstance(item, _REDACTABLE_TYPES) and _SECRET_KEY_RE.search(key):
                redacted.append(child_path)
                out[key] = REDACTED
                continue
            out[key] = _redact_tree(item, child_path, redacted)
        return out
    if isinstance(value, list):
        return [_redact_tree(item, f"{path}[{index}]", redacted) for index, item in enumerate(value)]
    if isinstance(value, str) and _DSN_WITH_USERINFO_RE.search(value):
        redacted.append(path)
        return REDACTED
    return value


def redact_payload_fields(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Scan the envelope fields that travel beside the config.

    Separate from ``redact_config`` because the two need different treatment, and conflating
    them is a regression waiting to happen: ``redact_config`` *drops* the ``db`` block's
    connection keys, and running it over a whole payload would instead leave a ``__redacted__``
    sentinel there -- the exact failure the note on ``REDACTED`` exists to prevent. These
    fields have no structural rules, only the generic scan.
    """
    redacted: List[str] = []
    result = dict(payload)
    for key in _SCANNED_PAYLOAD_FIELDS:
        if key in result:
            result[key] = _redact_tree(result[key], key, redacted)
    return result, redacted


def strip_redacted(value: Any, path: str = "", stripped: Optional[List[str]] = None) -> Tuple[Any, List[str]]:
    """Remove ``__redacted__`` sentinels, returning the value and the paths that held one.

    Import calls this so a redacted bundle does not land as a component that *looks* configured
    and fails on first use with a credential of the literal string ``__redacted__``. Dropping
    the key is the same doctrine the ``db`` block already follows: an absent ``api_key`` falls
    through to the provider SDK's environment lookup, which is usually the right answer in the
    target, where a sentinel is never the right answer anywhere.

    It also stops the sentinel compounding -- re-exporting an imported bundle would otherwise
    re-detect ``__redacted__`` as a fresh secret and grow the manifest on every hop.
    """
    collected: List[str] = [] if stripped is None else stripped
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if item == REDACTED:
                collected.append(child_path)
                continue
            out[key], _ = strip_redacted(item, child_path, collected)
        return out, collected
    if isinstance(value, list):
        items = []
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]"
            if item == REDACTED:
                collected.append(child_path)
                continue
            cleaned, _ = strip_redacted(item, child_path, collected)
            items.append(cleaned)
        return items, collected
    return value, collected


# ---------------------------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------------------------


def dump_document(document: Mapping[str, Any]) -> str:
    """Serialize a bundle canonically.

    Sorted and indented because bundles are checked into git as templates: a dump whose key
    order varies per run turns every re-export into a diff nobody can review.
    """
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def parse_document(raw: Union[str, bytes, Mapping[str, Any]]) -> Dict[str, Any]:
    """Parse and structurally validate a bundle, returning a copy the caller may mutate.

    This checks the envelope only -- that the components are well-formed enough to iterate.
    Whether each *config* is a legal definition is the injected validator's job, which runs per
    component inside ``import_document``.

    Raises:
        ComponentIOError: the document is not a bundle this build can read.
    """
    if isinstance(raw, (str, bytes)):
        try:
            document = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ComponentIOError(f"Not a valid JSON document: {exc}") from exc
    elif isinstance(raw, Mapping):
        document = copy.deepcopy(dict(raw))
    else:
        raise ComponentIOError(f"Expected JSON text or a mapping, got {type(raw).__name__}.")

    if not isinstance(document, dict):
        raise ComponentIOError("A component bundle must be a JSON object.")

    if document.get("format") != EXPORT_FORMAT:
        raise ComponentIOError(
            f"Not a component bundle: expected format '{EXPORT_FORMAT}', got {document.get('format')!r}."
        )

    format_version = document.get("format_version")
    if not isinstance(format_version, int) or isinstance(format_version, bool):
        raise ComponentIOError(f"format_version must be an integer, got {format_version!r}.")
    if format_version > EXPORT_FORMAT_VERSION:
        raise ComponentIOError(
            f"This bundle is format_version {format_version}; this build reads up to "
            f"{EXPORT_FORMAT_VERSION}. Upgrade before importing it."
        )

    components = document.get("components")
    if not isinstance(components, list) or not components:
        raise ComponentIOError("A component bundle must carry a non-empty 'components' list.")

    seen: Set[str] = set()
    for index, entry in enumerate(components):
        _check_entry_shape(entry, index, seen)

    document["components"] = components
    return document


def _check_entry_shape(entry: Any, index: int, seen: Set[str]) -> None:
    where = f"components[{index}]"
    if not isinstance(entry, dict):
        raise ComponentIOError(f"{where} must be an object.")

    component_id = entry.get("component_id")
    if not isinstance(component_id, str) or not component_id:
        raise ComponentIOError(f"{where}.component_id must be a non-empty string.")
    if component_id in seen:
        raise ComponentIOError(f"{where}.component_id '{component_id}' appears twice in the bundle.")
    seen.add(component_id)

    component_type = entry.get("component_type")
    if component_type not in COMPONENT_TYPES:
        raise ComponentIOError(
            f"{where}.component_type must be one of {', '.join(COMPONENT_TYPES)}, got {component_type!r}."
        )

    if not isinstance(entry.get("config"), dict):
        raise ComponentIOError(f"{where}.config must be an object.")

    # Required, though the Postgres column is nullable: SQLite's is not, so an entry without a
    # name reaches a NOT NULL constraint and comes back as an IntegrityError -- which is not a
    # ValueError, so it escapes the router's 400 arm and surfaces as a 500 for what is simply a
    # malformed caller document. Export always emits one.
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ComponentIOError(f"{where}.name must be a non-empty string.")

    stage = entry.get("stage", "draft")
    if stage not in _STAGES:
        raise ComponentIOError(f"{where}.stage must be 'draft' or 'published', got {stage!r}.")

    links = entry.get("links") or []
    if not isinstance(links, list):
        raise ComponentIOError(f"{where}.links must be a list.")
    for link_index, link in enumerate(links):
        if not isinstance(link, dict):
            raise ComponentIOError(f"{where}.links[{link_index}] must be an object.")
        for required in ("link_kind", "link_key", "child_component_id"):
            if not isinstance(link.get(required), str) or not link[required]:
                raise ComponentIOError(f"{where}.links[{link_index}].{required} must be a non-empty string.")


def _entry_from_payload(payload: Mapping[str, Any]) -> ComponentEntry:
    return ComponentEntry(
        component_id=payload["component_id"],
        component_type=payload["component_type"],
        config=copy.deepcopy(payload.get("config") or {}),
        name=payload.get("name"),
        description=payload.get("description"),
        metadata=payload.get("metadata"),
        stage=payload.get("stage") or "draft",
        label=payload.get("label"),
        notes=payload.get("notes"),
        links=[_portable_link(link) for link in payload.get("links") or []],
    )


def _portable_link(link: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep only the fields that mean the same thing in another database."""
    result = {key: link.get(key) for key in _PORTABLE_LINK_FIELDS if link.get(key) is not None}
    result.setdefault("position", 0)
    return result


# ---------------------------------------------------------------------------------------------
# Id rewriting
# ---------------------------------------------------------------------------------------------


def rewrite_component_ids(document: Mapping[str, Any], id_map: Mapping[str, str]) -> Dict[str, Any]:
    """Rename components throughout a bundle, references included.

    Ids live in five places, and hydration reads more than one of them, so rewriting fewer than
    all five produces a bundle that imports and then fails to hydrate:

    1. the entry's ``component_id``
    2. ``config["id"]`` -- the self-id, which a parent's serialized members are matched against
    3. ``links[*].child_component_id``
    4. a team config's ``members[*].agent_id`` / ``team_id``
    5. a workflow config's step ``agent_id`` / ``team_id`` / ``workflow_id``, recursed through
       the containers (``steps``, ``else_steps``, ``choices``)

    An id absent from ``id_map`` is left alone: a bundle may legitimately link to a component
    that already exists in the target. ``config["db"]["id"]`` is a *database* id, never a
    component id, and is never touched here -- ``import_document``'s ``db_id`` owns that.
    """
    result = copy.deepcopy(dict(document))
    rewritten: List[Dict[str, Any]] = []
    for payload in result.get("components") or []:
        entry = dict(payload)
        old_id = entry.get("component_id")
        new_id = id_map.get(old_id, old_id) if isinstance(old_id, str) else old_id
        entry["component_id"] = new_id

        config = dict(entry.get("config") or {})
        if isinstance(config.get("id"), str) and config["id"] in id_map:
            config["id"] = id_map[config["id"]]
        _rewrite_members(config, id_map)
        _rewrite_steps(config.get("steps"), id_map)
        entry["config"] = config

        links = []
        for link in entry.get("links") or []:
            link = dict(link)
            child = link.get("child_component_id")
            if isinstance(child, str) and child in id_map:
                link["child_component_id"] = id_map[child]
            links.append(link)
        entry["links"] = links
        rewritten.append(entry)

    result["components"] = rewritten
    root = result.get("root")
    if isinstance(root, str) and root in id_map:
        result["root"] = id_map[root]
    return result


_MEMBER_ID_KEYS = ("agent_id", "team_id", "workflow_id")


def _rewrite_members(config: Dict[str, Any], id_map: Mapping[str, str]) -> None:
    members = config.get("members")
    if not isinstance(members, list):
        return
    config["members"] = [_rewrite_ref(member, id_map) for member in members]


def _rewrite_steps(steps: Any, id_map: Mapping[str, str]) -> None:
    """Walk a serialized step tree in place, renaming executor references.

    The container attributes mirror the ones `Workflow.save` recurses through when it pins
    child versions -- ``steps`` (Steps/Loop/Parallel/Condition), ``else_steps`` (Condition) and
    ``choices`` (Router). Keeping the two lists identical is what stops a workflow from being
    saveable but not renameable.
    """
    if isinstance(steps, list):
        for step in steps:
            _rewrite_steps(step, id_map)
        return
    if not isinstance(steps, dict):
        return
    for key in _MEMBER_ID_KEYS:
        value = steps.get(key)
        if isinstance(value, str) and value in id_map:
            steps[key] = id_map[value]
    for container in ("steps", "else_steps", "choices"):
        _rewrite_steps(steps.get(container), id_map)


def _rewrite_ref(ref: Any, id_map: Mapping[str, str]) -> Any:
    if not isinstance(ref, dict):
        return ref
    result = dict(ref)
    for key in _MEMBER_ID_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value in id_map:
            result[key] = id_map[value]
    return result


# ---------------------------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------------------------


def configs_equal(stored: Mapping[str, Any], desired: Mapping[str, Any], component_type: str) -> bool:
    """Would writing ``desired`` over ``stored`` change anything?

    Both sides are normalized through ``migrate`` and ``stamp_schema_version`` first, so a
    schema bump does not make every stored config look different from every template, and a
    row written before stamping compares equal to a freshly stamped one.

    The caller's half of the contract matters more than anything here does: ``desired`` must be
    the config that would *actually be written* -- after db resolution and after validation --
    not the raw template. A template says ``db: {"id": "os-db"}`` while the stored row holds the
    resolved block with ``type`` and connection fields merged in, so comparing the raw file
    reports a difference on every run and seeding appends a version forever.

    ``default=str`` keeps a non-JSON-native value from raising mid-seed: a conservative
    "not equal" is a better failure than a ``TypeError``.
    """
    return _canonical(stored, component_type) == _canonical(desired, component_type)


def _canonical(config: Mapping[str, Any], component_type: str) -> str:
    normalized = stamp_schema_version(migrate(dict(config), component_type))
    return json.dumps(normalized, sort_keys=True, default=str)


# ---------------------------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------------------------


def export_component(
    db: Any,
    component_id: str,
    *,
    version: Optional[int] = None,
    label: Optional[str] = None,
    tenant_id: Optional[str] = None,
    include_dependencies: bool = True,
    include_secrets: bool = False,
) -> Dict[str, Any]:
    """Export a component definition, and by default everything it depends on, as a bundle.

    Args:
        db: A sync ``BaseDb`` with component support (Postgres or SQLite today).
        component_id: The component to export.
        version: A specific version. ``None`` selects the current one, falling back to the
            latest draft the way ``get_config`` does.
        label: A labelled version. Ignored when ``version`` is given.
        tenant_id: Scopes every read. ``None`` applies no tenant predicate.
        include_dependencies: Walk members/steps and carry them in the bundle. Turning this off
            produces a bundle that only imports where the children already exist.
        include_secrets: Keep connection fields instead of redacting them. Off by default; see
            ``redact_config``.

    Returns:
        The bundle, ready for ``dump_document``.

    Raises:
        ComponentIOError: the component is missing, has no config, or its graph contains a cycle
            or a child whose version cannot be resolved.
    """
    component = db.get_component(component_id, tenant_id=tenant_id)
    if component is None:
        raise ComponentIOError(f"Component '{component_id}' not found.")

    config_row = db.get_config(component_id, version=version, label=label, tenant_id=tenant_id)
    if config_row is None:
        raise ComponentIOError(f"Component '{component_id}' has no config to export.")
    resolved_version = config_row["version"]

    entries: List[ComponentEntry] = []
    if include_dependencies:
        # The version is passed explicitly rather than left to the graph loader: it resolves
        # `current_version`, which stays NULL until something is published, so a draft-only
        # component would come back as None here.
        graph = db.load_component_graph(component_id, version=resolved_version, tenant_id=tenant_id)
        if graph is None:
            raise ComponentIOError(f"Could not load the component graph for '{component_id}'.")
        _flatten_graph(graph, entries, {})
    else:
        links = db.get_links(component_id, resolved_version, tenant_id=tenant_id)
        entries.append(_entry_from_rows(component, config_row, links))

    payloads: List[Dict[str, Any]] = []
    redacted_paths: List[str] = []
    for index, entry in enumerate(entries):
        payload = entry.to_payload()
        if not include_secrets:
            # Both halves, because a bundle entry is nine fields and only one of them is the
            # config. `description` and `notes` are free text and `metadata` is an open dict --
            # scanning only the config left five fields carrying whatever an operator put in
            # them while `redacted` reported an empty list, which is worse than reporting
            # nothing: it certifies the file as clean.
            config, config_paths = redact_config(entry.config)
            payload["config"] = config
            redacted_paths.extend(f"components[{index}].config.{path}" for path in config_paths)

            payload, field_paths = redact_payload_fields(payload)
            redacted_paths.extend(f"components[{index}].{path}" for path in field_paths)
        payloads.append(payload)

    document: Dict[str, Any] = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "component_id": component_id,
            "tenant_id": tenant_id,
            "db_id": getattr(db, "id", None),
        },
        "root": component_id,
        "components": payloads,
        "redacted": redacted_paths,
    }
    if include_secrets:
        # Explicit so a CI check or a git hook can refuse to accept the file, rather than
        # having to re-derive from the absence of a `redacted` list whether it is safe.
        document["contains_secrets"] = True
    return document


def _flatten_graph(node: Mapping[str, Any], out: List[ComponentEntry], seen: Dict[str, int]) -> None:
    """Emit the graph children-first, deduplicated.

    Children first because link rows reference a ``child_version`` that has to exist, and
    Postgres declares the foreign key. Deduplicated because a diamond -- one agent used by two
    teams -- is a legitimate shape, not a cycle.
    """
    component = node["component"]
    component_id = component["component_id"]

    if node.get("cycle_detected"):
        raise ComponentIOError(
            f"Cannot export '{component_id}': its dependency graph contains a cycle. Break the cycle before exporting."
        )

    for child in node.get("children") or []:
        child_graph = child.get("graph")
        if child_graph is None:
            link = child.get("link") or {}
            raise ComponentIOError(
                f"Cannot export '{component_id}': its {link.get('link_kind', 'link')} to "
                f"'{link.get('child_component_id')}' cannot be resolved "
                f"({child.get('error') or 'child unavailable'}). Exporting it would produce a "
                f"bundle that cannot be imported."
            )
        _flatten_graph(child_graph, out, seen)

    config_row = node["config"]
    version = config_row["version"]
    if component_id in seen:
        if seen[component_id] != version:
            raise ComponentIOError(
                f"Cannot export '{component_id}': the graph pins it at both version "
                f"{seen[component_id]} and version {version}, and a bundle carries one config "
                f"per component."
            )
        return
    seen[component_id] = version

    links = [child["link"] for child in node.get("children") or [] if child.get("link")]
    out.append(_entry_from_rows(component, config_row, links))


def _entry_from_rows(
    component: Mapping[str, Any],
    config_row: Mapping[str, Any],
    links: Sequence[Mapping[str, Any]],
) -> ComponentEntry:
    return ComponentEntry(
        component_id=component["component_id"],
        component_type=component["component_type"],
        config=copy.deepcopy(config_row.get("config") or {}),
        name=component.get("name"),
        description=component.get("description"),
        metadata=copy.deepcopy(component.get("metadata")),
        stage=config_row.get("stage") or "draft",
        label=config_row.get("label"),
        notes=config_row.get("notes"),
        links=[_portable_link(link) for link in links],
    )


# ---------------------------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------------------------


@dataclass
class _Plan:
    """One component's decided outcome, before anything is written."""

    entry: ComponentEntry
    config: Dict[str, Any]
    action: str
    stage: str
    reason: Optional[str] = None
    #: The existing version this plan acts on, for actions that touch one in place ("published").
    version: Optional[int] = None


def import_document(
    db: Any,
    document: Union[str, bytes, Mapping[str, Any]],
    *,
    tenant_id: Optional[str] = None,
    validate: Optional[ConfigValidator] = None,
    on_conflict: str = "error",
    on_deleted: str = "error",
    id_map: Optional[Mapping[str, str]] = None,
    id_prefix: Optional[str] = None,
    new_component_id: Optional[str] = None,
    db_id: Optional[str] = None,
    drop_db: bool = False,
    stage: Optional[str] = None,
    actor: Optional[str] = None,
    audit_note: Optional[str] = None,
    dry_run: bool = False,
) -> List[ImportResult]:
    """Write a bundle into a target tenant.

    Runs in two phases, and the split is the point: **everything is resolved, validated and
    conflict-probed before anything is written.** A bundle that fails validation on its third
    component leaves nothing behind, rather than two orphans and an error.

    Phase two then writes children before parents, whatever order the document lists them in,
    so a link can never point at a component this run has not created yet.

    What phase one cannot cover: a soft-deleted id (no read API can see one, see
    ``ComponentSoftDeletedError``) and a database-level fault. Writes are per-component
    transactions -- ``BaseDb`` has no cross-component transaction -- so either can leave a
    partial bundle. When that happens the components already written are attached to the raised
    ``ComponentIOError`` as ``.results`` and logged, because the alternative to reporting a
    partial write is not preventing it, it is hiding it. Re-running with
    ``on_conflict="new_version"`` converges.

    Args:
        db: A sync ``BaseDb`` with component support.
        document: A bundle, as JSON text or an already-parsed mapping.
        tenant_id: The tenant the imported rows are stamped with, and the scope of every
            ownership check. ``None`` writes untenanted rows.
        validate: The EGPT-2.2 validator, injected. ``None`` skips validation and only stamps
            the schema version -- appropriate only for a caller that has already validated.
        on_conflict: What to do when the id is taken -- ``error`` (default), ``skip``, or
            ``new_version`` (append a version to a component this tenant already owns; a
            foreign-tenant holder degrades to ``error``, never to a write).
        on_deleted: ``error`` (default) or ``skip`` when the id is held by a soft-deleted row.
        id_map: Explicit old-id to new-id renames. Takes precedence over ``id_prefix``.
        id_prefix: Prefix every id in the bundle. The usual answer to the global-primary-key
            collision when seeding the same corpus into a second tenant.
        new_component_id: Rename the single component in the bundle. Rejected for a
            multi-component bundle, where "rename the bundle" has no single meaning.
        db_id: Point every config's existing ``db`` block at this database id. Configs without
            a ``db`` block are left alone.
        drop_db: Remove the ``db`` block entirely, so hydration attaches the caller's database.
        stage: Override the stage every component is written at. ``None`` keeps each entry's own.
        actor: Principal recorded on the audit rows.
        audit_note: Reason recorded on the audit rows. Defaults per entry to its ``notes``.
        dry_run: Do phase one and stop. Nothing is written; the returned actions are what would
            have happened.

    Returns:
        One ``ImportResult`` per component, in the order they were applied (children first).

    Raises:
        ComponentIOError: the bundle is unreadable, an id collides under ``on_conflict="error"``,
            or a link points at a component that is neither in the bundle nor in the target.
        ConfigValidationError: a config is not a legal definition (raised by ``validate``).
    """
    if on_conflict not in _CONFLICT_POLICIES:
        raise ComponentIOError(f"on_conflict must be one of {', '.join(_CONFLICT_POLICIES)}, got {on_conflict!r}.")
    if on_deleted not in _DELETED_POLICIES:
        raise ComponentIOError(f"on_deleted must be one of {', '.join(_DELETED_POLICIES)}, got {on_deleted!r}.")
    if stage is not None and stage not in _STAGES:
        raise ComponentIOError(f"stage must be 'draft' or 'published', got {stage!r}.")

    parsed = parse_document(document)
    bundle_ids = [payload["component_id"] for payload in parsed["components"]]

    effective_map = _build_id_map(bundle_ids, id_map=id_map, id_prefix=id_prefix, new_component_id=new_component_id)
    if effective_map:
        parsed = rewrite_component_ids(parsed, effective_map)

    entries = [_entry_from_payload(payload) for payload in parsed["components"]]
    bundle_members = {entry.component_id for entry in entries}

    plans = [
        _plan_entry(db, entry, tenant_id, validate, on_conflict, on_deleted, db_id, drop_db, stage) for entry in entries
    ]
    _check_links_resolvable(db, entries, bundle_members, tenant_id)

    # Children before parents, regardless of the order the document happens to list them in. A
    # parent-first bundle passes every phase-one check -- its child *is* present -- and then
    # dies mid-write on a link to a component that has not been created yet, leaving whatever
    # was written before it. Sorting here removes that failure rather than reporting it.
    plans = _topologically_order(plans)

    if dry_run:
        return [ImportResult(p.entry.component_id, p.entry.component_type, p.action, None, p.reason) for p in plans]

    results: List[ImportResult] = []
    versions: Dict[str, int] = {}
    for plan in plans:
        try:
            result = _apply_plan(
                db,
                plan,
                versions,
                tenant_id=tenant_id,
                actor=actor,
                audit_note=audit_note,
                on_deleted=on_deleted,
            )
        except Exception as exc:
            # Writes are one transaction per component, so anything already applied stays.
            # Attach it and say so rather than letting the caller discover it later.
            if results:
                log_error(
                    f"Import failed partway through: {len(results)} component(s) were already written "
                    f"({', '.join(r.component_id for r in results)}). Re-run with "
                    f"on_conflict='new_version' to converge."
                )
            if isinstance(exc, ComponentIOError):
                exc.results = results
            raise
        if result.version is not None:
            versions[result.component_id] = result.version
        results.append(result)
    return results


def _topologically_order(plans: List[_Plan]) -> List[_Plan]:
    """Order plans so every component is written after the components it links to.

    Depth-first post-order over the intra-bundle links, walked in document order so the result
    is deterministic -- a bundle that is already children-first comes back unchanged, which is
    what export produces and what the shipped templates carry.

    A link to a component outside the bundle is not an edge: it already exists in the target (or
    ``_check_links_resolvable`` has already refused the import), so it constrains nothing here.
    """
    by_id = {plan.entry.component_id: plan for plan in plans}
    ordered: List[_Plan] = []
    done: Set[str] = set()
    visiting: Set[str] = set()

    def visit(plan: _Plan, trail: List[str]) -> None:
        component_id = plan.entry.component_id
        if component_id in done:
            return
        if component_id in visiting:
            raise ComponentIOError(
                "This bundle's components reference each other in a cycle, so there is no order "
                f"in which they can be written: {' -> '.join([*trail, component_id])}."
            )
        visiting.add(component_id)
        for link in plan.entry.links:
            child = by_id.get(link["child_component_id"])
            if child is not None:
                visit(child, [*trail, component_id])
        visiting.discard(component_id)
        done.add(component_id)
        ordered.append(plan)

    for plan in plans:
        visit(plan, [])
    return ordered


def _build_id_map(
    bundle_ids: Sequence[str],
    *,
    id_map: Optional[Mapping[str, str]],
    id_prefix: Optional[str],
    new_component_id: Optional[str],
) -> Dict[str, str]:
    effective: Dict[str, str] = dict(id_map or {})

    if new_component_id is not None:
        if len(bundle_ids) != 1:
            raise ComponentIOError(
                f"new_component_id renames a single component, but this bundle carries "
                f"{len(bundle_ids)}. Use id_map or id_prefix instead."
            )
        effective.setdefault(bundle_ids[0], new_component_id)

    if id_prefix:
        for component_id in bundle_ids:
            effective.setdefault(component_id, f"{id_prefix}{component_id}")

    return {old: new for old, new in effective.items() if old != new}


def _prepare_config(
    config: Mapping[str, Any],
    *,
    db_id: Optional[str],
    drop_db: bool,
    component_id: str = "",
) -> Dict[str, Any]:
    result = copy.deepcopy(dict(config))

    # Sentinels first, so a redacted bundle does not import a component whose api_key is the
    # literal string "__redacted__" -- configured-looking and broken on first call. Dropping
    # the key lets the provider SDK fall back to its environment lookup, which is what the
    # target usually wants.
    result, stripped = strip_redacted(result)
    if stripped:
        log_warning(
            f"Importing '{component_id}': dropped {len(stripped)} redacted field(s) "
            f"({', '.join(stripped)}). Supply them in the target if the component needs them."
        )

    if drop_db:
        result.pop("db", None)
        return result
    if db_id is not None and isinstance(result.get("db"), dict):
        db_block = dict(result["db"])
        db_block["id"] = db_id
        result["db"] = db_block
    return result


def _plan_entry(
    db: Any,
    entry: ComponentEntry,
    tenant_id: Optional[str],
    validate: Optional[ConfigValidator],
    on_conflict: str,
    on_deleted: str,
    db_id: Optional[str],
    drop_db: bool,
    stage_override: Optional[str],
) -> _Plan:
    config = _prepare_config(entry.config, db_id=db_id, drop_db=drop_db, component_id=entry.component_id)
    config = migrate(config, entry.component_type)
    if validate is not None:
        config = validate(config, entry.component_type, component_id=entry.component_id, links=entry.links)
    else:
        config = stamp_schema_version(config)

    target_stage = stage_override or entry.stage

    # Deliberately unscoped: component_id is the global primary key, so this is the only probe
    # that predicts what the database will do with the write.
    #
    # A db built with `require_tenant_id` refuses an unscoped component read by design (EGPT-2.7),
    # and EgptOS turns that on for every db as soon as a tenant claim is configured -- so on any
    # tenancy-enabled deployment this probe raised and took `POST /components/import` and
    # `seed_from_directory` down with it (a 500 on every import). Fall back to the scoped read
    # there. It cannot see an id held by another tenant, so such a collision now surfaces as the
    # primary-key error from the write instead of as `ComponentIdConflictError` -- the same trade
    # already accepted for the studio id probe, and documented in docs/tenancy.md section 7.
    try:
        existing = db.get_component(entry.component_id, tenant_id=None)
    except TenantScopeRequiredError:
        existing = db.get_component(entry.component_id, tenant_id=tenant_id)
    if existing is None:
        return _Plan(entry, config, "created", target_stage)

    if on_conflict == "error":
        raise ComponentIdConflictError(entry.component_id)
    if on_conflict == "skip":
        return _Plan(entry, config, "skipped", target_stage, reason="component_id already exists")

    owned = db.get_component(entry.component_id, tenant_id=tenant_id)
    if owned is None:
        # The id is held by another tenant. `new_version` would mean writing into a component
        # this caller does not own, so it degrades to a refusal rather than to a write.
        raise ComponentIdConflictError(entry.component_id)

    current = db.get_config(entry.component_id, tenant_id=tenant_id)
    if current is None:
        return _Plan(entry, config, "new_version", target_stage)

    if not configs_equal(current.get("config") or {}, config, entry.component_type):
        return _Plan(entry, config, "new_version", target_stage)

    # Same config, different stage. Seeding a draft and then re-seeding with stage="published"
    # used to report "unchanged" and exit 0 while `current_version` stayed NULL -- a success
    # signal on a component nothing will serve. Publishing the version already there is both
    # what the operator asked for and cheaper than writing a duplicate one.
    if target_stage == "published" and current.get("stage") != "published":
        return _Plan(entry, config, "published", target_stage, version=current["version"])

    if (owned.get("name"), owned.get("description"), owned.get("metadata")) != (
        entry.name,
        entry.description,
        entry.metadata,
    ):
        return _Plan(entry, config, "metadata_updated", target_stage)

    return _Plan(entry, config, "unchanged", target_stage)


def _check_links_resolvable(
    db: Any,
    entries: Sequence[ComponentEntry],
    bundle_members: Set[str],
    tenant_id: Optional[str],
) -> None:
    """Fail on every dangling link at once, rather than one write at a time."""
    dangling: List[str] = []
    for entry in entries:
        for link in entry.links:
            child = link["child_component_id"]
            if child in bundle_members:
                continue
            if db.get_config(child, tenant_id=tenant_id) is None:
                dangling.append(f"{entry.component_id} -> {child}")
    if dangling:
        raise ComponentIOError(
            "This bundle links to components that are neither in it nor in the target tenant: "
            + ", ".join(dangling)
            + ". Export it with its dependencies, or import them first."
        )


def _resolve_links(
    db: Any,
    entry: ComponentEntry,
    versions: Mapping[str, int],
    tenant_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Pin each link to a version that exists in *this* database.

    A bundle carries no ``child_version`` -- it would name a version number from the source.
    Children written earlier in this run take the version this run created; anything else takes
    the target's current version.
    """
    resolved: List[Dict[str, Any]] = []
    for link in entry.links:
        child = link["child_component_id"]
        child_version = versions.get(child)
        if child_version is None:
            row = db.get_config(child, tenant_id=tenant_id)
            if row is None:
                raise ComponentIOError(
                    f"Cannot link '{entry.component_id}' to '{child}': it does not exist in this tenant."
                )
            child_version = row["version"]
        resolved.append({**link, "child_version": child_version})
    return resolved


def _apply_plan(
    db: Any,
    plan: _Plan,
    versions: Dict[str, int],
    *,
    tenant_id: Optional[str],
    actor: Optional[str],
    audit_note: Optional[str],
    on_deleted: str = "error",
) -> ImportResult:
    entry = plan.entry
    note = audit_note or entry.notes

    if plan.action == "skipped":
        current = db.get_config(entry.component_id, tenant_id=tenant_id)
        version = current["version"] if current else None
        return ImportResult(entry.component_id, entry.component_type, "skipped", version, plan.reason)

    if plan.action == "unchanged":
        current = db.get_config(entry.component_id, tenant_id=tenant_id)
        return ImportResult(
            entry.component_id,
            entry.component_type,
            "unchanged",
            current["version"] if current else None,
        )

    if plan.action == "published":
        # Publishing the version that is already there, rather than writing an identical one.
        # `upsert_config` moves `current_version` inside the same transaction, which is what
        # makes the component servable -- the thing the operator actually asked for.
        db.upsert_config(
            component_id=entry.component_id,
            version=plan.version,
            stage="published",
            tenant_id=tenant_id,
            actor=actor,
            audit_note=note,
        )
        return ImportResult(entry.component_id, entry.component_type, "published", plan.version)

    links = _resolve_links(db, entry, versions, tenant_id)

    if plan.action == "created":
        try:
            _, config_row = db.create_component_with_config(
                component_id=entry.component_id,
                component_type=ComponentType(entry.component_type),
                name=entry.name,
                config=plan.config,
                description=entry.description,
                metadata=entry.metadata,
                label=entry.label,
                stage=plan.stage,
                notes=entry.notes,
                links=links or None,
                tenant_id=tenant_id,
                actor=actor,
                audit_note=note,
            )
        except ValueError as exc:
            # The pre-pass saw no component under this id, so the only thing that can hold the
            # primary key now is a soft-deleted row -- invisible to every read API. This is the
            # one decision that cannot be made in the planning phase, which is why `on_deleted`
            # has to reach this far down.
            if str(exc) == f"Component {entry.component_id} already exists":
                if on_deleted == "skip":
                    return ImportResult(entry.component_id, entry.component_type, "skipped", None, "soft_deleted")
                raise ComponentSoftDeletedError(entry.component_id) from exc
            raise
        return ImportResult(entry.component_id, entry.component_type, "created", config_row["version"])

    if plan.action == "metadata_updated":
        db.upsert_component(
            component_id=entry.component_id,
            name=entry.name,
            description=entry.description,
            metadata=entry.metadata,
            tenant_id=tenant_id,
            actor=actor,
            audit_note=note,
        )
        current = db.get_config(entry.component_id, tenant_id=tenant_id)
        return ImportResult(
            entry.component_id,
            entry.component_type,
            "metadata_updated",
            current["version"] if current else None,
        )

    # new_version. The entry's label is deliberately not carried: labels are unique per
    # component and there is no API to move one off its current holder, so passing it here
    # would turn every re-import of a labelled definition into a "label already exists" error.
    config_row = db.upsert_config(
        component_id=entry.component_id,
        config=plan.config,
        stage=plan.stage,
        notes=entry.notes,
        links=links or None,
        tenant_id=tenant_id,
        actor=actor,
        audit_note=note,
    )
    return ImportResult(entry.component_id, entry.component_type, "new_version", config_row["version"])
