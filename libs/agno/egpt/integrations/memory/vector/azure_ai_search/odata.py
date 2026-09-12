"""Every ``$filter`` fragment the Azure AI Search memory substrate can emit (AZ-B).

Plays the role ``pgvector/sql.py`` plays for the Postgres backend: no filter string is composed at
a call site, so a slot added to :class:`~egpt.memory.types.Scope` or
:class:`~egpt.memory.types.MemoryQuery` reaches every predicate through the one function that
builds it, rather than through however many call sites someone remembered to update by hand.
``pgvector/sql.py``'s own docstring states the reason directly: tenant isolation here is a
predicate chosen by a value in the filter, like any other predicate, so a forgotten scope clause is
a cross-scope read whether it is missing from SQL or from OData.

There is no bind-parameter mechanism in Azure AI Search's ``$filter`` the way SQLAlchemy's ``text``
binds protect pgvector's statements -- a filter is a single string sent as a query-string value.
:func:`escape_odata_literal` is this module's equivalent of a bind: every scope value and query
literal that reaches a filter clause goes through it, so a value containing ``'`` cannot terminate
the literal early and inject a second clause.
"""

from typing import List, Optional, Sequence

from egpt.memory.types import Scope, ValidWindow, require_scope

__all__ = [
    "CURRENT_BELIEF_FILTER",
    "escape_odata_literal",
    "scope_filter",
    "search_filter",
    "tier_filter",
    "window_filter",
]

#: OData clause for "current belief only" (``expired_at IS NULL`` in pgvector's terms) -- applied
#: to every **ranked** read, exactly as :func:`~egpt.integrations.memory.vector.pgvector.sql.search_predicate`
#: applies its own ``_CURRENT_BELIEF`` constant. Not applied to ``scan`` or ``get_record``, which
#: are identity/enumeration reads rather than ranked ones -- see those methods' own docstrings on
#: :class:`~egpt.memory.substrates.VectorSubstrate` for why a superseded row must still be reachable
#: there.
CURRENT_BELIEF_FILTER = "expired_at eq null"


def escape_odata_literal(value: str) -> str:
    """Escape a string for use inside a single-quoted OData literal.

    OData escapes an embedded ``'`` by doubling it -- the same rule SQL string literals use, just
    expressed without a driver-level bind parameter to lean on. Every scope slot and every query
    literal this module writes into a filter clause passes through here first, so a value
    containing ``'`` cannot close the literal early and splice a second clause onto the filter.
    """
    return value.replace("'", "''")


def scope_filter(scope: Scope, *, method: str) -> str:
    """The conjunctive scope filter: ``tenant_id`` always, plus every other slot the scope sets.

    Mirrors :func:`~egpt.integrations.memory.vector.pgvector.sql.scope_predicate` exactly: an unset
    slot (``None``) contributes no clause, because ``None`` means "any" per
    :class:`~egpt.memory.substrates.VectorSubstrate`'s own docstring -- narrowing on an absent slot
    would silently exclude every record that has never had that slot set, which is not what "any"
    promises.

    ``require_scope`` runs here as well as at every port method that calls this, for the same
    reason ``pgvector/sql.py`` runs it in both places: a statement (here, a filter) built from this
    function later inherits the guard automatically, rather than depending on every future call
    site remembering to check first.
    """
    require_scope(scope, method=method)
    clauses = [f"tenant_id eq '{escape_odata_literal(scope.tenant_id)}'"]
    for slot in ("agent_id", "user_id", "team_id", "session_id"):
        value = getattr(scope, slot)
        if value is not None:
            clauses.append(f"{slot} eq '{escape_odata_literal(value)}'")
    return " and ".join(clauses)


def tier_filter(tiers: Sequence[str]) -> str:
    """``tier`` matches one of ``tiers``, as a disjunction -- OData has no ``IN``.

    ``tiers`` must be non-empty: the substrate resolves "no tiers to search" to an empty result
    before building any filter at all (mirroring
    ``PgVectorMemorySubstrate._searchable``), so an empty sequence reaching here is a bug in that
    caller, not a request meaning "no tier restriction" -- a vacuous ``or`` would be neither, so
    this refuses rather than emit one.
    """
    if not tiers:
        raise ValueError("tier_filter requires at least one tier; an empty tier set means no search, not any tier.")
    return "(" + " or ".join(f"tier eq '{escape_odata_literal(tier)}'" for tier in tiers) + ")"


def window_filter(window: Optional[ValidWindow]) -> Optional[str]:
    """The valid-time overlap filter, or ``None`` when no window is asked for.

    The OData half of :meth:`~egpt.memory.types.ValidWindow.overlaps`, and it must stay the same
    rule as the Python form and as ``pgvector.sql.window_predicate``'s SQL form: a window that means
    one thing in the stored filter and another on the merged result drops a record on one path and
    keeps it on the other, which reads as a flaky search rather than as a bug.

    An unset **record** bound (``t_valid``/``t_invalid`` both nullable in the index) is open at
    that end, so ``eq null`` passes it -- excluding those instead would silently drop every record
    whose valid time was never stamped.
    """
    if window is None:
        return None
    clauses: List[str] = []
    if window.valid_from is not None:
        clauses.append(f"(t_invalid eq null or t_invalid gt {window.valid_from})")
    if window.valid_to is not None:
        clauses.append(f"(t_valid eq null or t_valid lt {window.valid_to})")
    return " and ".join(clauses) if clauses else None


def search_filter(scope: Scope, tiers: Sequence[str], window: Optional[ValidWindow], *, method: str) -> str:
    """Every predicate a **ranked** read applies, composed once so a leg cannot omit one.

    Scope, tier, current-belief, and the valid-time window -- assembled here rather than at each of
    the vector and lexical legs, for the reason
    ``pgvector.sql.search_predicate``'s own docstring gives: a leg that built its own filter was
    correct the day it was written and one clause short the next time one was added, and two legs
    that disagree about which rows exist do not produce a ranking discrepancy, they produce a leak.
    Both :meth:`~egpt.integrations.memory.vector.azure_ai_search.azure_ai_search.AzureAISearchMemorySubstrate.search`
    and its lexical-leg sibling in ``search_candidates`` call this, never build their own.
    """
    clauses = [scope_filter(scope, method=method), tier_filter(tiers), CURRENT_BELIEF_FILTER]
    window_clause = window_filter(window)
    if window_clause:
        clauses.append(window_clause)
    return " and ".join(clauses)
