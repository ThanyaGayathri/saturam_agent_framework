import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Type, Union

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.routing import APIRoute, APIRouter
from pydantic import BaseModel, create_model
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from egpt.agent import Agent, AgentFactory, RemoteAgent
from egpt.agent.protocol import AgentProtocol
from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.factory import (
    FactoryContextRequired,
    FactoryError,
    FactoryPermissionError,
    FactoryValidationError,
    RequestContext,
)
from egpt.knowledge.knowledge import Knowledge
from egpt.media import Audio, Image, Video
from egpt.media import File as FileMedia
from egpt.models.message import Message
from egpt.os.config import EgptOSConfig
from egpt.os.tenancy import TENANCY_REQUIRED_DETAIL, current_tenant_id, tenancy_enabled
from egpt.registry import Registry
from egpt.registry.hydrate import HydrationError, NoPublishedVersionError
from egpt.registry.policy import PolicyViolationError
from egpt.remote.base import RemoteDb, RemoteKnowledge
from egpt.run.agent import RunOutputEvent
from egpt.run.base import RunContext
from egpt.run.team import TeamRunOutputEvent
from egpt.run.workflow import WorkflowRunOutputEvent
from egpt.settings import jwt_is_configured
from egpt.team import RemoteTeam, Team, TeamFactory
from egpt.tools import Function, Toolkit
from egpt.utils.log import log_debug, log_warning, logger
from egpt.workflow import RemoteWorkflow, Workflow, WorkflowFactory


def to_utc_datetime(value: Optional[Union[str, int, float, datetime]]) -> Optional[datetime]:
    """Convert a timestamp, ISO 8601 string, or datetime to a UTC datetime."""
    if value is None:
        return None

    if isinstance(value, datetime):
        # If already a datetime, make sure the timezone is UTC
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    if isinstance(value, str):
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    return datetime.fromtimestamp(value, tz=timezone.utc)


async def get_request_kwargs(request: Request, endpoint_func: Callable) -> Dict[str, Any]:
    """Given a Request and an endpoint function, return a dictionary with all extra form data fields.

    Args:
        request: The FastAPI Request object
        endpoint_func: The function exposing the endpoint that received the request

    Supported form parameters:
        - session_state: JSON string of session state dict
        - dependencies: JSON string of dependencies dict
        - metadata: JSON string of metadata dict
        - knowledge_filters: JSON string of knowledge filters
        - output_schema: JSON schema string (converted to Pydantic model by default)
        - use_json_schema: If "true", keeps output_schema as dict instead of converting to Pydantic model

    Returns:
        A dictionary of kwargs to pass to Agent/Team run methods
    """
    import inspect

    form_data = await request.form()
    sig = inspect.signature(endpoint_func)
    known_fields = set(sig.parameters.keys())
    kwargs: Dict[str, Any] = {key: value for key, value in form_data.items() if key not in known_fields}

    # Handle JSON parameters. They are passed as strings and need to be deserialized.
    if session_state := kwargs.get("session_state"):
        try:
            if isinstance(session_state, str):
                session_state_dict = json.loads(session_state)  # type: ignore
                kwargs["session_state"] = session_state_dict
        except json.JSONDecodeError as e:
            kwargs.pop("session_state")
            log_warning(f"Invalid session_state parameter couldn't be loaded: {session_state}: {str(e)}")

    if dependencies := kwargs.get("dependencies"):
        try:
            if isinstance(dependencies, str):
                dependencies_dict = json.loads(dependencies)  # type: ignore
                kwargs["dependencies"] = dependencies_dict
        except json.JSONDecodeError as e:
            kwargs.pop("dependencies")
            log_warning(f"Invalid dependencies parameter couldn't be loaded: {dependencies}: {str(e)}")

    if metadata := kwargs.get("metadata"):
        try:
            if isinstance(metadata, str):
                metadata_dict = json.loads(metadata)  # type: ignore
                kwargs["metadata"] = metadata_dict
        except json.JSONDecodeError as e:
            kwargs.pop("metadata")
            log_warning(f"Invalid metadata parameter couldn't be loaded: {metadata}: {str(e)}")

    if knowledge_filters := kwargs.get("knowledge_filters"):
        try:
            if isinstance(knowledge_filters, str):
                knowledge_filters_dict = json.loads(knowledge_filters)  # type: ignore

                # Try to deserialize FilterExpr objects
                from egpt.filters import from_dict

                # Check if it's a single FilterExpr dict or a list of FilterExpr dicts
                if isinstance(knowledge_filters_dict, dict) and "op" in knowledge_filters_dict:
                    # Single FilterExpr - convert to list format
                    kwargs["knowledge_filters"] = [from_dict(knowledge_filters_dict)]
                elif isinstance(knowledge_filters_dict, list):
                    # List of FilterExprs or mixed content
                    deserialized = []
                    for item in knowledge_filters_dict:
                        if isinstance(item, dict) and "op" in item:
                            deserialized.append(from_dict(item))
                        else:
                            # Keep non-FilterExpr items as-is
                            deserialized.append(item)
                    kwargs["knowledge_filters"] = deserialized
                else:
                    # Regular dict filter
                    kwargs["knowledge_filters"] = knowledge_filters_dict
        except json.JSONDecodeError as e:
            kwargs.pop("knowledge_filters")
            log_warning(f"Invalid knowledge_filters parameter couldn't be loaded: {knowledge_filters}: {str(e)}")
        except ValueError as e:
            # Filter deserialization failed
            kwargs.pop("knowledge_filters")
            log_warning(f"Invalid FilterExpr in knowledge_filters: {str(e)}")

    # Handle output_schema - convert JSON schema to Pydantic model or keep as dict
    # use_json_schema is a control flag consumed here (not passed to Agent/Team)
    # When true, output_schema stays as dict for direct JSON output
    use_json_schema = kwargs.pop("use_json_schema", False)
    if isinstance(use_json_schema, str):
        use_json_schema = use_json_schema.lower() == "true"

    if output_schema := kwargs.get("output_schema"):
        try:
            if isinstance(output_schema, str):
                schema_dict = json.loads(output_schema)

                if use_json_schema:
                    # Keep as dict schema for direct JSON output
                    kwargs["output_schema"] = schema_dict
                else:
                    # Convert to Pydantic model (default behavior)
                    dynamic_model = json_schema_to_pydantic_model(schema_dict)
                    kwargs["output_schema"] = dynamic_model
        except json.JSONDecodeError as e:
            kwargs.pop("output_schema")
            log_warning(f"Invalid output_schema JSON: {output_schema}: {str(e)}")
        except Exception as e:
            kwargs.pop("output_schema")
            log_warning(f"Failed to create output_schema model: {str(e)}")

    # Parse boolean and null values
    for key, value in kwargs.items():
        if isinstance(value, str) and value.lower() in ["true", "false"]:
            kwargs[key] = value.lower() == "true"
        elif isinstance(value, str) and value.lower() in ["null", "none"]:
            kwargs[key] = None

    return kwargs


def format_sse_event(event: Union[RunOutputEvent, TeamRunOutputEvent, WorkflowRunOutputEvent]) -> str:
    """Parse JSON data into SSE-compliant format.

    Args:
        event_dict: Dictionary containing the event data

    Returns:
        SSE-formatted response:

        ```
        event: EventName
        data: { ... }

        event: AnotherEventName
        data: { ... }
        ```
    """
    try:
        # Parse the JSON to extract the event type
        event_type = event.event or "message"

        # Serialize to valid JSON with double quotes and no newlines
        clean_json = event.to_json(separators=(",", ":"), indent=None)

        return f"event: {event_type}\ndata: {clean_json}\n\n"
    except json.JSONDecodeError:
        clean_json = event.to_json(separators=(",", ":"), indent=None)
        return f"event: message\ndata: {clean_json}\n\n"


def format_sse_event_with_index(
    event: Union[RunOutputEvent, TeamRunOutputEvent, WorkflowRunOutputEvent],
    event_index: Optional[int] = None,
    run_id: Optional[str] = None,
) -> str:
    """Format an event as SSE with injected event_index and run_id.

    Used by the agent/team response streamers to include reconnection metadata
    in SSE payloads without modifying the core event dataclasses.

    Args:
        event: The event object to serialize.
        event_index: Buffer index for reconnection tracking.
        run_id: Run ID to inject if not already present on the event.

    Returns:
        SSE-formatted string with event_index in the data payload.
    """
    from egpt.utils.serialize import json_serializer

    try:
        event_type = event.event or "message"
        event_dict = event.to_dict()

        if event_index is not None:
            event_dict["event_index"] = event_index
        if run_id and "run_id" not in event_dict:
            event_dict["run_id"] = run_id

        clean_json = json.dumps(event_dict, separators=(",", ":"), default=json_serializer, ensure_ascii=False)
        return f"event: {event_type}\ndata: {clean_json}\n\n"
    except Exception:
        clean_json = event.to_json(separators=(",", ":"), indent=None)
        return f"event: message\ndata: {clean_json}\n\n"


async def get_db(
    dbs: dict[str, list[Union[BaseDb, AsyncBaseDb, RemoteDb]]], db_id: Optional[str] = None, table: Optional[str] = None
) -> Union[BaseDb, AsyncBaseDb, RemoteDb]:
    """Return the database with the given ID and/or table, or the first database if no ID/table is provided."""

    if table and not db_id:
        raise HTTPException(status_code=400, detail="The db_id query parameter is required when passing a table")

    async def _has_table(db: Union[BaseDb, AsyncBaseDb, RemoteDb], table_name: str) -> bool:
        """Check if this database has the specified table (configured and actually exists)."""
        # First check if table name is configured
        is_configured = (
            hasattr(db, "session_table_name")
            and db.session_table_name == table_name
            or hasattr(db, "memory_table_name")
            and db.memory_table_name == table_name
            or hasattr(db, "learnings_table_name")
            and db.learnings_table_name == table_name
            or hasattr(db, "metrics_table_name")
            and db.metrics_table_name == table_name
            or hasattr(db, "eval_table_name")
            and db.eval_table_name == table_name
            or hasattr(db, "knowledge_table_name")
            and db.knowledge_table_name == table_name
        )

        if not is_configured:
            return False

        if isinstance(db, RemoteDb):
            # We have to assume remote DBs are always configured and exist
            return True

        # Then check if table actually exists in the database
        try:
            if isinstance(db, AsyncBaseDb):
                # For async databases, await the check
                return await db.table_exists(table_name)
            else:
                # For sync databases, call directly
                return db.table_exists(table_name)
        except (NotImplementedError, AttributeError):
            # If table_exists not implemented, fall back to configuration check
            return is_configured

    # If db_id is provided, first find the database with that ID
    if db_id:
        target_db_list = dbs.get(db_id)
        if not target_db_list:
            raise HTTPException(status_code=404, detail=f"No database found with id '{db_id}'")

        # If table is also specified, search through all databases with this ID to find one with the table
        if table:
            for db in target_db_list:
                if await _has_table(db, table):
                    return db
            raise HTTPException(status_code=404, detail=f"No database with id '{db_id}' has table '{table}'")

        # If no table specified, return the first database with this ID
        return target_db_list[0]

    # Raise if multiple databases are provided but no db_id is provided
    if len(dbs) > 1:
        raise HTTPException(
            status_code=400, detail="The db_id query parameter is required when using multiple databases"
        )

    # Raise if no database is registered (an empty dict, or ids mapped to empty lists)
    all_dbs = [db for db_list in dbs.values() for db in db_list]
    if not all_dbs:
        raise HTTPException(status_code=400, detail="No database is configured on this EgptOS")

    # Return the first (and only) database
    return all_dbs[0]


def _generate_knowledge_id(name: str, db_id: str, table_name: str) -> str:
    """Generate a deterministic unique ID for a knowledge instance.

    Uses db_id, table_name, and name to ensure uniqueness across all knowledge instances.
    """
    import hashlib

    id_seed = f"{db_id}:{table_name}:{name}"
    # Use SHA256 instead of MD5 for FIPS compliance
    hash_hex = hashlib.sha256(id_seed.encode()).hexdigest()
    return f"{hash_hex[:8]}-{hash_hex[8:12]}-{hash_hex[12:16]}-{hash_hex[16:20]}-{hash_hex[20:32]}"


def get_knowledge_instance(
    knowledge_instances: List[Union[Knowledge, RemoteKnowledge]],
    db_id: Optional[str] = None,
    knowledge_id: Optional[str] = None,
) -> Union[Knowledge, RemoteKnowledge]:
    """Return the knowledge instance matching the given criteria.

    Args:
        knowledge_instances: List of knowledge instances to search
        db_id: Database ID to filter by (for backward compatibility)
        knowledge_id: Unique generated ID to filter by (preferred)

    Returns:
        The matching knowledge instance

    Raises:
        HTTPException: If no matching instance is found or parameters are invalid
    """
    # If only one instance and no specific identifier requested, return it (backwards compatible)
    if len(knowledge_instances) == 1 and not knowledge_id and not db_id:
        return next(iter(knowledge_instances))

    # If knowledge_id provided, find by unique ID (preferred)
    if knowledge_id:
        for knowledge in knowledge_instances:
            if not knowledge.contents_db:
                continue
            # Use knowledge name or generate fallback name from db_id
            name = getattr(knowledge, "name", None) or f"knowledge_{knowledge.contents_db.id}"
            kb_table_name = knowledge.contents_db.knowledge_table_name or "unknown"
            # Generate the unique ID for this knowledge instance
            generated_id = _generate_knowledge_id(name, knowledge.contents_db.id, kb_table_name)

            # Match by unique generated ID
            if generated_id == knowledge_id:
                return knowledge

        raise HTTPException(status_code=404, detail=f"Knowledge base '{knowledge_id}' not found")

    # If db_id provided, find by database ID (backward compatible)
    if db_id:
        matches = [k for k in knowledge_instances if k.contents_db and k.contents_db.id == db_id]
        if not matches:
            raise HTTPException(status_code=404, detail=f"Knowledge instance with db_id '{db_id}' not found")
        if len(matches) == 1:
            return matches[0]
        # Multiple matches - recommend using knowledge_id
        knowledge_ids = []
        for k in matches:
            if k.contents_db:
                name = getattr(k, "name", None) or f"knowledge_{k.contents_db.id}"
                table_name = k.contents_db.knowledge_table_name or "unknown"
                knowledge_ids.append(_generate_knowledge_id(name, k.contents_db.id, table_name))
        raise HTTPException(
            status_code=400,
            detail=f"Multiple knowledge instances found for db_id '{db_id}'. "
            f"Please specify knowledge_id parameter. Available IDs: {knowledge_ids}",
        )

    # No identifiers provided - list available IDs
    knowledge_ids = []
    for k in knowledge_instances:
        if k.contents_db:
            name = getattr(k, "name", None) or f"knowledge_{k.contents_db.id}"
            table_name = k.contents_db.knowledge_table_name or "unknown"
            knowledge_ids.append(_generate_knowledge_id(name, k.contents_db.id, table_name))
    raise HTTPException(
        status_code=400,
        detail=f"db_id or knowledge_id query parameter is required when using multiple knowledge bases. "
        f"Available IDs: {knowledge_ids}",
    )


def get_run_input(run_dict: Dict[str, Any], is_workflow_run: bool = False) -> str:
    """Get the run input from the given run dictionary

    Uses the RunInput/TeamRunInput object which stores the original user input.
    """

    # For agent or team runs, use the stored input_content
    if not is_workflow_run and run_dict.get("input") is not None:
        input_data = run_dict.get("input")
        if isinstance(input_data, dict) and input_data.get("input_content") is not None:
            return stringify_input_content(input_data["input_content"])

    if is_workflow_run:
        # Check the input field directly
        input_value = run_dict.get("input")
        if input_value is not None:
            return stringify_input_content(input_value)

        # Check the step executor runs for fallback
        step_executor_runs = run_dict.get("step_executor_runs", [])
        if step_executor_runs:
            for message in reversed(step_executor_runs[0].get("messages", [])):
                if message.get("role") == "user":
                    return message.get("content", "")

    # Final fallback: scan messages
    if run_dict.get("messages") is not None:
        for message in reversed(run_dict["messages"]):
            if message.get("role") == "user":
                return message.get("content", "")

    return ""


def get_session_name(session: Dict[str, Any]) -> str:
    """Get the session name from the given session dictionary"""

    # If session_data.session_name is set, return that
    session_data = session.get("session_data")
    if session_data is not None and session_data.get("session_name") is not None:
        return session_data["session_name"]

    runs = session.get("runs", []) or []
    session_type = session.get("session_type")

    # Handle workflows separately
    if session_type == "workflow":
        if not runs:
            return ""
        workflow_run = runs[0]
        workflow_input = workflow_run.get("input")
        if isinstance(workflow_input, str):
            return workflow_input
        elif isinstance(workflow_input, dict):
            try:
                return json.dumps(workflow_input)
            except (TypeError, ValueError):
                pass
        workflow_name = session.get("workflow_data", {}).get("name")
        return f"New {workflow_name} Session" if workflow_name else ""

    # For team, filter to team runs (runs without agent_id); for agents, use all runs
    if session_type == "team":
        runs_to_check = [r for r in runs if not r.get("agent_id")]
    else:
        runs_to_check = runs

    # Find the first user message across runs
    for r in runs_to_check:
        if r is None:
            continue
        run_dict = r if isinstance(r, dict) else r.to_dict()

        for message in run_dict.get("messages") or []:
            if message.get("role") == "user" and message.get("content"):
                return message["content"]

        run_input = r.get("input")
        if run_input is not None:
            return stringify_input_content(run_input)

    return ""


def extract_input_media(run_dict: Dict[str, Any]) -> Dict[str, Any]:
    input_media: Dict[str, List[Any]] = {
        "images": [],
        "videos": [],
        "audios": [],
        "files": [],
    }

    input_data = run_dict.get("input", {})
    if isinstance(input_data, dict):
        input_media["images"].extend(input_data.get("images", []))
        input_media["videos"].extend(input_data.get("videos", []))
        input_media["audios"].extend(input_data.get("audios", []))
        input_media["files"].extend(input_data.get("files", []))

    return input_media


# Supported MIME types per media category, used to route uploaded files to the
# correct processor. Keep these aligned with `File.valid_mime_types()` in egpt.media
# for document types.
IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/tif",
    "image/avif",
    "image/heic",
    "image/heif",
}

AUDIO_MIME_TYPES = {
    "audio/wav",
    "audio/wave",
    "audio/mp3",
    "audio/mpeg",
    "audio/ogg",
    "audio/mp4",
    "audio/m4a",
    "audio/aac",
    "audio/flac",
}

VIDEO_MIME_TYPES = {
    "video/x-flv",
    "video/quicktime",
    "video/mpeg",
    "video/mpegs",
    "video/mpgs",
    "video/mpg",
    "video/mp4",
    "video/webm",
    "video/wmv",
    "video/3gpp",
}

# NOTE: Keep this in sync with `File.valid_mime_types()` in egpt.media. Every type here must
# be valid there, or the upload returns 200 but the file is silently dropped during FileMedia
# construction. Office binary/OOXML formats (.doc, .docx, .ppt, .pptx, .xls, .xlsx) are accepted
# at upload, but not all model providers support them as raw input - Anthropic and Gemini, for
# example, 400 on PowerPoint. Those uploads succeed here and fail later with a provider error.
DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "application/json",
    "application/x-javascript",
    # Office Open XML (modern Office formats)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    # Legacy binary Office formats
    "application/msword",  # .doc
    "application/vnd.ms-powerpoint",  # .ppt
    "application/vnd.ms-excel",  # .xls
    "application/vnd.ms-outlook",  # .msg
    "text/javascript",
    "application/x-python",
    "text/x-python",
    "text/plain",
    "text/html",
    "text/css",
    "text/markdown",
    "text/csv",
    "text/xml",
    "text/rtf",
}

# Fallback mapping from file extension to media category. Used when the browser sends a
# missing or ambiguous content type (e.g. `application/octet-stream` or empty for `.md`
# and `.pptx`, which are not in every OS MIME registry).
EXTENSION_CATEGORY: Dict[str, str] = {
    # documents
    "pdf": "document",
    "json": "document",
    "js": "document",
    "docx": "document",
    "doc": "document",
    "pptx": "document",
    "ppt": "document",
    "xlsx": "document",
    "xls": "document",
    "msg": "document",
    "py": "document",
    "txt": "document",
    "html": "document",
    "htm": "document",
    "css": "document",
    "md": "document",
    "markdown": "document",
    "csv": "document",
    "xml": "document",
    "rtf": "document",
    # images
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "webp": "image",
    "bmp": "image",
    "tiff": "image",
    "tif": "image",
    "avif": "image",
    "heic": "image",
    "heif": "image",
    # audio
    "wav": "audio",
    "mp3": "audio",
    "ogg": "audio",
    "m4a": "audio",
    "aac": "audio",
    "flac": "audio",
    # video
    "flv": "video",
    "mov": "video",
    "mpeg": "video",
    "mpg": "video",
    "mp4": "video",
    "webm": "video",
    "wmv": "video",
    "3gp": "video",
}

# Content types that are too generic to classify on their own; fall back to the
# file extension for these.
_AMBIGUOUS_CONTENT_TYPES = {None, "", "application/octet-stream"}


def classify_upload_file(file: UploadFile) -> Optional[str]:
    """Classify an uploaded file into one of: image, audio, video, document.

    Routes primarily by `content_type`. When the content type is missing or too generic
    to be useful (common for `.md` and `.pptx` uploaded from browsers), falls back to the
    filename extension. Returns None if the file type is not supported.
    """
    content_type = file.content_type
    if content_type in IMAGE_MIME_TYPES:
        return "image"
    if content_type in AUDIO_MIME_TYPES:
        return "audio"
    if content_type in VIDEO_MIME_TYPES:
        return "video"
    if content_type in DOCUMENT_MIME_TYPES:
        return "document"

    # Fall back to the file extension for ambiguous/missing content types.
    if content_type in _AMBIGUOUS_CONTENT_TYPES and file.filename and "." in file.filename:
        extension = file.filename.rsplit(".", 1)[-1].lower()
        return EXTENSION_CATEGORY.get(extension)

    return None


def process_image(file: UploadFile) -> Image:
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    return Image(content=content, format=extract_format(file), mime_type=file.content_type)


def process_audio(file: UploadFile) -> Audio:
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    return Audio(content=content, format=extract_format(file), mime_type=file.content_type)


def process_video(file: UploadFile) -> Video:
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    return Video(content=content, format=extract_format(file), mime_type=file.content_type)


# Map document file extensions to their canonical MIME type, used to recover a valid
# mime_type when the browser sends a missing or generic content type (e.g. `.md`).
_DOCUMENT_EXTENSION_MIME: Dict[str, str] = {
    "pdf": "application/pdf",
    "json": "application/json",
    "js": "text/javascript",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "msg": "application/vnd.ms-outlook",
    "py": "text/x-python",
    "txt": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "css": "text/css",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "xml": "text/xml",
    "rtf": "text/rtf",
}


def _resolve_document_mime_type(file: UploadFile) -> Optional[str]:
    """Resolve a valid document MIME type for an upload.

    Prefers a usable `content_type`; otherwise derives it from the file extension so
    documents with ambiguous content types (e.g. `.md` sent as octet-stream) still get a
    mime_type accepted by `FileMedia`.
    """
    if file.content_type and file.content_type in DOCUMENT_MIME_TYPES:
        return file.content_type
    if file.filename and "." in file.filename:
        extension = file.filename.rsplit(".", 1)[-1].lower()
        return _DOCUMENT_EXTENSION_MIME.get(extension)
    return file.content_type


def process_document(file: UploadFile) -> Optional[FileMedia]:
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    # FileMedia construction validates the mime_type against File.valid_mime_types(). Every
    # type in DOCUMENT_MIME_TYPES must also be valid there, otherwise the file is silently
    # dropped here (the upload still returns 200). The unit tests assert the two stay in sync.
    return FileMedia(
        content=content,
        filename=file.filename,
        format=extract_format(file),
        mime_type=_resolve_document_mime_type(file),
    )


def extract_format(file: UploadFile) -> Optional[str]:
    """Extract the File format from file name or content_type."""
    # Get the format from the filename
    if file.filename and "." in file.filename:
        return file.filename.split(".")[-1].lower()

    # Fallback to the file content_type
    if file.content_type:
        return file.content_type.strip().split("/")[-1]

    return None


def build_request_context(
    request: Request,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    factory_input: Optional[str] = None,
) -> RequestContext:
    """Build a RequestContext from a FastAPI request and form fields.

    Parses factory_input JSON and populates trusted context from request.state
    (set by auth middleware).
    """
    from egpt.factory import TrustedContext

    # Parse factory_input JSON string
    parsed_input: Any = None
    if factory_input is not None:
        try:
            parsed_input = json.loads(factory_input)
        except (json.JSONDecodeError, TypeError) as e:
            raise HTTPException(status_code=400, detail=f"factory_input must be valid JSON: {e}")
        if not isinstance(parsed_input, dict):
            raise HTTPException(
                status_code=400,
                detail=f"factory_input must be a JSON object, got {type(parsed_input).__name__}",
            )

    # Build trusted context from middleware-populated request.state
    claims = getattr(request.state, "claims", None) or {}
    scopes = getattr(request.state, "scopes", None) or frozenset()
    if isinstance(scopes, (list, set)):
        scopes = frozenset(scopes)
    trusted = TrustedContext(claims=claims, scopes=scopes)

    return RequestContext(
        user_id=user_id,
        session_id=session_id,
        request=request,
        input=parsed_input,
        trusted=trusted,
        tenant_id=resolve_tenant_id(claims, user_id, verified_user_id=getattr(request.state, "user_id", None)),
    )


def build_run_context_for_request(
    request: Optional[Any],
    *,
    session_id: str,
    run_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> RunContext:
    """A ``RunContext`` carrying the *verified* tenant for this HTTP request (EGPT-3.6).

    The seam reads ``RunContext.tenant_id`` to select a ``ModelPolicy`` and, once EGPT-3.6's
    enforcement lands, a spend budget. So the value must come from ``resolve_tenant_id`` and
    nowhere else: that function requires either a verified JWT claim or a ``user_id`` that matches
    the middleware-stamped identity, and returns ``None`` otherwise.

    Deliberately does **not** accept a ``tenant_id`` argument. A caller-supplied tenant on this
    path would be client-nameable -- the request body and query string are attacker-controlled --
    and naming your own tenant selects another tenant's backend, adapter and ``config`` blob,
    which may carry a base_url or credentials. That is the spoofability class PR #46 closed inside
    ``resolve_tenant_id``; re-opening it one layer up would undo the fix.

    ``request=None`` (a programmatic caller, or a route with no request in scope) yields
    ``tenant_id=None``, which selects the default policy and no budget.
    """
    tenant_id: Optional[str] = None
    if request is not None:
        state = getattr(request, "state", None)
        claims = getattr(state, "claims", None) or {}
        tenant_id = resolve_tenant_id(
            claims,
            user_id,
            verified_user_id=getattr(state, "user_id", None),
        )

    # `run_id` defaults to a placeholder the run replaces: RunContext requires one positionally,
    # but the *authoritative* id is minted inside the run. A router passing its own forked it into
    # two values, so callers on the run path deliberately omit it.
    return RunContext(
        run_id=run_id or "",
        session_id=session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        metadata=metadata,
    )


def resolve_tenant_id(
    claims: Mapping[str, Any],
    user_id: Optional[str],
    *,
    verified_user_id: Optional[str] = None,
) -> Optional[str]:
    """The tenant for this request: a verified claim if the IdP sends one, else the
    ``user_id`` -- but only when that user_id is the middleware-verified identity.

    A tenant id selects a ``ModelPolicy``: the backend, the adapter, and the ``config`` blob
    (which may carry a base_url or credentials). So it must never be client-nameable. The
    ``user_id`` that reaches this function originates from a ``Form(None)`` field, and is only
    overwritten by a verified value when JWT middleware or user isolation ran -- both of which
    stamp ``request.state.user_id``. The fallback therefore requires ``user_id`` to match
    ``verified_user_id`` (that stamped value): a raw form value on an unauthenticated or
    admin-scope request yields ``tenant_id=None`` and the default policy, not the tenant of
    the caller's choosing.

    There is still no real tenant concept in this tree: for verified callers, ``user_id`` is
    the de-facto tenant axis and config-driven resolution is per-user wearing a tenant-shaped
    interface. When Lane A's tenant column lands, only this function changes.

    The claim name is configurable (``EGPT_TENANT_CLAIM``) because IdPs disagree: Auth0 emits
    ``org_id``, Entra emits ``tid``.

    Two resolvers, two purposes (aicoe-tracker#174)
    ------------------------------------------------
    This is deliberately not ``egpt.os.tenancy.current_tenant_id``, and the ``user_id``
    fallback above is the reason: it exists for ``ModelPolicy``/budget selection
    (``RequestContext.tenant_id``, ``RunContext.tenant_id``), where a verified caller with no
    tenant claim getting their own per-user policy is a reasonable default. It must **never**
    reach a component *data-access* call -- ``tenant_id=None`` at the DB layer means "no
    predicate", so a guessed ``user_id`` tenant there would either widen a read past
    ``require_tenant_id``'s fail-closed refusal, or silently write into a tenant namespace no
    operator configured. Component hydration -- including the WebSocket/factory path, which has
    no ``Request`` for ``current_tenant_id`` to read -- must resolve through
    ``tenant_for_ws_hydration`` (or ``_tenant_for_hydration``/``tenant_for_direct_read`` off a
    real ``Request``) instead, all of which fail closed the same way ``require_tenant_id`` does
    rather than falling back to ``user_id``.
    """
    from egpt.settings.providers import resolve_provider_setting

    # A single live env read, not a whole ProviderSettings model: this runs on every
    # factory-backed request, and constructing the model per request costs ~0.5ms to read one
    # string that changes only on redeploy.
    claim_name = resolve_provider_setting("tenant_claim") or "tenant_id"
    value = claims.get(claim_name) if claims else None
    if isinstance(value, str) and value.strip():
        return value
    if user_id is not None and user_id == verified_user_id:
        return user_id
    return None


def find_factory_by_id(
    component_id: str,
    components: Optional[Sequence[Any]],
) -> Optional[Any]:
    """Find a factory entry by ID from a list of components."""
    if not components:
        return None
    from egpt.factory.base import BaseFactory

    for component in components:
        if isinstance(component, BaseFactory) and component.id == component_id:
            return component
    return None


def get_agent_by_id(
    agent_id: str,
    agents: Optional[Sequence[Union[Agent, RemoteAgent, AgentProtocol, AgentFactory]]] = None,
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    registry: Optional[Registry] = None,
    version: Optional[int] = None,
    create_fresh: bool = False,
    ctx: Optional[RequestContext] = None,
    tenant_id: Optional[str] = None,
) -> Optional[Union[Agent, RemoteAgent, AgentProtocol]]:
    """Get an agent by ID, optionally creating a fresh instance for request isolation.

    When create_fresh=True, creates a new agent instance using deep_copy() to prevent
    state contamination between concurrent requests. The new instance shares heavy
    resources (db, model, MCP tools) but has isolated mutable state.

    If the matched entry is an AgentFactory, invokes the factory with the provided
    RequestContext to produce a fresh Agent.

    Args:
        agent_id: The agent ID to look up
        agents: List of agents (and/or AgentFactory entries) to search
        create_fresh: If True, creates a new instance using deep_copy()
        ctx: RequestContext for factory invocation (required if a factory is matched)
        tenant_id: Optional caller tenant (EGPT-2.5). Scopes the DB arm only -- code-defined
            agents in the in-memory list are deployment-owned, not tenant rows.

    Returns:
        The agent instance (shared or fresh copy based on create_fresh)
    """
    if agent_id is None:
        return None

    # Try to get the agent from the list of agents
    if agents:
        for agent in agents:
            if agent.id == agent_id:
                # Base Agent — most common path, early exit
                if isinstance(agent, Agent):
                    if create_fresh:
                        fresh_agent = agent.deep_copy()
                        fresh_agent.team_id = None
                        fresh_agent.workflow_id = None
                        return fresh_agent
                    return agent
                # Factory path
                if isinstance(agent, AgentFactory):
                    if ctx is None:
                        raise FactoryContextRequired(f"Agent '{agent_id}' is a factory and requires a RequestContext.")
                    return agent.resolve(ctx, expected_type=Agent)
                # RemoteAgent or other
                return agent

    # Try to get the agent from the database
    if db and isinstance(db, BaseDb):
        from egpt.agent.agent import get_agent_by_id as get_agent_by_id_db

        try:
            db_agent = get_agent_by_id_db(db=db, id=agent_id, version=version, registry=registry, tenant_id=tenant_id)
            return db_agent
        # A policy refusal must reach resolve_agent, which turns it into a 403, and a hydration
        # failure must reach it as a 409/500. Collapsing either to None here would surface as
        # "agent not found" instead.
        except (PolicyViolationError, HydrationError):
            raise
        except Exception:
            logger.exception(f"Error getting agent {agent_id} from database")
            return None

    return None


async def get_agent_by_id_async(
    agent_id: str,
    agents: Optional[Sequence[Union[Agent, RemoteAgent, AgentProtocol, AgentFactory]]] = None,
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    registry: Optional[Registry] = None,
    version: Optional[int] = None,
    create_fresh: bool = False,
    ctx: Optional[RequestContext] = None,
    tenant_id: Optional[str] = None,
) -> Optional[Union[Agent, RemoteAgent, AgentProtocol]]:
    """Async variant of get_agent_by_id that supports async factories."""
    if agent_id is None:
        return None

    if agents:
        for agent in agents:
            if agent.id == agent_id:
                # Base Agent — most common path, early exit
                if isinstance(agent, Agent):
                    if create_fresh:
                        fresh_agent = agent.deep_copy()
                        fresh_agent.team_id = None
                        fresh_agent.workflow_id = None
                        return fresh_agent
                    return agent
                # Factory path
                if isinstance(agent, AgentFactory):
                    if ctx is None:
                        raise FactoryContextRequired(f"Agent '{agent_id}' is a factory and requires a RequestContext.")
                    result = await agent.resolve_async(ctx, expected_type=Agent)
                    return result
                # RemoteAgent or other
                return agent

    if db and isinstance(db, BaseDb):
        from egpt.agent.agent import get_agent_by_id as get_agent_by_id_db

        try:
            # The component getter is sync (AsyncBaseDb has no component API), so hop to the
            # threadpool rather than blocking the event loop on DB reads.
            db_agent = await run_in_threadpool(
                get_agent_by_id_db, db=db, id=agent_id, version=version, registry=registry, tenant_id=tenant_id
            )
            return db_agent
        # A policy refusal must reach resolve_agent, which turns it into a 403, and a hydration
        # failure must reach it as a 409/500. Collapsing either to None here would surface as
        # "agent not found" instead.
        except (PolicyViolationError, HydrationError):
            raise
        except Exception:
            logger.exception(f"Error getting agent {agent_id} from database")
            return None

    return None


def get_team_by_id(
    team_id: str,
    teams: Optional[Sequence[Union[Team, RemoteTeam, TeamFactory]]] = None,
    create_fresh: bool = False,
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    version: Optional[int] = None,
    registry: Optional[Registry] = None,
    ctx: Optional[RequestContext] = None,
    tenant_id: Optional[str] = None,
) -> Optional[Union[Team, RemoteTeam]]:
    """Get a team by ID, optionally creating a fresh instance for request isolation.

    When create_fresh=True, creates a new team instance using deep_copy() to prevent
    state contamination between concurrent requests. Member agents are also deep copied.

    If the matched entry is a TeamFactory, invokes the factory with the provided
    RequestContext to produce a fresh Team.

    Args:
        team_id: The team ID to look up
        teams: List of teams (and/or TeamFactory entries) to search
        create_fresh: If True, creates a new instance using deep_copy()
        ctx: RequestContext for factory invocation (required if a factory is matched)

    Returns:
        The team instance (shared or fresh copy based on create_fresh)
    """
    if team_id is None:
        return None

    if teams:
        for team in teams:
            if team.id == team_id:
                if isinstance(team, Team):
                    if create_fresh:
                        return team.deep_copy()
                    return team
                if isinstance(team, TeamFactory):
                    if ctx is None:
                        raise FactoryContextRequired(f"Team '{team_id}' is a factory and requires a RequestContext.")
                    result = team.resolve(ctx, expected_type=Team)
                    return result
                return team

    if db and isinstance(db, BaseDb):
        from egpt.team.team import get_team_by_id as get_team_by_id_db

        try:
            db_team = get_team_by_id_db(db=db, id=team_id, version=version, registry=registry, tenant_id=tenant_id)
            return db_team
        except (PolicyViolationError, HydrationError):
            raise
        except Exception:
            logger.exception(f"Error getting team {team_id} from database")
            return None

    return None


async def get_team_by_id_async(
    team_id: str,
    teams: Optional[Sequence[Union[Team, RemoteTeam, TeamFactory]]] = None,
    create_fresh: bool = False,
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    version: Optional[int] = None,
    registry: Optional[Registry] = None,
    ctx: Optional[RequestContext] = None,
    tenant_id: Optional[str] = None,
) -> Optional[Union[Team, RemoteTeam]]:
    """Async variant of get_team_by_id that supports async factories."""
    if team_id is None:
        return None

    if teams:
        for team in teams:
            if team.id == team_id:
                if isinstance(team, Team):
                    if create_fresh:
                        return team.deep_copy()
                    return team
                if isinstance(team, TeamFactory):
                    if ctx is None:
                        raise FactoryContextRequired(f"Team '{team_id}' is a factory and requires a RequestContext.")
                    result = await team.resolve_async(ctx, expected_type=Team)
                    return result
                return team

    if db and isinstance(db, BaseDb):
        from egpt.team.team import get_team_by_id as get_team_by_id_db

        try:
            # Sync component getter -- threadpool hop keeps DB reads off the event loop.
            db_team = await run_in_threadpool(
                get_team_by_id_db, db=db, id=team_id, version=version, registry=registry, tenant_id=tenant_id
            )
            return db_team
        except (PolicyViolationError, HydrationError):
            raise
        except Exception:
            logger.exception(f"Error getting team {team_id} from database")
            return None

    return None


def get_workflow_by_id(
    workflow_id: str,
    workflows: Optional[Sequence[Union[Workflow, RemoteWorkflow, WorkflowFactory]]] = None,
    create_fresh: bool = False,
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    version: Optional[int] = None,
    registry: Optional[Registry] = None,
    ctx: Optional[RequestContext] = None,
    tenant_id: Optional[str] = None,
) -> Optional[Union[Workflow, RemoteWorkflow]]:
    """Get a workflow by ID, optionally creating a fresh instance for request isolation.

    When create_fresh=True, creates a new workflow instance using deep_copy() to prevent
    state contamination between concurrent requests. Steps containing agents/teams are also deep copied.

    If the matched entry is a WorkflowFactory, invokes the factory with the provided
    RequestContext to produce a fresh Workflow.

    Args:
        workflow_id: The workflow ID to look up
        workflows: List of workflows (and/or WorkflowFactory entries) to search
        create_fresh: If True, creates a new instance using deep_copy()
        db: Optional database interface
        version: Workflow version, if needed
        registry: Optional Registry instance
        ctx: RequestContext for factory invocation (required if a factory is matched)

    Returns:
        The workflow instance (shared or fresh copy based on create_fresh)
    """
    if workflow_id is None:
        return None

    if workflows:
        for workflow in workflows:
            if workflow.id == workflow_id:
                if isinstance(workflow, Workflow):
                    if create_fresh:
                        return workflow.deep_copy()
                    return workflow
                if isinstance(workflow, WorkflowFactory):
                    if ctx is None:
                        raise FactoryContextRequired(
                            f"Workflow '{workflow_id}' is a factory and requires a RequestContext."
                        )
                    result = workflow.resolve(ctx, expected_type=Workflow)
                    return result
                return workflow

    if db and isinstance(db, BaseDb):
        from egpt.workflow.workflow import get_workflow_by_id as get_workflow_by_id_db

        try:
            db_workflow = get_workflow_by_id_db(
                db=db, id=workflow_id, version=version, registry=registry, tenant_id=tenant_id
            )
            return db_workflow
        except (PolicyViolationError, HydrationError):
            raise
        except Exception:
            logger.exception(f"Error getting workflow {workflow_id} from database")
            return None

    return None


async def get_workflow_by_id_async(
    workflow_id: str,
    workflows: Optional[Sequence[Union[Workflow, RemoteWorkflow, WorkflowFactory]]] = None,
    create_fresh: bool = False,
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    version: Optional[int] = None,
    registry: Optional[Registry] = None,
    ctx: Optional[RequestContext] = None,
    tenant_id: Optional[str] = None,
) -> Optional[Union[Workflow, RemoteWorkflow]]:
    """Async variant of get_workflow_by_id that supports async factories."""
    if workflow_id is None:
        return None

    if workflows:
        for workflow in workflows:
            if workflow.id == workflow_id:
                if isinstance(workflow, Workflow):
                    if create_fresh:
                        return workflow.deep_copy()
                    return workflow
                if isinstance(workflow, WorkflowFactory):
                    if ctx is None:
                        raise FactoryContextRequired(
                            f"Workflow '{workflow_id}' is a factory and requires a RequestContext."
                        )
                    result = await workflow.resolve_async(ctx, expected_type=Workflow)
                    return result
                return workflow

    if db and isinstance(db, BaseDb):
        from egpt.workflow.workflow import get_workflow_by_id as get_workflow_by_id_db

        try:
            # Sync component getter -- threadpool hop keeps DB reads off the event loop.
            db_workflow = await run_in_threadpool(
                get_workflow_by_id_db, db=db, id=workflow_id, version=version, registry=registry, tenant_id=tenant_id
            )
            return db_workflow
        except (PolicyViolationError, HydrationError):
            raise
        except Exception:
            logger.exception(f"Error getting workflow {workflow_id} from database")
            return None

    return None


def resolve_origins(user_origins: Optional[List[str]] = None, default_origins: Optional[List[str]] = None) -> List[str]:
    """
    Get CORS origins - user-provided origins override defaults.

    Args:
        user_origins: Optional list of user-provided CORS origins

    Returns:
        List of allowed CORS origins (user-provided if set, otherwise defaults)
    """
    # User-provided origins override defaults
    if user_origins:
        return user_origins

    # Legacy upstream (agno.com) origins, deliberately retained: removing them would cut
    # off consoles already pointed at this API. Out of scope for the EGPT-0.12 rebrand
    # (aicoe-tracker#149) and tracked as its own decision — not an oversight.
    # Kept in sync with the identical default list in egpt/os/settings.py.
    return default_origins or [
        "http://localhost:3000",
        "https://agno.com",
        "https://www.agno.com",
        "https://app.agno.com",
        "https://os-stg.agno.com",
        "https://os.agno.com",
    ]


def resolve_ws_jwt_config(app: FastAPI) -> Dict[str, Any]:
    """Resolve JWT auth config for the WebSocket entrypoint.

    EgptOS (authorization=True) eagerly populates ``app.state.jwt_validator``,
    ``app.state.jwt_verify_audience``, ``app.state.jwt_audience``, and
    ``app.state.admin_scope`` from the authorization config.

    For the manual ``app.add_middleware(JWTMiddleware, ...)`` path those
    attributes are only populated lazily by ``JWTMiddleware.dispatch`` on the
    FIRST HTTP request. WebSocket connections do not run that dispatch, so a
    WebSocket connection that arrives before any HTTP request would otherwise
    see no validator and silently fall through to ``requires_auth=False``.

    This helper bridges that gap by walking ``app.user_middleware`` to find a
    ``JWTMiddleware`` entry, building a validator from its kwargs the same way
    the middleware does, and caching the result on ``app.state``.
    """
    # Every key here must also be produced by the two populated returns below, and stamped by
    # JWTMiddleware's app.state cache block. `test_ws_jwt_config_carries_every_field` pins the
    # key set so a field cannot be added in one place and forgotten in the others -- which is
    # exactly how tenant_id_claim was missed when it was introduced (PR #53 review).
    blank: Dict[str, Any] = {
        "validator": None,
        "verify_audience": False,
        "audience": None,
        "admin_scope": None,
        "user_isolation": False,
        "tenant_id_claim": None,
        "auth_required": False,
    }

    state = getattr(app, "state", None)
    if state is None:
        return blank

    validator = getattr(state, "jwt_validator", None)
    if validator is not None:
        return {
            "validator": validator,
            "verify_audience": getattr(state, "jwt_verify_audience", False),
            "audience": getattr(state, "jwt_audience", None),
            "admin_scope": getattr(state, "admin_scope", None),
            "user_isolation": bool(getattr(state, "user_isolation_enabled", False)),
            "tenant_id_claim": getattr(state, "tenant_id_claim", None),
            "auth_required": True,
        }

    # Lazy resolution for manual setup: locate JWTMiddleware in user_middleware
    # and build its validator from kwargs. Avoid importing JWTMiddleware at
    # module import time to keep WebSocket-less imports light.
    user_middleware = getattr(app, "user_middleware", None)
    if not user_middleware:
        return blank

    from egpt.os.middleware.jwt import JWTMiddleware, JWTValidator, jwt_kwargs_have_key_source

    for entry in user_middleware:
        if getattr(entry, "cls", None) is JWTMiddleware:
            kwargs = getattr(entry, "kwargs", {}) or {}
            # EgptOS installs this same middleware class as the general auth layer
            # for security-key / service-account-only deployments, with no JWT key
            # source. Those entries are not JWT-intended: skip them so the WS
            # endpoint falls through to the PAT and security-key auth paths instead
            # of demanding JWTs nobody can mint. Env-configured keys still count --
            # JWTValidator reads JWT_VERIFICATION_KEY / JWT_JWKS_FILE itself.
            if not jwt_kwargs_have_key_source(kwargs) and not jwt_is_configured():
                continue
            # Mirror JWTMiddleware.__init__ deprecated secret_key handling:
            # append to verification_keys so manual setups using secret_key
            # still get a working WebSocket validator.
            verification_keys = list(kwargs.get("verification_keys") or [])
            legacy_secret = kwargs.get("secret_key")
            if legacy_secret and legacy_secret not in verification_keys:
                verification_keys.append(legacy_secret)
            try:
                lazy_validator = JWTValidator(
                    verification_keys=verification_keys or None,
                    jwks_file=kwargs.get("jwks_file"),
                    algorithm=kwargs.get("algorithm", "RS256"),
                    validate=kwargs.get("validate", True),
                    scopes_claim=kwargs.get("scopes_claim", "scopes"),
                    user_id_claim=kwargs.get("user_id_claim", "sub"),
                    session_id_claim=kwargs.get("session_id_claim", "session_id"),
                    audience_claim=kwargs.get("audience_claim", "aud"),
                )
            except Exception as e:
                log_warning(f"Could not lazily construct JWTValidator for WebSocket auth: {e}")
                # JWTMiddleware IS configured, so auth was intended. Return
                # auth_required=True so the WS endpoint rejects connections
                # instead of silently falling through to unauthenticated mode.
                return {**blank, "auth_required": True}

            verify_audience = bool(kwargs.get("verify_audience", False))
            audience = kwargs.get("audience")
            admin_scope = kwargs.get("admin_scope")
            user_isolation = bool(kwargs.get("user_isolation", False))
            tenant_id_claim = kwargs.get("tenant_id_claim")

            # Cache on app.state so subsequent WebSocket connections and the
            # HTTP middleware see the same validator instance.
            state.jwt_validator = lazy_validator
            state.jwt_verify_audience = verify_audience
            state.jwt_audience = audience
            if admin_scope:
                state.admin_scope = admin_scope
            state.user_isolation_enabled = user_isolation
            # Guarded like admin_scope above: an entry that names no claim must not clear one
            # EgptOS (or an earlier entry) already stamped. tenancy_enabled_for_app reads this
            # attribute for both transports, so an unconditional None here would silently turn
            # tenancy off everywhere (PR #121 review).
            if tenant_id_claim:
                state.tenant_id_claim = tenant_id_claim

            return {
                "validator": lazy_validator,
                "verify_audience": verify_audience,
                "audience": audience,
                "admin_scope": admin_scope,
                "user_isolation": user_isolation,
                "tenant_id_claim": tenant_id_claim,
                "auth_required": True,
            }

    return blank


def update_cors_middleware(app: FastAPI, new_origins: list):
    existing_origins: List[str] = []

    # TODO: Allow more options where CORS is properly merged and user can disable this behaviour

    # Extract existing origins from current CORS middleware
    for middleware in app.user_middleware:
        if middleware.cls == CORSMiddleware:
            if hasattr(middleware, "kwargs"):
                origins_value = middleware.kwargs.get("allow_origins", [])
                if isinstance(origins_value, list):
                    existing_origins = origins_value
                else:
                    existing_origins = []
            break
    # Merge origins
    merged_origins = list(set(new_origins + existing_origins))
    final_origins = [origin for origin in merged_origins if origin != "*"]

    # Remove existing CORS
    app.user_middleware = [m for m in app.user_middleware if m.cls != CORSMiddleware]
    app.middleware_stack = None

    # Add updated CORS
    app.add_middleware(
        CORSMiddleware,  # type: ignore
        allow_origins=final_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )


def flatten_routes(routes: Sequence[Any]) -> List[Any]:
    """Expand included routers into their underlying routes.

    FastAPI 0.137 wraps each included router in a single path-less object instead of
    inlining its routes, so recurse through those wrappers to recover the real routes.

    Each route keeps the path defined on its own router; a prefix passed at include time
    (include_router(prefix=...)) is not applied. EgptOS bakes prefixes into the routers
    themselves, so its routes are unaffected.

    Returns:
        List[Any]: The routes with any included routers expanded in place.
    """
    flattened_routes: List[Any] = []
    for route in routes:
        included_router = getattr(route, "original_router", None)
        if included_router is not None and hasattr(included_router, "routes"):
            flattened_routes.extend(flatten_routes(included_router.routes))
        else:
            flattened_routes.append(route)
    return flattened_routes


def get_existing_route_paths(fastapi_app: FastAPI) -> Dict[str, List[str]]:
    """Get all existing route paths and methods from the FastAPI app.

    Returns:
        Dict[str, List[str]]: Dictionary mapping paths to list of HTTP methods
    """
    existing_paths: Dict[str, Any] = {}
    for route in fastapi_app.routes:
        if isinstance(route, APIRoute):
            path = route.path
            methods = list(route.methods) if route.methods else []
            if path in existing_paths:
                existing_paths[path].extend(methods)
            else:
                existing_paths[path] = methods
    return existing_paths


def find_conflicting_routes(fastapi_app: FastAPI, router: APIRouter) -> List[Dict[str, Any]]:
    """Find conflicting routes in the FastAPI app.

    Args:
        fastapi_app: The FastAPI app with all existing routes
        router: The APIRouter to add

    Returns:
        List[Dict[str, Any]]: List of conflicting routes
    """
    existing_paths = get_existing_route_paths(fastapi_app)

    conflicts = []

    for route in router.routes:
        if isinstance(route, APIRoute):
            full_path = route.path
            route_methods = list(route.methods) if route.methods else []

            if full_path in existing_paths:
                conflicting_methods: Set[str] = set(route_methods) & set(existing_paths[full_path])
                if conflicting_methods:
                    conflicts.append({"path": full_path, "methods": list(conflicting_methods), "route": route})
    return conflicts


def load_yaml_config(config_file_path: str) -> EgptOSConfig:
    """Load a YAML config file and return the configuration as an EgptOSConfig instance."""
    from pathlib import Path

    import yaml

    # Validate that the path points to a YAML file
    path = Path(config_file_path)
    if path.suffix.lower() not in [".yaml", ".yml"]:
        raise ValueError(f"Config file must have a .yaml or .yml extension, got: {config_file_path}")

    # Load the YAML file
    with open(config_file_path, "r") as f:
        return EgptOSConfig.model_validate(yaml.safe_load(f))


def collect_mcp_tools_from_team(team: Team, mcp_tools: List[Any]) -> None:
    """Recursively collect MCP tools from a team and its members."""
    # Check the team tools
    if team.tools and isinstance(team.tools, list):
        for tool in team.tools:
            # Alternate method of using isinstance(tool, (MCPTools, MultiMCPTools)) to avoid imports
            if hasattr(type(tool), "__mro__") and any(
                c.__name__ in ["MCPTools", "MultiMCPTools"] for c in type(tool).__mro__
            ):
                if tool not in mcp_tools:
                    mcp_tools.append(tool)

    # Recursively check team members
    if team.members and isinstance(team.members, list):
        for member in team.members:
            if isinstance(member, Agent):
                if member.tools and isinstance(member.tools, list):
                    for tool in member.tools:
                        # Alternate method of using isinstance(tool, (MCPTools, MultiMCPTools)) to avoid imports
                        if hasattr(type(tool), "__mro__") and any(
                            c.__name__ in ["MCPTools", "MultiMCPTools"] for c in type(tool).__mro__
                        ):
                            if tool not in mcp_tools:
                                mcp_tools.append(tool)

            elif isinstance(member, Team):
                # Recursively check nested team
                collect_mcp_tools_from_team(member, mcp_tools)


def collect_mcp_tools_from_registry(registry: Optional[Registry], mcp_tools: List[Any]) -> None:
    """Collect MCP tools declared directly on the registry.

    Registry tools are not attached to any agent, team or workflow, so the
    other collectors never see them. They still must be connected in the
    EgptOS lifespan: components created from registry tools (e.g. via
    StudioTool) serialize a toolkit's functions at persist time, and an
    unconnected MCP toolkit has none -- its tools would be silently dropped.
    """
    if registry is None or not registry.tools:
        return
    for tool in registry.tools:
        # Alternate method of using isinstance(tool, (MCPTools, MultiMCPTools)) to avoid imports
        if hasattr(type(tool), "__mro__") and any(
            c.__name__ in ["MCPTools", "MultiMCPTools"] for c in type(tool).__mro__
        ):
            if tool not in mcp_tools:
                mcp_tools.append(tool)


def collect_mcp_tools_from_workflow(workflow: Workflow, mcp_tools: List[Any]) -> None:
    """Recursively collect MCP tools from a workflow and its steps."""
    from egpt.workflow.steps import Steps

    # Recursively check workflow steps
    if workflow.steps:
        if isinstance(workflow.steps, list):
            # Handle list of steps
            for step in workflow.steps:
                collect_mcp_tools_from_workflow_step(step, mcp_tools)

        elif isinstance(workflow.steps, Steps):
            # Handle Steps container
            if steps := workflow.steps.steps:
                for step in steps:
                    collect_mcp_tools_from_workflow_step(step, mcp_tools)

        elif callable(workflow.steps):
            pass


def collect_mcp_tools_from_workflow_step(step: Any, mcp_tools: List[Any]) -> None:
    """Collect MCP tools from a single workflow step."""
    from egpt.workflow.condition import Condition
    from egpt.workflow.loop import Loop
    from egpt.workflow.parallel import Parallel
    from egpt.workflow.router import Router
    from egpt.workflow.step import Step
    from egpt.workflow.steps import Steps

    if isinstance(step, Step):
        # Check step's agent
        if step.agent:
            if step.agent.tools and isinstance(step.agent.tools, list):
                for tool in step.agent.tools:
                    # Alternate method of using isinstance(tool, (MCPTools, MultiMCPTools)) to avoid imports
                    if hasattr(type(tool), "__mro__") and any(
                        c.__name__ in ["MCPTools", "MultiMCPTools"] for c in type(tool).__mro__
                    ):
                        if tool not in mcp_tools:
                            mcp_tools.append(tool)
        # Check step's team
        if step.team:
            collect_mcp_tools_from_team(step.team, mcp_tools)

    elif isinstance(step, Steps):
        if steps := step.steps:
            for step in steps:
                collect_mcp_tools_from_workflow_step(step, mcp_tools)

    elif isinstance(step, (Parallel, Loop, Condition, Router)):
        # These contain other steps - recursively check them
        if hasattr(step, "steps") and step.steps:
            for sub_step in step.steps:
                collect_mcp_tools_from_workflow_step(sub_step, mcp_tools)

    elif isinstance(step, Agent):
        # Direct agent in workflow steps
        if step.tools and isinstance(step.tools, list):
            for tool in step.tools:
                # Alternate method of using isinstance(tool, (MCPTools, MultiMCPTools)) to avoid imports
                if hasattr(type(tool), "__mro__") and any(
                    c.__name__ in ["MCPTools", "MultiMCPTools"] for c in type(tool).__mro__
                ):
                    if tool not in mcp_tools:
                        mcp_tools.append(tool)

    elif isinstance(step, Team):
        # Direct team in workflow steps
        collect_mcp_tools_from_team(step, mcp_tools)

    elif isinstance(step, Workflow):
        # Nested workflow
        collect_mcp_tools_from_workflow(step, mcp_tools)


def _collect_fallback_models(owner: Any, registry: Registry) -> None:
    """Add an agent's or team's fallback models to the registry.

    Fallback models may be provided directly via ``fallback_models`` (before
    initialization) or normalised into a ``FallbackConfig`` with per-trigger
    lists (after initialization). Both shapes are handled.
    """
    fallback_models = getattr(owner, "fallback_models", None)
    if isinstance(fallback_models, list):
        for fallback_model in fallback_models:
            # May contain plain string ids; Registry.add_model ignores non-Model values
            registry.add_model(fallback_model)

    fallback_config = getattr(owner, "fallback_config", None)
    if fallback_config is not None:
        for attr in ("on_error", "on_rate_limit", "on_context_overflow"):
            models = getattr(fallback_config, attr, None)
            if isinstance(models, list):
                for fallback_model in models:
                    registry.add_model(fallback_model)


def _collect_components_from_knowledge(knowledge: Any, registry: Registry) -> None:
    """Add the vector db and contents db backing a knowledge instance to the registry.

    ``knowledge`` may be a Knowledge instance, a custom KnowledgeProtocol
    implementation, or a callable factory. Attribute access is guarded so any
    of these shapes is handled safely.
    """
    if knowledge is None:
        return
    registry.add_vector_db(getattr(knowledge, "vector_db", None))
    registry.add_db(getattr(knowledge, "contents_db", None))


def collect_components_from_agent(agent: Any, registry: Registry, visited: Set[int]) -> None:
    """Add the models, tools, db and vector db referenced by an agent to the registry.

    ``visited`` tracks already-walked agents/teams/workflows (by object id) to
    avoid redundant work and infinite recursion on cyclic composition graphs.
    """
    if id(agent) in visited:
        return
    visited.add(id(agent))

    registry.add_model(getattr(agent, "model", None))
    registry.add_model(getattr(agent, "reasoning_model", None))
    registry.add_model(getattr(agent, "parser_model", None))
    registry.add_model(getattr(agent, "output_model", None))
    _collect_fallback_models(agent, registry)

    tools = getattr(agent, "tools", None)
    if isinstance(tools, list):
        for tool in tools:
            registry.add_tool(tool)

    registry.add_db(getattr(agent, "db", None))
    _collect_components_from_knowledge(getattr(agent, "knowledge", None), registry)


def collect_components_from_team(team: Any, registry: Registry, visited: Set[int]) -> None:
    """Add a team's components to the registry, recursing into all of its members."""
    if id(team) in visited:
        return
    visited.add(id(team))

    registry.add_model(getattr(team, "model", None))
    registry.add_model(getattr(team, "reasoning_model", None))
    registry.add_model(getattr(team, "parser_model", None))
    registry.add_model(getattr(team, "output_model", None))
    _collect_fallback_models(team, registry)

    tools = getattr(team, "tools", None)
    if isinstance(tools, list):
        for tool in tools:
            registry.add_tool(tool)

    registry.add_db(getattr(team, "db", None))
    _collect_components_from_knowledge(getattr(team, "knowledge", None), registry)

    members = getattr(team, "members", None)
    if isinstance(members, list):
        for member in members:
            if isinstance(member, Agent):
                collect_components_from_agent(member, registry, visited)
            elif isinstance(member, Team):
                collect_components_from_team(member, registry, visited)


def collect_components_from_workflow(workflow: Any, registry: Registry, visited: Set[int]) -> None:
    """Add a workflow's components (coordinator agent and step tree) to the registry."""
    if id(workflow) in visited:
        return
    visited.add(id(workflow))

    registry.add_db(getattr(workflow, "db", None))

    # Agentic workflow coordinator (WorkflowAgent is an Agent subclass)
    workflow_agent = getattr(workflow, "agent", None)
    if workflow_agent is not None:
        collect_components_from_agent(workflow_agent, registry, visited)

    _collect_components_from_steps(getattr(workflow, "steps", None), registry, visited)


def _collect_components_from_steps(steps: Any, registry: Registry, visited: Set[int]) -> None:
    """Add components from a workflow's ``steps`` value (list, container or callable)."""
    if steps is None:
        return
    if isinstance(steps, list):
        for step in steps:
            _collect_components_from_step(step, registry, visited)
    else:
        _collect_components_from_step(steps, registry, visited)


def _collect_components_from_step(step: Any, registry: Registry, visited: Set[int]) -> None:
    """Add components from a single workflow step of any type.

    Handles primitive steps (Step pointing at an agent/team/nested workflow),
    agents/teams/workflows used directly as steps, and the composite container
    types. Composite types are walked across ``steps``, ``else_steps`` (Condition)
    and ``choices`` (Router) so no branch is missed. Plain callables are skipped.
    """
    from egpt.workflow.condition import Condition
    from egpt.workflow.loop import Loop
    from egpt.workflow.parallel import Parallel
    from egpt.workflow.router import Router
    from egpt.workflow.step import Step
    from egpt.workflow.steps import Steps

    if step is None:
        return

    if isinstance(step, Step):
        if step.agent is not None:
            collect_components_from_agent(step.agent, registry, visited)
        if step.team is not None:
            collect_components_from_team(step.team, registry, visited)
        nested_workflow = getattr(step, "workflow", None)
        if nested_workflow is not None:
            collect_components_from_workflow(nested_workflow, registry, visited)

    elif isinstance(step, Agent):
        collect_components_from_agent(step, registry, visited)

    elif isinstance(step, Team):
        collect_components_from_team(step, registry, visited)

    elif isinstance(step, Workflow):
        collect_components_from_workflow(step, registry, visited)

    elif isinstance(step, (Steps, Loop, Parallel, Condition, Router)):
        # Walk every sub-step container: `steps` (all), `else_steps` (Condition)
        # and `choices` (Router, before it is prepared into `steps`).
        for attr in ("steps", "else_steps", "choices"):
            sub_steps = getattr(step, attr, None)
            if isinstance(sub_steps, list):
                for sub_step in sub_steps:
                    _collect_components_from_step(sub_step, registry, visited)

    # else: plain callable executor or unknown step type -> nothing to collect


def collect_components_from_os(
    agents: Optional[List[Any]],
    teams: Optional[List[Any]],
    workflows: Optional[List[Any]],
    registry: Registry,
) -> None:
    """Walk all agents, teams and workflows of an EgptOS and add their components to ``registry``.

    The registry owns deduplication (see ``Registry.add_*``), so components are
    added directly during the walk. Each top-level node is walked inside its own
    guard, so a single malformed agent/team/workflow degrades to "not collected"
    rather than failing the whole walk. Remote and factory components are skipped
    because they expose no locally-walkable instances.
    """
    visited: Set[int] = set()

    for agent in agents or []:
        if not isinstance(agent, Agent):
            continue
        try:
            collect_components_from_agent(agent, registry, visited)
        except Exception as e:
            log_debug(f"Registry auto-population: skipped agent due to error: {e}")

    for team in teams or []:
        if not isinstance(team, Team):
            continue
        try:
            collect_components_from_team(team, registry, visited)
        except Exception as e:
            log_debug(f"Registry auto-population: skipped team due to error: {e}")

    for workflow in workflows or []:
        if not isinstance(workflow, Workflow):
            continue
        try:
            collect_components_from_workflow(workflow, registry, visited)
        except Exception as e:
            log_debug(f"Registry auto-population: skipped workflow due to error: {e}")


def _get_python_type_from_json_schema(field_schema: Dict[str, Any], field_name: str = "NestedModel") -> Type:
    """Map JSON schema type to Python type with recursive handling.

    Args:
        field_schema: JSON schema dictionary for a single field
        field_name: Name of the field (used for nested model naming)

    Returns:
        Python type corresponding to the JSON schema type
    """
    if not isinstance(field_schema, dict):
        return Any

    json_type = field_schema.get("type")

    # Handle basic types
    if json_type == "string":
        return str
    elif json_type == "integer":
        return int
    elif json_type == "number":
        return float
    elif json_type == "boolean":
        return bool
    elif json_type == "null":
        return type(None)
    elif json_type == "array":
        # Handle arrays with item type specification
        items_schema = field_schema.get("items")
        if items_schema and isinstance(items_schema, dict):
            item_type = _get_python_type_from_json_schema(items_schema, f"{field_name}Item")
            return List[item_type]  # type: ignore
        else:
            # No item type specified - use generic list
            return List[Any]
    elif json_type == "object":
        # Recursively create nested Pydantic model
        nested_properties = field_schema.get("properties", {})
        nested_required = field_schema.get("required", [])
        nested_title = field_schema.get("title", field_name)

        # Build field definitions for nested model
        nested_fields = {}
        for nested_field_name, nested_field_schema in nested_properties.items():
            nested_field_type = _get_python_type_from_json_schema(nested_field_schema, nested_field_name)

            if nested_field_name in nested_required:
                nested_fields[nested_field_name] = (nested_field_type, ...)
            else:
                nested_fields[nested_field_name] = (Optional[nested_field_type], None)  # type: ignore[assignment]

        # Create nested model if it has fields
        if nested_fields:
            return create_model(nested_title, **nested_fields)  # type: ignore
        else:
            # Empty object schema - use generic dict
            return Dict[str, Any]
    else:
        # Unknown or unspecified type - fallback to Any
        if json_type:
            logger.warning(f"Unknown JSON schema type '{json_type}' for field '{field_name}', using Any")
        return Any  # type: ignore


def json_schema_to_pydantic_model(schema: Dict[str, Any]) -> Type[BaseModel]:
    """Convert a JSON schema dictionary to a Pydantic BaseModel class.

    This function dynamically creates a Pydantic model from a JSON schema specification,
    handling nested objects, arrays, and optional fields.

    Args:
        schema: JSON schema dictionary with 'properties', 'required', 'type', etc.

    Returns:
        Dynamically created Pydantic BaseModel class
    """
    import copy

    # Deep copy to avoid modifying the original schema
    schema = copy.deepcopy(schema)

    # Extract schema components
    model_name = schema.get("title", "DynamicModel")
    properties = schema.get("properties", {})
    required_fields = schema.get("required", [])

    # Validate schema has properties
    if not properties:
        logger.warning(f"JSON schema '{model_name}' has no properties, creating empty model")

    # Build field definitions for create_model
    field_definitions = {}
    for field_name, field_schema in properties.items():
        try:
            field_type = _get_python_type_from_json_schema(field_schema, field_name)

            if field_name in required_fields:
                # Required field: (type, ...)
                field_definitions[field_name] = (field_type, ...)
            else:
                # Optional field: (Optional[type], None)
                field_definitions[field_name] = (Optional[field_type], None)  # type: ignore[assignment]
        except Exception as e:
            log_warning(f"Failed to process field '{field_name}' in schema '{model_name}': {str(e)}")
            # Skip problematic fields rather than failing entirely
            continue

    # Create and return the dynamic model
    try:
        return create_model(model_name, **field_definitions)  # type: ignore
    except Exception:
        logger.exception(f"Failed to create dynamic model '{model_name}'")
        # Return a minimal model as fallback
        return create_model(model_name)


def setup_tracing_for_os(db: Union[BaseDb, AsyncBaseDb, RemoteDb]) -> None:
    """Set up OpenTelemetry tracing for this agent/team/workflow."""
    try:
        from egpt.tracing import setup_tracing

        setup_tracing(db=db)
    except ImportError as e:
        log_warning(
            f"tracing=True but OpenTelemetry packages not installed. : {e}"
            f"Install with: pip install opentelemetry-api opentelemetry-sdk openinference-instrumentation-agno: {e}"
        )

    except Exception as e:
        log_warning(f"Failed to enable tracing: {str(e)}")


def format_duration_ms(duration_ms: Optional[int]) -> str:
    """Format a duration in milliseconds to a human-readable string.

    Args:
        duration_ms: Duration in milliseconds

    Returns:
        Formatted string like "150ms" or "1.50s"
    """
    if duration_ms is None or duration_ms < 1000:
        return f"{duration_ms or 0}ms"
    return f"{duration_ms / 1000:.2f}s"


def timestamp_to_datetime(datetime_str: str, param_name: str = "datetime") -> "datetime":
    """Parse an ISO 8601 datetime string and convert to UTC.

    Args:
        datetime_str: ISO 8601 formatted datetime string (e.g., '2025-11-19T10:00:00Z' or '2025-11-19T15:30:00+05:30')
        param_name: Name of the parameter for error messages

    Returns:
        datetime object in UTC timezone

    Raises:
        HTTPException: If the datetime string is invalid
    """
    from egpt.utils.dttm import parse_datetime_utc

    try:
        return parse_datetime_utc(datetime_str)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {param_name} format. Use ISO 8601 format (e.g., '2025-11-19T10:00:00Z' or '2025-11-19T10:00:00+05:30'): {e}",
        )


def format_team_tools(team_tools: List[Union[Function, dict]]):
    formatted_tools: List[Dict] = []
    if team_tools is not None:
        for tool in team_tools:
            if isinstance(tool, dict):
                formatted_tools.append(tool)
            elif isinstance(tool, Function):
                formatted_tools.append(tool.to_dict())
    return formatted_tools


def format_tools(agent_tools: List[Union[Dict[str, Any], Toolkit, Function, Callable]]):
    formatted_tools: List[Dict] = []
    if agent_tools is not None:
        for tool in agent_tools:
            if isinstance(tool, dict):
                formatted_tools.append(tool)
            elif isinstance(tool, Toolkit):
                for _, f in tool.functions.items():
                    formatted_tools.append(f.to_dict())
            elif isinstance(tool, Function):
                formatted_tools.append(tool.to_dict())
            elif callable(tool):
                func = Function.from_callable(tool)
                formatted_tools.append(func.to_dict())
            else:
                logger.warning(f"Unknown tool type: {type(tool)}")
    return formatted_tools


def stringify_input_content(input_content: Union[str, Dict[str, Any], List[Any], BaseModel]) -> str:
    """Convert any given input_content into its string representation.

    This handles both serialized (dict) and live (object) input_content formats.
    """
    import json

    if isinstance(input_content, str):
        return input_content
    elif isinstance(input_content, Message):
        return json.dumps(input_content.to_dict())
    elif isinstance(input_content, dict):
        return json.dumps(input_content, indent=2, default=str)
    elif isinstance(input_content, list):
        if input_content:
            # Handle live Message objects
            if isinstance(input_content[0], Message):
                return json.dumps([m.to_dict() for m in input_content])
            # Handle serialized Message dicts
            elif isinstance(input_content[0], dict) and input_content[0].get("role") == "user":
                return input_content[0].get("content", str(input_content))
        return str(input_content)
    else:
        return str(input_content)


# ---------------------------------------------------------------------------
# High-level resolvers with error handling for routers
# ---------------------------------------------------------------------------


def _tenant_for_hydration(
    request: Optional[Request],
    tenant_id: Optional[str],
    *,
    in_memory: bool,
    db: Optional[Union[BaseDb, AsyncBaseDb]],
    tenancy_enabled_override: Optional[bool] = None,
) -> Optional[str]:
    """The tenant a resolve_* call hydrates as, failing closed on the DB arm (EGPT-2.5).

    An explicit tenant_id wins; otherwise the tenant comes off the request the way the
    components router reads it (egpt.os.tenancy). When the deployment has opted into tenancy
    and the caller resolved to no tenant, a lookup that would fall through to the DB is
    refused with the same 403 the components routes give -- `tenant_id=None` at the DB layer
    means *no predicate*, so letting it through would hydrate across every tenant. The
    in-memory list is exempt: code-defined agents are deployment-owned, not tenant rows.

    `tenancy_enabled_override` lets a caller with no `Request` (the WebSocket surface, which
    has no `request.state` for `tenancy_enabled` to read) supply the same yes/no it would
    otherwise derive from one -- see `tenant_for_ws_hydration`. Left unset, this falls back to
    `request is not None and tenancy_enabled(request)`, unchanged from before this parameter
    existed.
    """
    tenant = tenant_id if tenant_id is not None else current_tenant_id(request)
    enabled = (
        tenancy_enabled_override
        if tenancy_enabled_override is not None
        else (request is not None and tenancy_enabled(request))
    )
    if tenant is None and not in_memory and db is not None and enabled:
        raise HTTPException(status_code=403, detail=TENANCY_REQUIRED_DETAIL)
    return tenant


def tenant_for_direct_read(
    request: Optional[Request],
    *,
    db: Optional[Union[BaseDb, AsyncBaseDb]],
    components: Optional[Sequence[Any]] = None,
    component_id: Optional[str] = None,
) -> Optional[str]:
    """Tenant for a router/MCP path that reaches the component getters without resolve_*.

    Same fail-closed contract as the resolvers (EGPT-2.5): the auth middleware deliberately
    yields no tenant for an otherwise-valid principal whose token omits the configured tenant
    claim, and `tenant_id=None` at the DB layer means *no predicate* -- so when the deployment
    has opted into tenancy, a read that would fall through to the DB unscoped is refused with
    the components-router 403 instead of returning every tenant's rows. A lookup whose id is
    served from the in-memory `components` list is exempt (code-defined components are
    deployment-owned, not tenant rows); listings pass no component_id and always take the
    fail-closed arm.
    """
    in_memory = (
        component_id is not None
        and components is not None
        and any(getattr(c, "id", None) == component_id for c in components)
    )
    return _tenant_for_hydration(request, None, in_memory=in_memory, db=db)


def tenant_for_ws_hydration(
    tenant_id: Optional[str],
    tenancy_enabled_override: bool,
    *,
    in_memory: bool,
    db: Optional[Union[BaseDb, AsyncBaseDb]],
) -> Optional[str]:
    """The tenant a WebSocket/factory hydration call scopes to (aicoe-tracker#174).

    This is the WebSocket counterpart of `_tenant_for_hydration`: same fail-closed contract,
    reached the way a websocket connection actually carries a tenant. A websocket has no
    `Request` for `current_tenant_id` to read `request.state.tenant_id` off of, so
    `get_websocket_router` populates `websocket_user_context["tenant_id"]` at authenticate-time
    the same way `AuthMiddleware` populates `request.state.tenant_id`: the configured tenant
    claim via the same `extract_tenant_claim`, with no fallback to `user_id`. The dispatcher then
    hands every hydrating handler a typed `WebSocketAuthContext` (built once, in
    `build_ws_auth`), and callers pass its `tenant_id`/`tenancy_enabled` fields here -- never a
    raw `.get()` off the untyped context dict, so a typo in a field name fails under mypy instead
    of silently resolving to `None`/`False` and failing the gate *open*.

    An empty or whitespace-only `tenant_id` is normalised to `None` before the gate: `""` is not
    a tenant, and `"" is not None` would otherwise let it through.

    Deliberately does **not** call `resolve_tenant_id`. That function's `user_id` fallback
    exists for `ModelPolicy` selection (`RequestContext.tenant_id`, `RunContext.tenant_id`) --
    a verified user acting as their own tenant is a reasonable default for policy in a
    deployment with no tenancy configured. It is not a reasonable default for scoping a
    component *read*: `tenant_id=None` at the DB layer means "no predicate", so a lookup that
    fell through to the DB unscoped would return another tenant's row if the ids happened to
    collide, and a write would land in a tenant namespace no operator configured. Component
    hydration must fail closed instead, exactly as the HTTP resolver does -- see
    aicoe-tracker#174 and `docs/adr/0004-component-tenant-isolation.md`.

    `tenancy_enabled_override` carries the deployment's opt-in (`tenant_id_claim` configured),
    which the dispatcher derives from `egpt.os.tenancy.tenancy_enabled_for_app(websocket.app)` --
    the same function the HTTP path's `tenancy_enabled(request)` wraps, so both transports agree
    on whether the gate is live. (Named like `_tenant_for_hydration`'s parameter rather than
    `tenancy_enabled`, which is that imported function.) The in-memory list is exempt, same as
    `_tenant_for_hydration`: a workflow served from the in-memory `workflows`/`agents`/`teams`
    list (including a matched factory, which never reaches the DB through this call) is
    code-defined, not a tenant row.
    """
    normalised_tenant = tenant_id.strip() if isinstance(tenant_id, str) and tenant_id.strip() else None
    return _tenant_for_hydration(
        None,
        normalised_tenant,
        in_memory=in_memory,
        db=db,
        tenancy_enabled_override=tenancy_enabled_override,
    )


async def resolve_agent(
    agent_id: str,
    agents: Optional[Sequence[Union[Agent, RemoteAgent, AgentProtocol, AgentFactory]]],
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    registry: Optional[Registry] = None,
    version: Optional[int] = None,
    request: Optional[Request] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    factory_input: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Union[Agent, RemoteAgent, AgentProtocol]:
    """Resolve an agent by ID with proper error handling for both factory and non-factory paths.

    For factory agents: builds RequestContext, invokes factory, handles factory-specific errors.
    For non-factory agents: resolves via deep_copy or DB lookup.

    tenant_id overrides the tenant otherwise read off the request (for callers without one,
    e.g. websocket paths); the DB arm is scoped to it and fails closed when tenancy is enabled
    but the caller has no tenant.

    Raises HTTPException on all error paths.
    """
    in_memory = agents is not None and any(getattr(a, "id", None) == agent_id for a in agents)
    tenant = _tenant_for_hydration(request, tenant_id, in_memory=in_memory, db=db)
    is_factory = agents and any(isinstance(a, AgentFactory) and a.id == agent_id for a in agents)
    if is_factory:
        if request is None:
            raise HTTPException(status_code=400, detail="Request context is required for factory agents")
        ctx = build_request_context(request, user_id=user_id, session_id=session_id, factory_input=factory_input)
        try:
            agent = await get_agent_by_id_async(
                agent_id, agents, db, registry, version=version, create_fresh=True, ctx=ctx, tenant_id=tenant
            )
        except FactoryValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FactoryPermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except FactoryError as e:
            logger.error(f"Factory error for agent '{agent_id}': {e}")
            raise HTTPException(status_code=500, detail="Agent factory error")
        except Exception as e:
            logger.error(f"Error in agent factory '{agent_id}': {e}")
            raise HTTPException(status_code=500, detail=f"Error in agent factory: {e}")
    else:
        try:
            agent = get_agent_by_id(
                agent_id, agents, db, registry, version=version, create_fresh=True, tenant_id=tenant
            )
        # 403, not 404 or 500: the component exists and hydrated fine, the caller's tenant is
        # just not permitted the capabilities it names. Sits beside the FactoryPermissionError
        # arm above, which is the same kind of outcome on the factory path.
        except PolicyViolationError as e:
            logger.warning(f"Capability policy blocked agent '{agent_id}': {e}")
            raise HTTPException(status_code=403, detail=str(e))
        # 409: the component exists but has no published config to run. Same-tenant callers may
        # know it exists; publishing is the fix, so a conflict beats a lie of 404.
        except NoPublishedVersionError as e:
            logger.warning(f"Published-only hydration refused agent '{agent_id}': {e}")
            raise HTTPException(status_code=409, detail=str(e))
        # 500 with the actual cause: the row exists but could not be built. Before EGPT-2.5
        # this collapsed to None and rendered as a misleading 404.
        except HydrationError as e:
            logger.error(f"Hydration failed for agent '{agent_id}': {e}")
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"Error resolving agent '{agent_id}': {e}")
            raise HTTPException(status_code=500, detail=f"Error resolving agent: {e}")

    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


async def resolve_team(
    team_id: str,
    teams: Optional[Sequence[Union[Team, RemoteTeam, TeamFactory]]],
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    registry: Optional[Registry] = None,
    version: Optional[int] = None,
    request: Optional[Request] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    factory_input: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Union[Team, RemoteTeam]:
    """Resolve a team by ID with proper error handling for both factory and non-factory paths.

    See resolve_agent for the tenant semantics (EGPT-2.5).
    """
    in_memory = teams is not None and any(getattr(t, "id", None) == team_id for t in teams)
    tenant = _tenant_for_hydration(request, tenant_id, in_memory=in_memory, db=db)
    is_factory = teams and any(isinstance(t, TeamFactory) and t.id == team_id for t in teams)
    if is_factory:
        if request is None:
            raise HTTPException(status_code=400, detail="Request context is required for factory teams")
        ctx = build_request_context(request, user_id=user_id, session_id=session_id, factory_input=factory_input)
        try:
            team = await get_team_by_id_async(
                team_id, teams, db=db, version=version, registry=registry, create_fresh=True, ctx=ctx, tenant_id=tenant
            )
        except FactoryValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FactoryPermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except FactoryError as e:
            logger.error(f"Factory error for team '{team_id}': {e}")
            raise HTTPException(status_code=500, detail="Team factory error")
        except Exception as e:
            logger.error(f"Error in team factory '{team_id}': {e}")
            raise HTTPException(status_code=500, detail=f"Error in team factory: {e}")
    else:
        try:
            team = get_team_by_id(
                team_id, teams, db=db, version=version, registry=registry, create_fresh=True, tenant_id=tenant
            )
        except PolicyViolationError as e:
            logger.warning(f"Capability policy blocked team '{team_id}': {e}")
            raise HTTPException(status_code=403, detail=str(e))
        except NoPublishedVersionError as e:
            logger.warning(f"Published-only hydration refused team '{team_id}': {e}")
            raise HTTPException(status_code=409, detail=str(e))
        except HydrationError as e:
            logger.error(f"Hydration failed for team '{team_id}': {e}")
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"Error resolving team '{team_id}': {e}")
            raise HTTPException(status_code=500, detail=f"Error resolving team: {e}")

    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


async def resolve_workflow(
    workflow_id: str,
    workflows: Optional[Sequence[Union[Workflow, RemoteWorkflow, WorkflowFactory]]],
    db: Optional[Union[BaseDb, AsyncBaseDb]] = None,
    registry: Optional[Registry] = None,
    version: Optional[int] = None,
    request: Optional[Request] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    factory_input: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Union[Workflow, RemoteWorkflow]:
    """Resolve a workflow by ID with proper error handling for both factory and non-factory paths.

    See resolve_agent for the tenant semantics (EGPT-2.5).
    """
    in_memory = workflows is not None and any(getattr(w, "id", None) == workflow_id for w in workflows)
    tenant = _tenant_for_hydration(request, tenant_id, in_memory=in_memory, db=db)
    is_factory = workflows and any(isinstance(w, WorkflowFactory) and w.id == workflow_id for w in workflows)
    if is_factory:
        if request is None:
            raise HTTPException(status_code=400, detail="Request context is required for factory workflows")
        ctx = build_request_context(request, user_id=user_id, session_id=session_id, factory_input=factory_input)
        try:
            workflow = await get_workflow_by_id_async(
                workflow_id,
                workflows,
                db=db,
                version=version,
                registry=registry,
                create_fresh=True,
                ctx=ctx,
                tenant_id=tenant,
            )
        except FactoryValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FactoryPermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except FactoryError as e:
            logger.error(f"Factory error for workflow '{workflow_id}': {e}")
            raise HTTPException(status_code=500, detail="Workflow factory error")
        except Exception as e:
            logger.error(f"Error in workflow factory '{workflow_id}': {e}")
            raise HTTPException(status_code=500, detail=f"Error in workflow factory: {e}")
    else:
        try:
            workflow = get_workflow_by_id(
                workflow_id, workflows, db=db, version=version, registry=registry, create_fresh=True, tenant_id=tenant
            )
        except PolicyViolationError as e:
            logger.warning(f"Capability policy blocked workflow '{workflow_id}': {e}")
            raise HTTPException(status_code=403, detail=str(e))
        except NoPublishedVersionError as e:
            logger.warning(f"Published-only hydration refused workflow '{workflow_id}': {e}")
            raise HTTPException(status_code=409, detail=str(e))
        except HydrationError as e:
            logger.error(f"Hydration failed for workflow '{workflow_id}': {e}")
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"Error resolving workflow '{workflow_id}': {e}")
            raise HTTPException(status_code=500, detail=f"Error resolving workflow: {e}")

    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow
