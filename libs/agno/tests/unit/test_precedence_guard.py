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
from unittest.mock import MagicMock

import pytest

# --------------------------------------------------------------------------- #
# The two decided call sites (aicoe-tracker#156)
# --------------------------------------------------------------------------- #


class TestSalesforceInstructionsDecision:
    """Decision: ``add_instructions`` gates only the built-in default."""

    @pytest.fixture
    def cls(self, monkeypatch):
        pytest.importorskip("simple_salesforce", reason="SalesforceTools imports its SDK at module level")
        import egpt.integrations.tools.salesforce.salesforce as mod

        # Stub the SDK client: SalesforceTools.__init__ performs a real SOAP login at
        # construction, so without this the test issued an outbound request to
        # login.salesforce.com on every CI run and then silently skipped -- the
        # assertions below had never executed anywhere. self.instructions is assigned
        # before any client construction, so the stub changes nothing under test.
        monkeypatch.setattr(mod, "Salesforce", lambda *a, **k: MagicMock())
        return mod.SalesforceTools

    def _make(self, cls, **kw):
        return cls(username="u", password="p", security_token="t", **kw)

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
        from egpt.context.calendar.provider import GoogleCalendarContextProvider

        return GoogleCalendarContextProvider

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

    def _line_offsets(src):
        offsets, total = [0], 0
        for line in src.splitlines(keepends=True):
            total += len(line)
            offsets.append(total)
        return offsets

    for path in sorted(_egpt_root().rglob("*.py")):
        src = path.read_text()
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError:  # pragma: no cover
            continue
        checked_files += 1
        offsets = _line_offsets(src)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.IfExp) and isinstance(node.body, ast.BoolOp)):
                continue
            body = node.body
            start = offsets[body.lineno - 1] + body.col_offset
            end = offsets[body.end_lineno - 1] + body.end_col_offset
            # Both delimiters are required: checking only the character before the BoolOp
            # let a *call* parenthesis satisfy the guard -- `foo(a or b if c else d)` is
            # unparenthesized grouping-wise but starts right after a `(`. Slicing the whole
            # source (not the line) makes rstrip/lstrip skip newlines too, so multi-line
            # grouped forms stay clean.
            if not (src[:start].rstrip().endswith("(") and src[end:].lstrip().startswith(")")):
                offenders.append(f"{path.relative_to(_egpt_root().parent)}:{body.lineno}")

    assert checked_files > 500, f"only {checked_files} files scanned; the root is probably stale and this guard vacuous"
    assert not offenders, (
        "unparenthesized `A or B if C else D` found -- Python parses this as "
        "`(A or B) if C else D`, which has silently discarded caller-supplied values "
        f"five times in this codebase. Add explicit parentheses: {offenders}"
    )
