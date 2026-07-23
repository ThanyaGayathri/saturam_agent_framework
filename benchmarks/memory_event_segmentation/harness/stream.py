"""Synthetic labelled multi-topic conversation streams.

A *stream* is a chronological list of messages (``Event``) built by concatenating
coherent single-topic *segments*. The join between two segments is a ground-truth
episode boundary. This gives us exact labels to score segmentation against, plus
knobs to control the difficulty (time gaps, backchannel filler, topic drift).

Design choices (mirrors the SM-R5 harness):
    - Deterministic per ``seed`` (numpy ``RandomState``); ``generate_stream`` and
      ``agenerate_stream`` produce identical output.
    - Each event carries a synthetic embedding ``vector`` (topic base + noise,
      scaled by 1/sqrt(dim) — the SM-R5 lesson so noise does not swamp the topic
      signal). ``embeddings.embed_stream`` can overwrite these with real text
      embeddings for a confirmation run.
    - Not every boundary gets a long time gap (``p_time_gap`` < 1), so the
      time-gap segmenter cannot trivially win — this models a user who sometimes
      switches topic without pausing.

Boundary convention: a boundary at position ``i`` (1 <= i <= N-1) means "a new
episode starts at event ``i``" i.e. the break sits *between* event ``i-1`` and
``i``. ``Event.starts_segment`` marks those first-of-segment events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, cast

import numpy as np

from harness.topics import BACKCHANNEL_LINES, TOPICS, topic_count

# Base epoch for created_at timestamps (arbitrary fixed origin, seconds).
_EPOCH_BASE = 1_700_000_000.0
# Seed offset for the per-topic base embedding vectors (independent of run seed).
_TOPIC_VEC_SEED = 20260716


@dataclass
class StreamParams:
    """Generation knobs. The robustness sweep varies the noise fields."""

    dim: int = 64
    min_segments: int = 8
    max_segments: int = 13
    min_exchanges: int = 3  # per segment (an exchange = user + assistant = 2 events)
    max_exchanges: int = 8
    short_gap_s: tuple = (20.0, 180.0)  # within-segment inter-message gap
    long_gap_s: tuple = (1800.0, 14400.0)  # 30 min .. 4 h, at some boundaries
    p_time_gap: float = 0.6  # fraction of boundaries that get a long gap
    vec_noise: float = 0.35  # embedding noise magnitude
    backchannel_rate: float = 0.08  # topic-neutral filler (not a boundary)
    drift_rate: float = 0.05  # single off-topic excursion (not a boundary)
    # -- external-validity hardening (off by default; the "hard" config turns these on) --
    # Gradual/overlapping boundaries: the first N events of a new segment have their
    # embedding blended prev->new (ramp), so the boundary event is NOT a pure on-topic
    # sample. Addresses the "boundaries maximally detectable by construction" critique.
    gradual_shift_turns: int = 0
    # Mid-topic long pauses: an interior (non-boundary) event may be preceded by a long
    # gap, so time_gap can emit FALSE boundaries — its precision is no longer tautological.
    mid_topic_pause_rate: float = 0.0


@dataclass
class Event:
    """One message in the stream."""

    idx: int
    role: str  # "user" | "assistant"
    text: str
    created_at: float  # epoch seconds
    topic_id: int  # the segment's topic
    starts_segment: bool  # True if this event opens a new (non-first) segment
    is_noise: bool  # backchannel or drift filler
    vector: Optional[np.ndarray] = None  # embedding (synthetic or real)


@dataclass
class Stream:
    """A labelled conversation stream."""

    events: List[Event]
    seed: int
    params: StreamParams = field(default_factory=StreamParams)
    source: str = "synthetic"  # "synthetic" | "tiage"; real data has no timestamps
    has_timestamps: bool = True  # False for real datasets -> time_gap/hybrid not applicable

    @property
    def n_events(self) -> int:
        return len(self.events)

    def true_boundaries(self) -> List[int]:
        """Ground-truth boundary positions (indices that open a new segment)."""
        return [e.idx for e in self.events if e.starts_segment]

    def segment_ids(self) -> List[int]:
        """Segment id per event (monotonic, starts at 0). For Pk / WindowDiff."""
        ids: List[int] = []
        seg = 0
        for e in self.events:
            if e.starts_segment:
                seg += 1
            ids.append(seg)
        return ids

    def matrix(self) -> np.ndarray:
        """(N, dim) stacked embedding matrix. Assumes vectors are populated."""
        return np.vstack([cast(np.ndarray, e.vector) for e in self.events])


def _topic_base_vectors(dim: int) -> np.ndarray:
    """One fixed unit base vector per topic (independent of the run seed)."""
    rng = np.random.RandomState(_TOPIC_VEC_SEED)
    mat = rng.randn(topic_count(), dim)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    return mat


def _event_vector(base: np.ndarray, noise: float, rng: np.random.RandomState) -> np.ndarray:
    """Topic base + isotropic noise scaled by 1/sqrt(dim), renormalised."""
    dim = base.shape[0]
    v = base + noise * rng.randn(dim) / np.sqrt(dim)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def generate_stream(seed: int = 0, params: Optional[StreamParams] = None) -> Stream:
    """Build one deterministic labelled stream for ``seed``."""
    p = params or StreamParams()
    rng = np.random.RandomState(seed)
    bases = _topic_base_vectors(p.dim)
    n_topics = topic_count()

    # Segment topic order (no immediate repeat).
    n_segments = int(rng.randint(p.min_segments, p.max_segments + 1))
    topic_seq: List[int] = []
    prev = -1
    for _ in range(n_segments):
        choices = [t for t in range(n_topics) if t != prev]
        tid = int(rng.choice(choices))
        topic_seq.append(tid)
        prev = tid

    events: List[Event] = []
    t = _EPOCH_BASE
    idx = 0
    # Track last-used line index per (topic, role) to avoid immediate repeats.
    last_line: dict = {}
    # Separate RNG for the external-validity hardening (mid-topic pauses) so enabling
    # it does NOT perturb the segment structure / labels — the only difference vs the
    # base stream is the injected pauses (and gradual boundary blending), giving a
    # controlled comparison rather than a different stream.
    hard_rng = np.random.RandomState(seed + 999983)

    for seg_index, topic_id in enumerate(topic_seq):
        # Boundary time gap before every segment after the first.
        if seg_index > 0:
            if rng.random() < p.p_time_gap:
                t += rng.uniform(*p.long_gap_s)
            else:
                t += rng.uniform(*p.short_gap_s)

        prev_topic = topic_seq[seg_index - 1] if seg_index > 0 else topic_id
        n_events_seg = 2 * int(rng.randint(p.min_exchanges, p.max_exchanges + 1))
        for j in range(n_events_seg):
            role = "user" if j % 2 == 0 else "assistant"
            starts_segment = seg_index > 0 and j == 0

            # Mid-topic long pause: an interior event may follow a long gap without
            # being a boundary -> lets time_gap emit false positives (issue #2).
            if not starts_segment and j > 0 and hard_rng.random() < p.mid_topic_pause_rate:
                t += hard_rng.uniform(*p.long_gap_s)

            is_noise = False
            eff_topic = topic_id
            # Inject noise only on interior events (keep the boundary event clean).
            if not starts_segment:
                roll = rng.random()
                if roll < p.backchannel_rate:
                    # Topic-neutral filler; stays in the current segment.
                    text = BACKCHANNEL_LINES[int(rng.randint(len(BACKCHANNEL_LINES)))]
                    is_noise = True
                elif roll < p.backchannel_rate + p.drift_rate:
                    # Single off-topic excursion (spurious drift, NOT a boundary).
                    others = [x for x in range(n_topics) if x != topic_id]
                    eff_topic = int(rng.choice(others))
                    lines = cast(List[str], TOPICS[eff_topic]["user_lines" if role == "user" else "asst_lines"])
                    text = str(lines[int(rng.randint(len(lines)))])
                    is_noise = True
                else:
                    text = _pick_line(topic_id, role, rng, last_line)
            else:
                text = _pick_line(topic_id, role, rng, last_line)

            # Gradual/overlapping boundary: blend the first few events of a new segment
            # between the previous and new topic so the boundary is not a pure on-topic
            # sample (issue #1). Ramp new-topic weight from ~0.5 toward 1.0.
            if seg_index > 0 and p.gradual_shift_turns > 0 and j < p.gradual_shift_turns and not is_noise:
                w_new = 0.5 + 0.5 * (j + 1) / (p.gradual_shift_turns + 1)
                base_vec = w_new * bases[topic_id] + (1.0 - w_new) * bases[prev_topic]
                vector = _event_vector(base_vec, p.vec_noise, rng)
            else:
                vector = _event_vector(bases[eff_topic], p.vec_noise, rng)
            events.append(
                Event(
                    idx=idx,
                    role=role,
                    text=text,
                    created_at=t,
                    topic_id=topic_id,
                    starts_segment=starts_segment,
                    is_noise=is_noise,
                    vector=vector,
                )
            )
            idx += 1
            t += rng.uniform(*p.short_gap_s)

    return Stream(events=events, seed=seed, params=p)


async def agenerate_stream(seed: int = 0, params: Optional[StreamParams] = None) -> Stream:
    """Async mirror of :func:`generate_stream` (identical output)."""
    return generate_stream(seed=seed, params=params)


def _pick_line(topic_id: int, role: str, rng: np.random.RandomState, last_line: dict) -> str:
    key = "user_lines" if role == "user" else "asst_lines"
    lines = cast(List[str], TOPICS[topic_id][key])
    n = len(lines)
    prev = last_line.get((topic_id, role), -1)
    choice = int(rng.randint(n))
    if n > 1 and choice == prev:
        choice = (choice + 1) % n
    last_line[(topic_id, role)] = choice
    return str(lines[choice])
