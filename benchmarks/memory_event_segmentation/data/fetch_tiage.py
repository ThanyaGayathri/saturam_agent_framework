"""Fetch + process the TIAGE topic-shift test set into a compact local file.

TIAGE (Xie et al., EMNLP-Findings 2021, "TIAGE: A Benchmark for Topic-Shift Aware
Dialog Modeling") provides **human-annotated topic-shift labels** over real
PersonaChat dialogues — short, elliptical, context-dependent turns. We use its
annotated test split as an **independent, non-circular** external cross-check for
the segmentation benchmark (different authors write the text and label the
boundaries than the harness author).

Source: https://github.com/HuiyuanXie/tiage  (MIT License, (c) 2021 Huiyuan Xie)
File:   data/personachat/anno/test/anno_test.json

Label convention in the source: each turn is ``[text, label]`` where
``label == '1'`` marks a **topic shift** (a new episode begins at that turn),
``'0'`` continues the current topic, and ``'-1'`` is the first turn. We convert a
label-'1' at turn ``i`` into a boundary at position ``i`` (matches the harness
convention: a boundary opens a new segment at event ``i``; ``0`` is never one).

Run once to (re)generate ``data/tiage_test.json`` (committed for offline runs):

    python data/fetch_tiage.py
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

_REPO = "HuiyuanXie/tiage"
_SRC_PATH = "data/personachat/anno/test/anno_test.json"
_OUT = Path(__file__).parent / "tiage_test.json"


def _download_raw() -> dict:
    """Fetch anno_test.json via the authenticated gh API (base64 contents)."""
    out = subprocess.check_output(
        ["gh", "api", f"repos/{_REPO}/contents/{_SRC_PATH}", "--jq", ".content"],
        text=True,
    )
    return json.loads(base64.b64decode(out).decode())


def process(raw: dict) -> dict:
    """Convert the raw {id: [[text, label], ...]} into our compact schema."""
    dialogues = []
    for dlg_id, turns in raw.items():
        texts = [str(t[0]).strip() for t in turns]
        boundaries = [i for i, t in enumerate(turns) if str(t[1]) == "1" and i > 0]
        dialogues.append({"id": str(dlg_id), "turns": texts, "boundaries": boundaries})
    return {
        "source": "TIAGE (Xie et al. 2021), PersonaChat annotated test split",
        "url": f"https://github.com/{_REPO}",
        "license": "MIT (c) 2021 Huiyuan Xie",
        "label_note": "boundary = turn annotated as a topic shift (label '1'); real elliptical turns; no timestamps",
        "n_dialogues": len(dialogues),
        "dialogues": dialogues,
    }


def main() -> None:
    raw = _download_raw()
    data = process(raw)
    _OUT.write_text(json.dumps(data, indent=2))
    n_turns = sum(len(d["turns"]) for d in data["dialogues"])
    n_b = sum(len(d["boundaries"]) for d in data["dialogues"])
    print(f"Wrote {_OUT}")
    print(f"dialogues={data['n_dialogues']} turns={n_turns} boundaries={n_b}")


if __name__ == "__main__":
    main()
