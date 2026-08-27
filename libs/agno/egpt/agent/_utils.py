"""Shared utility helpers for Agent."""

from __future__ import annotations

import json
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Union,
)

if TYPE_CHECKING:
    from egpt.agent.agent import Agent

from egpt.filters import FilterExpr
from egpt.models.base import carry_over_pin, update_pins_a_new_model
from egpt.utils.log import log_debug, log_error, log_warning


def get_effective_filters(
    agent: Agent, knowledge_filters: Optional[Union[Dict[str, Any], List[FilterExpr]]] = None
) -> Optional[Any]:
    """
    Determine which knowledge filters to use, with priority to run-level filters.

    Args:
        agent: The Agent instance.
        knowledge_filters: Filters passed at run time.

    Returns:
        The effective filters to use, with run-level filters taking priority.
    """
    effective_filters = None

    # If agent has filters, use those as a base
    if agent.knowledge_filters:
        effective_filters = agent.knowledge_filters.copy()

    # If run has filters, they override agent filters
    if knowledge_filters:
        if effective_filters:
            if isinstance(knowledge_filters, dict):
                if isinstance(effective_filters, dict):
                    effective_filters.update(knowledge_filters)
                else:
                    effective_filters = knowledge_filters
            elif isinstance(knowledge_filters, list):
                effective_filters = [*effective_filters, *knowledge_filters]
        else:
            effective_filters = knowledge_filters

    if effective_filters:
        log_debug(f"Using knowledge filters: {effective_filters}")

    return effective_filters


def convert_documents_to_string(agent: Agent, docs: List[Union[Dict[str, Any], str]]) -> str:
    if docs is None or len(docs) == 0:
        return ""

    if agent.references_format == "yaml":
        import yaml

        return yaml.dump(docs, allow_unicode=True)

    return json.dumps(docs, indent=2, ensure_ascii=False)


def convert_dependencies_to_string(agent: Agent, context: Dict[str, Any]) -> str:
    """Convert the context dictionary to a string representation.

    Args:
        agent: The Agent instance.
        context: Dictionary containing context data

    Returns:
        String representation of the context, or empty string if conversion fails
    """
    if context is None:
        return ""

    try:
        return json.dumps(context, indent=2, default=str, ensure_ascii=False)
    except (TypeError, ValueError, OverflowError) as e:
        log_warning(f"Failed to convert context to JSON: {str(e)}")
        # Attempt a fallback conversion for non-serializable objects
        sanitized_context = {}
        for key, value in context.items():
            try:
                # Try to serialize each value individually
                json.dumps({key: value}, default=str)
                sanitized_context[key] = value
            except Exception:
                # If serialization fails, convert to string representation
                sanitized_context[key] = str(value)

        try:
            return json.dumps(sanitized_context, indent=2, ensure_ascii=False)
        except Exception as e:
            log_error(f"Failed to convert sanitized context to JSON: {str(e)}")
            return str(context)


# ---------------------------------------------------------------------------
# Deep copy
# ---------------------------------------------------------------------------


def deep_copy(agent: Agent, *, update: Optional[Dict[str, Any]] = None) -> Agent:
    """Create and return a deep copy of this Agent, optionally updating fields.

    Args:
        agent: The Agent instance to copy.
        update (Optional[Dict[str, Any]]): Optional dictionary of fields for the new Agent.

    Returns:
        Agent: A new Agent instance.
    """
    from dataclasses import fields
    from inspect import signature

    # Get the set of valid __init__ parameter names
    init_params = set(signature(agent.__class__.__init__).parameters.keys()) - {"self"}

    # Extract the fields to set for the new Agent
    fields_for_new_agent: Dict = {}

    for f in fields(agent):
        # Skip private fields and fields not accepted by __init__
        if f.name.startswith("_") or f.name not in init_params:
            continue

        field_value = getattr(agent, f.name)
        if field_value is not None:
            try:
                fields_for_new_agent[f.name] = deep_copy_field(agent, f.name, field_value)
            except Exception as e:
                log_warning(f"Failed to deep copy field '{f.name}'. Using original value.: {str(e)}")
                fields_for_new_agent[f.name] = field_value

    # Update fields if provided
    if update:
        fields_for_new_agent.update(update)

    # Create a new Agent
    try:
        new_agent = agent.__class__(**fields_for_new_agent)
        # `model_provider` (EGPT-3.1) is set as a plain attribute, not a dataclass field, so the
        # `fields(agent)` loop above cannot see it. Carried explicitly because dropping it is
        # silent and lands on the hottest paths -- os/utils.py deep-copies an agent per request
        # and team/_utils.py copies every member -- where the effect is that the narrower,
        # more deliberate configuration is the one discarded. Shared by reference, matching how
        # the process-wide provider is shared: a provider is a resolution policy, not per-agent
        # state. EGPT-3.4 declares the real field, after which this becomes redundant.
        provider = getattr(agent, "model_provider", None)
        if provider is not None:
            # setattr, not attribute assignment: the name is not declared on Agent until
            # EGPT-3.4, so mypy rightly rejects the direct form.
            setattr(new_agent, "model_provider", provider)  # noqa: B010

        # Carry the *original's* pin rather than letting `__init__` re-derive one from the copied
        # `model`. Same class of problem as `model_provider` above, opposite direction: `model` IS
        # a declared field, so the loop copies it -- and by copy time it holds whatever a provider
        # assigned, which `Agent.__init__` would then record as "the caller's pin". On the AgentOS
        # path that is every request (startup calls `initialize_agent`, each request calls
        # `deep_copy`), so a config-driven provider would see `requested` set on every run and
        # return it unchanged -- bypassing per-tenant routing for the whole deployment and
        # attributing it to a pin no caller made.
        #
        # Correct in all three cases: a genuine pin carries over, a provider-assigned model does
        # not become one, and a pinless agent stays pinless.
        #
        # The exception is a *new* model passed via `update`: `deep_copy(update={"model": X})` is
        # the public way to say "this copy runs X", and `__init__` has already recorded it as the
        # pin (`fields_for_new_agent.update(update)` makes `update["model"]` the constructor's
        # `model` argument). So the only job here is to decide whether to overwrite that with the
        # original's pin, and there are two ways to get it wrong:
        #
        # * Skipping the carry-over on the mere *presence* of the key drops a real pin, because
        #   `update={"model": None}` leaves `__init__` with nothing to record. A copy of
        #   `Agent(model=X)` then runs unpinned and the provider substitutes its policy default --
        #   a model no caller chose, and a regression against carrying the pin unconditionally.
        #
        # * Treating any non-None `update` model as a fresh pin re-opens the laundering this block
        #   exists to prevent, one hop removed: `deep_copy(update={"model": agent.model})` -- "copy
        #   this agent with its current model" -- would pin whatever a provider last assigned, and
        #   every later run of the copy would bypass routing and escalation on the strength of a
        #   pin no caller made.
        #
        # `same_model`, not `is`. A pure identity test looked sufficient and is not, because
        # every in-tree provider builds a *fresh* `Model` per resolve (`mode_a` and `mode_b` both
        # go through `_construct`), so a caller who passes a model reconstructed from the same
        # configuration hands over an equal-but-distinct object.
        #
        # Comparing by value rather than by identity fixed this in *both* directions:
        #
        # * A copy of a *pinned* agent whose model a provider had swapped took the fresh-pin
        #   branch and came back pinned to the **provider's** model, destroying the caller's
        #   choice. That is the precise inversion this field exists to prevent.
        # * A copy of an *unpinned* agent gained a pin nobody asked for, and it persisted into
        #   every later copy of that object graph.
        #
        # `same_model` compares the connection fields, not just class and id. Two models sharing
        # an id while pointing at different tenants' endpoints are a different model for every
        # purpose this decision serves: keeping the earlier object as the pin would make a
        # pin-honouring provider return it, and the copy would serve requests from the previous
        # tenant's endpoint under the previous tenant's key. The rationale lives with the helper
        # in `egpt/models/base.py`.
        #
        # `new_agent.model`, not `update["model"]`: `__init__` has already coerced the
        # `"provider:id"` string form into a `Model`, so this compares like with like whichever
        # form the caller wrote. Comparing the raw value would make every string-form caller fall
        # through to "not a pin" and leave both directions of the bug open for them.
        #
        # Only the live model is compared against, not `agent._user_pinned_model` as well. The
        # second check looks like it should be needed -- the pin is the caller's choice and the
        # live model is the provider's -- but it is unreachable: when the passed model matches the
        # pin, `__init__` records that same id, so the fresh-pin and carry-over branches agree.
        # Verified by removing it, which changes no behaviour and fails no test. Left out
        # deliberately: a clause nothing can reach reads as protection while providing none.
        # `update={"model": None}` means "do not change the model", not "clear the pin"
        # (aicoe-tracker#172). Both readings are defensible, so this is the recorded decision rather than an
        # accident of the guard: a pin is a tenancy-sensitive control, and the safe direction for
        # an ambiguous input is to keep the caller's explicit choice. Clearing it would hand the
        # copy to whatever policy the provider defaults to -- a model no caller chose. A caller
        # who wants a different model passes one; there is no way to *ask* to be un-pinned, which
        # is deliberate.
        #
        # `carry_over_pin`, not the object itself: the pin must not be shared across copies. See
        # the helper for why (mode_a returns the pinned object as the serving model, and
        # `_get_client_params` mutates it).
        if not update_pins_a_new_model(update, new_agent.model, agent.model):
            # When the original's pin *is* its live model -- the ordinary case, since
            # `get_models` aliases them in `__init__` -- the copy must keep them one object too.
            # Copying the pin alone splits the alias, and nothing on the run path heals it
            # (`initialize_agent` calls `set_default_model`, which early-returns when a model is
            # already set). The copy would then hold two Models that drift: a post-copy
            # `copy.model.temperature = ...` is invisible to a pin-honouring provider, which
            # serves the pin. Found in the third review round of #107.
            #
            # Assigning `.model` as well also *strengthens* the fix this helper exists for: the
            # credential filled by one copy's client build no longer lands in the shared
            # `agent.model` either. Guarded on the copy still sharing the original's model, so an
            # `update={"model": X}` that supplied its own is left alone.
            carried = carry_over_pin(agent._user_pinned_model)
            if agent._user_pinned_model is agent.model and new_agent.model is agent.model:
                new_agent.model = carried
            new_agent._user_pinned_model = carried
        log_debug(f"Created new {agent.__class__.__name__}")
        return new_agent
    except Exception as e:
        log_error(f"Failed to create deep copy of {agent.__class__.__name__}: {str(e)}")
        raise


def deep_copy_field(agent: Agent, field_name: str, field_value: Any) -> Any:
    """Helper function to deep copy a field based on its type."""
    from copy import copy, deepcopy

    from pydantic import BaseModel

    # For memory and reasoning_agent, use their deep_copy methods
    if field_name == "reasoning_agent":
        return field_value.deep_copy()  # type: ignore

    # For tools, return callable factories by reference; share MCP tools but copy others
    if field_name == "tools" and field_value is not None:
        from egpt.tools import Toolkit
        from egpt.tools.function import Function
        from egpt.utils.callables import is_callable_factory

        # Callable-factory tools are shared by reference and resolved per-run
        if is_callable_factory(field_value, excluded_types=(Toolkit, Function)):
            return field_value

        try:
            copied_tools = []
            for tool in field_value:  # type: ignore
                try:
                    # Share MCP tools (they maintain server connections)
                    is_mcp_tool = hasattr(type(tool), "__mro__") and any(
                        c.__name__ in ["MCPTools", "MultiMCPTools"] for c in type(tool).__mro__
                    )
                    if is_mcp_tool:
                        copied_tools.append(tool)
                    else:
                        try:
                            copied_tools.append(deepcopy(tool))
                        except Exception:
                            # Tool can't be deep copied, share by reference
                            copied_tools.append(tool)
                except Exception:
                    # MCP detection failed, share tool by reference to be safe
                    copied_tools.append(tool)
            return copied_tools
        except Exception as e:
            # If entire tools processing fails, log and return original list
            log_warning(f"Failed to process tools for deep copy: {str(e)}")
            return field_value

    # Share heavy resources - these maintain connections/pools that shouldn't be duplicated
    if field_name in (
        "db",
        "model",
        "reasoning_model",
        "knowledge",
        "memory_manager",
        "memory_provider",
        "parser_model",
        "output_model",
        "session_summary_manager",
        "culture_manager",
        "compression_manager",
        "learning",
        "skills",
    ):
        return field_value

    # For compound types, attempt a deep copy
    if isinstance(field_value, (list, dict, set)):
        try:
            return deepcopy(field_value)
        except Exception:
            try:
                return copy(field_value)
            except Exception as e:
                log_warning(f"Failed to copy field: {field_name}: {str(e)}")
                return field_value

    # For pydantic models, attempt a model_copy
    if isinstance(field_value, BaseModel):
        try:
            return field_value.model_copy(deep=True)
        except Exception:
            try:
                return field_value.model_copy(deep=False)
            except Exception as e:
                log_warning(f"Failed to copy field: {field_name}: {str(e)}")
                return field_value

    # For other types, attempt a shallow copy first
    try:
        return copy(field_value)
    except Exception:
        # If copy fails, return as is
        return field_value
