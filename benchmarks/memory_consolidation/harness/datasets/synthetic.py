"""Hand-authored synthetic episodic->semantic dataset.

Everything a label-based metric needs is planted on purpose:
  - stable facts (allergy, pet, language, diet, goal, hobby)
  - duplicates: the same fact restated in a later session (must dedup to 1)
  - conflicts: a preference/circumstance that changes over time (must 'keep newer'
    and retire the stale fact) — job title, city, beverage
  - distractors: chit-chat episodes with no durable fact (must be filtered as noise)

Gold ``source_episode_ids`` are recorded per target so provenance can be scored.
Timestamps increase by session so 'keep newer' has a well-defined direction.

The scenario is deterministic (no RNG) so results are reproducible and the cache
key is stable across runs.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from harness.datasets.base import Dataset
from harness.schemas import Episode, TargetFact

_DAY = 86400
_BASE_TS = 1_700_000_000  # fixed epoch so runs are reproducible

# Each entry: (episode_id, session_index, text). Session index drives timestamp.
_EPISODES: List[Tuple[str, int, str]] = [
    # -- Session 0 --
    ("e00", 0, "Priya introduced herself and said she works as a software engineer at a fintech startup."),
    ("e01", 0, "Priya mentioned she prefers Python for almost all her side projects."),
    ("e02", 0, "Priya said she is allergic to peanuts and always checks ingredient labels."),
    ("e03", 0, "We chatted a bit about the rainy weather in the morning."),
    # -- Session 1 --
    ("e10", 1, "Priya talked about her dog named Rio, a golden retriever she adopted last year."),
    ("e11", 1, "Priya said she currently lives in Bangalore, close to her office."),
    ("e12", 1, "Priya mentioned she is vegetarian and enjoys cooking South Indian food."),
    ("e13", 1, "Priya said she wants to learn Rust this year to get into systems programming."),
    # -- Session 2 --
    ("e20", 2, "Priya shared that she loves weekend hiking trips in the Western Ghats."),
    ("e21", 2, "Priya usually drinks tea in the mornings and finds coffee too bitter."),
    ("e22", 2, "We talked about a movie she watched last night, nothing memorable."),
    # -- Session 3 --
    ("e30", 3, "Priya got promoted and is now an engineering manager leading a team of six."),
    ("e31", 3, "Priya reiterated that Python is still her go-to language at work."),
    # -- Session 4 --
    ("e40", 4, "Priya said she recently moved from Bangalore to Pune for a new office location."),
    ("e41", 4, "Priya noted she has switched from tea to coffee and now drinks it every morning."),
    # -- Session 5 --
    ("e50", 5, "Priya confirmed she is still vegetarian and has been trying new lentil recipes."),
    ("e51", 5, "Priya said her Rust learning is going well; she built a small CLI tool."),
]

# Targets: gold semantic facts. Conflicts use should_be_active=False for the stale
# side and `supersedes` on the winning side.
_TARGETS: List[TargetFact] = [
    TargetFact(
        "t_lang",
        "Priya prefers Python",
        ["Priya's preferred language is Python", "likes Python"],
        source_episode_ids=["e01", "e31"],
        topics=["preferences"],
    ),
    TargetFact(
        "t_allergy", "Priya is allergic to peanuts", ["peanut allergy"], source_episode_ids=["e02"], topics=["health"]
    ),
    TargetFact(
        "t_pet",
        "Priya has a dog named Rio",
        ["Rio is Priya's dog", "owns a golden retriever named Rio"],
        source_episode_ids=["e10"],
        topics=["personal"],
    ),
    TargetFact(
        "t_diet",
        "Priya is vegetarian",
        ["Priya follows a vegetarian diet"],
        source_episode_ids=["e12", "e50"],
        topics=["preferences"],
    ),
    TargetFact(
        "t_goal",
        "Priya is learning Rust",
        ["Priya wants to learn Rust", "studying Rust"],
        source_episode_ids=["e13", "e51"],
        topics=["goals"],
    ),
    TargetFact(
        "t_hobby", "Priya enjoys hiking", ["likes weekend hiking"], source_episode_ids=["e20"], topics=["hobbies"]
    ),
    # Conflict 1: job title  engineer(old) -> manager(new)
    TargetFact(
        "t_job_old",
        "Priya is a software engineer",
        ["works as a software engineer"],
        should_be_active=False,
        source_episode_ids=["e00"],
        topics=["work"],
    ),
    TargetFact(
        "t_job_new",
        "Priya is an engineering manager",
        ["Priya works as an engineering manager"],
        supersedes="t_job_old",
        source_episode_ids=["e30"],
        topics=["work"],
    ),
    # Conflict 2: city  Bangalore(old) -> Pune(new)
    TargetFact(
        "t_city_old",
        "Priya lives in Bangalore",
        ["based in Bangalore"],
        should_be_active=False,
        source_episode_ids=["e11"],
        topics=["location"],
    ),
    TargetFact(
        "t_city_new",
        "Priya lives in Pune",
        ["Priya moved to Pune", "based in Pune"],
        supersedes="t_city_old",
        source_episode_ids=["e40"],
        topics=["location"],
    ),
    # Conflict 3: beverage  tea(old) -> coffee(new)
    TargetFact(
        "t_bev_old",
        "Priya drinks tea in the mornings",
        ["prefers tea"],
        should_be_active=False,
        source_episode_ids=["e21"],
        topics=["preferences"],
    ),
    TargetFact(
        "t_bev_new",
        "Priya drinks coffee in the mornings",
        ["switched to coffee", "prefers coffee now"],
        supersedes="t_bev_old",
        source_episode_ids=["e41"],
        topics=["preferences"],
    ),
]

_QA: List[Dict[str, str]] = [
    {"question": "What is Priya's current job title?", "answer": "engineering manager"},
    {"question": "Which city does Priya currently live in?", "answer": "Pune"},
    {"question": "What does Priya drink in the mornings now?", "answer": "coffee"},
    {"question": "Does Priya have any food allergies?", "answer": "peanuts"},
    {"question": "What pet does Priya have?", "answer": "a dog named Rio"},
    {"question": "What programming language does Priya prefer?", "answer": "Python"},
    {"question": "What is Priya's dietary preference?", "answer": "vegetarian"},
    {"question": "What new language is Priya learning?", "answer": "Rust"},
    {"question": "What outdoor hobby does Priya enjoy?", "answer": "hiking"},
]


def build_synthetic() -> Dataset:
    n_sessions = max(s for _, s, _ in _EPISODES) + 1
    sessions: List[List[Episode]] = [[] for _ in range(n_sessions)]
    for eid, sidx, text in _EPISODES:
        sessions[sidx].append(
            Episode(
                episode_id=eid,
                text=text,
                timestamp=_BASE_TS + sidx * _DAY,
                user_id="priya",
                session_id=f"s{sidx}",
            )
        )
    return Dataset(name="synthetic", sessions=sessions, targets=list(_TARGETS), qa_pairs=list(_QA))
