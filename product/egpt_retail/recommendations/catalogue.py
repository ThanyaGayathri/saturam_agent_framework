"""The next-best-action catalogue (K26): the sales plays a recommendation can carry.

Shipped as data, worded the way a sales organisation already talks -- a play, who owns it,
by when, and when it applies. ``play_for`` chooses by rule from the subject the ``score_risk``
executor produced (its drivers, the account's last loss, its RFM segment, its stage and
value), never by the model: the model says how likely, the catalogue says what to do, and
the record shows why that play was chosen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

__all__ = ["PLAYS", "Play", "play_for", "play_of"]


@dataclass(frozen=True)
class Play:
    code: str
    title: str
    action: str
    when: str
    owner: str
    sla_hours: Optional[int]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


PLAYS: Sequence[Play] = (
    Play(
        "RE_ENGAGE",
        "Re-engage before the quote lapses",
        "Personal follow-up from the rep: confirm the requirement is still live, the "
        "decision maker and the date they need the equipment by.",
        "The quote is past the category's usual time to close and nothing on the "
        "account explains it.",
        "Sales rep",
        48,
    ),
    Play(
        "ESCALATE",
        "Bring in the territory manager",
        "A senior contact on a high-value, high-risk quote: a call from the territory "
        "manager to the customer's decision maker within three days.",
        "High risk on a quote worth more than the territory's typical open quote.",
        "Territory manager",
        72,
    ),
    Play(
        "REQUOTE_SPEC",
        "Re-quote with the alternative specification",
        "Match the configuration the customer compared us against last time: revise the "
        "quote with the alternative model or options and send it with a comparison.",
        "The account's last quote was lost to an alternative specification.",
        "Sales rep",
        120,
    ),
    Play(
        "COMMERCIAL_OFFER",
        "Targeted commercial offer within policy",
        "A discount tier or financing option on the open quote, within the pricing desk's "
        "policy, offered with a close date.",
        "The account's last quote was lost on price or budget.",
        "Sales rep with the pricing desk",
        72,
    ),
    Play(
        "COMPETITIVE_RESPONSE",
        "Position against the named competitor",
        "Send the comparison sheet, a reference site and the warranty terms; ask for the "
        "competing quote's terms.",
        "The account's last quote was lost to a competitor.",
        "Sales rep",
        72,
    ),
    Play(
        "DECISION_SUPPORT",
        "Send the total-cost-of-ownership summary to the decision maker",
        "A one-page summary of total cost of ownership and lead time for the decision "
        "maker, with an offer to walk them through it.",
        "The account's last quote was lost to a delayed decision or no response.",
        "Sales rep",
        120,
    ),
    Play(
        "CHANNEL_PARTNER",
        "Engage the dealer or consultant on the account",
        "Bring the dealer, distributor or foodservice consultant on the account into the "
        "conversation; agree who moves it forward.",
        "A dealer, distributor or consultant account whose quote is still in "
        "qualification.",
        "Channel manager",
        120,
    ),
    Play(
        "ACCOUNT_REVIEW",
        "Account review call; refresh the pricing agreement",
        "A win-back call with the account owner: review what was bought, what changed, and "
        "refresh the pricing agreement before the open quote is discussed.",
        "A valuable account gone quiet: at risk of churn, RFM at risk or cannot lose.",
        "Account owner",
        168,
    ),
    Play(
        "REACTIVATE",
        "Reactivation touch",
        "Add the account to the next reactivation sequence; the rep follows up only if "
        "they respond.",
        "A small account gone quiet: at risk of churn, RFM hibernating.",
        "Marketing",
        None,
    ),
    Play(
        "RELEASE",
        "Release from the active pipeline",
        "Move the quote to nurture and stop counting it as open pipeline; note the reason "
        "on the account.",
        "A low-value quote open past twice the usual time with no reply.",
        "Sales rep",
        None,
    ),
)

_BY_CODE: Dict[str, Play] = {p.code: p for p in PLAYS}

#: Words in a lost reason that name the loss; the data scrambles word order, so match words.
_LOSS_WORDS = {
    "REQUOTE_SPEC": ("specification", "alternative"),
    "COMMERCIAL_OFFER": ("price", "budget", "constraints"),
    "COMPETITIVE_RESPONSE": ("competitor",),
    "DECISION_SUPPORT": ("decision", "delayed", "response"),
}
_CHANNEL_SEGMENTS = ("dealer", "distributor", "consultant")
_VALUABLE_SEGMENTS = ("cannot lose them", "at risk", "champions", "loyal")
_SMALL_SEGMENTS = ("hibernating", "lost", "new")


def play_of(code: str) -> Optional[Play]:
    return _BY_CODE.get(code)


def _loss_play(reason: Optional[str]) -> Optional[str]:
    words = {w.strip().lower() for w in str(reason or "").replace("/", " ").split()}
    if not words:
        return None
    for code, keys in _LOSS_WORDS.items():
        if any(k in words for k in keys):
            return code
    return None


def play_for(
    subject: Mapping[str, Any], *, territory_median_value: Optional[float] = None
) -> Dict[str, Any]:
    """The play for one scored subject and why it was chosen. Rules in the order a sales
    manager would apply them: what the account already told us (its last loss) first, the
    account's own state next, then the quote's age and value."""
    account = subject.get("account") or {}
    level = str(subject.get("level") or "low")
    value = float(subject.get("value") or 0)
    age = float(subject.get("age_days") or 0)
    usual = float(subject.get("usual_days") or 0)
    stalled = bool(subject.get("stalled"))
    stage = str(subject.get("stage") or "").lower()
    segment = str(subject.get("segment") or "").lower()
    rfm = str(account.get("segment") or "").lower()
    account_at_risk = bool(account.get("at_risk"))
    reasons: List[str] = []

    code = _loss_play(subject.get("last_lost_reason"))
    if code:
        reasons.append(
            f"the account's last quote was lost: {subject.get('last_lost_reason')}"
        )
    elif account_at_risk and rfm in _VALUABLE_SEGMENTS:
        code = "ACCOUNT_REVIEW"
        reasons.append(
            f"the account has not quoted in {int(account.get('recency_days') or 0)} days "
            f"and is RFM {account.get('segment')}"
        )
    elif account_at_risk and rfm in _SMALL_SEGMENTS:
        code = "REACTIVATE"
        reasons.append(
            f"the account has not quoted in {int(account.get('recency_days') or 0)} days "
            f"and is RFM {account.get('segment')}"
        )
    elif any(s in segment for s in _CHANNEL_SEGMENTS) and "qualification" in stage:
        code = "CHANNEL_PARTNER"
        reasons.append(
            f"a {subject.get('segment')} account still in {subject.get('stage')}"
        )
    elif (
        stalled
        and usual
        and age > 2 * usual
        and value < 0.5 * (territory_median_value or value + 1)
    ):
        code = "RELEASE"
        reasons.append(
            f"open {int(age)} days, twice the usual {int(usual)}, and worth less than "
            f"half the territory's typical open quote"
        )
    elif level == "high" and rfm in ("cannot lose them", "champions"):
        # The account, not the quote, is what is at stake: a senior contact, whatever
        # the quote's size.
        code = "ESCALATE"
        quotes = int(account.get("frequency") or 0)
        won = float(account.get("monetary") or 0)
        reasons.append(
            f"high risk on a {account.get('segment')} account "
            f"({quotes} quote{'s' if quotes != 1 else ''} this year"
            + (f", ${won:,.0f} won)" if won > 0 else ")")
        )
    elif level == "high" and rfm == "new":
        # A first buyer stalling is a trust problem, not a price problem.
        code = "DECISION_SUPPORT"
        reasons.append("high risk on a new account's first quotes")
    elif level == "high" and territory_median_value and value > territory_median_value:
        code = "ESCALATE"
        reasons.append(
            f"high risk on ${value:,.0f}, above the territory's typical open quote "
            f"of ${territory_median_value:,.0f}"
        )
    else:
        code = "RE_ENGAGE"
        left = int(max(usual - age, 0))
        reasons.append(
            f"open {int(age)} days against a usual {int(usual)}"
            if stalled
            else f"{level} risk with {left} day{'' if left == 1 else 's'} of the usual "
            "range left"
        )
    play = _BY_CODE[code]
    return {
        "code": play.code,
        "title": play.title,
        "action": play.action,
        "owner": play.owner,
        "sla_hours": play.sla_hours,
        "from_catalogue": True,
        "because": "; ".join(reasons),
    }
