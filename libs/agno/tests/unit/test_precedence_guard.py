"""Pin the two aicoe-tracker#156 decisions, and ban the mis-parse pattern repo-wide.

``A or B if C else D`` parses as ``(A or B) if C else D`` -- the conditional binds
loosest. Seven occurrences of that shape were live in the tree; five silently discarded
a caller-supplied value (four id seeds fixed by #153/PR #36/PR #37, plus the salesforce
and calendar cases pinned below), and two were reachability-safe only by accident. The
guard at the bottom rejects the *unparenthesized* form everywhere, so the next
occurrence cannot merge; explicit parentheses -- either grouping -- satisfy it, which is
also how the two deliberate cases (``utils/timer.py``, ``os/routers/teams/schema.py``)
are recorded.
"""

import ast
import pathlib

import pytest

# --------------------------------------------------------------------------- #
# The two decided call sites (aicoe-tracker#156)
# --------------------------------------------------------------------------- #


class TestSalesforceInstructionsDecision:
    """Decision: ``add_instructions`` gates only the built-in default."""

    @pytest.fixture
    def cls(self):
        pytest.importorskip("simple_salesforce", reason="SalesforceTools imports its SDK at module level")
        from egpt.integrations.tools.salesforce.salesforce import SalesforceTools

        return SalesforceTools

    def _make(self, cls, **kw):
        # No auth is exercised: instructions are assigned before any connection.
        try:
            return cls(username="u", password="p", security_token="t", **kw)
        except Exception as exc:  # pragma: no cover - construction may validate creds
            pytest.skip(f"SalesforceTools could not construct without live creds: {exc}")

    def test_caller_instructions_survive_add_instructions_false(self, cls):
        tools = self._make(cls, instructions="custom", add_instructions=False)
        assert tools.instructions == "custom", (
            "explicit instructions= must always win; add_instructions gates only the built-in default"
        )

    def test_default_still_gated(self, cls):
        assert self._make(cls, add_instructions=False).instructions is None
        assert self._make(cls, add_instructions=True).instructions is not None


class TestCalendarDelegatedUserDecision:
    """Decision: an explicit ``delegated_user=`` is always honoured."""

    @pytest.fixture
    def cls(self):
        pytest.importorskip("googleapiclient", reason="the calendar provider imports Google SDKs at module level")
        from egpt.context.calendar.provider import CalendarContextProvider

        return CalendarContextProvider

    def test_explicit_delegated_user_survives_without_sa_path(self, cls, monkeypatch):
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
        monkeypatch.delenv("GOOGLE_DELEGATED_USER", raising=False)
        provider = cls(delegated_user="user@example.com")
        assert provider._delegated_user == "user@example.com", (
            "an explicitly passed delegated_user was silently discarded whenever no service-account path was configured"
        )

    def test_env_fallback_still_gated_on_sa_path(self, cls, monkeypatch):
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_FILE", raising=False)
        monkeypatch.setenv("GOOGLE_DELEGATED_USER", "env@example.com")
        assert cls()._delegated_user is None, "the env fallback stays gated on the SA path"


# --------------------------------------------------------------------------- #
# Repo-wide guard
# --------------------------------------------------------------------------- #


def _egpt_root() -> pathlib.Path:
    root = pathlib.Path(__file__).resolve().parents[2] / "egpt"
    assert root.is_dir(), f"egpt package root not found: {root}"
    return root


def test_no_unparenthesized_boolop_inside_a_conditional():
    """Reject ``A or B if C else D`` without explicit grouping, everywhere under egpt/.

    Parentheses do not exist in the AST, so the check reads the source: an ``IfExp``
    whose ``body`` is a ``BoolOp`` is allowed only when the character before the BoolOp
    is ``(`` -- i.e. the grouping is explicit. Both groupings are accepted; relying on
    the non-obvious precedence rule is what is banned. This single check would have
    caught all seven historical occurrences of the family (#153, PR #36, PR #37,
    aicoe-tracker#156).
    """
    offenders = []
    checked_files = 0

    for path in sorted(_egpt_root().rglob("*.py")):
        src = path.read_text()
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError:  # pragma: no cover
            continue
        checked_files += 1
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.IfExp) and isinstance(node.body, ast.BoolOp)):
                continue
            body = node.body
            line = lines[body.lineno - 1]
            before = line[: body.col_offset].rstrip()
            if not before.endswith("("):
                offenders.append(f"{path.relative_to(_egpt_root().parent)}:{body.lineno}")

    assert checked_files > 500, f"only {checked_files} files scanned; the root is probably stale and this guard vacuous"
    assert not offenders, (
        "unparenthesized `A or B if C else D` found -- Python parses this as "
        "`(A or B) if C else D`, which has silently discarded caller-supplied values "
        f"five times in this codebase. Add explicit parentheses: {offenders}"
    )
