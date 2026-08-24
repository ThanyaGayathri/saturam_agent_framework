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
        #: What the most recent :meth:`get_messages` call actually returned. `offload` reads this
        #: after summarizing to trim/write exactly what got captured -- `conversation_limit` can
        #: narrow the input below the full buffer, and blindly trimming/writing the whole input
        #: regardless (PR #132 round 3 finding) silently drops the un-summarized remainder when no
        #: provider is given, or writes content the summary text never accounted for when one is.
        #: Starts as the full input: a manager with no model never calls `get_messages` at all
        #: (`create_session_summary` returns `None` first), and that branch never reads this field.
        self.messages_used: List[Message] = list(messages)

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
        # `last_n_runs` is accepted and silently unused: a buffer is not organised into runs, so
        # there is nothing to apply it to. A `summary_manager` shared with real session
        # summarization and configured with `last_n_runs` set gets un-windowed behavior here with
        # no error -- disposed rather than fixed (raising needs `WorkingMemory` to inspect a
        # manager's field at a point this duck-type does not control), documented on
        # `WorkingMemory.summary_manager` instead so it is visible before a caller hits it.
        selected = list(self._messages) if limit is None else (self._messages[-limit:] if limit > 0 else [])
        self.messages_used = selected
        return selected


def _typed(session: _BufferSession) -> Union["AgentSession", "TeamSession"]:
    """Type an already-built :class:`_BufferSession`, as what the summary manager declares rather
    than what it checks.

    ``SessionSummaryManager.create_session_summary`` is annotated ``Union[AgentSession,
    TeamSession]`` but only ever calls the two members :class:`_BufferSession` implements -- the
    cast states that gap once, here, instead of an ``# type: ignore`` at every call site. Takes the
    already-built session (rather than building one itself, as the single-purpose helper this
    replaced did) so a caller keeps a typed reference to read ``.messages_used`` off after the
    summary call returns.
    """
    return cast("Union[AgentSession, TeamSession]", session)


def _has_text(message: Message) -> bool:
    """Whether ``message`` carries content this seam's ``str``-only fields can hold.

    One predicate, used everywhere a message must clear this bar before it can become a
    ``MemoryEvent`` (:meth:`WorkingMemory._events_for`) or a rendered context line
    (:meth:`WorkingMemory.as_context`) -- both used to hand-roll the same
    ``isinstance(message.content, str) and message.content.strip()`` check, which is exactly the
    "one decision, two copies" shape the rest of this package extracts rather than repeats.
    """
    return isinstance(message.content, str) and bool(message.content.strip())


def _merge_summary(previous: Optional[SessionSummary], new: SessionSummary) -> SessionSummary:
    """Fold a fresh offload's summary into the running one instead of replacing it.

    A second offload that dropped the first summary would silently lose everything it covered --
    exactly the "ignored parameter returns a wrong answer that looks right" shape the rest of this
    package refuses. Topics are unioned, order-stable; the summary text is appended as a new
    paragraph so a later offload reads as "what I knew, plus what happened since."

    **Known limitation, not fixed here: unbounded growth.** Nothing here caps, ages out, or
    re-compacts the running summary -- a long-lived buffer that offloads many times accumulates an
    ever-longer ``summary.summary``, which `WorkingMemory.as_context` renders in full every call.
    That directly works against "summarize/offload-when-large"'s own point, which is to keep
    prompt size bounded. Left as an explicit follow-up rather than fixed in this pass: a real fix
    needs a design decision this ticket doesn't own -- a length/age cap with truncation loses
    information silently unless it is itself disclosed, and periodic re-compaction is another
    (bounded) model call on the offload path. Whoever picks this up should decide which, and
    where the bound lives (a `WorkingMemory` field, most likely).
    """
    if previous is None:
        return new
    topics = list(previous.topics or [])
    topics.extend(t for t in (new.topics or []) if t not in topics)
    # `topics` unconditionally, not `topics or None`: an empty list here means both summaries
    # genuinely named no topics, which is a real, distinguishable answer from "topics was never
    # asked about" -- collapsing it to `None` would conflate the two the same way
    # `last_offload_receipt` already documents as a known gap elsewhere in this file.
    return SessionSummary(
        summary="\n\n".join([previous.summary, new.summary]),
        topics=topics,
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
    #: The default (`model=None`) never produces a summary -- `create_session_summary` declines
    #: unconditionally without one -- so `offload`/`aoffload` are a permanent no-op unless a
    #: manager carrying a real model is supplied. "Summarize/offload-when-large" is a bound this
    #: buffer *can* enforce, not one it enforces unconditionally out of the box. A manager whose
    #: `last_n_runs` is set is also accepted but silently unused here: see `_BufferSession`.
    summary_manager: SessionSummaryManager = field(default_factory=SessionSummaryManager)

    messages: List[Message] = field(default_factory=list)
    scratchpad: List[str] = field(default_factory=list)
    goals: List[str] = field(default_factory=list)
    summary: Optional[SessionSummary] = None
    #: The provider write from the **most recently completed** ``offload``/``aoffload`` call --
    #: reset to ``None`` at the top of every call, not left holding a stale value from an earlier
    #: one. ``None`` means that call did not write anything to long-term storage: it was a no-op
    #: (below threshold, or the summary manager declined), it ran with no ``provider``, or it ran
    #: with one but every overflow message lacked string content so there was nothing to hand it.
    #: Those last two are still not distinguishable from each other here -- narrowing that needs a
    #: sentinel distinct from a genuine empty receipt, left as follow-up rather than folded into
    #: this fix -- but every non-``None`` value is now trustworthy as "the call that just returned
    #: actually wrote this", which the reset is what guarantees.
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
        """Append a scratchpad entry -- working state that is not a conversation turn.

        **Known limitation, not fixed here: unbounded.** `should_offload`/`offload` size the
        buffer by `len(self.messages)` alone; nothing here caps or offloads `scratchpad`, despite
        it being one of the three things the module docstring and the ticket describe this buffer
        as holding. `goals` doesn't share this gap in practice -- `set_goals` replaces rather than
        appends -- but a caller calling `note()` in a loop grows this list without bound. Left as
        an explicit follow-up: whether scratchpad entries should offload at all (they're not
        conversation turns `SessionSummaryManager` knows how to summarize) is a product decision
        this ticket doesn't settle, not a mechanical fix.
        """
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

    def _events_for(self, messages: Sequence[Message]) -> List[MemoryEvent]:
        """One ``EPISODIC``-tier event per message with real content.

        Raw and non-lossy, per the write path's own contract (``provider.py`` on ``write``) --
        offload does not distil, it hands the untouched turn to the tier that does. A message with
        no string content (multimodal-only, or empty) has nothing this seam's ``MemoryEvent.content``
        can carry, so it is summarized but not separately written.

        ``occurred_at`` carries ``message.created_at`` -- valid time, when the turn actually
        happened -- rather than being left unstamped. Left ``None``, the write path defaults it to
        write time (``now``), which is wrong here specifically because `keep_after_offload` can
        leave a message sitting in the buffer for a long time before an offload finally writes it:
        every such event's ``t_valid`` would silently read as "wrote it", not "said it."
        """
        events = []
        for message in messages:
            if _has_text(message):
                events.append(
                    MemoryEvent(
                        content=message.content,  # type: ignore[arg-type]  # narrowed by _has_text
                        scope=self.scope,
                        tier=DEFAULT_WRITE_TIER,
                        role=message.role,
                        occurred_at=message.created_at,
                    )
                )
        return events

    @staticmethod
    def _trim(current: List[Message], overflow_len: int, summarized_len: int) -> List[Message]:
        """The buffer after removing exactly the messages that got summarized.

        ``summarized_len`` may be **less** than ``overflow_len``: ``summary_manager`` may carry a
        ``conversation_limit`` that narrows what ``get_messages`` actually returned to
        `SessionSummaryManager` below the full overflow. Trimming the whole overflow regardless
        used to silently drop the un-summarized remainder when no ``provider`` was given (data
        loss with no trace), or write it through while the summary text disagreed about what was
        captured (with one). Trimming only what was truly summarized leaves the rest in the buffer
        for a later cycle instead -- consistent with the ordinary "not summarized yet" case.
        """
        unsummarized = overflow_len - summarized_len
        return current[:unsummarized] + current[overflow_len:]

    def offload(self, provider: Optional["MemoryProvider"] = None) -> Optional[SessionSummary]:
        """Summarize the overflow and drop it from the buffer, keeping the tail verbatim.

        Summarization runs **first**. Only once the summary manager actually produces a summary
        does the *summarized subset* get written through ``provider`` (as ``EPISODIC`` events --
        offload **to long-term**, not merely compaction) and trimmed from the buffer. Reordering
        this the other way round -- write, then summarize -- made a declined summary a *partial*
        no-op: the docstring promised "changes nothing," but the write had already landed, and the
        same overflow slice would write the same events again on every subsequent decline, since
        nothing was ever trimmed to stop `_overflow` from recomputing it.

        **The summarized subset, not the whole overflow.** ``summary_manager.conversation_limit``,
        if set, narrows what actually gets summarized below `_overflow`'s full slice; what gets
        written and trimmed now follows that same narrower set (:meth:`_trim`), so a message
        `conversation_limit` excluded from the summary stays in the buffer for a later cycle
        instead of silently disappearing (no provider) or being written under a summary that never
        described it (with one).

        Each written event carries ``occurred_at=message.created_at`` -- the turn's own valid time,
        not write time -- since `keep_after_offload` can leave a message sitting in the buffer for
        a long time before an offload finally writes it out.

        **Not idempotent under a partial provider failure.** Every `MemoryEvent` here is built with
        no ``id``, so a caller that catches `~egpt.memory.types.PartialWriteError` (real per
        `CoLocatedMemoryProvider._prepare`) and calls `offload` again recomputes the overflow from
        scratch and mints **fresh** ids for content that may already have landed on the first,
        partial attempt -- duplicating it. `PartialWriteError.event_ids` names exactly the ids a
        safe retry should reuse; this method does not thread them back in. A caller whose `provider`
        can fail partway must not blindly retry by calling `offload` again.

        A no-op (returns ``None``, writes nothing, changes nothing, and resets
        :attr:`last_offload_receipt` to ``None`` so a caller cannot read a stale receipt from an
        earlier, unrelated offload) when :meth:`should_offload` is false or the summary manager
        declines (``SessionSummaryManager`` returns ``None`` for an empty or unmeaningful window,
        which is what the default, model-less :attr:`summary_manager` always does -- offload only
        actually offloads once a manager carrying a real model is supplied).

        Summarization is a real (usually blocking) model call, so :meth:`aoffload` is the primary
        lane on a request path; this sync one is for tests, scripts, and manual triggers -- same
        framing as :meth:`~egpt.memory.provider.MemoryProvider.consolidate`'s.

        **Not safe to call concurrently on the same instance.** Both halves read ``self.messages``
        before a real suspension point (a model call, and here an awaited provider write) and
        mutate ``self.messages``/``self.summary``/``last_offload_receipt`` afterward with no guard.
        Two in-flight calls can compute the same overflow, both write it through a given
        ``provider`` (no dedup -- each stamps fresh event ids), and the second completion trims
        against an already-shrunk buffer. A caller driving offload from more than one task must
        serialize its own calls to one instance, the same responsibility ``consolidate``'s own
        docstring places on a caller driving concurrent passes over one scope.
        """
        if not self.should_offload():
            self.last_offload_receipt = None
            return None
        overflow = self._overflow()
        session = _BufferSession(overflow)
        new_summary = self.summary_manager.create_session_summary(_typed(session))
        if new_summary is None:
            self.last_offload_receipt = None
            return None
        summarized = session.messages_used
        if provider is not None:
            # Cleared before attempting: if `provider.write` raises, this call confirmed no write,
            # and a stale receipt from an earlier successful call must not survive it.
            self.last_offload_receipt = None
            events = self._events_for(summarized)
            if events:
                self.last_offload_receipt = provider.write(self.scope, events)
        else:
            self.last_offload_receipt = None
        self.summary = _merge_summary(self.summary, new_summary)
        self.messages = self._trim(self.messages, len(overflow), len(summarized))
        return self.summary

    async def aoffload(self, provider: Optional["MemoryProvider"] = None) -> Optional[SessionSummary]:
        """Async variant of :meth:`offload`, and the primary entry point -- see its docstring."""
        if not self.should_offload():
            self.last_offload_receipt = None
            return None
        overflow = self._overflow()
        session = _BufferSession(overflow)
        new_summary = await self.summary_manager.acreate_session_summary(_typed(session))
        if new_summary is None:
            self.last_offload_receipt = None
            return None
        summarized = session.messages_used
        if provider is not None:
            self.last_offload_receipt = None
            events = self._events_for(summarized)
            if events:
                self.last_offload_receipt = await provider.awrite(self.scope, events)
        else:
            self.last_offload_receipt = None
        self.summary = _merge_summary(self.summary, new_summary)
        self.messages = self._trim(self.messages, len(overflow), len(summarized))
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
            if _has_text(message):
                lines.extend(render_context_lines(message.role, cast(str, message.content)))
        text = "\n".join(lines)
        if persistent is not None:
            persistent_text = persistent.as_context()
            if persistent_text:
                text = "\n".join(part for part in (text, persistent_text) if part)
        return text
