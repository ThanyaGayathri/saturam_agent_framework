"""The feature frames the at-risk score reads (K26), as SQL over the quote fact, leak-free.

Two frames, one shape. **Training**: every closed quote, snapshotted at the ages 0, 7,
14, 21 and 28 days at which it was still open, with every feature computed from what was
known at the quote's own date -- the account's history strictly before it -- and the label
"closed won within the category's usual time to close". **Scoring**: every open quote at
the run's date, at its current age, the same features. The same SQL builds both, with the
snapshot ages as the only difference, so a feature can never mean one thing in training
and another at scoring.
"""

from __future__ import annotations

from typing import List, Sequence

__all__ = [
    "CATEGORICAL",
    "NUMERIC",
    "SNAPSHOT_AGES",
    "scoring_sql",
    "training_sql",
]

SNAPSHOT_AGES: Sequence[int] = (0, 7, 14, 21, 28)

NUMERIC: List[str] = [
    "age_days",
    "age_ratio",  # age over the category's usual time to close
    "deal_value_usd",
    "quantity",
    "prior_quotes",
    "prior_win_rate",
    "days_since_prev_quote",
    "prior_won_value",
    "live_chat_buy",
]
CATEGORICAL: List[str] = [
    "pim_category",
    "sales_rep_territory",
    "customer_segment",
    "quote_source",
    "audience_channel",
    "last_lost_reason",
]

_USUAL = (
    "(SELECT pim_category, PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY days_to_close) AS "
    "usual_days FROM {table} WHERE days_to_close IS NOT NULL AND closed_date <= CAST(:as_of "
    "AS date) GROUP BY pim_category)"
)

_HISTORY = """
  COUNT(*) OVER w AS prior_quotes,
  COALESCE(SUM(CASE WHEN q.pipeline_stage = 'Closed Won' THEN 1 ELSE 0 END) OVER w, 0) AS prior_won,
  COALESCE(SUM(CASE WHEN q.pipeline_stage = 'Closed Won' THEN q.deal_value_usd ELSE 0 END) OVER w, 0) AS prior_won_value,
  LAG(q.quote_date) OVER (PARTITION BY q.customer_id ORDER BY q.quote_date, q.quote_id) AS prev_quote_date,
  (SELECT l.lost_reason FROM {table} l WHERE l.customer_id = q.customer_id AND l.pipeline_stage = 'Closed Lost'
     AND l.closed_date < q.quote_date ORDER BY l.closed_date DESC LIMIT 1) AS last_lost_reason
"""

_BASE = """
WITH usual AS {usual},
quotes AS (
  SELECT q.quote_id, q.customer_id, q.customer_name, q.quote_date, q.closed_date, q.days_to_close,
    q.pipeline_stage, q.pim_category, q.brand, q.sales_rep_territory, q.sales_rep_name,
    q.customer_segment, q.quote_source, q.audience_channel, q.deal_value_usd, q.quantity,
    CASE WHEN q.live_chat_buy_inquiry_flag THEN 1 ELSE 0 END AS live_chat_buy,
    {history}
  FROM {table} q
  WHERE q.quote_date <= CAST(:as_of AS date)
  WINDOW w AS (PARTITION BY q.customer_id ORDER BY q.quote_date, q.quote_id
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
)
"""


def _select(age_expr: str, extra_where: str) -> str:
    return f"""
SELECT s.quote_id, s.customer_id, s.customer_name, s.quote_date, s.pipeline_stage,
  s.pim_category, s.brand, s.sales_rep_territory, s.sales_rep_name, s.customer_segment,
  s.quote_source, s.audience_channel, s.deal_value_usd, s.quantity, s.live_chat_buy,
  s.prior_quotes, s.prior_won, s.prior_won_value,
  CASE WHEN s.prior_quotes > 0 THEN CAST(s.prior_won AS float) / s.prior_quotes ELSE 0 END AS prior_win_rate,
  COALESCE(s.quote_date - s.prev_quote_date, 0) AS days_since_prev_quote,
  s.last_lost_reason, u.usual_days,
  {age_expr} AS age_days,
  CASE WHEN u.usual_days > 0 THEN ({age_expr}) / u.usual_days ELSE 0 END AS age_ratio,
  s.days_to_close, s.closed_date
FROM quotes s JOIN usual u ON u.pim_category = s.pim_category
WHERE {extra_where}
"""


def training_sql(table: str) -> str:
    """Closed quotes at each snapshot age they were still open, labelled downstream by
    ``closed won AND days_to_close <= usual_days``."""
    base = _BASE.format(
        usual=_USUAL.format(table=table),
        history=_HISTORY.format(table=table),
        table=table,
    )
    ages = ", ".join(f"({a})" for a in SNAPSHOT_AGES)
    # Only quotes that genuinely closed by the run's date: the fact carries a closed_date
    # on open quotes too (a planned date), which must not label anything.
    body = _select(
        "a.age",
        "s.pipeline_stage IN ('Closed Won', 'Closed Lost') AND s.closed_date <= CAST(:as_of AS date) "
        "AND s.days_to_close IS NOT NULL AND a.age < s.days_to_close",
    )
    body = body.replace(
        "FROM quotes s JOIN usual u",
        f"FROM quotes s CROSS JOIN (VALUES {ages}) AS a(age) JOIN usual u",
    )
    return base + body


def scoring_sql(table: str) -> str:
    """Every open quote at the run's date, at its current age."""
    base = _BASE.format(
        usual=_USUAL.format(table=table),
        history=_HISTORY.format(table=table),
        table=table,
    )
    return base + _select(
        "(CAST(:as_of AS date) - s.quote_date)",
        "s.pipeline_stage NOT IN ('Closed Won', 'Closed Lost')",
    )
