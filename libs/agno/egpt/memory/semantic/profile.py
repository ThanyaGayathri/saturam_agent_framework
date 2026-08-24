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
is a flat set of fields, not a triple or a node with edges, and neither substrate port offers a
plain "get by id"; :meth:`SemanticProfileStore.get` reads it back through ``scan`` filtered to
that one id, which degenerates to a point lookup once ``scope.user_id`` narrows the bucket.
"""

from typing import Any, Optional, Type

from egpt.learn.schemas import UserProfile
from egpt.learn.utils import from_dict_safe, to_dict_safe
from egpt.memory.substrates import VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryOperationError, MemoryRecord, Scope, WriteReceipt, require_scope

__all__ = ["SemanticProfileStore", "profile_record_id"]

#: Marks a vector row as this store's, so a scan over the SEMANTIC tier -- which also holds
#: ordinary facts (SM-S1 #118) -- can tell the two apart without guessing from the id shape alone.
_RECORD_KIND = "user_profile"


def profile_record_id(scope: Scope) -> str:
    """The deterministic id a user's profile row is upserted/read under.

    Requires ``scope.user_id``: a profile is per-user by definition, and ``require_scope`` alone
    does not enforce that -- it only refuses a blank tenant.
    """
    if not scope.user_id:
        raise MemoryOperationError(
            "A user profile requires scope.user_id; a tenant-only scope has no user to key it by.",
            field_name="user_id",
        )
    return f"{_RECORD_KIND}:{scope.tenant_id}:{scope.user_id}"


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
        record = self._record(scope, profile)
        if record is None:
            return None
        self.vector.upsert(scope, [record])
        return WriteReceipt(accepted=1, event_ids=[record.id], tiers_written=[MemoryTier.SEMANTIC])

    async def awrite(self, scope: Scope, profile: Any) -> Optional[WriteReceipt]:
        """Async twin of :meth:`write`, making the same decisions."""
        require_scope(scope, method="awrite")
        if self.vector is None:
            return None
        record = self._record(scope, profile)
        if record is None:
            return None
        await self.vector.aupsert(scope, [record])
        return WriteReceipt(accepted=1, event_ids=[record.id], tiers_written=[MemoryTier.SEMANTIC])

    def get(self, scope: Scope) -> Optional[Any]:
        """The user's profile, or ``None`` when unwired, never written, or unparseable."""
        require_scope(scope, method="get")
        if self.vector is None:
            return None
        record_id = profile_record_id(scope)
        for record in self.vector.scan(scope, MemoryTier.SEMANTIC):
            if record.id == record_id and record.metadata.get("record_kind") == _RECORD_KIND:
                return from_dict_safe(self.schema, record.metadata.get("profile"))
        return None

    async def aget(self, scope: Scope) -> Optional[Any]:
        """Async twin of :meth:`get`, making the same decisions."""
        require_scope(scope, method="aget")
        if self.vector is None:
            return None
        record_id = profile_record_id(scope)
        for record in await self.vector.ascan(scope, MemoryTier.SEMANTIC):
            if record.id == record_id and record.metadata.get("record_kind") == _RECORD_KIND:
                return from_dict_safe(self.schema, record.metadata.get("profile"))
        return None
