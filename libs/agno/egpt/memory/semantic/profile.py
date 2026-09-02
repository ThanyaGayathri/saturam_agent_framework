"""The structured user-profile store: a per-user singleton on the semantic tier (SM-S3 #120).

The seed's ``egpt.learn.stores.user_profile.UserProfileStore`` persists through
``BaseDb.get_learning``/``upsert_learning``, which carries no ``tenant_id`` at all -- confirmed
against ``db/base.py``, and SM-C4 (#92) deliberately left it that way: retrofitting tenant
isolation onto 19 backends for one caller is worse than the store's current explicit
single-tenant refusal, and owns that as a separate ticket (#175). This store does not touch that
path. It reuses the seed's schema (:class:`~egpt.learn.schemas.UserProfile`) and field semantics,
but persists through :class:`~egpt.memory.substrates.VectorSubstrate`, so isolation comes from
the same conjunctive :class:`~egpt.memory.types.Scope` predicate every other semantic-tier read
and write already gets (SM-C4) rather than from an unguessable id.

There is exactly one row per user: :func:`profile_record_id` derives a deterministic id from
``scope.user_id``, and a write replaces it in place (``VectorSubstrate.upsert`` is upsert-by-id).
Unlike :class:`~egpt.memory.semantic.facts.SemanticFactStore`, there is no graph leg -- a profile
is a flat set of fields, not a triple or a node with edges. :meth:`SemanticProfileStore.get` reads
it back through :meth:`~egpt.memory.substrates.VectorSubstrate.get_record` (aicoe-tracker #198),
a real point lookup rather than a client-side filter over an enumeration -- SM-S3 (#120) originally
shipped this as a paginated ``scan`` filtered to one id, an O(N) walk of the scope's whole
SEMANTIC tier (which also holds every ordinary fact, SM-S1 #118) that paid its full row count on
every consolidation pass, since a profile is read on every one (PR #135 round 3 review). ``#198``
closed that: the substrate-port change was scoped as its own ticket rather than folded in here,
since it has implications for every other caller of this seam, not only this one.

**Every profile read/write operates on a scope narrowed to ``(tenant_id, user_id)`` only**
(:func:`_profile_scope`), regardless of what ``agent_id``/``team_id``/``session_id`` the caller's
``scope`` carries. A profile is per-user, not per-(user, agent) or per-(user, session) -- and
``VectorSubstrate``'s conjunctive scoping contract means a caller ``scope`` that sets any of those
three slots would otherwise read and write a *different* row than a caller that leaves them unset,
for the same tenant and user (QC round 2, PR #135). Narrowing once, at the top of every public
method, and threading the narrowed scope through id derivation, the stored record, and every
substrate call keeps them from drifting apart -- the bug this fixes was exactly two of those four
places using the raw ``scope`` and the other two not.
"""

from typing import Any, Optional, Type

from egpt.learn.schemas import UserProfile
from egpt.learn.utils import from_dict_safe, to_dict_safe
from egpt.memory.substrates import VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    MemoryOperationError,
    MemoryRecord,
    MemoryScopeRequiredError,
    Scope,
    WriteReceipt,
    escape_key_slot,
    require_scope,
)
from egpt.utils.log import log_warning

__all__ = ["SemanticProfileStore", "profile_record_id"]

#: Marks a vector row as this store's, so a lookup that lands on the right id but a foreign
#: scope's row shaped like a profile (or vice versa) can still be told apart without guessing
#: from the id shape alone (QC round 2, PR #135).
_RECORD_KIND = "user_profile"


def _profile_scope(scope: Scope) -> Scope:
    """Narrow ``scope`` to ``(tenant_id, user_id)`` -- see the module docstring for why every
    profile read/write must use this, never the raw caller ``scope``, from here on."""
    return Scope(tenant_id=scope.tenant_id, user_id=scope.user_id)


def profile_record_id(scope: Scope) -> str:
    """The deterministic id a user's profile row is upserted/read under.

    Requires ``scope.user_id``: a profile is per-user by definition, and ``require_scope`` alone
    does not enforce that -- it only refuses a blank tenant. Raises the same
    :class:`~egpt.memory.types.MemoryScopeRequiredError` every other "scope is missing a required
    slot" refusal on this seam raises, rather than a different type for this one case.

    Slots are percent-escaped and ``/``-separated via
    :func:`~egpt.memory.types.escape_key_slot`, matching :meth:`Scope.key`: an un-escaped
    separator inside a tenant- or user-supplied id is exactly the collision that function exists
    to prevent (its own docstring gives the worked example), and a second, differently-escaped
    id scheme here would just relocate the risk rather than remove it.

    Takes ``scope`` as given rather than narrowing it itself -- callers (:meth:`_record`,
    :meth:`SemanticProfileStore.get`/:meth:`aget`) already pass the output of
    :func:`_profile_scope`, and narrowing twice would hide a call site that forgot to.
    """
    if not scope.user_id:
        raise MemoryScopeRequiredError(
            "A user profile requires scope.user_id; a tenant-only scope has no user to key it by.",
            field_name="user_id",
            verb="profile_record_id",
        )
    return "/".join([_RECORD_KIND, escape_key_slot(scope.tenant_id), escape_key_slot(scope.user_id)])


class SemanticProfileStore:
    """Persist and retrieve one :class:`~egpt.learn.schemas.UserProfile` per user, tenant-scoped.

    ``vector`` may be ``None`` -- a graph-only deployment (SM-C5 #93) simply cannot serve a
    profile, matching how the rest of this package treats an unwired substrate: :meth:`write`
    and :meth:`get` become no-ops rather than raising, the same treatment ``SemanticFactStore``
    gives an unwired leg.
    """

    def __init__(self, vector: Optional[VectorSubstrate], schema: Type[Any] = UserProfile) -> None:
        self.vector = vector
        self.schema = schema

    def _record(self, scope: Scope, profile: Any) -> Optional[MemoryRecord]:
        """``scope`` here is already the narrowed ``(tenant_id, user_id)`` scope -- every caller
        below passes :func:`_profile_scope`'s output, never the raw one.

        Refuses a ``profile`` that is not an instance of ``self.schema`` rather than silently
        serialising whatever ``to_dict_safe`` can extract from it (QC round 4, PR #135) -- now
        meaningful because ``self.schema`` is the schema this store was actually built with
        (:class:`CoLocatedMemoryProvider` threads a wired ``profile_store``'s own schema through
        at construction), not always the base :class:`~egpt.learn.schemas.UserProfile`. Checking
        this *before* that fix would have caught nothing useful: every schema in practice
        subclasses ``UserProfile``, so an ``isinstance`` check against a store that never saw the
        subclass would still pass.
        """
        if not isinstance(profile, self.schema):
            raise MemoryOperationError(
                f"SemanticProfileStore is configured with schema={self.schema.__name__}, but got "
                f"a {type(profile).__name__}. A profile must be an instance of the store's own "
                "schema to serialise correctly.",
                field_name="profile",
            )
        # In practice unreachable for a well-formed instance of `self.schema`: `user_id` is a
        # required field on every real profile dataclass, so `to_dict_safe` never returns empty
        # once the `isinstance` check above has already passed (QC round 4, PR #135 -- the
        # `write`/`awrite` docstrings previously implied this guards against a legitimate empty
        # profile, which it does not). What it still catches: `to_dict_safe` itself raising and
        # being swallowed internally, for a `self.schema` whose fields aren't plain dataclass
        # fields `asdict`-compatible -- a schema-shape problem the `isinstance` check above cannot
        # see, since it only checks the *type*, not the *fields*.
        content = to_dict_safe(profile)
        if not content:
            return None
        return MemoryRecord(
            id=profile_record_id(scope),
            tier=MemoryTier.SEMANTIC,
            content=f"user profile for {scope.user_id}",
            scope=scope,
            metadata={"record_kind": _RECORD_KIND, "profile": content},
        )

    def write(self, scope: Scope, profile: Any) -> Optional[WriteReceipt]:
        """Upsert ``profile`` under ``scope``. Returns ``None`` when there is nothing to write --
        no vector substrate wired -- and raises :class:`~egpt.memory.types.MemoryOperationError`
        when ``profile`` is not an instance of ``self.schema`` (see :meth:`_record`)."""
        require_scope(scope, method="write")
        if self.vector is None:
            return None
        narrowed = _profile_scope(scope)
        record = self._record(narrowed, profile)
        if record is None:
            return None
        self.vector.upsert(narrowed, [record])
        return WriteReceipt(accepted=1, event_ids=[record.id], tiers_written=[MemoryTier.SEMANTIC])

    async def awrite(self, scope: Scope, profile: Any) -> Optional[WriteReceipt]:
        """Async twin of :meth:`write`, making the same decisions."""
        require_scope(scope, method="awrite")
        if self.vector is None:
            return None
        narrowed = _profile_scope(scope)
        record = self._record(narrowed, profile)
        if record is None:
            return None
        await self.vector.aupsert(narrowed, [record])
        return WriteReceipt(accepted=1, event_ids=[record.id], tiers_written=[MemoryTier.SEMANTIC])

    def _parse(self, narrowed: Scope, record: MemoryRecord) -> Optional[Any]:
        """Both cases -- "never written" and "written, but unparseable" -- return ``None`` to the
        caller (see :meth:`get`'s docstring); a corrupted row is not, on its own, grounds to raise
        out of a read path every consolidation pass depends on. ``get``/``aget`` staying silent
        does not mean *nothing* stays silent, though: a mismatched-schema or corrupted row is a
        data problem, not routine cache-miss volume, so it logs at ``warning`` (QC round 4, PR
        #135) -- loud enough that an operator watching normal logs sees it, without changing the
        return contract every existing caller (including this store's own tests) already relies on.
        """
        parsed = from_dict_safe(self.schema, record.metadata.get("profile"))
        if parsed is None:
            log_warning(
                f"SemanticProfileStore: matched profile row {record.id!r} for "
                f"user_id={narrowed.user_id!r} but from_dict_safe could not parse it -- "
                f"possibly corrupted or written under an incompatible schema."
            )
        return parsed

    def get(self, scope: Scope) -> Optional[Any]:
        """The user's profile, or ``None`` when unwired, never written, or unparseable.

        A real point lookup (:meth:`~egpt.memory.substrates.VectorSubstrate.get_record`,
        aicoe-tracker #198) rather than a scan -- see the module docstring for the O(N) cost this
        replaced. The ``record_kind`` check still runs after the lookup returns: the deterministic
        id alone cannot rule out a foreign-scope row that happens to collide on it (QC round 2,
        PR #135), and ``get_record`` answers by id within the given scope, not by content.

        No longer tier-scoped the way the removed ``scan(..., MemoryTier.SEMANTIC, ...)`` loop
        was -- ``get_record`` takes no ``tier`` (correct for a point-lookup port: ``id`` is the
        dedup key across a scope's whole corpus, not per-tier), so a profile-id row planted
        outside ``SEMANTIC`` would now resolve where it previously returned ``None``. Benign in
        practice: :meth:`_record` only ever writes ``MemoryTier.SEMANTIC``, so this store never
        produces such a row itself, and :data:`_RECORD_KIND` remains as the second guard against
        anything that did (round-2 review, PR #175).
        """
        require_scope(scope, method="get")
        if self.vector is None:
            return None
        narrowed = _profile_scope(scope)
        record = self.vector.get_record(narrowed, profile_record_id(narrowed))
        if record is None or record.metadata.get("record_kind") != _RECORD_KIND:
            return None
        return self._parse(narrowed, record)

    async def aget(self, scope: Scope) -> Optional[Any]:
        """Async twin of :meth:`get`, making the same decisions."""
        require_scope(scope, method="aget")
        if self.vector is None:
            return None
        narrowed = _profile_scope(scope)
        record = await self.vector.aget_record(narrowed, profile_record_id(narrowed))
        if record is None or record.metadata.get("record_kind") != _RECORD_KIND:
            return None
        return self._parse(narrowed, record)
