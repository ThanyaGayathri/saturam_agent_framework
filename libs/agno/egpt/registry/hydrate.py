"""Hydration support for DB-defined components (EGPT-2.5).

This module holds the pieces that sit between a stored component config and a live
Agent/Team/Workflow:

- ComponentConfigCache: a bounded, TTL'd LRU of validated config rows, keyed by
  (db, tenant, component id, version selector). It caches the *row* returned by
  ``db.get_config`` -- not the built instance -- so ``from_dict`` re-runs per request
  (cheap: constructors are attribute assignment) and the capability allow-list re-check
  (EGPT-2.3), which sits after the read in every getter, runs on cache hits and misses
  alike.
- HydrationError / NoPublishedVersionError: raised when a stored config exists but
  cannot be turned into a live component, so callers can distinguish "not found" (None)
  from "found but broken" instead of both collapsing into a 404.

Invalidation doctrine (mirrors ServiceAccountVerifier's cache): the worker that
processes a component mutation evicts its entries immediately via ``invalidate()``;
other workers -- and writers that bypass the components API, such as ``Agent.save`` or
the studio toolkit -- converge as entries age out within the TTL. TTL <= 0 disables
the cache entirely. ``invalidate()`` remains the seam an event-driven invalidation would
call; EGPT-2.6 shipped the lifecycle audit trail and deliberately left cache mechanics
alone, so that replacement is still unbuilt.

``get_links`` and ``load_component_graph`` reads are not cached in EGPT-2.5 -- they are
one call per hydration. The win here is the per-row ``get_config`` fan-out (one call
per team member / workflow step).
"""

import copy
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple, Union

__all__ = [
    "ComponentConfigCache",
    "HydrationError",
    "NoPublishedVersionError",
    "fetch_config_row",
    "require_published",
]

# The version selector is the *requested* version: an int, or None meaning "current".
# None-selector entries can go stale when current_version moves (publish/rollback);
# explicit-version entries only when a draft is edited in place. Both are bounded by
# the TTL and by explicit invalidation, so a uniform TTL keeps the semantics simple.
_CacheKey = Tuple[Union[int, str], Optional[str], str, Optional[int]]


class HydrationError(Exception):
    """A stored component config exists but could not be hydrated into a live object.

    Distinct from a missing component (the getters return None for that): this means the
    row was found -- and passed any tenant predicate -- but building the component failed.
    Surfacing it instead of returning None keeps "broken config" from rendering as a 404.
    """

    def __init__(self, message: str, *, component_id: str, component_type: str):
        super().__init__(message)
        self.component_id = component_id
        self.component_type = component_type


class NoPublishedVersionError(HydrationError):
    """Published-only hydration was requested but the selected config is not published."""


class ComponentConfigCache:
    """Bounded, TTL'd LRU of config rows from ``db.get_config``, keyed per db and tenant.

    The cached value is the raw row dict (config + tenant_id + version + stage). Callers
    MUST treat a returned row as shared and copy before mutating -- the read-through
    helpers in the component getters deep-copy on every hit, because ``from_dict``
    implementations shallow-copy the config and hand nested dicts to constructors.
    """

    def __init__(self, ttl_seconds: int, max_entries: int = 2048):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: "OrderedDict[_CacheKey, Tuple[Dict[str, Any], float]]" = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0

    @staticmethod
    def _db_key(db: Any) -> Union[int, str]:
        # A Registry (and therefore this cache) can outlive or span DB handles; the id
        # attribute is stable across clones of the same logical database, id() is the
        # fallback for handles without one.
        return getattr(db, "id", None) or id(db)

    def make_key(
        self,
        db: Any,
        component_id: str,
        version: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> _CacheKey:
        return (self._db_key(db), tenant_id, component_id, version)

    def get(self, key: _CacheKey) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        entry = self._entries.get(key)
        if entry is None:
            return None
        row, cached_at = entry
        if time.monotonic() - cached_at > self.ttl_seconds:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return row

    def put(self, key: _CacheKey, row: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        self._entries[key] = (row, time.monotonic())
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def invalidate(
        self,
        component_id: str,
        tenant_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> int:
        """Drop cached rows for a component. Returns the number of entries evicted.

        With only component_id, drops every entry for that component across all tenants,
        versions and db handles -- the safe default for a mutation. tenant_id / version
        narrow the sweep. The scan is O(len(cache)) over at most max_entries; mutations
        are rare relative to reads.
        """
        matched = [
            key
            for key in self._entries
            if key[2] == component_id
            and (tenant_id is None or key[1] == tenant_id)
            and (version is None or key[3] == version)
        ]
        for key in matched:
            self._entries.pop(key, None)
        return len(matched)

    def clear(self) -> None:
        self._entries.clear()


def fetch_config_row(
    db: Any,
    component_id: str,
    component_type: str,
    *,
    version: Optional[int] = None,
    label: Optional[str] = None,
    tenant_id: Optional[str] = None,
    registry: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Read a component's config row through the registry's cache, if one is parked there.

    The single read-through path shared by the component getters (get_agent_by_id,
    get_team_by_id, get_workflow_by_id and the *.load classmethods). Returns the row dict
    from ``db.get_config`` or None when the component does not exist -- for a tenant-scoped
    call, a foreign tenant's component is indistinguishable from a missing one.

    Every row that crosses this boundary is a private copy: cache hits AND the row stored on
    a miss are deep-copied, because from_dict implementations shallow-copy the config and
    hand nested dicts straight to constructors -- returning a shared row would let one
    request's hydration mutate another's (or another tenant's) cached config.

    Labeled lookups bypass the cache: the cache key is the version selector, and a label is
    a mutable alias to a version, so caching under it could serve a stale target. Labels are
    an authoring flow, not a runtime hot path.

    When the registry carries hydrate_published_only=True, a row whose stage is not
    "published" raises NoPublishedVersionError instead of being returned -- including rows
    selected by explicit version: production should not run drafts at all.
    """
    # isinstance rather than truthiness: registry duck-types here (tests pass mocks, and a
    # bare programmatic caller may pass anything), so only a real cache is a cache.
    cache = getattr(registry, "component_config_cache", None)
    if not isinstance(cache, ComponentConfigCache) or label is not None:
        cache = None

    row: Optional[Dict[str, Any]] = None
    key = None
    if cache is not None:
        key = cache.make_key(db, component_id, version=version, tenant_id=tenant_id)
        cached = cache.get(key)
        if cached is not None:
            row = copy.deepcopy(cached)

    if row is None:
        row = db.get_config(component_id=component_id, version=version, label=label, tenant_id=tenant_id)
        if row is None:
            return None
        if cache is not None and key is not None:
            cache.put(key, copy.deepcopy(row))

    require_published(row, component_id, component_type, registry=registry)

    return row


def require_published(
    row: Dict[str, Any],
    component_id: str,
    component_type: str,
    *,
    registry: Optional[Any] = None,
) -> None:
    """Raise NoPublishedVersionError if published-only hydration refuses this config row.

    A no-op unless the registry carries hydrate_published_only=True. Split out of
    fetch_config_row for the read paths that do not go through it -- Team.load reads whole
    graphs via load_component_graph, and must apply the same rule to the root and every
    member config the graph resolved.
    """
    # `is True` rather than truthiness: registry duck-types here, and anything but an
    # explicit opt-in must not flip a refusal path on.
    if getattr(registry, "hydrate_published_only", False) is True and row.get("stage") != "published":
        raise NoPublishedVersionError(
            f"Component '{component_id}' resolved to a config that is not published "
            f"(stage={row.get('stage')!r}) and hydration is configured published-only. "
            "Publish a version of this component to make it runnable.",
            component_id=component_id,
            component_type=component_type,
        )
