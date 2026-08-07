"""Logic shared across different database implementations"""

import inspect
import json
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union
from uuid import UUID

from egpt.db.table_names import LEGACY_TABLE_PREFIX, TABLE_SUFFIXES
from egpt.metrics import ModelMetrics, RunMetrics, SessionMetrics
from egpt.models.message import Message
from egpt.registry import discovery
from egpt.utils.log import log_error, log_warning

if TYPE_CHECKING:
    from egpt.db.base import AsyncBaseDb, BaseDb, SessionType
    from egpt.registry.registry import Registry
    from egpt.session import Session


# Keys in a serialized db dict that correspond to table-name overrides.
# Matches the parameters BaseDb.__init__ accepts for customizing table names.
DB_TABLE_NAME_KEYS: frozenset = frozenset(TABLE_SUFFIXES)

# Serialized db keys that customize table naming (prefix and/or per-table overrides).
DB_CONFIG_OVERRIDE_KEYS: frozenset = frozenset({"table_prefix"}) | DB_TABLE_NAME_KEYS


def unsupported_table_overrides(cls: type) -> frozenset:
    """Table-name parameters ``cls.__init__`` does not accept.

    A backend whose clone_with_table_overrides forwards a table kwarg its own __init__
    lacks raises TypeError, which _clone_db_with_table_overrides turns into a logged error
    and a silent fallback -- the component then keeps the registry instance's tables.
    Enumerating the constructor rather than hand-listing the
    gap is what keeps this correct in both drift directions: when the class gains the
    missing parameters the set empties itself, and when a new table kwarg is added to
    TABLE_SUFFIXES ahead of the constructor it is caught instead of forwarded.
    """
    # signature(cls) rather than signature(cls.__init__): it resolves the constructor
    # without the self parameter, and avoids mypy's unsound-instance-__init__ complaint.
    return frozenset(TABLE_SUFFIXES) - frozenset(inspect.signature(cls).parameters)


# Every DB backend shipped in the integrations layer is declared in
# egpt/registry/_declarations/dbs.py and resolved through the discovery seam, so core names
# no backend module. The `reconstructable` / `async_attr` semantics live with those rows.


def _is_legacy_default_table_name(key: str, value: Any) -> bool:
    """Whether a serialized table-name value is a pre-rename (agno_*) default.

    One-release compatibility shim: configs stored before the egpt_ rename
    serialized every resolved default name (agno_sessions, agno_memories, ...).
    Those are snapshots of old defaults, not intentional user overrides, so
    they must not be treated as explicit table-name customizations — doing so
    would pin reloaded components to the legacy tables while the registry db
    and new components use the current defaults (split-brain), or silently
    discard the config on backends the cloner does not support.

    Deployments that genuinely want to keep using the legacy tables should
    set table_prefix="agno_" explicitly, which is honored as a real override.
    """
    suffix = TABLE_SUFFIXES.get(key)
    return suffix is not None and value == f"{LEGACY_TABLE_PREFIX}{suffix}"


def detect_session_type(record: Dict[str, Any]) -> str:
    """Detect session type from a raw session dict, inferring from component IDs if needed.

    Priority: stored session_type > component IDs (agent_id > team_id > workflow_id) > fallback "agent".

    Args:
        record: Raw session dictionary.

    Returns:
        Session type string ("agent", "team", or "workflow").
    """
    st = record.get("session_type")
    if st:
        return st.value if hasattr(st, "value") else st
    if record.get("agent_id"):
        return "agent"
    if record.get("team_id"):
        return "team"
    if record.get("workflow_id"):
        return "workflow"
    return "agent"


def deserialize_session_by_type(record: Dict[str, Any]) -> "Session":
    """Deserialize a raw session dict into the correct Session subclass based on detected type.

    Args:
        record: Raw session dictionary.

    Returns:
        Session subclass instance (AgentSession, TeamSession, or WorkflowSession).
    """
    from egpt.session import AgentSession, TeamSession, WorkflowSession

    st = detect_session_type(record)
    if st == "agent":
        return AgentSession.from_dict(record)  # type: ignore
    elif st == "team":
        return TeamSession.from_dict(record)  # type: ignore
    elif st == "workflow":
        return WorkflowSession.from_dict(record)  # type: ignore
    return AgentSession.from_dict(record)  # type: ignore


def deserialize_session(session_type: Optional["SessionType"], record: Dict[str, Any]) -> "Session":
    """Deserialize a raw session dict into the correct Session subclass.

    Args:
        session_type: The type to deserialize as. If None, auto-detects from the record's component IDs.
        record: Raw session dictionary.

    Returns:
        Session subclass instance (AgentSession, TeamSession, or WorkflowSession).

    Raises:
        ValueError: If session_type is not a valid SessionType.
    """
    from egpt.db.base import SessionType
    from egpt.session import AgentSession, TeamSession, WorkflowSession

    if session_type is None:
        return deserialize_session_by_type(record)
    if session_type == SessionType.AGENT:
        return AgentSession.from_dict(record)  # type: ignore
    elif session_type == SessionType.TEAM:
        return TeamSession.from_dict(record)  # type: ignore
    elif session_type == SessionType.WORKFLOW:
        return WorkflowSession.from_dict(record)  # type: ignore
    raise ValueError(f"Invalid session type: {session_type}")


def deserialize_sessions(session_type: Optional["SessionType"], records: List[Dict[str, Any]]) -> List["Session"]:
    """Deserialize a list of raw session dicts into the correct Session subclasses.

    Args:
        session_type: The type to deserialize as. If None, auto-detects each record individually.
        records: List of raw session dictionaries.

    Returns:
        List of Session subclass instances.
    """
    return [deserialize_session(session_type, record) for record in records]


async def resolve_session_type(
    db: Union["BaseDb", "AsyncBaseDb"],
    session_id: str,
    session_type: Optional["SessionType"],
    user_id: Optional[str] = None,
) -> Tuple[Optional["SessionType"], Optional[Any]]:
    """Resolve session type by auto-detecting from DB if not provided.

    Args:
        db: Database adapter instance (sync or async).
        session_id: The session ID to look up.
        session_type: The session type if already known. If None, auto-detects from DB.
        user_id: Optional user ID filter.

    Returns:
        Tuple of (resolved_type, raw_session):
        - If session_type is already set: (session_type, None) — no DB fetch needed.
        - If session_type is None and session found: (detected_type, raw_dict).
        - If session_type is None and session not found: (None, None).
    """
    if session_type is not None:
        return session_type, None

    from egpt.db.base import AsyncBaseDb, SessionType

    if isinstance(db, AsyncBaseDb):
        raw = await db.get_session(session_id=session_id, user_id=user_id, deserialize=False)
    else:
        raw = db.get_session(session_id=session_id, user_id=user_id, deserialize=False)

    if not raw:
        return None, None

    detected = detect_session_type(raw if isinstance(raw, dict) else {})
    resolved = SessionType(detected)
    return resolved, raw


def get_sort_value(record: Dict[str, Any], sort_by: str) -> Any:
    """Get the sort value for a record, with fallback to created_at for updated_at.

    When sorting by 'updated_at', this function falls back to 'created_at' if
    'updated_at' is None. This ensures pre-2.0 records (which may have NULL
    updated_at values) are sorted correctly by their creation time.

    Args:
        record: The record dictionary to get the sort value from
        sort_by: The field to sort by

    Returns:
        The value to use for sorting
    """
    value = record.get(sort_by)
    # For updated_at, fall back to created_at if updated_at is None
    if value is None and sort_by == "updated_at":
        value = record.get("created_at")
    return value


class CustomJSONEncoder(json.JSONEncoder):
    """Custom encoder to handle non JSON serializable types."""

    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        elif isinstance(obj, (date, datetime)):
            return obj.isoformat()
        elif isinstance(obj, Message):
            return obj.to_dict()
        elif isinstance(obj, (RunMetrics, SessionMetrics, ModelMetrics)):
            return obj.to_dict()
        elif isinstance(obj, type):
            return str(obj)

        return super().default(obj)


def json_serializer(obj: Any) -> str:
    """Custom JSON serializer for SQLAlchemy engine.

    This function is used as the json_serializer parameter when creating
    SQLAlchemy engines for PostgreSQL. It handles non-JSON-serializable
    types like datetime, date, UUID, etc.

    Args:
        obj: The object to serialize to JSON.

    Returns:
        JSON string representation of the object.
    """
    return json.dumps(obj, cls=CustomJSONEncoder)


def serialize_session_json_fields(session: dict) -> dict:
    """Serialize all JSON fields in the given Session dictionary.

    Uses CustomJSONEncoder to handle non-JSON-serializable types like
    datetime, date, UUID, Message, Metrics, etc.

    Args:
        session (dict): The session dictionary to serialize JSON fields in.

    Returns:
        dict: The dictionary with JSON fields serialized.
    """
    if session.get("session_data") is not None:
        session["session_data"] = json.dumps(session["session_data"], cls=CustomJSONEncoder)
    if session.get("agent_data") is not None:
        session["agent_data"] = json.dumps(session["agent_data"], cls=CustomJSONEncoder)
    if session.get("team_data") is not None:
        session["team_data"] = json.dumps(session["team_data"], cls=CustomJSONEncoder)
    if session.get("workflow_data") is not None:
        session["workflow_data"] = json.dumps(session["workflow_data"], cls=CustomJSONEncoder)
    if session.get("metadata") is not None:
        session["metadata"] = json.dumps(session["metadata"], cls=CustomJSONEncoder)
    if session.get("chat_history") is not None:
        session["chat_history"] = json.dumps(session["chat_history"], cls=CustomJSONEncoder)
    if session.get("summary") is not None:
        session["summary"] = json.dumps(session["summary"], cls=CustomJSONEncoder)
    if session.get("runs") is not None:
        session["runs"] = json.dumps(session["runs"], cls=CustomJSONEncoder)

    return session


def deserialize_session_json_fields(session: dict) -> dict:
    """Deserialize JSON fields in the given Session dictionary.

    Args:
        session (dict): The dictionary to deserialize.

    Returns:
        dict: The dictionary with JSON string fields deserialized to objects.
    """
    from egpt.utils.log import log_warning

    if session.get("agent_data") is not None and isinstance(session["agent_data"], str):
        try:
            session["agent_data"] = json.loads(session["agent_data"])
        except (json.JSONDecodeError, TypeError) as e:
            log_warning(f"Warning: Could not parse agent_data as JSON, keeping as string: {str(e)}")

    if session.get("team_data") is not None and isinstance(session["team_data"], str):
        try:
            session["team_data"] = json.loads(session["team_data"])
        except (json.JSONDecodeError, TypeError) as e:
            log_warning(f"Warning: Could not parse team_data as JSON, keeping as string: {str(e)}")

    if session.get("workflow_data") is not None and isinstance(session["workflow_data"], str):
        try:
            session["workflow_data"] = json.loads(session["workflow_data"])
        except (json.JSONDecodeError, TypeError) as e:
            log_warning(f"Warning: Could not parse workflow_data as JSON, keeping as string: {str(e)}")

    if session.get("metadata") is not None and isinstance(session["metadata"], str):
        try:
            session["metadata"] = json.loads(session["metadata"])
        except (json.JSONDecodeError, TypeError) as e:
            log_warning(f"Warning: Could not parse metadata as JSON, keeping as string: {str(e)}")

    if session.get("chat_history") is not None and isinstance(session["chat_history"], str):
        try:
            session["chat_history"] = json.loads(session["chat_history"])
        except (json.JSONDecodeError, TypeError) as e:
            log_warning(f"Warning: Could not parse chat_history as JSON, keeping as string: {str(e)}")

    if session.get("summary") is not None and isinstance(session["summary"], str):
        try:
            session["summary"] = json.loads(session["summary"])
        except (json.JSONDecodeError, TypeError) as e:
            log_warning(f"Warning: Could not parse summary as JSON, keeping as string: {str(e)}")

    if session.get("session_data") is not None and isinstance(session["session_data"], str):
        try:
            session["session_data"] = json.loads(session["session_data"])
        except (json.JSONDecodeError, TypeError) as e:
            log_warning(f"Warning: Could not parse session_data as JSON, keeping as string: {str(e)}")

    # Handle runs field with session type checking
    if session.get("runs") is not None and isinstance(session["runs"], str):
        try:
            session["runs"] = json.loads(session["runs"])
        except (json.JSONDecodeError, TypeError) as e:
            log_warning(f"Warning: Could not parse runs as JSON, keeping as string: {str(e)}")

    return session


def db_from_dict(db_data: Dict[str, Any]) -> Optional["BaseDb"]:
    """
    Create a database instance from a dictionary.

    The backend class is resolved through the discovery seam and imported lazily, so core
    carries no import of any concrete backend and no optional driver is pulled in for a
    backend that is not being reconstructed.

    A backend that ships but cannot be rebuilt from a config is refused here rather than
    dispatched to, and says so in its own words. Dispatching would raise a TypeError about
    a table-name keyword deep inside the inherited ``BaseDb.from_dict`` -- the caller gets
    None either way, but that message names the wrong culprit, and "unknown type" is no
    better when the type is plainly one the framework supports.

    Args:
        db_data: Dictionary containing database configuration, keyed on ``type``

    Returns:
        Database instance or None if creation fails
    """
    db_type = db_data.get("type")
    registration = discovery.get("db", db_type) if isinstance(db_type, str) else None
    if registration is None or not registration.metadata.get("reconstructable"):
        if registration is not None:
            log_warning(
                f"Database type '{db_type}' cannot be rebuilt from a stored config; "
                "pass the instance through the registry instead."
            )
        else:
            log_warning(f"Unknown database type: {db_type}")
        return None

    class_name = registration.attr
    try:
        return registration.load().from_dict(db_data)
    except Exception as e:
        log_error(f"Error reconstructing {class_name} from dictionary: {str(e)}")
        return None


def _clone_db_with_table_overrides(
    source_db: "BaseDb",
    db_data: Dict[str, Any],
) -> Tuple[Optional["BaseDb"], bool]:
    """Create a new ``BaseDb`` that shares ``source_db``'s engine but
    applies the table-name overrides from ``db_data``.

    Sharing the underlying SQLAlchemy engine is critical: otherwise every
    component load would spin up its own connection pool and blow past
    backend connection limits. This helper is used when the stored
    config references a known db (same id) but customizes table names.

    Connection metadata (``db_url`` / ``db_file`` / ``db_schema``) is
    carried over from ``source_db`` so the clone's ``to_dict`` still
    round-trips to a usable config if it is re-saved and later loaded
    without a registry.

    Returns ``(clone, raised)``. ``clone`` is ``None`` when the overrides
    could not be applied, so the caller can decide how to fall back;
    ``raised`` distinguishes the two ways that happens -- a backend that
    does not support cloning at all (``False``, ``BaseDb``'s default
    returns ``None``) from one that supports it but rejected these
    overrides (``True``). They warrant different diagnostics: only the
    former is a property of the backend.
    """
    overrides: Dict[str, Any] = {
        key: db_data[key]
        for key in DB_CONFIG_OVERRIDE_KEYS
        if key in db_data
        and db_data[key] is not None
        # Skip pre-rename default names: they are snapshots of old defaults,
        # not user overrides, and pinning them would split reads/writes
        # between agno_* and the current default tables.
        and not _is_legacy_default_table_name(key, db_data[key])
    }

    # Dispatched on the backend rather than branched on here: core must not import a
    # concrete backend. Backends that cannot share a connection inherit BaseDb's default,
    # which returns None.
    try:
        return source_db.clone_with_table_overrides(**overrides), False
    except Exception as e:
        log_error(
            f"Error cloning {type(source_db).__name__} with table overrides "
            f"({type(e).__name__}: {e}); reusing the registered instance with its "
            "configured table names."
        )
        return None, True


def resolve_db_from_config(
    db_data: Dict[str, Any],
    registry: Optional["Registry"] = None,
) -> Optional["BaseDb"]:
    """Resolve a serialized db config to a concrete ``BaseDb`` instance.

    Prefers a registered db instance (for connection reuse) when the
    serialized config does not override any table names. If it does, a
    clone of the registered instance is returned that **shares the same
    engine/connection pool** but carries the table-name overrides, so
    component reloads don't proliferate engines.

    Only when there is no registry match (or the registered db type is
    unknown to the cloner) do we fall through to :func:`db_from_dict`,
    which builds a fresh instance with its own engine.

    Args:
        db_data: Serialized db config dict (as produced by
            ``BaseDb.to_dict``). Expected to carry a ``type`` plus any
            table-name overrides.
        registry: Optional ``Registry`` to look up an already-constructed
            db instance by id.

    Returns:
        A ``BaseDb`` instance, or ``None`` if reconstruction fails.
    """
    db_id = db_data.get("id")
    if registry is not None and db_id:
        registry_db = registry.get_db(db_id)
        if registry_db is not None:
            registry_dict = registry_db.to_dict()
            has_table_overrides = any(
                key in db_data
                and db_data[key] is not None
                and db_data[key] != registry_dict.get(key)
                # Pre-rename configs serialized the old agno_* defaults for
                # every table; when the registry db uses its own default for
                # that table (key not serialized), treat the legacy name as
                # equal to the current default rather than as an explicit
                # override (one-release shim).
                and not (_is_legacy_default_table_name(key, db_data[key]) and registry_dict.get(key) is None)
                for key in DB_CONFIG_OVERRIDE_KEYS
            )
            if not has_table_overrides:
                return registry_db
            # Stored config customizes table names. Clone the registered
            # db so we reuse its engine/pool and only swap table names.
            clone, clone_raised = _clone_db_with_table_overrides(registry_db, db_data)
            if clone is not None:
                return clone
            # Either the registered backend doesn't implement clone_with_table_overrides
            # (e.g. JsonDb, RedisDb, FirestoreDb, DynamoDb, ...), so it inherits BaseDb's
            # default and returns None, or its clone raised. Both fall back to the
            # registered instance rather than building a fresh one via db_from_dict, which
            # would drop the shared engine. This means table overrides are silently ignored
            # here, but the component still gets a working db — same as the pre-override
            # behavior. Only the first case is a property of the backend, so only it gets
            # the "cannot apply" warning; the raising case already logged the exception
            # class and the same fallback in the helper.
            if not clone_raised:
                log_warning(
                    f"Cannot apply table-name overrides to db of type {type(registry_db).__name__}; "
                    "reusing the registered instance with its configured table names."
                )
            return registry_db

    return db_from_dict(db_data)
