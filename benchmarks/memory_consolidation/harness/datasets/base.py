"""Common dataset container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from harness.schemas import Episode, TargetFact


@dataclass
class Dataset:
    """A stream of episodes grouped into sessions, with optional gold labels.

    - ``sessions`` is the consolidation input: each inner list is one batch
      (a session), fed to the strategy in order.
    - ``targets`` are gold semantic facts (synthetic only) for label metrics.
    - ``qa_pairs`` are question/answer probes for QA correctness (any dataset).
    """

    name: str
    sessions: List[List[Episode]]
    targets: List[TargetFact] = field(default_factory=list)
    qa_pairs: List[Dict[str, str]] = field(default_factory=list)

    @property
    def episode_ids(self) -> set:
        return {e.episode_id for s in self.sessions for e in s}

    @property
    def n_episodes(self) -> int:
        return sum(len(s) for s in self.sessions)
