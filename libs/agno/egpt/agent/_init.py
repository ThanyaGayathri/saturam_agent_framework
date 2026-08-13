"""Initialization helpers for Agent."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Literal,
    Optional,
    Sequence,
    Union,
    cast,
)

from egpt.utils.env import get_branded_env, get_branded_env_bool

if TYPE_CHECKING:
    from egpt.agent.agent import Agent
    from egpt.models.base import Model

    # Type-only: importing egpt.providers at module scope would give egpt.agent an
    # import-time edge to the provider seam, which the EGPT-3.1 no-op guarantee rules out.
    from egpt.providers.model_provider import ModelProvider, ModelResolveRequest

from egpt.compression.manager import CompressionManager
from egpt.culture.manager import CultureManager
from egpt.db.base import AsyncBaseDb
from egpt.learn.machine import LearningMachine
from egpt.memory import MemoryManager
from egpt.models.utils import get_model
from egpt.session import SessionSummaryManager
from egpt.tools import Toolkit
from egpt.tools.function import Function
from egpt.utils.log import (
    log_debug,
    log_exception,
    log_info,
    log_warning,
    set_log_level_to_debug,
    set_log_level_to_info,
)
from egpt.utils.safe_formatter import SafeFormatter
from egpt.utils.string import generate_id_from_name


def set_id(agent: Agent) -> None:
    if agent.id is None:
        agent.id = generate_id_from_name(agent.name)


def set_debug(agent: Agent, debug_mode: Optional[bool] = None) -> None:
    # Get the debug level from the environment variable or the default debug level
    debug_level: Literal[1, 2] = (
        cast(Literal[1, 2], int(env)) if (env := get_branded_env("DEBUG_LEVEL")) in ("1", "2") else agent.debug_level
    )
    # If the default debug mode is set, or passed on run, or via environment variable, set the debug mode to True
    if agent.debug_mode or debug_mode or get_branded_env_bool("DEBUG"):
        set_log_level_to_debug(level=debug_level)
    else:
        set_log_level_to_info()


def set_telemetry(agent: Agent) -> None:
    """Override telemetry settings based on environment variables."""
    if get_branded_env("TELEMETRY") is not None:
        agent.telemetry = get_branded_env_bool("TELEMETRY", default=False)


def set_checkpoint(agent: Agent) -> None:
    """Resolve the agent's checkpoint setting.

    Constructor default is None so that OS-level inheritance can fill it. If still
    None at first run, fall back to "runs" (today's terminal-only behavior).

    "tools" is reserved for 3.0 (see ADR-006 in specs/agno/features/checkpointing/decisions.md)
    and raises NotImplementedError if requested.
    """
    if agent.checkpoint is None:
        agent.checkpoint = "runs"
    elif agent.checkpoint == "tools":
        raise NotImplementedError(
            'checkpoint="tools" is reserved for the 3.0 runs-table split and not available yet. Use "tool-batch" or "runs".'
        )
    elif agent.checkpoint not in ("runs", "tool-batch"):
        raise ValueError(
            f'Invalid checkpoint level: {agent.checkpoint!r}. Expected one of: "runs", "tool-batch", "tools".'
        )


def _model_provider_for(component: Any) -> Optional[ModelProvider]:
    """The provider that decides this component's model, or None when none is configured.

    Takes an ``Agent`` or a ``Team``: both carry an optional ``model_provider`` and both must
    reach the seam, because a Team that resolved its own default would send its prompts --
    which carry member output and user input -- to whatever the fallback provider is. Typed as
    ``Any`` rather than importing ``Team``: ``egpt.team`` imports ``egpt.agent``, so naming it
    here would close a cycle.

    None is the overwhelmingly common case, and it must stay cheap: ``egpt.providers`` is
    imported inside this function rather than at module scope so ``egpt.agent`` never grows an
    import-time edge to the provider seam, and the package ``__init__`` re-exports lazily, so
    the miss costs one module import and two attribute reads -- no provider class, no SDK.

    A per-component ``model_provider`` wins over the process-wide default: the narrower
    configuration is the more deliberate one. The test is ``is not None``, not truthiness: a
    provider whose class defines ``__len__`` (an empty rule table, say) is still a provider,
    and once EGPT-3.4 declares the field, assigning None must mean "opt out", not "fall back
    to the process-wide default".
    """
    from egpt.providers import get_default_model_provider

    own = getattr(component, "model_provider", None)
    if own is not None:
        return own  # type: ignore[no-any-return]
    return get_default_model_provider()


def _resolve_via_provider(component: Any, *, role: str = "model", run_context: Optional[Any] = None) -> Optional[Model]:
    """Ask the configured provider which model serves this component (EGPT-3.1).

    Returns None when no provider is configured, leaving the caller on the pre-EGPT-3.1
    default path, byte for byte.

    ``run_context`` carries the identity of the run this resolve is being performed for, when
    there is one (#173). It is what stops a residency-pinned tenant resolving under the default
    policy: without it, the request reaches the provider with ``tenant_id=None`` and a policy
    like ``acme -> in-VPC, default -> frontier`` picks the frontier tier for acme's own turn.

    A configured provider that returns None is a different outcome and is *not* silently
    treated as "no provider": the seam's whole purpose is that a Mode A deployment never
    reaches the frontier default, so a decline that fell through to
    ``get_model("openai-responses:gpt-5.4")`` would ship tenant prompts to api.openai.com with
    nothing in the log to say why. Raise instead, matching the ValueError the pre-existing
    path already used for an unresolvable model.
    """
    provider = _model_provider_for(component)
    if provider is None:
        # Record the miss so installing a provider later can warn that this component already
        # resolved and will not be revisited.
        from egpt.providers import note_resolved_without_provider

        note_resolved_without_provider(getattr(component, "id", None))
        return None

    from egpt.providers.model_provider import ModelResolveRequest

    # `requested` stays None: the caller's guard means no model was pinned, so there is no
    # user intent for the provider to honor. Per-run signals (token estimates, tool counts,
    # escalation state) belong to resolve_run_model below, which EGPT-3.5 wires from the run
    # path -- only *identity* is threaded here, because identity is what selects the policy and
    # therefore the endpoint (#173). The two resolves per run now agree on the tenant; they
    # still differ on the load-dependent signals, which is the point of re-entering per run.
    resolved = provider.resolve(
        ModelResolveRequest(
            agent_id=component.id,
            role=role,
            tenant_id=getattr(run_context, "tenant_id", None),
            user_id=getattr(run_context, "user_id", None),
            session_id=getattr(run_context, "session_id", None),
        )
    )
    if resolved is None:
        raise ValueError(
            f"Model provider {getattr(provider, 'name', type(provider).__name__)!r} returned no model. "
            "A provider must return a Model or raise; returning None would fall through to the "
            "default frontier model, which defeats the point of configuring a provider."
        )
    return resolved


def resolve_run_model(agent: Any, request: ModelResolveRequest) -> Optional[Model]:
    """Resolve the model for a single run (EGPT-3.1; wired by EGPT-3.5).

    Takes an ``Agent`` or a ``Team`` -- see ``_model_provider_for`` for why this is ``Any``.

    ``set_default_model`` resolves once per Agent *object*: ``initialize_agent`` is
    idempotent by the ``if agent.model is None`` guard, so once a model is assigned no later
    run re-enters the seam. That is correct for a long-lived ``Agent``, but it means
    per-request routing (per-tenant endpoints, escalation) cannot come from that path -- it
    needs either this hook called per run, or the ``AgentFactory`` path, which builds a fresh
    Agent per request and therefore re-resolves naturally.

    This is the former. EGPT-3.5 wires it from ``egpt/agent/_run.py`` and
    ``egpt/team/_run.py`` -- both, because a Team runs turns against a model too, and a Team
    that could not escalate would be stuck on the small model for precisely the requests most
    likely to need the frontier: a team run carries every member's output.

    Returns None when no provider is configured, leaving the caller on its current model.
    """
    provider = _model_provider_for(agent)
    if provider is None:
        return None
    return provider.resolve(request)


async def aresolve_run_model(agent: Any, request: ModelResolveRequest) -> Optional[Model]:
    """Async counterpart of ``resolve_run_model``.

    Required rather than decorative: ``resolve`` may read a per-tenant policy row (EGPT-3.2) or
    probe a gateway (EGPT-3.4), so calling the sync variant from an async run path blocks the
    event loop for the duration. ``aresolve_via`` is what selects a provider's own ``aresolve``
    when it has one, and offloads to a thread when it does not -- so this is also the only path
    on which an ``AsyncModelProvider`` implementation is actually reached.
    """
    from egpt.providers.model_provider import aresolve_via

    provider = _model_provider_for(agent)
    if provider is None:
        return None
    return await aresolve_via(provider, request)


def set_default_model(agent: Agent, run_context: Optional[Any] = None) -> None:
    """Give the agent a model if it has none.

    ``run_context`` is the run this initialisation is being performed for, when there is one.
    It exists to carry the tenant into the seam (EGPT-164/#173): without it this resolve runs
    under the *default* policy, so a tenant pinned to in-VPC inference by a residency policy
    would have an out-of-region backend constructed here and swapped out later by
    ``apply_run_model`` -- the client is built and authenticated before the correction lands.

    Optional because this is also reached outside any run: ``EgptOS._initialize_agents`` at
    startup and ``AgentFactory._post_resolve``. Those have no run to speak of, and passing
    ``None`` is the pre-#173 behaviour exactly.
    """
    # Use the default Model (OpenAIResponses) if no model is provided
    if agent.model is None:
        # The provider seam (EGPT-3.1). A configured provider decides; with none configured
        # this returns None immediately and the original default path below runs unchanged.
        resolved = _resolve_via_provider(agent, run_context=run_context)
        if resolved is not None:
            agent.model = resolved
            return

        try:
            model = get_model("openai-responses:gpt-5.4")
        # Provider modules raise a bare ImportError, not ModuleNotFoundError, when their SDK is
        # missing. ModuleNotFoundError is a subclass of ImportError, so catching the narrower type
        # here never fired and this handler was unreachable.
        except ImportError as e:
            log_exception(e)
            raise ImportError(
                "EGPT agents use `openai` as the default model provider. Please provide a `model` or install `openai`."
            ) from e

        # get_model is typed Optional[Model]; it returns None only for a None argument, which
        # cannot happen here. Guard anyway so a widened contract fails loudly rather than leaving
        # agent.model unset for downstream code to trip over, matching culture/ and memory/.
        if model is None:
            raise ValueError("Could not resolve the default model.")

        log_info("Setting default model to OpenAI Responses")
        agent.model = model


def set_culture_manager(agent: Agent) -> None:
    if agent.db is None:
        log_warning("Database not provided. Cultural knowledge will not be stored.")

    if agent.culture_manager is None:
        agent.culture_manager = CultureManager(model=agent.model, db=agent.db)
    else:
        if agent.culture_manager.model is None:
            agent.culture_manager.model = agent.model
        if agent.culture_manager.db is None:
            agent.culture_manager.db = agent.db

    if agent.add_culture_to_context is None:
        agent.add_culture_to_context = (
            agent.enable_agentic_culture or agent.update_cultural_knowledge or agent.culture_manager is not None
        )


def set_memory_manager(agent: Agent) -> None:
    if agent.db is None:
        log_warning("Database not provided. Memories will not be stored.")

    if agent.memory_manager is None:
        agent.memory_manager = MemoryManager(model=agent.model, db=agent.db)
    else:
        if agent.memory_manager.model is None:
            agent.memory_manager.model = agent.model
        if agent.memory_manager.db is None:
            agent.memory_manager.db = agent.db

    if agent.add_memories_to_context is None:
        agent.add_memories_to_context = (
            agent.update_memory_on_run or agent.enable_agentic_memory or agent.memory_manager is not None
        )


def set_learning_machine(agent: Agent) -> None:
    """Initialize LearningMachine with agent's db and model.

    Sets the internal _learning field without modifying the public learning field.

    Handles:
    - learning=True: Create default LearningMachine
    - learning=False/None: Disabled
    - learning=LearningMachine(...): Use provided, inject db/model/knowledge
    """
    agent._learning_init_attempted = True

    # Handle learning=False or learning=None
    if agent.learning is None or agent.learning is False:
        agent._learning = None
        return

    # Check db requirement
    if agent.db is None:
        log_warning("Database not provided. LearningMachine not initialized.")
        agent._learning = None
        return

    # Handle learning=True: create default LearningMachine
    # Enables user_profile (structured fields) and user_memory (unstructured observations)
    if agent.learning is True:
        agent._learning = LearningMachine(db=agent.db, model=agent.model, user_profile=True, user_memory=True)
        return

    # Handle learning=LearningMachine(...): inject dependencies
    if isinstance(agent.learning, LearningMachine):
        if agent.learning.db is None:
            agent.learning.db = agent.db
        if agent.learning.model is None:
            agent.learning.model = agent.model
        agent._learning = agent.learning

        # PROPOSE/HITL modes need chat history for multi-turn confirmation
        if agent._learning.requires_history and not agent.add_history_to_context:
            agent.add_history_to_context = True


def set_session_summary_manager(agent: Agent) -> None:
    if agent.enable_session_summaries and agent.session_summary_manager is None:
        agent.session_summary_manager = SessionSummaryManager(model=agent.model)

    if agent.session_summary_manager is not None:
        if agent.session_summary_manager.model is None:
            agent.session_summary_manager.model = agent.model

    if agent.add_session_summary_to_context is None:
        agent.add_session_summary_to_context = (
            agent.enable_session_summaries or agent.session_summary_manager is not None
        )


def set_compression_manager(agent: Agent) -> None:
    if agent.compress_tool_results and agent.compression_manager is None:
        agent.compression_manager = CompressionManager(
            model=agent.model,
        )

    if agent.compression_manager is not None and agent.compression_manager.model is None:
        agent.compression_manager.model = agent.model

    # Check compression flag on the compression manager
    if agent.compression_manager is not None and agent.compression_manager.compress_tool_results:
        agent.compress_tool_results = True


def _initialize_session_state(
    session_state: Dict[str, Any],
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Inject current_user_id, current_session_id, and current_run_id into session_state.

    These transient values are stripped before persisting to the database (see _session.py)
    but must be available at runtime so that tool functions and instruction templates can
    reference them.  Teams and Workflows already do this; this brings Agents to parity.
    """
    if user_id:
        session_state["current_user_id"] = user_id
    if session_id is not None:
        session_state["current_session_id"] = session_id
    if run_id is not None:
        session_state["current_run_id"] = run_id
    return session_state


def has_async_db(agent: Agent) -> bool:
    """Return True if the db the agent is equipped with is an Async implementation."""
    return agent.db is not None and isinstance(agent.db, AsyncBaseDb)


def get_models(agent: Agent) -> None:
    from egpt.metrics import ModelType

    if agent.model is not None:
        was_user_pinned = agent._user_pinned_model is not None
        agent.model = get_model(agent.model)
        if agent.model is not None:
            agent.model.model_type = ModelType.MODEL
        # Re-point the recorded pin at the coerced object, so the provider seam's `requested`
        # always carries a real `Model` rather than the `"provider:id"` string a caller may have
        # passed. Gated on there having *been* a pin: assigning unconditionally would turn a
        # provider-assigned model into "the user's pin" on the next run, which is the latching
        # bug this record exists to avoid.
        if was_user_pinned:
            agent._user_pinned_model = agent.model
    if agent.reasoning_model is not None:
        agent.reasoning_model = get_model(agent.reasoning_model)
        if agent.reasoning_model is not None:
            agent.reasoning_model.model_type = ModelType.REASONING_MODEL
    if agent.parser_model is not None:
        agent.parser_model = get_model(agent.parser_model)
        if agent.parser_model is not None:
            agent.parser_model.model_type = ModelType.PARSER_MODEL
    if agent.output_model is not None:
        agent.output_model = get_model(agent.output_model)
        if agent.output_model is not None:
            agent.output_model.model_type = ModelType.OUTPUT_MODEL

    if agent.fallback_config is not None:
        agent.fallback_config.resolve_models()

    if agent.compression_manager is not None and agent.compression_manager.model is None:
        agent.compression_manager.model = agent.model


def initialize_agent(agent: Agent, debug_mode: Optional[bool] = None, run_context: Optional[Any] = None) -> None:
    # set_id before set_default_model (EGPT-3.1): the seam passes agent.id to the provider as
    # its per-agent routing signal, and Agent.id defaults to None, so resolving first made that
    # signal always None for any agent declared without an explicit id -- the common case.
    # set_id only fills a missing id from the name, so hoisting it changes nothing else.
    set_id(agent)
    set_default_model(agent, run_context=run_context)
    set_debug(agent, debug_mode=debug_mode)
    set_telemetry(agent)
    set_checkpoint(agent)
    if agent.update_memory_on_run or agent.enable_agentic_memory or agent.memory_manager is not None:
        set_memory_manager(agent)
    if (
        agent.add_culture_to_context
        or agent.update_cultural_knowledge
        or agent.enable_agentic_culture
        or agent.culture_manager is not None
    ):
        set_culture_manager(agent)
    if agent.enable_session_summaries or agent.session_summary_manager is not None:
        set_session_summary_manager(agent)
    if agent.compress_tool_results or agent.compression_manager is not None:
        set_compression_manager(agent)
    if agent.learning is not None and agent.learning is not False:
        set_learning_machine(agent)

    log_debug(f"Agent ID: {agent.id}", center=True)

    if agent._formatter is None:
        agent._formatter = SafeFormatter()


def add_tool(agent: Agent, tool: Union[Toolkit, Callable, Function, Dict]) -> None:
    from egpt.utils.callables import is_callable_factory

    if is_callable_factory(agent.tools, excluded_types=(Toolkit, Function)):
        raise RuntimeError(
            "Cannot add_tool() when tools is a callable factory. Use set_tools() to replace the factory."
        )
    if not agent.tools:
        agent.tools = []
    agent.tools.append(tool)  # type: ignore[union-attr]


def set_tools(agent: Agent, tools: Union[Sequence[Union[Toolkit, Callable, Function, Dict]], Callable]) -> None:
    from egpt.utils.callables import is_callable_factory

    if is_callable_factory(tools, excluded_types=(Toolkit, Function)):
        agent.tools = tools  # type: ignore[assignment]
        agent._callable_tools_cache.clear()
    else:
        agent.tools = list(tools) if tools else []  # type: ignore[arg-type]


async def connect_mcp_tools(agent: Agent) -> None:
    """Connect the MCP tools to the agent."""
    if agent.tools and isinstance(agent.tools, list):
        for tool in agent.tools:
            # Alternate method of using isinstance(tool, (MCPTools, MultiMCPTools)) to avoid imports
            if (
                hasattr(type(tool), "__mro__")
                and any(c.__name__ in ["MCPTools", "MultiMCPTools"] for c in type(tool).__mro__)
                and not tool.initialized  # type: ignore
            ):
                try:
                    # Connect the MCP server
                    await tool.connect()  # type: ignore
                    agent._mcp_tools_initialized_on_run.append(tool)  # type: ignore
                except Exception as e:
                    log_warning(f"Error connecting tool: {str(e)}")


async def disconnect_mcp_tools(agent: Agent) -> None:
    """Disconnect the MCP tools from the agent."""
    for tool in agent._mcp_tools_initialized_on_run:
        try:
            await tool.close()
        except Exception as e:
            log_warning(f"Error disconnecting tool: {str(e)}")
    agent._mcp_tools_initialized_on_run = []


def connect_connectable_tools(agent: Agent) -> None:
    """Connect tools that require connection management (e.g., database connections)."""
    if agent.tools and isinstance(agent.tools, list):
        for tool in agent.tools:
            if (
                hasattr(tool, "requires_connect")
                and tool.requires_connect  # type: ignore
                and hasattr(tool, "connect")
                and tool not in agent._connectable_tools_initialized_on_run
            ):
                try:
                    tool.connect()  # type: ignore
                    agent._connectable_tools_initialized_on_run.append(tool)
                except Exception as e:
                    log_warning(f"Error connecting tool: {str(e)}")


def disconnect_connectable_tools(agent: Agent) -> None:
    """Disconnect tools that require connection management."""
    for tool in agent._connectable_tools_initialized_on_run:
        if hasattr(tool, "close"):
            try:
                tool.close()  # type: ignore
            except Exception as e:
                log_warning(f"Error disconnecting tool: {str(e)}")
    agent._connectable_tools_initialized_on_run = []
