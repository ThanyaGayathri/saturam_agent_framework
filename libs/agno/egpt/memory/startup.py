"""Startup checks for a deployment that expects its agents to remember (G1).

Memory fails quietly. An agent with no provider runs perfectly well and simply recalls
nothing; an agent whose run carries no tenant takes the early return in
``_agent_memory_scope`` and writes nothing, also without complaint. Neither shows up as an
error, in a log line, or in a response. It shows up weeks later as "the assistant does not
seem to remember anything", by which point nobody can say when it stopped.

So the checks here are deliberately **fatal at startup**. A deployment that means to have
memory and does not is worse than one that refuses to boot: the second is fixed in a minute,
the first is discovered after it has been trusted.

Two conditions, and the second is the one people miss:

* **Every agent has a memory provider.** The obvious check.
* **Every agent that writes memory has a stable identity.** ``agent_id`` is
  ``generate_id_from_name(agent.name)``, and for an agent with neither ``id`` nor ``name`` that
  is a uuid4-derived value minted per construction -- a fresh memory partition on every process
  start. Writes still land; they land somewhere new each restart, so recall degrades to
  "remembers only what happened since the last deploy" and nothing reports it.
"""

from typing import Any, Iterable, List, Sequence

#: Distinguishes "this deployment has no configured tenant" from "it has one and it is unset".
#: Without it, ``tenant_id=None`` is ambiguous and the refusal names the symptom -- an agent with
#: no provider -- rather than the cause, which is the missing customer slug.
NO_CONFIGURED_TENANT = object()

__all__ = [
    "NO_CONFIGURED_TENANT",
    "MemoryNotWired",
    "agents_missing_memory",
    "agents_without_stable_identity",
    "assert_memory_wired",
]


class MemoryNotWired(RuntimeError):
    """A deployment that expects memory has agents that cannot use it."""


def _agent_label(agent: Any, index: int) -> str:
    for attribute in ("id", "name"):
        value = getattr(agent, attribute, None)
        if isinstance(value, str) and value.strip():
            return value
    return f"<unnamed agent #{index}>"


def agents_missing_memory(agents: Iterable[Any]) -> List[str]:
    """Names of agents with no memory provider attached."""
    return [
        _agent_label(agent, index)
        for index, agent in enumerate(agents)
        if getattr(agent, "memory_provider", None) is None
    ]


def agents_without_stable_identity(agents: Iterable[Any]) -> List[str]:
    """Agents that have a memory provider but no ``id`` and no ``name``.

    Checked only for agents that actually write memory. An agent without memory is free to be
    anonymous; one that writes needs a partition key that survives a restart.
    """
    unstable: List[str] = []
    for index, agent in enumerate(agents):
        if getattr(agent, "memory_provider", None) is None:
            continue
        has_identity = any(
            isinstance(getattr(agent, attribute, None), str) and getattr(agent, attribute).strip()
            for attribute in ("id", "name")
        )
        if not has_identity:
            unstable.append(f"<agent #{index}>")
    return unstable


def assert_memory_wired(
    agents: Sequence[Any],
    *,
    tenant_id: Any = NO_CONFIGURED_TENANT,
    require_agents: bool = True,
) -> None:
    """Refuse to start unless every agent can actually use memory.

    Args:
        agents: The agents this deployment resolved.
        tenant_id: The configured tenant. Pass it whenever the deployment is meant to have
            one, **including when the value is ``None``** -- that is the G1 case, and naming it
            is the difference between a refusal that says "no customer slug configured" and one
            that says "an agent has no provider", which is the symptom two steps downstream.
            Omit the argument entirely for a deployment that resolves its tenant per request.
        require_agents: Whether an empty agent list is itself a failure. True by default,
            because a deployment that resolved no agents at all has usually failed to load its
            registry rather than deliberately shipped none.

    Raises:
        MemoryNotWired: with every problem listed at once, so a misconfigured deployment is
            fixed in one pass instead of one restart per fault.
    """
    problems: List[str] = []

    if require_agents and not agents:
        problems.append(
            "no agents resolved at all -- the registry is empty, which is almost always a "
            "loading failure rather than an intentionally agentless deployment"
        )

    if tenant_id is not NO_CONFIGURED_TENANT and not (isinstance(tenant_id, str) and tenant_id.strip()):
        problems.append(
            f"no customer identifier is configured (got {tenant_id!r}). This is the root "
            "cause: every run would carry tenant_id=None, `_agent_memory_scope` returns None "
            "for that, and memory would do nothing without reporting it"
        )

    missing = agents_missing_memory(agents)
    if missing:
        problems.append(
            f"{len(missing)} agent(s) have no memory provider: {', '.join(missing)}. "
            "They will answer normally and remember nothing"
        )

    unstable = agents_without_stable_identity(agents)
    if unstable:
        problems.append(
            f"{len(unstable)} agent(s) write memory but have neither id nor name: "
            f"{', '.join(unstable)}. `agent_id` is then a uuid4-derived value minted per "
            "construction, so each restart writes to a fresh partition"
        )

    if problems:
        raise MemoryNotWired(
            "This deployment expects memory and cannot use it:\n  - "
            + "\n  - ".join(problems)
            + "\nStartup is refused rather than continuing, because an agent that silently "
            "remembers nothing is discovered long after it stopped working."
        )
