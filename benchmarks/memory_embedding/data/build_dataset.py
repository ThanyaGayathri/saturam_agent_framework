"""Build the synthetic memory-retrieval evaluation set (SM-R4).

The corpus text is hand-authored to represent the *memory tier*: short,
personal, episodic statements (user preferences, past events, entity facts)
rather than the long encyclopedic passages found in generic IR benchmarks.

Queries deliberately exercise the retrieval failure modes that matter for a
memory subsystem:

  - paraphrase        : query wording differs from the stored memory
  - temporal          : "last/previous/back in ..." phrasing over dated events
  - coreference       : query refers to an entity by role/pronoun, not name
  - hard-negative     : a lexically similar but wrong memory exists in the corpus

Relevance judgements (qrels) are binary graded (1 = relevant). Everything is
deterministic; re-running this script reproduces byte-identical JSON.

Usage:
    python data/build_dataset.py        # writes corpus.json / queries.json / qrels.json
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# Memory corpus. `type` is metadata only (not used for scoring) but documents
# the intent of each item and keeps the mix representative.
# --------------------------------------------------------------------------- #
CORPUS: list[dict] = [
    # -- user preferences --------------------------------------------------- #
    {
        "id": "m01",
        "type": "preference",
        "text": "The user prefers dark mode in all their applications.",
    },
    {
        "id": "m02",
        "type": "preference",
        "text": "The user is vegetarian and avoids all meat and fish.",
    },
    {
        "id": "m03",
        "type": "preference",
        "text": "The user likes their coffee black with no sugar.",
    },
    {
        "id": "m04",
        "type": "preference",
        "text": "The user prefers to receive meeting invites in the morning, never after 4pm.",
    },
    {
        "id": "m05",
        "type": "preference",
        "text": "The user wants code reviews to focus on correctness before style.",
    },
    {
        "id": "m06",
        "type": "preference",
        "text": "The user reads technical books on their Kindle, not on paper.",
    },
    {
        "id": "m07",
        "type": "preference",
        "text": "The user dislikes phone calls and prefers async written communication.",
    },
    {
        "id": "m08",
        "type": "preference",
        "text": "The user keeps Slack notifications muted between 8pm and 8am.",
    },
    # -- episodic events (dated) ------------------------------------------- #
    {
        "id": "m09",
        "type": "episodic",
        "text": "On 2025-03-12 the user deployed the billing service to production for the first time.",
    },
    {
        "id": "m10",
        "type": "episodic",
        "text": "Last quarter the user migrated the analytics database from MySQL to Postgres.",
    },
    {
        "id": "m11",
        "type": "episodic",
        "text": "In January the user gave a conference talk about vector databases in Berlin.",
    },
    {
        "id": "m12",
        "type": "episodic",
        "text": "The user broke the CI pipeline on Friday by merging an untested dependency bump.",
    },
    {
        "id": "m13",
        "type": "episodic",
        "text": "Two weeks ago the user onboarded a new backend engineer named Priya.",
    },
    {
        "id": "m14",
        "type": "episodic",
        "text": "The user cancelled the Tuesday standup because half the team was on holiday.",
    },
    {
        "id": "m15",
        "type": "episodic",
        "text": "During the outage on 2025-06-01 the user rolled back the payments release at 2am.",
    },
    {
        "id": "m16",
        "type": "episodic",
        "text": "The user spent last weekend debugging a memory leak in the ingestion worker.",
    },
    {
        "id": "m17",
        "type": "episodic",
        "text": "The user filed a patent draft for the neuro-symbolic memory design in May.",
    },
    # -- entity facts ------------------------------------------------------- #
    {
        "id": "m18",
        "type": "entity",
        "text": "Priya is the backend engineer who owns the authentication service.",
    },
    {
        "id": "m19",
        "type": "entity",
        "text": "The billing service is written in Go and runs on Kubernetes.",
    },
    {
        "id": "m20",
        "type": "entity",
        "text": "The analytics database now runs Postgres 16 with the pgvector extension.",
    },
    {
        "id": "m21",
        "type": "entity",
        "text": "Acme Corp is the user's largest enterprise customer, based in Singapore.",
    },
    {
        "id": "m22",
        "type": "entity",
        "text": "The user's manager is Dana, who leads the platform organization.",
    },
    {
        "id": "m23",
        "type": "entity",
        "text": "The ingestion worker is a Python service that batches events into Kafka.",
    },
    {
        "id": "m24",
        "type": "entity",
        "text": "The staging cluster has 3 nodes; the production cluster has 12 nodes.",
    },
    {
        "id": "m25",
        "type": "entity",
        "text": "The memory subsystem stores episodic vectors in a pgvector-backed store.",
    },
    # -- tasks / commitments ----------------------------------------------- #
    {
        "id": "m26",
        "type": "task",
        "text": "The user promised to send Dana the quarterly roadmap by end of week.",
    },
    {
        "id": "m27",
        "type": "task",
        "text": "The user still needs to rotate the production database credentials.",
    },
    {
        "id": "m28",
        "type": "task",
        "text": "The user agreed to mentor Priya on the release process next sprint.",
    },
    {
        "id": "m29",
        "type": "task",
        "text": "The user wants to add rate limiting to the public API before launch.",
    },
    {
        "id": "m30",
        "type": "task",
        "text": "The user plans to evaluate embedding models for the memory tier this month.",
    },
    # -- hard negatives (lexically close to a query but not the answer) ----- #
    {
        "id": "m31",
        "type": "entity",
        "text": "The user's personal laptop runs Linux, but their work laptop runs macOS.",
    },
    {
        "id": "m32",
        "type": "preference",
        "text": "The user prefers tea in the afternoon even though they drink coffee in the morning.",
    },
    {
        "id": "m33",
        "type": "episodic",
        "text": "The user attended a conference in Berlin two years ago but did not speak.",
    },
    {
        "id": "m34",
        "type": "entity",
        "text": "Dana used to be a backend engineer before moving into management.",
    },
    {
        "id": "m35",
        "type": "episodic",
        "text": "The user migrated a logging pipeline from Elasticsearch to Loki last year.",
    },
    {
        "id": "m36",
        "type": "entity",
        "text": "The payments service is maintained by a separate team in the Dublin office.",
    },
    {
        "id": "m37",
        "type": "preference",
        "text": "The user turns on light mode only when giving presentations on a projector.",
    },
    {
        "id": "m38",
        "type": "episodic",
        "text": "The user interviewed three backend candidates before hiring Priya.",
    },
    # -- distractors (topically adjacent, unrelated to any query) ----------- #
    {
        "id": "m39",
        "type": "entity",
        "text": "The company uses GitHub Actions for continuous integration.",
    },
    {
        "id": "m40",
        "type": "entity",
        "text": "The design system is maintained in a shared Figma workspace.",
    },
    {
        "id": "m41",
        "type": "preference",
        "text": "The user enjoys hiking on weekends when the weather is clear.",
    },
    {
        "id": "m42",
        "type": "episodic",
        "text": "The user upgraded their monitor to an ultrawide display in April.",
    },
    {
        "id": "m43",
        "type": "entity",
        "text": "The customer success team handles onboarding for small accounts.",
    },
    {
        "id": "m44",
        "type": "task",
        "text": "The user intends to write a blog post about air-gapped deployments.",
    },
    {
        "id": "m45",
        "type": "entity",
        "text": "The frontend is a React application deployed on a CDN.",
    },
    {
        "id": "m46",
        "type": "episodic",
        "text": "The user gave positive feedback to the SRE team after the smooth Q1 release.",
    },
    {
        "id": "m47",
        "type": "preference",
        "text": "The user prefers concise pull request descriptions with a bullet summary.",
    },
    {
        "id": "m48",
        "type": "entity",
        "text": "The observability stack is Prometheus for metrics and Grafana for dashboards.",
    },
    {
        "id": "m49",
        "type": "task",
        "text": "The user wants to deprecate the legacy SOAP endpoint by next quarter.",
    },
    {
        "id": "m50",
        "type": "episodic",
        "text": "The user paired with Priya to fix the authentication token refresh bug.",
    },
    {
        "id": "m51",
        "type": "entity",
        "text": "The user's timezone is CET and Dana's timezone is PST.",
    },
    {
        "id": "m52",
        "type": "preference",
        "text": "The user wants alerts delivered to PagerDuty, not email.",
    },
    {
        "id": "m53",
        "type": "episodic",
        "text": "The user archived the old analytics dashboards after the Postgres migration.",
    },
    {
        "id": "m54",
        "type": "entity",
        "text": "The neuro-symbolic memory design combines a vector tier with a symbolic graph.",
    },
]

# --------------------------------------------------------------------------- #
# Queries + gold relevance. Each query lists the memory ids that genuinely
# answer it. `mode` documents the challenge type (metadata only).
# --------------------------------------------------------------------------- #
QUERIES: list[dict] = [
    {
        "id": "q01",
        "mode": "paraphrase",
        "text": "What color theme does the user like for their apps?",
        "gold": ["m01"],
    },
    {
        "id": "q02",
        "mode": "paraphrase",
        "text": "Does the user eat meat?",
        "gold": ["m02"],
    },
    {
        "id": "q03",
        "mode": "paraphrase",
        "text": "How does the user take their coffee?",
        "gold": ["m03"],
    },
    {
        "id": "q04",
        "mode": "paraphrase",
        "text": "When is the best time to schedule a meeting with the user?",
        "gold": ["m04"],
    },
    {
        "id": "q05",
        "mode": "paraphrase",
        "text": "What should reviewers prioritise when reviewing the user's code?",
        "gold": ["m05"],
    },
    {
        "id": "q06",
        "mode": "coreference",
        "text": "How does the user like to be contacted?",
        "gold": ["m07"],
    },
    {
        "id": "q07",
        "mode": "temporal",
        "text": "When did the user first ship billing to prod?",
        "gold": ["m09"],
    },
    {
        "id": "q08",
        "mode": "temporal",
        "text": "What database change did the user make in the previous quarter?",
        "gold": ["m10"],
    },
    {
        "id": "q09",
        "mode": "paraphrase",
        "text": "Where did the user speak about vector databases?",
        "gold": ["m11"],
    },
    {
        "id": "q10",
        "mode": "temporal",
        "text": "What did the user break at the end of last week?",
        "gold": ["m12"],
    },
    {
        "id": "q11",
        "mode": "coreference",
        "text": "Who joined the backend team recently?",
        "gold": ["m13", "m18"],
    },
    {
        "id": "q12",
        "mode": "temporal",
        "text": "What happened during the June outage?",
        "gold": ["m15"],
    },
    {
        "id": "q13",
        "mode": "paraphrase",
        "text": "What was the user debugging over the weekend?",
        "gold": ["m16"],
    },
    {
        "id": "q14",
        "mode": "coreference",
        "text": "Which service does the new engineer own?",
        "gold": ["m18"],
    },
    {
        "id": "q15",
        "mode": "paraphrase",
        "text": "What language and platform is the billing service built on?",
        "gold": ["m19"],
    },
    {
        "id": "q16",
        "mode": "paraphrase",
        "text": "What is the analytics database running now?",
        "gold": ["m20"],
    },
    {
        "id": "q17",
        "mode": "coreference",
        "text": "Who is the user's biggest customer and where are they?",
        "gold": ["m21"],
    },
    {
        "id": "q18",
        "mode": "coreference",
        "text": "Who does the user report to?",
        "gold": ["m22"],
    },
    {
        "id": "q19",
        "mode": "paraphrase",
        "text": "How does the ingestion worker move data?",
        "gold": ["m23"],
    },
    {
        "id": "q20",
        "mode": "paraphrase",
        "text": "How many machines are in production versus staging?",
        "gold": ["m24"],
    },
    {
        "id": "q21",
        "mode": "paraphrase",
        "text": "What did the user commit to sending their manager?",
        "gold": ["m26"],
    },
    {
        "id": "q22",
        "mode": "paraphrase",
        "text": "What security chore is still outstanding?",
        "gold": ["m27"],
    },
    {
        "id": "q23",
        "mode": "coreference",
        "text": "What did the user agree to teach the new hire?",
        "gold": ["m28"],
    },
    {
        "id": "q24",
        "mode": "paraphrase",
        "text": "What protection does the user want before the API launch?",
        "gold": ["m29"],
    },
    {
        "id": "q25",
        "mode": "paraphrase",
        "text": "What evaluation is the user planning this month?",
        "gold": ["m30"],
    },
    {
        "id": "q26",
        "mode": "hard-negative",
        "text": "What operating system does the user's work machine run?",
        "gold": ["m31"],
    },
    {
        "id": "q27",
        "mode": "hard-negative",
        "text": "What does the user drink in the mornings?",
        "gold": ["m03"],
    },
    {
        "id": "q28",
        "mode": "hard-negative",
        "text": "When did the user present a talk in Berlin?",
        "gold": ["m11"],
    },
    {
        "id": "q29",
        "mode": "coreference",
        "text": "What was Dana's job before management?",
        "gold": ["m34"],
    },
    {
        "id": "q30",
        "mode": "temporal",
        "text": "What logging migration happened last year?",
        "gold": ["m35"],
    },
    {
        "id": "q31",
        "mode": "coreference",
        "text": "Which office looks after the payments service?",
        "gold": ["m36"],
    },
    {
        "id": "q32",
        "mode": "coreference",
        "text": "Who did the user pair with on the auth token bug?",
        "gold": ["m50", "m18"],
    },
]


def build() -> None:
    corpus_out = {m["id"]: {"text": m["text"], "type": m["type"]} for m in CORPUS}
    queries_out = {q["id"]: {"text": q["text"], "mode": q["mode"]} for q in QUERIES}
    qrels_out = {q["id"]: {mid: 1 for mid in q["gold"]} for q in QUERIES}

    # sanity: every gold id must exist in the corpus
    corpus_ids = set(corpus_out)
    for qid, rels in qrels_out.items():
        missing = set(rels) - corpus_ids
        if missing:
            raise ValueError(
                f"query {qid} references unknown memory ids: {sorted(missing)}"
            )

    (DATA_DIR / "corpus.json").write_text(
        json.dumps(corpus_out, indent=2, sort_keys=True) + "\n"
    )
    (DATA_DIR / "queries.json").write_text(
        json.dumps(queries_out, indent=2, sort_keys=True) + "\n"
    )
    (DATA_DIR / "qrels.json").write_text(
        json.dumps(qrels_out, indent=2, sort_keys=True) + "\n"
    )

    print(f"corpus:  {len(corpus_out)} memories")
    print(f"queries: {len(queries_out)} queries")
    print(f"qrels:   {sum(len(v) for v in qrels_out.values())} relevance judgements")


if __name__ == "__main__":
    build()
