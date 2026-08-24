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
is a flat set of fields, not a triple or a node with edges. Neither substrate port offers "get by
id" (only ``search``/``scan``), so :meth:`SemanticProfileStore.get` reads it back through
``scan`` filtered to that one id -- an **O(N) walk of the scope's whole SEMANTIC tier**, which
also holds every ordinary fact (SM-S1 #118). ``scope.user_id`` narrows *which tenant/user bucket*
is scanned, not how many rows within it are read; a user with many accumulated facts pays this
scan on every consolidation pass. A true point lookup needs a
``VectorSubstrate.get_record(scope, id)`` port method that does not exist yet -- tracked as
aicoe-tracker #187, not built here: it is a substrate-port change with implications for every
other caller of this seam, a decision this ticket does not own.

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
    MemoryRecord,
    MemoryScopeRequiredError,
    Scope,
    WriteReceipt,
    escape_key_slot,
    require_scope,
)
from egpt.utils.log import log_debug

__all__ = ["SemanticProfileStore", "profile_record_id"]

#: Marks a vector row as this store's, so a scan over the SEMANTIC tier -- which also holds
#: ordinary facts (SM-S1 #118) -- can tell the two apart without guessing from the id shape alone.
_RECORD_KIND = "user_profile"

#: Above this many scanned rows, :meth:`SemanticProfileStore.get`/:meth:`aget` log rather than
#: stay silent -- an interim signal for the O(N) cost this module's docstring already names,
#: cheap enough to carry until aicoe-tracker #187 lands a real point lookup.
_SCAN_WARNING_THRESHOLD = 500


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
        below passes :func:`_profile_scope`'s output, never the raw one."""
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
        no vector substrate wired, or a profile with no fields ``to_dict_safe`` could serialise."""
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

    def get(self, scope: Scope) -> Optional[Any]:
        """The user's profile, or ``None`` when unwired, never written, or unparseable."""
        require_scope(scope, method="get")
        if self.vector is None:
            return None
        narrowed = _profile_scope(scope)
        record_id = profile_record_id(narrowed)
        rows = self.vector.scan(narrowed, MemoryTier.SEMANTIC)
        if len(rows) > _SCAN_WARNING_THRESHOLD:
            log_debug(
                f"SemanticProfileStore.get: scanned {len(rows)} SEMANTIC-tier rows for one "
                f"profile lookup (user_id={narrowed.user_id}); see aicoe-tracker #187."
            )
        for record in rows:
            if record.id == record_id and record.metadata.get("record_kind") == _RECORD_KIND:
                return from_dict_safe(self.schema, record.metadata.get("profile"))
        return None

    async def aget(self, scope: Scope) -> Optional[Any]:
        """Async twin of :meth:`get`, making the same decisions."""
        require_scope(scope, method="aget")
        if self.vector is None:
            return None
        narrowed = _profile_scope(scope)
        record_id = profile_record_id(narrowed)
        rows = await self.vector.ascan(narrowed, MemoryTier.SEMANTIC)
        if len(rows) > _SCAN_WARNING_THRESHOLD:
            log_debug(
                f"SemanticProfileStore.aget: scanned {len(rows)} SEMANTIC-tier rows for one "
                f"profile lookup (user_id={narrowed.user_id}); see aicoe-tracker #187."
            )
        for record in rows:
            if record.id == record_id and record.metadata.get("record_kind") == _RECORD_KIND:
                return from_dict_safe(self.schema, record.metadata.get("profile"))
        return None
