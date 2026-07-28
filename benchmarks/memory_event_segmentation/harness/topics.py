"""Topic content for the synthetic conversation stream.

Each topic is a clearly distinct domain with human-readable user/assistant lines,
so that (a) real text embeddings separate topics cleanly and (b) the LLM /
on-prem SLM segmenter has genuine topical signal to detect shifts. Lines are
self-contained (no slot filling) to keep generation deterministic.

Topic-neutral BACKCHANNEL lines belong to whatever segment they fall in (they do
NOT open a new episode) — they stress over-segmenters and embedding drift.
"""

from __future__ import annotations

from typing import Dict, List

# 12 distinct domains. user_lines / asst_lines are sampled (no immediate repeat)
# to build a coherent multi-turn segment on that topic.
TOPICS: List[Dict[str, object]] = [
    {
        "name": "trip_planning",
        "user_lines": [
            "I'm planning a two week trip to Japan in spring.",
            "Should I get a rail pass for travelling between Tokyo and Kyoto?",
            "What are the must see temples and gardens in Kyoto?",
            "How many days should I budget for Osaka and the food scene?",
            "Is it easy to get by in rural areas without speaking Japanese?",
        ],
        "asst_lines": [
            "Spring is cherry blossom season, so book accommodation early.",
            "A seven day rail pass pays off if you do Tokyo, Kyoto and Osaka.",
            "Fushimi Inari and the bamboo grove in Arashiyama are highlights.",
            "Two full days is enough for Osaka's Dotonbori and day trips.",
            "Translation apps cover most rural situations comfortably.",
        ],
    },
    {
        "name": "python_debugging",
        "user_lines": [
            "My Python script throws a KeyError when I parse the config.",
            "The traceback points at a dictionary lookup inside a loop.",
            "Should I use dict.get with a default instead of indexing?",
            "Now I get a TypeError comparing a string to an integer.",
            "How do I add logging so I can see the offending value?",
        ],
        "asst_lines": [
            "A KeyError means the key is missing, print the keys you actually have.",
            "Wrap the lookup in dict.get(key, fallback) to avoid the crash.",
            "Cast the config value with int() before the numeric comparison.",
            "Add logging.debug with the variable so you can trace the input.",
            "A defensive parser with validation will stop this recurring.",
        ],
    },
    {
        "name": "cooking_recipe",
        "user_lines": [
            "I want to make a weeknight vegetable curry from scratch.",
            "What spices do I need for a basic tomato and lentil base?",
            "How long should the lentils simmer before they soften?",
            "Can I make this in advance and freeze portions?",
            "What should I serve alongside it, rice or flatbread?",
        ],
        "asst_lines": [
            "Start by frying onion, garlic and ginger until golden.",
            "Cumin, turmeric, coriander and a little chilli make the base.",
            "Red lentils take about twenty minutes to go soft.",
            "It freezes well for up to three months in sealed portions.",
            "Steamed basmati rice or warm naan both work nicely.",
        ],
    },
    {
        "name": "personal_finance",
        "user_lines": [
            "I want to start building an emergency fund this year.",
            "How many months of expenses should I aim to save?",
            "Should the emergency fund sit in a high yield savings account?",
            "After that, is it better to pay debt or invest first?",
            "How do index funds fit into a long term plan?",
        ],
        "asst_lines": [
            "An emergency fund is your first buffer before investing.",
            "Three to six months of essential expenses is the usual target.",
            "A high yield savings account keeps it liquid and safe.",
            "Clear high interest debt before adding to investments.",
            "Low cost index funds are a solid core for long term growth.",
        ],
    },
    {
        "name": "home_repair",
        "user_lines": [
            "The kitchen tap has started dripping constantly.",
            "Do I need to shut off the water before I open it up?",
            "It looks like a worn rubber washer inside the valve.",
            "What size replacement washer should I buy?",
            "After reassembling it still drips a little, what now?",
        ],
        "asst_lines": [
            "Turn off the isolation valve under the sink first.",
            "Yes, shut the supply and open the tap to drain pressure.",
            "A perished washer is the usual cause of a steady drip.",
            "Take the old washer to the shop to match the size.",
            "Check the valve seat for grit and reseat the washer.",
        ],
    },
    {
        "name": "fitness_training",
        "user_lines": [
            "I want to start running to build up my stamina.",
            "How many days a week should a beginner run?",
            "Should I follow a couch to 5k style plan?",
            "My shins ache after runs, what am I doing wrong?",
            "When can I start adding longer distance runs?",
        ],
        "asst_lines": [
            "Begin with a run walk mix so your joints adapt.",
            "Three sessions a week with rest days is plenty at first.",
            "Couch to 5k is a proven, gentle progression.",
            "Shin pain often means too much too soon, ease back.",
            "Add distance only once the current week feels easy.",
        ],
    },
    {
        "name": "gardening",
        "user_lines": [
            "I want to grow tomatoes on my balcony this summer.",
            "How much sun do the pots need each day?",
            "How often should I water them in hot weather?",
            "The leaves are turning yellow at the bottom, why?",
            "When should I start feeding them tomato fertiliser?",
        ],
        "asst_lines": [
            "Tomatoes are hungry for light, give them full sun.",
            "Aim for at least six hours of direct sun a day.",
            "In heat they may need watering every morning.",
            "Yellow lower leaves often mean they need feeding.",
            "Start feeding weekly once the first fruits set.",
        ],
    },
    {
        "name": "movie_recommendation",
        "user_lines": [
            "Can you recommend a good science fiction film for tonight?",
            "I like slow, thoughtful stories over big action.",
            "Something with a strong visual style would be great.",
            "Is that one suitable to watch with older kids?",
            "What should I watch next if I enjoyed it?",
        ],
        "asst_lines": [
            "For thoughtful science fiction, Arrival is a great pick.",
            "It is character driven and quite meditative in pace.",
            "The cinematography is striking and atmospheric.",
            "It is fine for teenagers, themes are mature but gentle.",
            "If you liked it, try Blade Runner 2049 next.",
        ],
    },
    {
        "name": "language_learning",
        "user_lines": [
            "I want to start learning Spanish from scratch.",
            "How much should I study each day to make progress?",
            "Are flashcards worth the time for vocabulary?",
            "When should I start speaking rather than just reading?",
            "How do I keep myself motivated over months?",
        ],
        "asst_lines": [
            "Consistency beats intensity when learning a language.",
            "Even fifteen focused minutes a day builds real momentum.",
            "Spaced repetition flashcards are excellent for vocabulary.",
            "Start speaking early, even simple phrases out loud.",
            "Tie study to a daily habit so it becomes automatic.",
        ],
    },
    {
        "name": "car_maintenance",
        "user_lines": [
            "My car's engine warning light just came on.",
            "It still drives fine but the light stays orange.",
            "Could it be something simple like a loose fuel cap?",
            "How urgent is it to get the fault code read?",
            "Can I keep driving it to work this week?",
        ],
        "asst_lines": [
            "A steady orange light is a fault to investigate soon.",
            "First check the fuel cap is tight, it is a common trigger.",
            "Any parts shop can read the code for free in minutes.",
            "If the light is not flashing, short trips are usually fine.",
            "Get it scanned before any longer journeys though.",
        ],
    },
    {
        "name": "pet_care",
        "user_lines": [
            "We just adopted a rescue puppy last weekend.",
            "How do I start toilet training in an apartment?",
            "She cries a lot when left alone in a room.",
            "How often should a young puppy be fed each day?",
            "When can she start meeting other dogs safely?",
        ],
        "asst_lines": [
            "Congratulations, routine is everything for a new puppy.",
            "Take her out after every meal, nap and play session.",
            "Build alone time up slowly to ease separation anxiety.",
            "Young puppies do best on three or four small meals.",
            "Wait until her vaccinations are complete before dog parks.",
        ],
    },
    {
        "name": "resume_career",
        "user_lines": [
            "I'm updating my resume for a product manager role.",
            "Should I lead with a summary or jump into experience?",
            "How do I show impact rather than list duties?",
            "Is a single page still expected at my level?",
            "How should I tailor it for each application?",
        ],
        "asst_lines": [
            "Lead with a tight summary that frames your target role.",
            "Quantify outcomes, numbers make impact concrete.",
            "Turn duties into results with metrics and scope.",
            "One page is fine unless you have a decade plus.",
            "Mirror the job description's language for each role.",
        ],
    },
]

# Topic-neutral filler that stays within the current segment (never a boundary).
BACKCHANNEL_LINES: List[str] = [
    "Okay, that makes sense.",
    "Great, thank you.",
    "Got it, I appreciate the help.",
    "Right, understood.",
    "Perfect, that is really useful.",
    "Sounds good to me.",
]


def topic_count() -> int:
    return len(TOPICS)
