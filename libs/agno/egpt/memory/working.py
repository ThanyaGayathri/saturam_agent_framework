"""The WORKING tier, in code (SM-C6 #94): a per-run buffer, not a store.

``tiers.TIER_POLICY[WORKING]`` routes to no substrate and names this ticket as owner -- that empty
``substrates`` frozenset *is* the boundary with the long-term tiers (AC "boundary with long-term
tiers is clear"): nothing here is ever read back by :meth:`MemoryProvider.read`, and the only way
content in this buffer reaches long-term storage is :meth:`WorkingMemory.offload` handing the
overflow to the *existing* write path as ordinary ``EPISODIC`` events -- the same route any other
caller uses, not a second one.

Summarization is reused, not reinvented (Design Decision #2 on the issue): ``offload`` /
``aoffload`` call :class:`~egpt.session.summary.SessionSummaryManager`, the same manager
``session/summary.py`` already uses to compact a whole session.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, Sequence, Union, cast

from egpt.memory.tiers import DEFAULT_WRITE_TIER
from egpt.memory.types import MemoryEvent, Scope, WriteReceipt, render_context_lines, require_scope
from egpt.models.message import Message
from egpt.session.summary import SessionSummary, SessionSummaryManager

if TYPE_CHECKING:  # `provider` imports this module for its own hook; importing it back would cycle
    from egpt.memory.provider import MemoryProvider
    from egpt.memory.types import MemorySlice
    from egpt.session.agent import AgentSession
    from egpt.session.team import TeamSession

__all__ = ["WorkingMemory"]


class _BufferSession:
    """The minimal ``Session`` duck-type ``SessionSummaryManager`` actually calls.

    ``_prepare_summary_messages`` only ever calls ``session.get_messages(last_n_runs=, limit=)``
    and, after generating a summary, sets ``session.summary``. A :class:`WorkingMemory` buffer is
    not organised into runs the way a real ``AgentSession``/``TeamSession`` is, so building one of
    those from a flat message list -- to satisfy an interface two attributes wide -- would be
    machinery this ticket does not own standing up.
    """

    def __init__(self, messages: Sequence[Message]) -> None:
        self._messages = list(messages)
        self.summary: Optional[SessionSummary] = None

    def get_messages(
        self,
        last_n_runs: Optional[int] = None,
        limit: Optional[int] = None,
        **_: object,  # absorbs any keyword `_prepare_summary_messages` adds later, so a call
        # shape this duck-type does not yet know about degrades to "narrows by less" instead of
        # `TypeError` three frames from anything this module controls.
    ) -> List[Message]:
        # `last_n_runs` has no meaning here -- there are no runs -- so only `limit` narrows.
        # `limit=0` must mean "return nothing", not "return everything": `self._messages[-0:]` is
        # `self._messages[0:]` (Python's `-0 == 0`), so a naive `if limit` ternary took the
        # falsy-zero branch and returned the *whole* buffer for the one input meant to return none
        # of it. Only currently unreachable because `SessionSummaryManager.__post_init__` requires
        # `conversation_limit > 0` -- an invariant this duck-type must not depend on to be correct.
        if limit is None:
            return list(self._messages)
        return self._messages[-limit:] if limit > 0 else []


def _as_session(overflow: Sequence[Message]) -> Union["AgentSession", "TeamSession"]:
    """Build the summary manager's argument, typed as what it declares rather than what it checks.

    ``SessionSummaryManager.create_session_summary`` is annotated ``Union[AgentSession,
    TeamSession]`` but only ever calls the two members :class:`_BufferSession` implements -- the
    cast states that gap once, here, instead of an ``# type: ignore`` at every call site.
    """
    return cast("Union[AgentSession, TeamSession]", _BufferSession(overflow))


def _merge_summary(previous: Optional[SessionSummary], new: SessionSummary) -> SessionSummary:
    """Fold a fresh offload's summary into the running one instead of replacing it.

    A second offload that dropped the first summary would silently lose everything it covered --
    exactly the "ignored parameter returns a wrong answer that looks right" shape the rest of this
    package refuses. Topics are unioned, order-stable; the summary text is appended as a new
    paragraph so a later offload reads as "what I knew, plus what happened since."
    """
    if previous is None:
        return new
    topics = list(previous.topics or [])
    topics.extend(t for t in (new.topics or []) if t not in topics)
    return SessionSummary(
        summary="\n\n".join([previous.summary, new.summary]),
        topics=topics or None,
        updated_at=new.updated_at,
    )


@dataclass
class WorkingMemory:
    """One run's working buffer: recent turns, a scratchpad, and active goals.

    Not a store -- see the module docstring. ``max_messages`` is the offload threshold
    (AC "summarize/offload-when-large"); ``keep_after_offload`` is how much of the buffer stays
    verbatim after an offload, so the seam a caller reads immediately after never loses the turn
    that triggered it.
    """

    scope: Scope
    max_messages: int = 40
    keep_after_offload: int = 10
    summary_manager: SessionSummaryManager = field(default_factory=SessionSummaryManager)

    messages: List[Message] = field(default_factory=list)
    scratchpad: List[str] = field(default_factory=list)
    goals: List[str] = field(default_factory=list)
    summary: Optional[SessionSummary] = None
    #: The receipt from the most recent offload's provider write, when there was one to make.
    #: ``None`` conflates three states this field cannot currently tell apart: no offload with a
    #: provider has run yet, the most recent one *had* a provider but every overflow message
    #: lacked string content so there was nothing to write, or (before the reordering fixed in
    #: this ticket) a declined summary. A caller that must distinguish "no provider" from
    #: "provider given nothing to write" cannot do so from this field alone today -- narrowing it
    #: needs a sentinel distinct from a genuine empty receipt, which is follow-up work, not this
    #: ticket's.
    last_offload_receipt: Optional[WriteReceipt] = None

    def __post_init__(self) -> None:
        require_scope(self.scope, method="WorkingMemory")
        if self.max_messages <= 0:
            raise ValueError(f"WorkingMemory.max_messages must be positive, got {self.max_messages}.")
        if not (0 <= self.keep_after_offload < self.max_messages):
            raise ValueError(
                "WorkingMemory.keep_after_offload must be in [0, max_messages); got "
                f"{self.keep_after_offload} against max_messages={self.max_messages}."
            )

    # -- mutation --------------------------------------------------------------------------------

    def add_message(self, message: Message) -> None:
        """Append one turn to the context window."""
        self.messages.append(message)

    def note(self, text: str) -> None:
        """Append a scratchpad entry -- working state that is not a conversation turn."""
        stripped = text.strip()
        if stripped:
            self.scratchpad.append(stripped)

    def set_goals(self, goals: Sequence[str]) -> None:
        """Replace the active-goals list. Blank entries are dropped, not stored as empty goals."""
        self.goals = [g.strip() for g in goals if g.strip()]

    # -- offload -----------------------------------------------------------------------------------

    def should_offload(self) -> bool:
        # `>=`, not `>`: `max_messages` is the bound the buffer stays *at*, not one past. `>` let
        # the buffer hold `max_messages + 1` messages before triggering, which reads as an
        # off-by-one against a name a caller reasonably takes as a ceiling.
        return len(self.messages) >= self.max_messages

    def _overflow(self) -> List[Message]:
        """The messages an offload right now would summarize away, oldest-first."""
        cut = len(self.messages) - self.keep_after_offload
        return self.messages[:cut] if cut > 0 else []

    def _events_for(self, overflow: Sequence[Message]) -> List[MemoryEvent]:
        """One ``EPISODIC``-tier event per offloaded message with real content.

        Raw and non-lossy, per the write path's own contract (``provider.py`` on ``write``) --
        offload does not distil, it hands the untouched turn to the tier that does. A message with
        no string content (multimodal-only, or empty) has nothing this seam's ``MemoryEvent.content``
        can carry, so it is summarized but not separately written.
        """
        events = []
        for message in overflow:
            if isinstance(message.content, str) and message.content.strip():
                events.append(
                    MemoryEvent(
                        content=message.content,
                        scope=self.scope,
                        tier=DEFAULT_WRITE_TIER,
                        role=message.role,
                    )
                )
        return events

    def offload(self, provider: Optional["MemoryProvider"] = None) -> Optional[SessionSummary]:
        """Summarize the overflow and drop it from the buffer, keeping the tail verbatim.

        Summarization runs **first**. Only once the summary manager actually produces a summary
        does the overflow get written through ``provider`` (as ``EPISODIC`` events -- offload **to
        long-term**, not merely compaction) and trimmed from the buffer. Reordering this the other
        way round -- write, then summarize -- made a declined summary a *partial* no-op: the
        docstring promised "changes nothing," but the write had already landed, and the same
        overflow slice would write the same events again on every subsequent decline, since nothing
        was ever trimmed to stop `_overflow` from recomputing it.

        A no-op (returns ``None``, writes nothing, changes nothing) when :meth:`should_offload` is
        false or the summary manager declines (``SessionSummaryManager`` returns ``None`` for an
        empty or unmeaningful window) -- offload is never forced past what there is to say.
        """
        if not self.should_offload():
            return None
        overflow = self._overflow()
        new_summary = self.summary_manager.create_session_summary(_as_session(overflow))
        if new_summary is None:
            return None
        if provider is not None:
            events = self._events_for(overflow)
            self.last_offload_receipt = provider.write(self.scope, events) if events else None
        self.summary = _merge_summary(self.summary, new_summary)
        self.messages = self.messages[len(overflow) :]
        return self.summary

    async def aoffload(self, provider: Optional["MemoryProvider"] = None) -> Optional[SessionSummary]:
        """Async variant of :meth:`offload`."""
        if not self.should_offload():
            return None
        overflow = self._overflow()
        new_summary = await self.summary_manager.acreate_session_summary(_as_session(overflow))
        if new_summary is None:
            return None
        if provider is not None:
            events = self._events_for(overflow)
            self.last_offload_receipt = await provider.awrite(self.scope, events) if events else None
        self.summary = _merge_summary(self.summary, new_summary)
        self.messages = self.messages[len(overflow) :]
        return self.summary

    # -- assembly ----------------------------------------------------------------------------------

    def as_context(self, persistent: Optional["MemorySlice"] = None) -> str:
        """Render this buffer, optionally followed by a persistent-tier read, as one prompt string.

        Line rendering is :func:`~egpt.memory.types.render_context_lines`, the same
        injection-safe helper ``MemorySlice.as_context`` uses, so a goal, a note, or a message
        containing a forged ``"\\n[semantic] ..."`` line cannot impersonate another entry here
        either.

        Order: goals, then the running summary, then the scratchpad, then live messages -- oldest
        first, matching how a caller reads a transcript -- then ``persistent`` if given. This is
        the WORKING half of SM-H6's retrieval assembly; how a ranked persistent slice should
        *interleave* with a live buffer, rather than simply follow it, is that ticket's fusion
        question, not this one's.
        """
        lines: List[str] = []
        for goal in self.goals:
            lines.extend(render_context_lines("goal", goal))
        if self.summary is not None:
            lines.extend(render_context_lines("summary", self.summary.summary))
        for entry in self.scratchpad:
            lines.extend(render_context_lines("note", entry))
        for message in self.messages:
            if isinstance(message.content, str) and message.content.strip():
                lines.extend(render_context_lines(message.role, message.content))
        text = "\n".join(lines)
        if persistent is not None:
            persistent_text = persistent.as_context()
            if persistent_text:
                text = "\n".join(part for part in (text, persistent_text) if part)
        return text
