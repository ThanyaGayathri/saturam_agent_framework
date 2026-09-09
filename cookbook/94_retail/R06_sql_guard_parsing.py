"""R6 -- the SQL guard's classification rules, read beside the statements they refuse.

G3's gap: `SQLTools.run_sql` executes whatever it is handed -- no read-only check, no
statement timeout, and a row limit the calling model controls. This script exercises the
guard's classification logic in isolation: which statements it refuses, why, and the three
cases a naive table-name scan would get wrong (a CTE alias, a table-valued function call in
FROM position, and a leftover unassembled `{predicate}` template placeholder).

`R06_sql_guard_live.py` is the other half -- the same guard, wired onto a real agent and a
real warehouse, producing the delivery plan's literal evidence (delete refused, out-of-scope
read refused, a million-row request capped, a prompt-injection paste refused).

Needs no services and no model.

Run:
    .venv/bin/python cookbook/94_retail/R06_sql_guard_parsing.py
"""

from egpt.exceptions import SQLGuardRefusal
from egpt.guardrails.sql_guard import SQLGuard


class Allowlist:
    """A frozen table set -- the shape product/egpt_retail/app/sql_guard.py's
    SemanticTableAllowlist wraps a real SemanticRead into."""

    def __init__(self, *tables):
        self._tables = frozenset(tables)

    def allowed_tables(self):
        return self._tables

    async def aallowed_tables(self):
        return self._tables


def guard(*allowed, row_cap=1000):
    return SQLGuard(allowlist=Allowlist(*allowed), row_cap=row_cap)


def show(label, sql, *, allowed):
    g = guard(*allowed)
    try:
        tables = g.check(sql)
        print(f"  ALLOWED  {label:<45} tables={tables}")
    except SQLGuardRefusal as e:
        print(f"  REFUSED  {label:<45} [{e.check}] {e.message}")


print("R6 -- SQL guard classification (G3)")
print("=" * 78)

print("\n1. Reads pass, writes refuse")
print("-" * 78)
for sql in [
    "SELECT * FROM orders",
    "DELETE FROM orders",
    "UPDATE orders SET status = 'x'",
    "INSERT INTO orders VALUES (1)",
    "DROP TABLE orders",
    "TRUNCATE orders",
]:
    show(sql, sql, allowed=["orders"])

print("\n2. UNION is still a read -- exp.Select alone would misclassify this")
print("-" * 78)
show(
    "SELECT id FROM orders UNION SELECT id FROM order_items",
    "SELECT id FROM orders UNION SELECT id FROM order_items",
    allowed=["orders", "order_items"],
)

print("\n3. Table allow-list -- names exactly what's missing")
print("-" * 78)
show(
    "SELECT * FROM customers (not allowed)",
    "SELECT * FROM customers",
    allowed=["orders"],
)

print("\n4. The case a naive table-name scan gets wrong: a CTE alias is not a table")
print("-" * 78)
show(
    "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
    "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
    allowed=["orders"],
)
show(
    "same CTE, but orders is not allowed",
    "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent",
    allowed=[],
)

print("\n5. Joins and correlated subqueries -- every referenced table is checked")
print("-" * 78)
show(
    "JOIN across two allowed tables",
    "SELECT o.id, i.id FROM orders o JOIN order_items i ON o.id = i.order_id",
    allowed=["orders", "order_items"],
)
show(
    "correlated subquery reaching a disallowed table",
    "SELECT * FROM orders WHERE customer_id IN "
    "(SELECT customer_id FROM customers WHERE segment = 'vip')",
    allowed=["orders"],
)

print("\n6. A function call in table position is not a table")
print("-" * 78)
show(
    "FROM pg_read_file(...)",
    "SELECT * FROM pg_read_file('/etc/passwd')",
    allowed=["orders"],
)
show(
    "FROM generate_series(...)",
    "SELECT * FROM generate_series(1, 1000000000)",
    allowed=["orders"],
)

print("\n7. Stacked statements refuse before either half is inspected")
print("-" * 78)
show(
    "SELECT 1; DROP TABLE orders;",
    "SELECT 1; DROP TABLE orders;",
    allowed=["orders"],
)

print("\n8. The assembled-statement rule (CHANGELOG.md: parse the assembled statement,")
print("   never the template)")
print("-" * 78)
template = "SELECT * FROM orders WHERE {predicate}"
show("unassembled template, {predicate} still literal", template, allowed=["orders"])
assembled = template.format(predicate="age BETWEEN 18 AND 25")
show(f"assembled: {assembled!r}", assembled, allowed=["orders"])

print("\n9. The row cap overrides what the model asks for, regardless of the request")
print("-" * 78)
g = guard("orders", row_cap=1000)
for requested in (None, 10, 10_000_000):
    effective = g.effective_limit(requested)
    print(f"  requested={requested!r:<12} -> effective_limit={effective}")

print()
print("All nine sections ran without a database or a model.")
