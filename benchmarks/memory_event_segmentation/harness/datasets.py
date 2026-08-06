"""Real-dataset loaders (external, non-circular cross-check).

Loads the vendored TIAGE topic-shift test split (`data/tiage_test.json`, built by
`data/fetch_tiage.py`) into the same `Stream`/`Event` structures the synthetic
harness uses, so every segmenter runs on it unchanged. TIAGE turns are **real,
short, elliptical PersonaChat utterances** with **human-annotated** boundaries —
independent of the harness author, which is exactly the external validity the
synthetic stream cannot provide.

Real dialogues have **no per-turn timestamps**, so `time_gap` (and the
time-gap-anchored hybrid) are not applicable here — the runner skips them for
`source == "tiage"`. Embeddings are real text embeddings (filled by
`embeddings.embed_stream`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from harness.stream import Event, Stream, StreamParams

_DATA = Path(__file__).parent.parent / "data" / "tiage_test.json"


def load_tiage(path: Optional[Path] = None, limit: Optional[int] = None) -> List[Stream]:
    """Load TIAGE dialogues as `Stream`s (one per dialogue). Vectors unfilled."""
    src = path or _DATA
    if not src.exists():
        raise SystemExit(f"{src} missing — run: ./.venv/bin/python data/fetch_tiage.py")
    payload = json.loads(src.read_text())
    dialogues = payload["dialogues"]
    if limit is not None:
        dialogues = dialogues[:limit]

    streams: List[Stream] = []
    for d_i, dlg in enumerate(dialogues):
        turns: List[str] = dlg["turns"]
        boundaries = set(int(b) for b in dlg["boundaries"])
        events: List[Event] = []
        seg = 0
        for i, text in enumerate(turns):
            starts = i in boundaries and i > 0
            if starts:
                seg += 1
            events.append(
                Event(
                    idx=i,
                    role="user" if i % 2 == 0 else "assistant",
                    text=text,
                    created_at=float(i),  # placeholder; not a real timestamp
                    topic_id=seg,
                    starts_segment=starts,
                    is_noise=False,
                    vector=None,
                )
            )
        streams.append(
            Stream(
                events=events,
                seed=d_i,
                params=StreamParams(),
                source="tiage",
                has_timestamps=False,
            )
        )
    return streams


async def aload_tiage(path: Optional[Path] = None, limit: Optional[int] = None) -> List[Stream]:
    """Async mirror of :func:`load_tiage`."""
    return load_tiage(path=path, limit=limit)
