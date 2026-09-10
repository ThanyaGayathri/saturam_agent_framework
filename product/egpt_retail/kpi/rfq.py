"""The RFQ template's own two KPI definitions (R10, D12).

The API contract's example compiled plan (``paths/use-cases.yaml``) names them by identifier:
``rfq__quote_requests_by_brand`` and ``rfq__quote_to_deal_rate``, both at ``brand`` grain.
K12 (Lane K's KPI suggestion agent, P4) does not exist yet to propose them, and the RFQ use
case (D12, the first one, explain-only) needs a registry to read gold from regardless of
whether K12 has landed -- so these two ship with the template rather than waiting on it,
the same way K3's ontology ships core concepts a customer has not yet authored an overlay
for. A deployment that later runs K12 adopts proposals through K7's review path exactly as
any other KPI would; these two are not exempt from that path once it exists, they simply
predate it.

Neither declares a `{scope}` predicate parameter: a use case's own KPIs are not scoped by a
concept's accepted `sql_filter` the way a customer-authored formula might be, so there is
nothing for `materialise_gold`'s `_scope_filters` to bind and no concept mapping this module
needs to wait on.
"""

from __future__ import annotations

from typing import Tuple

from egpt_retail.contracts.kpi import KpiDefinition

__all__ = ["RFQ_KPI_DEFINITIONS", "QUOTE_REQUESTS_BY_BRAND", "QUOTE_TO_DEAL_RATE"]

#: Count of RFQ quote-request deals, one row per brand. A "quote request" is a deal carrying
#: the landing contract's known-dirty `quote_request_source_url` -- see
#: `silver.crm_deal.quote_request_source_url`'s own comment -- so this counts deals that
#: originated from an RFQ rather than every deal in the pipeline.
QUOTE_REQUESTS_BY_BRAND = KpiDefinition(
    identifier="rfq__quote_requests_by_brand",
    name="Quote requests by brand",
    description="Count of RFQ quote-request deals, one row per brand.",
    sql=(
        "SELECT brand, count(*) AS value FROM crm_deal "
        "WHERE quote_request_source_url IS NOT NULL GROUP BY brand"
    ),
    grain="brand",
    business_area="sales",
    source_tables=frozenset({"crm_deal"}),
)

#: Share of RFQ quote requests that reached a recorded deal-stage transition, one row per
#: brand. `crm_deal_stage_history.deal_id` references `crm_deal.object_id` (its own comment
#: says so) -- not `crm_deal.deal_id`, a column silver does not have.
QUOTE_TO_DEAL_RATE = KpiDefinition(
    identifier="rfq__quote_to_deal_rate",
    name="Quote-to-deal conversion rate",
    description=(
        "Share of RFQ quote requests with at least one recorded deal-stage transition, "
        "one row per brand."
    ),
    sql=(
        "SELECT d.brand AS brand, "
        "(count(*) FILTER (WHERE h.deal_id IS NOT NULL))::numeric / NULLIF(count(*), 0) AS value "
        "FROM crm_deal d LEFT JOIN crm_deal_stage_history h ON h.deal_id = d.object_id "
        "WHERE d.quote_request_source_url IS NOT NULL GROUP BY d.brand"
    ),
    grain="brand",
    business_area="sales",
    source_tables=frozenset({"crm_deal", "crm_deal_stage_history"}),
)

#: Both, in the order the API contract's example plan reads them.
RFQ_KPI_DEFINITIONS: Tuple[KpiDefinition, ...] = (
    QUOTE_REQUESTS_BY_BRAND,
    QUOTE_TO_DEAL_RATE,
)
