"""R15 -- ``RehearsalReviewStore`` against real Postgres (D14).

``test_r15_golden_gate.py`` covers the gate's own pure logic without a database. What needs a
real Postgres is the state machine ``_decide``/``record_run`` enforce in SQL -- the
``WHERE status = 'pending'``/``RETURNING`` predicate, and the conditional reset on re-run --
gated the way ``integration.yml`` gates ``test_semantic_live.py``'s own suites:
``EGPT_INTEGRATION_POSTGRES``, skipped otherwise so the unit run stays database-free.

Locally, against the ad-hoc R15 Postgres container (see ``cookbook/94_retail/
R15_golden_sets.py``'s own docstring for how to start one):

    EGPT_INTEGRATION_POSTGRES=1 EGPT_RETAIL_R15_DSN=postgresql://ai:ai@localhost:5834/ai \\
      pytest product/tests/test_r15_rehearsal_live.py

Everything written here is keyed under a ``skill_identifier`` prefixed ``livetest_r15_`` and
removed at the end of each test.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

from egpt_retail.evals.rehearsal import RehearsalReviewStore, RehearsalStatus

_LIVE = os.environ.get("EGPT_INTEGRATION_POSTGRES")
pytestmark = pytest.mark.skipif(
    not _LIVE, reason="needs EGPT_INTEGRATION_POSTGRES=1 with a live Postgres"
)

DSN = os.environ.get("EGPT_RETAIL_R15_DSN", "postgresql://ai:ai@localhost:5834/ai")


@pytest.fixture
def store() -> Iterator[RehearsalReviewStore]:
    psycopg = pytest.importorskip("psycopg")
    connection = psycopg.connect(DSN)
    connection.autocommit = False
    review_store = RehearsalReviewStore(connection)
    review_store.ensure_schema()
    marker = f"livetest_r15_{uuid.uuid4().hex[:12]}"
    review_store._marker = marker  # type: ignore[attr-defined]  # test-only bookkeeping
    yield review_store
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM retail_evals.rehearsal_review WHERE skill_identifier = %s",
            (marker,),
        )
    connection.commit()
    connection.close()


def _identifier(review_store: RehearsalReviewStore) -> str:
    return review_store._marker  # type: ignore[attr-defined]


def test_redecide_with_a_different_reviewer_raises(store: RehearsalReviewStore) -> None:
    """id=3988596555 / id=3988655156: any re-decide of an already-decided row is refused, not
    only one that asks for a different status -- the chosen precedent is 'any re-decide
    raises', matching K7's ProposalStore in spirit (an already-decided item is never silently
    re-accepted)."""
    identifier = _identifier(store)
    store.record_run(identifier, "1.0.0", pass_rate=0.9, total_cases=10)
    store.approve(identifier, "1.0.0", "alice@example.com", note="first look")

    with pytest.raises(ValueError, match="already approved"):
        store.approve(identifier, "1.0.0", "bob@example.com", note="second look")

    current = store.get(identifier, "1.0.0")
    assert current is not None
    assert current.reviewer == "alice@example.com"
    assert current.note == "first look"


def test_redecide_to_the_same_status_by_the_same_reviewer_also_raises(
    store: RehearsalReviewStore,
) -> None:
    """The chosen semantics: re-deciding to the *same* status by the *same* reviewer is not
    special-cased as an idempotent no-op -- it raises too, so a caller cannot silently discard
    a would-be second note by calling approve() twice."""
    identifier = _identifier(store)
    store.record_run(identifier, "1.0.0", pass_rate=0.9, total_cases=10)
    store.approve(identifier, "1.0.0", "alice@example.com", note="first look")

    with pytest.raises(ValueError, match="already approved"):
        store.approve(identifier, "1.0.0", "alice@example.com", note="first look")


def test_redecide_of_a_rejected_row_raises(store: RehearsalReviewStore) -> None:
    identifier = _identifier(store)
    store.record_run(identifier, "1.0.0", pass_rate=0.5, total_cases=10)
    store.reject(identifier, "1.0.0", "alice@example.com", note="too weak")

    with pytest.raises(ValueError, match="already rejected"):
        store.reject(identifier, "1.0.0", "bob@example.com", note="agreed")


def test_identical_rerun_preserves_the_approval_and_its_audit_trail(
    store: RehearsalReviewStore,
) -> None:
    """id=3988596574: recording the *same* score again (a CI re-run with no prompt change)
    must not wipe out who approved it."""
    identifier = _identifier(store)
    store.record_run(identifier, "1.0.0", pass_rate=0.9, total_cases=10)
    approved = store.approve(
        identifier, "1.0.0", "alice@example.com", note="looks fine"
    )

    review = store.record_run(identifier, "1.0.0", pass_rate=0.9, total_cases=10)

    assert review.status is RehearsalStatus.APPROVED
    assert review.reviewer == "alice@example.com"
    assert review.note == "looks fine"
    assert review.decided_at == approved.decided_at


def test_a_different_score_rerun_reopens_review_to_pending(
    store: RehearsalReviewStore,
) -> None:
    """The counterpart to the identical-rerun test: a real re-measurement still reopens review,
    exactly as before this change -- only the *identical* case is now special-cased."""
    identifier = _identifier(store)
    store.record_run(identifier, "1.0.0", pass_rate=0.9, total_cases=10)
    store.approve(identifier, "1.0.0", "alice@example.com", note="looks fine")

    review = store.record_run(identifier, "1.0.0", pass_rate=0.5, total_cases=10)

    assert review.status is RehearsalStatus.PENDING
    assert review.reviewer is None
    assert review.note == ""
    assert review.decided_at is None


def test_a_different_total_cases_rerun_also_reopens_review(
    store: RehearsalReviewStore,
) -> None:
    """total_cases changing while pass_rate stays the same is still a different measurement
    (a bigger or smaller golden set), not an identical re-run."""
    identifier = _identifier(store)
    store.record_run(identifier, "1.0.0", pass_rate=0.9, total_cases=10)
    store.approve(identifier, "1.0.0", "alice@example.com", note="looks fine")

    review = store.record_run(identifier, "1.0.0", pass_rate=0.9, total_cases=12)

    assert review.status is RehearsalStatus.PENDING
    assert review.reviewer is None


def test_record_run_on_a_pending_row_does_not_disturb_it(
    store: RehearsalReviewStore,
) -> None:
    """A pending row re-recorded with the same score stays pending with no reviewer -- the
    conditional reset only ever matters once a row has been decided."""
    identifier = _identifier(store)
    store.record_run(identifier, "1.0.0", pass_rate=0.9, total_cases=10)

    review = store.record_run(identifier, "1.0.0", pass_rate=0.9, total_cases=10)

    assert review.status is RehearsalStatus.PENDING
    assert review.reviewer is None


def test_approve_returns_the_freshly_decided_row(store: RehearsalReviewStore) -> None:
    """id=3988596564 / id=3988596592: approve()'s return value comes from the same UPDATE
    ... RETURNING as the write, not a second SELECT -- exercised indirectly here by simply
    checking the returned row already reflects the decision."""
    identifier = _identifier(store)
    store.record_run(identifier, "1.0.0", pass_rate=0.95, total_cases=20)

    approved = store.approve(identifier, "1.0.0", "alice@example.com", note="ship it")

    assert approved.status is RehearsalStatus.APPROVED
    assert approved.reviewer == "alice@example.com"
    assert approved.note == "ship it"
    assert approved.decided_at is not None


def test_approve_a_never_recorded_version_raises(store: RehearsalReviewStore) -> None:
    identifier = _identifier(store)
    with pytest.raises(ValueError, match="no golden run has been recorded"):
        store.approve(identifier, "9.9.9", "alice@example.com")


def test_ensure_schema_is_idempotent(store: RehearsalReviewStore) -> None:
    """Calling it twice (as the fixture and a caller both might) must not raise."""
    store.ensure_schema()
    store.ensure_schema()
