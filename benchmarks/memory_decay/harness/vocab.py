"""Vocabulary for generating natural-language forms of synthetic episodes.

Only used by the embedding relevance mode. Each topic is a ``(person, attribute)``
pair; a fact version assigns a value to that attribute. So superseding versions
share the same subject and differ only in the value -- exactly the "same subject,
updated answer" case decay must resolve. A query asks about the subject, so it
embeds close to *every* version of its topic (the challenge) and far from other
topics.
"""

from __future__ import annotations

from typing import List, Tuple

PERSONS: List[str] = [
    "Alice",
    "Bob",
    "Carol",
    "David",
    "Eve",
    "Frank",
    "Grace",
    "Heidi",
    "Ivan",
    "Judy",
    "Mallory",
    "Niaj",
    "Olivia",
    "Peggy",
    "Quentin",
    "Rupert",
    "Sybil",
    "Trent",
    "Uma",
    "Victor",
    "Wendy",
    "Xavier",
    "Yara",
    "Zane",
    "Aditi",
    "Bilal",
    "Chen",
    "Diego",
    "Elena",
    "Farah",
    "Gita",
    "Hiro",
    "Ines",
    "Jamal",
    "Kavya",
    "Lars",
    "Mei",
    "Noor",
    "Omar",
    "Priya",
]

# (attribute phrase, candidate values). Values are chosen so consecutive versions
# differ, modelling a fact that is updated over time.
ATTRIBUTES: List[Tuple[str, List[str]]] = [
    (
        "favorite programming language",
        [
            "Python",
            "Rust",
            "Go",
            "TypeScript",
            "Kotlin",
            "Elixir",
            "Scala",
            "Java",
            "Swift",
            "Ruby",
        ],
    ),
    (
        "home city",
        [
            "Bangalore",
            "Berlin",
            "Toronto",
            "Austin",
            "Lisbon",
            "Singapore",
            "Nairobi",
            "Dublin",
            "Seattle",
            "Chennai",
        ],
    ),
    (
        "current employer",
        [
            "Acme Corp",
            "Globex",
            "Initech",
            "Umbrella",
            "Hooli",
            "Stark Industries",
            "Soylent",
            "Massive Dynamic",
        ],
    ),
    (
        "favorite cuisine",
        [
            "Thai",
            "Italian",
            "Mexican",
            "Japanese",
            "Ethiopian",
            "Lebanese",
            "Korean",
            "Greek",
            "Vietnamese",
        ],
    ),
    (
        "preferred contact method",
        [
            "email",
            "Slack",
            "a phone call",
            "SMS",
            "Signal",
            "WhatsApp",
            "Teams",
            "Discord",
        ],
    ),
    (
        "phone model",
        [
            "an iPhone 15",
            "a Pixel 8",
            "a Galaxy S24",
            "a Nothing Phone 2",
            "a OnePlus 12",
            "an Xperia 1",
        ],
    ),
    (
        "car",
        [
            "a Tesla Model 3",
            "a Honda Civic",
            "a Toyota Corolla",
            "a Ford Mustang",
            "a VW Golf",
            "a Kia EV6",
        ],
    ),
    (
        "pet",
        [
            "a golden retriever",
            "a siamese cat",
            "a parrot",
            "two goldfish",
            "a beagle",
            "a hamster",
        ],
    ),
    (
        "gym membership",
        [
            "Gold's Gym",
            "Anytime Fitness",
            "Planet Fitness",
            "Equinox",
            "a local CrossFit box",
            "the YMCA",
        ],
    ),
    (
        "current project codename",
        [
            "Bluebird",
            "Falcon",
            "Nimbus",
            "Orion",
            "Pegasus",
            "Titan",
            "Vortex",
            "Zephyr",
        ],
    ),
    (
        "dietary restriction",
        [
            "vegan",
            "vegetarian",
            "gluten-free",
            "pescatarian",
            "lactose-intolerant",
            "nut-allergic",
        ],
    ),
    (
        "preferred meeting time",
        [
            "early morning",
            "late morning",
            "just after lunch",
            "mid-afternoon",
            "the late evening",
        ],
    ),
]


def subject_pairs() -> List[Tuple[str, str, List[str]]]:
    """All ``(person, attribute_phrase, value_pool)`` combinations."""
    return [(p, attr, values) for p in PERSONS for (attr, values) in ATTRIBUTES]


def memory_text(person: str, attribute: str, value: str) -> str:
    return f"{person}'s {attribute} is {value}."


def query_text(person: str, attribute: str) -> str:
    return f"What is {person}'s {attribute}?"
