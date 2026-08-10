"""Versioned component-definition config schema and its semantic validator.

A component config is the dict produced by ``Agent.to_dict`` / ``Team.to_dict`` /
``Workflow.to_dict`` and consumed by the matching ``from_dict``. It is what the components
API persists as JSONB, and what a DB-defined agent is hydrated from at request time. This
module is the gate that keeps an unhydratable definition from being stored in the first
place: shape, then resolvable references, then a dry-run hydration.

Why the validation is split across two layers
---------------------------------------------
``egpt.registry`` is core, and core must not import orchestration (ADR 0001). A lazy
``from egpt.agent import Agent`` inside a function body would not dodge that -- import-linter
builds its graph from the AST and records function-local imports exactly like module-level
ones -- and a process-global "hydrator table" that nothing registered into would make the
dry-run check silently do nothing, which is the worst failure mode a validator can have.

So the component class is *injected*: the caller passes a :class:`ComponentSpec` built by an
orchestration-owned factory (``egpt.agent._storage.component_spec`` and its siblings), and
this module never names ``Agent``. Everything that can be answered from ``discovery``, the
``Registry`` and the config itself is answered here.

Two properties the checks hold to:

* **Accumulate, never short-circuit** -- a caller fixing a config should see every problem in
  one response, not peel them off one request at a time.
* **Validate, never rewrite** -- the returned dict is the caller's dict plus ``schema_version``.
  Pydantic's coerced output is deliberately discarded, so what is persisted is what was sent
  and the write -> read -> ``from_dict`` round trip is byte-stable.
"""

from __future__ import annotations

import copy
import difflib
from dataclasses import dataclass, field
from functools import lru_cache
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Literal,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
)

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from egpt.models.utils import resolve_provider_key
from egpt.registry import discovery
from egpt.registry.policy import (
    # Redundant aliases mark these as deliberate re-exports rather than unused imports: they are
    # pulled in so the closed vocabulary stays enumerable from `vars(schema)`, which is how the
    # doc guard checks the error-code table. See the CODE_* block below.
    CODE_CAPABILITY_NOT_PERMITTED as CODE_CAPABILITY_NOT_PERMITTED,
    CODE_PRIVILEGED_TOOL_BLOCKED as CODE_PRIVILEGED_TOOL_BLOCKED,
    CapabilityPolicy,
    check_capabilities,
)
from egpt.utils.log import log_error, log_warning

if TYPE_CHECKING:
    from egpt.registry.registry import Registry


# The version of the config *schema*, not of a stored config row (that is `component_configs.version`).
SCHEMA_VERSION: int = 1
SCHEMA_VERSION_KEY: str = "schema_version"

COMPONENT_TYPES: Tuple[str, ...] = ("agent", "team", "workflow")

# The closed error-code vocabulary. Documented in docs/component-config-schema.md; clients are
# expected to branch on these rather than on message text.
CODE_MISSING_REQUIRED_FIELD = "missing_required_field"
CODE_INVALID_TYPE = "invalid_type"
CODE_INVALID_VALUE = "invalid_value"
CODE_INVALID_FORMAT = "invalid_format"
CODE_UNKNOWN_FIELD = "unknown_field"
CODE_UNRESOLVABLE_REF = "unresolvable_ref"
CODE_CYCLIC_REFERENCE = "cyclic_reference"
CODE_UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
CODE_HYDRATION_FAILED = "hydration_failed"
CODE_PROVIDER_NOT_INSTALLED = "provider_not_installed"
# The vocabulary also includes the two capability-policy codes (EGPT-2.3),
# CODE_CAPABILITY_NOT_PERMITTED and CODE_PRIVILEGED_TOOL_BLOCKED. They are defined in
# `egpt.registry.policy` -- which owns the checks that raise them -- and imported into this
# module's namespace above, so the closed vocabulary stays enumerable from `vars(schema)` the
# way the doc guard expects.

# How many registered names to list back in an "unresolvable ref" message before truncating.
_MAX_SUGGESTED_NAMES = 12

WORKFLOW_STEP_TYPES: Tuple[str, ...] = ("Step", "Steps", "Loop", "Parallel", "Condition", "Router")


@dataclass(frozen=True)
class ConfigError:
    """One field-level problem with a config.

    ``path`` is relative to the config object -- ``"model.provider"``, ``"tools[1].name"``, or
    ``""`` for the config as a whole. The API layer prefixes it with ``config.`` so the paths
    it returns are body-relative, matching FastAPI's own ``loc`` convention.
    """

    path: str
    message: str
    code: str

    def to_dict(self) -> Dict[str, str]:
        return {"path": self.path, "message": self.message, "code": self.code}


class ConfigValidationError(ValueError):
    """Raised when a config fails validation, carrying every error found.

    Subclasses ``ValueError`` so a caller that only knows the old contract still fails closed
    rather than persisting a bad config. Handlers that want the field-level detail must catch
    this *before* their ``except ValueError`` arm, or the errors flatten into a plain string.
    """

    def __init__(self, errors: Sequence[ConfigError], component_type: str) -> None:
        self.errors: List[ConfigError] = list(errors)
        self.component_type = component_type
        count = len(self.errors)
        super().__init__(
            f"Invalid {component_type} configuration: {count} error{'s' if count != 1 else ''}. "
            + "; ".join(f"{e.path or '<config>'}: {e.message}" for e in self.errors)
        )


@dataclass(frozen=True)
class ComponentSpec:
    """What core needs from the layer that owns the component class.

    ``known_keys`` is the set of config keys the component can actually consume -- derived from
    its constructor signature by the factory, never hand-listed, so adding a field to ``Agent``
    cannot leave the validator rejecting it. ``hydrate`` performs a dry-run ``from_dict``; it is
    optional only so core can be unit-tested without orchestration, and the API layer always
    supplies one.
    """

    component_type: str
    known_keys: FrozenSet[str] = frozenset()
    hydrate: Optional[Callable[[Dict[str, Any]], Any]] = None


# ---------------------------------------------------------------------------------------------
# The declarative schema
# ---------------------------------------------------------------------------------------------
#
# These models carry the required fields, the nested ref shapes and the fields with a real value
# domain. They are deliberately NOT a restatement of all ~90 serializable Agent settings: that
# would duplicate `Agent.__init__` and drift from it within a release. The scalar long tail passes
# through `extra="allow"`, and unknown keys are caught against `ComponentSpec.known_keys`, which is
# derived from the constructor itself.
#
# Fields whose accepted shape is a union (`model`, `input_schema`, `learning`, ...) are typed `Any`
# here and checked by hand below. Pydantic reports a union failure once per member, which would
# turn one wrong field into three errors pointing at synthetic paths like `model.ModelRef.id`.


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow", protected_namespaces=())


class ModelRef(_ConfigModel):
    """A serialized model reference, as emitted by ``Model.to_dict``."""

    id: str
    name: Optional[str] = None
    provider: Optional[str] = None


class ToolRef(_ConfigModel):
    """A serialized tool, as emitted by ``Function.to_dict``."""

    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    strict: Optional[bool] = None
    requires_confirmation: Optional[bool] = None
    external_execution: Optional[bool] = None
    approval_type: Optional[str] = None


class DbRef(_ConfigModel):
    """A serialized db reference: an ``id`` naming a registered db, a ``type`` to construct one, or both.

    Left open (``extra="allow"``) because the connection and table-name fields differ per
    backend -- ``db_url`` / ``db_file`` / ``db_schema`` / per-table overrides.
    """

    id: Optional[str] = None
    type: Optional[str] = None


class KnowledgeRef(_ConfigModel):
    """Knowledge is serialized by name only; the instance itself holds live connections."""

    name: str


class MemberRef(_ConfigModel):
    """A team member reference, as emitted by ``Team.to_dict``."""

    type: Literal["agent", "team"]
    agent_id: Optional[str] = None
    team_id: Optional[str] = None


class _BaseComponentConfig(_ConfigModel):
    schema_version: Optional[int] = None
    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    instructions: Optional[Union[str, List[str]]] = None
    metadata: Optional[Dict[str, Any]] = None
    session_state: Optional[Dict[str, Any]] = None
    dependencies: Optional[Dict[str, Any]] = None
    debug_level: Optional[Literal[1, 2]] = None
    num_history_runs: Optional[int] = Field(default=None, ge=0)
    num_history_messages: Optional[int] = Field(default=None, ge=0)
    retries: Optional[int] = Field(default=None, ge=0)


class AgentConfigV1(_BaseComponentConfig):
    """The agent-definition config. ``model`` is the only required field.

    ``id`` and ``name`` are optional on purpose: the component row already carries a
    ``component_id`` and a display name, and a definition that omits them is legitimate.
    """

    model: Any
    tools: Optional[List[Dict[str, Any]]] = None
    db: Optional[Dict[str, Any]] = None
    knowledge: Optional[Dict[str, Any]] = None
    knowledge_filters: Optional[Dict[str, Any]] = None
    input_schema: Any = None
    output_schema: Any = None
    learning: Any = None
    max_tool_calls_from_history: Optional[int] = Field(default=None, ge=0)
    tool_call_limit: Optional[int] = Field(default=None, ge=1)
    reasoning_min_steps: Optional[int] = Field(default=None, ge=1)
    reasoning_max_steps: Optional[int] = Field(default=None, ge=1)


class TeamConfigV1(_BaseComponentConfig):
    """A team definition. Validated at the envelope level only in this release (EGPT-2.2)."""

    members: Optional[List[Dict[str, Any]]] = None
    mode: Optional[str] = None


class WorkflowConfigV1(_BaseComponentConfig):
    """A workflow definition. Validated at the envelope level only in this release (EGPT-2.2)."""

    steps: Optional[List[Dict[str, Any]]] = None


_CONFIG_MODELS: Dict[str, Type[_BaseComponentConfig]] = {
    "agent": AgentConfigV1,
    "team": TeamConfigV1,
    "workflow": WorkflowConfigV1,
}

# Keyed by the version being migrated *from*, applied in ascending order before validation.
# Empty at v1; this is where a v1 -> v2 normalizer goes so old rows keep validating.
_MIGRATIONS: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


# ---------------------------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------------------------


def config_schema_version(config: Dict[str, Any]) -> int:
    """The schema version a config declares. Absent means "pre-versioning", reported as 0.

    v1 *describes* what ``to_dict`` already emitted before this key existed, so an unversioned
    config is structurally a v1 and validates as one.
    """
    if not isinstance(config, dict):
        return 0
    raw = config.get(SCHEMA_VERSION_KEY)
    # bool is an int subclass, and `schema_version: true` is a mistake, not version 1.
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return raw


def stamp_schema_version(config: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of ``config`` carrying the current schema version."""
    return {**config, SCHEMA_VERSION_KEY: SCHEMA_VERSION}


def migrate(config: Dict[str, Any], component_type: str) -> Dict[str, Any]:
    """Bring a config forward to the current schema version. A no-op at v1."""
    version = config_schema_version(config)
    migrated = config
    for from_version in sorted(v for v in _MIGRATIONS if v >= version):
        migrated = _MIGRATIONS[from_version](migrated)
    return migrated


def json_schema(component_type: str) -> Dict[str, Any]:
    """The JSON Schema for a component type's config, for docs and OpenAPI."""
    model_cls = _CONFIG_MODELS.get(component_type)
    if model_cls is None:
        raise ValueError(f"Unknown component type '{component_type}'. Known types: {', '.join(COMPONENT_TYPES)}.")
    schema = model_cls.model_json_schema()
    schema["title"] = f"{component_type.capitalize()} config v{SCHEMA_VERSION}"
    return schema


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------


@dataclass
class _Errors:
    """Accumulator that keeps one error per path so a union failure cannot triple-report."""

    items: List[ConfigError] = field(default_factory=list)
    _seen: set = field(default_factory=set)

    def add(self, path: str, message: str, code: str) -> None:
        if path in self._seen:
            return
        self._seen.add(path)
        self.items.append(ConfigError(path=path, message=message, code=code))

    def __bool__(self) -> bool:
        return bool(self.items)


def _path_from_loc(loc: Sequence[Any]) -> str:
    """Turn a pydantic error location into a dotted/indexed config path."""
    parts: List[str] = []
    for element in loc:
        if isinstance(element, int):
            parts.append(f"[{element}]")
        else:
            parts.append(f".{element}" if parts else str(element))
    return "".join(parts)


def _known_names(kind: str) -> str:
    names = discovery.names(kind)
    shown = ", ".join(names[:_MAX_SUGGESTED_NAMES])
    if len(names) > _MAX_SUGGESTED_NAMES:
        shown += f", ... ({len(names) - _MAX_SUGGESTED_NAMES} more)"
    return shown or "<none>"


_PYDANTIC_CODE_MAP: Dict[str, str] = {
    "missing": CODE_MISSING_REQUIRED_FIELD,
    "greater_than_equal": CODE_INVALID_VALUE,
    "less_than_equal": CODE_INVALID_VALUE,
    "literal_error": CODE_INVALID_VALUE,
}


@lru_cache(maxsize=None)
def _union_fields(component_type: str) -> FrozenSet[str]:
    """Fields declared as a real union, whose pydantic errors carry a member tag in their loc.

    Pydantic reports a union failure once per member and appends the member's name to the
    location -- ``('instructions', 'str')`` and ``('instructions', 'list[str]')``. Left alone
    that turns one wrong field into several errors pointing at paths no config ever had.
    """
    fields = _CONFIG_MODELS[component_type].model_fields
    union_names = set()
    for name, info in fields.items():
        if get_origin(info.annotation) is Union:
            if len([arg for arg in get_args(info.annotation) if arg is not type(None)]) > 1:
                union_names.add(name)
    return frozenset(union_names)


def _check_shape(config: Dict[str, Any], component_type: str, errors: _Errors) -> None:
    model_cls = _CONFIG_MODELS[component_type]
    union_fields = _union_fields(component_type)
    try:
        model_cls.model_validate(config)
    except ValidationError as exc:
        for detail in exc.errors():
            loc = detail.get("loc", ())
            path = str(loc[0]) if loc and loc[0] in union_fields else _path_from_loc(loc)
            code = _PYDANTIC_CODE_MAP.get(str(detail.get("type", "")), CODE_INVALID_TYPE)
            message = str(detail.get("msg", "Invalid value."))
            if code == CODE_MISSING_REQUIRED_FIELD:
                message = f"Field '{path}' is required for {'an' if component_type == 'agent' else 'a'} {component_type} definition."
            errors.add(path, message, code)


def _check_schema_version(config: Dict[str, Any], errors: _Errors) -> None:
    raw = config.get(SCHEMA_VERSION_KEY)
    if raw is None:
        return  # pre-versioning; validated as v1 and stamped on write
    if isinstance(raw, bool) or not isinstance(raw, int):
        errors.add(SCHEMA_VERSION_KEY, f"'{SCHEMA_VERSION_KEY}' must be an integer.", CODE_INVALID_TYPE)
        return
    if raw > SCHEMA_VERSION:
        errors.add(
            SCHEMA_VERSION_KEY,
            f"Config declares schema version {raw}, but this framework understands up to "
            f"{SCHEMA_VERSION}. Upgrade before writing this definition.",
            CODE_UNSUPPORTED_SCHEMA_VERSION,
        )


def _check_unknown_keys(config: Dict[str, Any], spec: Optional[ComponentSpec], errors: _Errors) -> None:
    """Reject keys the component cannot consume.

    ``from_dict`` builds its kwargs from an explicit list, so an unknown key is not merely
    ignored -- it is *silently* ignored, which is how ``instruction:`` for ``instructions:``
    survives all the way to a run that quietly has no instructions.
    """
    if spec is None or not spec.known_keys:
        return  # nothing to compare against; a bare core caller opts out of this check
    allowed = set(spec.known_keys) | set(_CONFIG_MODELS[spec.component_type].model_fields) | {SCHEMA_VERSION_KEY}
    for key in config:
        if key in allowed:
            continue
        suggestions = difflib.get_close_matches(key, sorted(allowed), n=1)
        hint = f" Did you mean '{suggestions[0]}'?" if suggestions else ""
        errors.add(str(key), f"Unknown field '{key}'.{hint}", CODE_UNKNOWN_FIELD)


def _check_model_ref(config: Dict[str, Any], registry: Optional["Registry"], errors: _Errors) -> None:
    model_data = config.get("model")
    if model_data is None:
        return  # absence is reported by the shape check

    if isinstance(model_data, str):
        provider, _, model_id = model_data.partition(":")
        provider, model_id = provider.strip().lower(), model_id.strip()
        if not provider or not model_id:
            errors.add(
                "model",
                f"Invalid model string '{model_data}'. Expected '<provider>:<model_id>', e.g. 'openai:gpt-5.5'.",
                CODE_INVALID_FORMAT,
            )
            return
        _check_provider(provider, None, model_id, "model", registry, errors)
        return

    if not isinstance(model_data, dict):
        errors.add(
            "model",
            "Model must be a serialized model object ({'id': ..., 'provider': ...}) or a "
            "'<provider>:<model_id>' string.",
            CODE_INVALID_TYPE,
        )
        return

    serialized_id = model_data.get("id")
    if not serialized_id or not isinstance(serialized_id, str):
        errors.add("model.id", "Model reference is missing 'id'.", CODE_MISSING_REQUIRED_FIELD)
        return
    _check_provider(
        model_data.get("provider"), model_data.get("name"), serialized_id, "model.provider", registry, errors
    )


def _check_provider(
    provider: Optional[str],
    name: Optional[str],
    model_id: str,
    path: str,
    registry: Optional["Registry"],
    errors: _Errors,
) -> None:
    """Confirm the provider is resolvable, importing nothing.

    A live instance in the registry settles it outright. Otherwise ``discovery.get`` answers
    from the declaration table -- it returns a ``Registration`` without calling ``load()``, so
    a config naming a provider whose SDK is absent still passes this check (the dry run is
    where a missing SDK surfaces, as ``provider_not_installed``).
    """
    if registry is not None and registry.get_model(model_id, provider=provider, name=name) is not None:
        return
    key = resolve_provider_key(provider, name)
    if discovery.get("model", key) is not None:
        return
    named = provider or name or key
    errors.add(
        path,
        f"Model provider '{named}' is not registered. Registered providers: {_known_names('model')}.",
        CODE_UNRESOLVABLE_REF,
    )


def _check_tool_refs(config: Dict[str, Any], registry: Optional["Registry"], errors: _Errors) -> None:
    tools = config.get("tools")
    if tools is None:
        return
    if not isinstance(tools, list):
        return  # reported by the shape check
    for index, tool in enumerate(tools):
        path = f"tools[{index}]"
        if not isinstance(tool, dict):
            errors.add(path, "Each tool must be a serialized function object.", CODE_INVALID_TYPE)
            continue
        tool_name = tool.get("name")
        if not tool_name or not isinstance(tool_name, str):
            errors.add(f"{path}.name", "Tool is missing 'name'.", CODE_MISSING_REQUIRED_FIELD)
            continue
        parameters = tool.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            errors.add(f"{path}.parameters", "Tool 'parameters' must be a JSON Schema object.", CODE_INVALID_TYPE)
        # Registry-gated: with no registry there is no way to tell "not registered" from
        # "nothing is registered here", and a validator that cannot tell must not reject.
        if registry is not None and not registry.has_entrypoint(tool_name):
            errors.add(
                f"{path}.name",
                f"Tool '{tool_name}' is not registered; no entrypoint was found. Register the tool "
                "or its toolkit on the Registry (for MCP toolkits, make sure they are connected).",
                CODE_UNRESOLVABLE_REF,
            )


def _check_db_ref(config: Dict[str, Any], unresolved_db_id: Optional[str], errors: _Errors) -> None:
    db_data = config.get("db")
    if db_data is None:
        return
    if not isinstance(db_data, dict):
        return  # reported by the shape check
    db_id, db_type = db_data.get("id"), db_data.get("type")
    if not db_id and not db_type:
        errors.add(
            "db",
            "Database reference needs an 'id' (to reference a registered db) or a 'type' (to construct one).",
            CODE_MISSING_REQUIRED_FIELD,
        )
        return
    if db_type is not None and discovery.get("db", str(db_type)) is None:
        errors.add(
            "db.type",
            f"Database type '{db_type}' is not registered. Registered types: {_known_names('db')}.",
            CODE_UNRESOLVABLE_REF,
        )
    if unresolved_db_id is not None and unresolved_db_id == db_id:
        errors.add(
            "db.id",
            f"Database '{db_id}' could not be resolved. It matches neither the OS database nor any "
            "db registered on the Registry.",
            CODE_UNRESOLVABLE_REF,
        )


def _check_registry_refs(config: Dict[str, Any], registry: Optional["Registry"], errors: _Errors) -> None:
    knowledge = config.get("knowledge")
    if knowledge is not None:
        if not isinstance(knowledge, dict):
            errors.add("knowledge", "Knowledge must be a reference object ({'name': ...}).", CODE_INVALID_TYPE)
        else:
            knowledge_name = knowledge.get("name")
            if not knowledge_name or not isinstance(knowledge_name, str):
                errors.add("knowledge.name", "Knowledge reference is missing 'name'.", CODE_MISSING_REQUIRED_FIELD)
            elif registry is not None and knowledge_name not in registry.get_knowledge_names():
                errors.add(
                    "knowledge.name",
                    f"Knowledge '{knowledge_name}' is not registered on the Registry.",
                    CODE_UNRESOLVABLE_REF,
                )

    for key in ("input_schema", "output_schema"):
        value = config.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            if registry is not None and registry.get_schema(value) is None:
                errors.add(key, f"Schema '{value}' is not registered on the Registry.", CODE_UNRESOLVABLE_REF)
        elif isinstance(value, dict):
            if not value:
                errors.add(key, f"Inline '{key}' must be a non-empty JSON Schema object.", CODE_INVALID_TYPE)
        else:
            errors.add(
                key,
                f"'{key}' must be the name of a registered schema class or an inline JSON Schema object.",
                CODE_INVALID_TYPE,
            )

    learning = config.get("learning")
    if learning is not None and not isinstance(learning, (bool, dict)):
        errors.add("learning", "'learning' must be a boolean or a serialized learning machine.", CODE_INVALID_TYPE)


def _check_members(config: Dict[str, Any], self_component_id: Optional[str], errors: _Errors) -> None:
    members = config.get("members")
    if members is None or (isinstance(members, list) and not members):
        # Not an error: creating a team and adding its members afterwards is a legitimate flow,
        # so this stays the warning it has always been.
        log_warning(
            "Team definition has no members. If this is unintended, add members to the config before publishing it."
        )
        return
    if not isinstance(members, list):
        return  # a wrong type is reported by the shape check
    for index, member in enumerate(members):
        path = f"members[{index}]"
        if not isinstance(member, dict):
            errors.add(path, "Each member must be a reference object.", CODE_INVALID_TYPE)
            continue
        member_type = member.get("type")
        if member_type not in ("agent", "team"):
            errors.add(f"{path}.type", "Member 'type' must be 'agent' or 'team'.", CODE_INVALID_VALUE)
            continue
        id_key = "agent_id" if member_type == "agent" else "team_id"
        member_id = member.get(id_key)
        if not member_id or not isinstance(member_id, str):
            errors.add(f"{path}.{id_key}", f"Member is missing '{id_key}'.", CODE_MISSING_REQUIRED_FIELD)
        elif self_component_id is not None and member_id == self_component_id:
            errors.add(f"{path}.{id_key}", "A team cannot contain itself.", CODE_CYCLIC_REFERENCE)


def _check_steps(config: Dict[str, Any], errors: _Errors) -> None:
    steps = config.get("steps")
    if steps is None or not isinstance(steps, list):
        return
    for index, step in enumerate(steps):
        path = f"steps[{index}]"
        if not isinstance(step, dict):
            errors.add(path, "Each step must be a serialized step object.", CODE_INVALID_TYPE)
            continue
        step_type = step.get("type", "Step")
        if step_type not in WORKFLOW_STEP_TYPES:
            errors.add(
                f"{path}.type",
                f"Unknown step type '{step_type}'. Known types: {', '.join(WORKFLOW_STEP_TYPES)}.",
                CODE_INVALID_VALUE,
            )


# Best-effort mapping from the ValueErrors the hydration path raises to the field they are about,
# so a dry-run failure still lands on a field instead of on the config as a whole.
_HYDRATION_ERROR_PATHS: Tuple[Tuple[str, str], ...] = (
    ("Model provider", "model.provider"),
    ("Model data is missing an 'id'", "model.id"),
    ("Model data must be a dictionary", "model"),
    ("Invalid model string format", "model"),
    ("Model string must be", "model"),
    ("Unknown step type", "steps"),
)


def _hydration_error_path(message: str) -> str:
    for prefix, path in _HYDRATION_ERROR_PATHS:
        if prefix in message:
            return path
    return ""


def _dry_run(config: Dict[str, Any], spec: ComponentSpec, errors: _Errors) -> None:
    """Build the component and throw it away, to prove the config actually hydrates.

    Two things make this side-effect free rather than merely usually-harmless:

    * ``db`` is stripped from the copy. ``from_dict`` -> ``resolve_db_from_config`` ->
      ``db_from_dict`` constructs a real backend when the id is not a registered instance,
      engine and pool included. The db block is fully checked above, so dropping it here
      costs nothing.
    * the payload is deep-copied. ``from_dict`` copies the dict *shallowly*, then deletes keys
      and hands the nested dicts to the constructor, so without this the object we persist
      could differ from the one the caller sent.

    Nothing else in the path runs: model classes build their client lazily, rehydration only
    assigns entrypoints, and the constructors are plain attribute assignment.
    """
    if spec.hydrate is None:
        return
    payload = copy.deepcopy(config)
    payload.pop("db", None)
    try:
        spec.hydrate(payload)
    except ValidationError as exc:
        for detail in exc.errors():
            errors.add(
                _path_from_loc(detail.get("loc", ())), str(detail.get("msg", "Invalid value.")), CODE_INVALID_TYPE
            )
    except ImportError as exc:
        errors.add(
            "model.provider",
            f"The provider named by this config is registered but not installed here: {exc}",
            CODE_PROVIDER_NOT_INSTALLED,
        )
    except (ValueError, TypeError) as exc:
        message = str(exc)
        errors.add(_hydration_error_path(message), f"Config could not be hydrated: {message}", CODE_HYDRATION_FAILED)
    except Exception as exc:  # noqa: BLE001 - a hydration bug must not become a 500
        log_error(f"Unexpected error while dry-running a {spec.component_type} config: {exc}")
        errors.add("", f"Config could not be hydrated: {type(exc).__name__}: {exc}", CODE_HYDRATION_FAILED)


def _check_policy(
    config: Dict[str, Any],
    component_type: str,
    policy: Optional[CapabilityPolicy],
    tenant_id: Optional[str],
    registry: Optional["Registry"],
    errors: _Errors,
) -> None:
    """Reject capabilities this tenant's policy does not permit (EGPT-2.3).

    Unlike every other reference check here, this one runs even when ``registry`` is None. That
    inversion is deliberate and reads like a bug without the reason: the checks above skip
    themselves without a registry because they cannot tell "unknown name" from "nothing is
    registered", but policy has a static fallback for exactly that case. A privileged-tool check
    that switched itself off when unwired would be off on the one path where nothing else can
    catch a ``run_shell_command``.

    ``mode="warn"`` logs instead of rejecting, so an operator can audit an existing corpus before
    turning enforcement on.
    """
    if policy is None or not policy.enabled:
        return
    for violation in check_capabilities(config, component_type, policy=policy, tenant_id=tenant_id, registry=registry):
        if policy.mode == "warn":
            log_warning(f"[capability-policy:warn] {violation.message}")
            continue
        errors.add(violation.path, violation.message, violation.code)


def validate_config(
    config: Dict[str, Any],
    component_type: str,
    *,
    registry: Optional["Registry"] = None,
    spec: Optional[ComponentSpec] = None,
    unresolved_db_id: Optional[str] = None,
    self_component_id: Optional[str] = None,
    policy: Optional[CapabilityPolicy] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a component config and return it stamped with the current schema version.

    Agent definitions are validated in full: shape, reference resolution and a dry-run
    hydration. Team and workflow definitions get the envelope -- shape, schema version, unknown
    keys, and the structure of ``members`` / ``steps`` -- with deep reference resolution left to
    a follow-up (EGPT-2.2 is scoped to the agent definition).

    Checks that consult the ``Registry`` (tools, knowledge, schemas) are skipped when
    ``registry`` is None: with nothing registered there is no way to distinguish an unknown
    name from an unconfigured process, and rejecting on that basis would break every caller
    that never wired a registry.

    Args:
        config: The config to validate. Never mutated.
        component_type: "agent", "team" or "workflow".
        registry: The application's live instance catalog, when there is one.
        spec: The component contract, from the orchestration-owned factory. Without it the
            unknown-key check and the dry run are skipped.
        unresolved_db_id: A db id the caller already failed to resolve, reported at ``db.id``.
        self_component_id: The id being written, used to catch a component referencing itself.
        policy: The tenant capability policy in force, when one is configured. ``None`` (the
            default) skips the capability checks entirely, which is what keeps EGPT-2.3 a no-op
            for a deployment that has not opted in.
        tenant_id: The tenant authoring this definition, used to select its allow-list. ``None``
            selects the policy's ``defaults`` block.

    Returns:
        The config plus ``schema_version``.

    Raises:
        ConfigValidationError: the config has one or more field-level problems.
        ValueError: ``component_type`` is not a known component type.
    """
    if component_type not in _CONFIG_MODELS:
        raise ValueError(f"Unknown component type '{component_type}'. Known types: {', '.join(COMPONENT_TYPES)}.")

    errors = _Errors()
    if not isinstance(config, dict):
        errors.add("", "Config must be an object.", CODE_INVALID_TYPE)
        raise ConfigValidationError(errors.items, component_type)

    config = migrate(config, component_type)

    _check_schema_version(config, errors)
    _check_shape(config, component_type, errors)
    _check_unknown_keys(config, spec, errors)

    if component_type == "agent":
        _check_model_ref(config, registry, errors)
        _check_tool_refs(config, registry, errors)
        _check_db_ref(config, unresolved_db_id, errors)
        _check_registry_refs(config, registry, errors)
    elif component_type == "team":
        _check_db_ref(config, unresolved_db_id, errors)
        _check_members(config, self_component_id, errors)
    else:
        _check_db_ref(config, unresolved_db_id, errors)
        _check_steps(config, errors)

    # Runs for all three component types, not just agents: a team config carries a `model`, and
    # `tools` reaches every type through `extra="allow"`, so scoping this to agents would leave
    # a team definition as the bypass.
    _check_policy(config, component_type, policy, tenant_id, registry, errors)

    # Hydration is the catch-all for everything the checks above cannot model, so it only runs
    # once they pass -- dry-running a config already known to be broken produces noise, not
    # information.
    if not errors and spec is not None and component_type == "agent":
        _dry_run(config, spec, errors)

    if errors:
        raise ConfigValidationError(errors.items, component_type)
    return stamp_schema_version(config)
