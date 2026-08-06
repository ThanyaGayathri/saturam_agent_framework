"""Eval datasets for the SM-R8 retrieval-fusion + reranker decision.

Two independent, non-circular sources (nobody on this team authored the corpora,
the queries, or the labels):

- **BEIR** (SciFact / NFCorpus / FiQA) -- established IR benchmarks with
  independently-authored, graded/binary relevance judgements. Primary evidence
  for fusion quality (nDCG / recall / MRR). Shared-corpus retrieval.
- **LongMemEval-S** (Wu et al., ICLR'25) -- 500 memory questions, each retrieving
  the evidence *session* out of a ~50-session chat haystack, with per-session
  timestamps and a question "now" date. This is the memory-shaped set, and the
  only one that carries a real recency signal, so it is where time-aware composite
  scoring (option e) is measured. Per-question corpus.

Both shapes are unified behind `RetrievalTask`: a self-contained
(corpus, queries, qrels[, times]) unit. A dataset is a list of tasks -- BEIR is one
big task; LongMemEval is 500 small ones. Metrics aggregate per-query across tasks,
so the two shapes are scored identically (see `metrics.py`).

Nothing here touches egpt core. Downloads cache under `MEMBENCH_DATA_DIR`
(default: a local `.membench_cache/` dir) so runs are re-runnable offline after the
first fetch.
"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

# Not a dev-machine scratchpad path -- must resolve on any host that clones this repo.
DEFAULT_DATA_DIR = os.environ.get(
    "MEMBENCH_DATA_DIR",
    os.path.join(tempfile.gettempdir(), "membench_cache", "data"),
)

BEIR_URL = (
    "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{name}.zip"
)
BEIR_DATASETS = ("scifact", "nfcorpus", "fiqa", "arguana", "scidocs")
LONGMEMEVAL_REPO = "xiaowu0162/longmemeval"


# --------------------------------------------------------------------------- #
# Common shape
# --------------------------------------------------------------------------- #
@dataclass
class RetrievalTask:
    """One self-contained retrieval problem: a corpus + queries + judgements.

    doc_times / query_times are populated only for time-aware datasets
    (LongMemEval); they stay None for BEIR, where recency is not a signal.
    """

    task_id: str
    corpus: Dict[str, str]
    queries: Dict[str, str]
    qrels: Dict[str, Dict[str, int]]
    doc_times: Optional[Dict[str, float]] = None
    query_times: Optional[Dict[str, float]] = None


@dataclass
class Dataset:
    name: str
    tasks: List[RetrievalTask]
    has_time: bool
    label_source: str
    notes: List[str] = field(default_factory=list)

    # --- convenience aggregates (used by the data-quality report) ------------ #
    @property
    def n_queries(self) -> int:
        return sum(len(t.queries) for t in self.tasks)

    @property
    def n_docs(self) -> int:
        return sum(len(t.corpus) for t in self.tasks)

    @property
    def n_judged(self) -> int:
        return sum(len(rels) for t in self.tasks for rels in t.qrels.values())


# --------------------------------------------------------------------------- #
# BEIR
# --------------------------------------------------------------------------- #
def _download(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)


def _ensure_beir(name: str, data_dir: str) -> str:
    """Download + unzip a BEIR dataset once; return its extracted folder."""
    folder = os.path.join(data_dir, "beir", name)
    if os.path.isdir(folder) and os.path.exists(os.path.join(folder, "corpus.jsonl")):
        return folder
    zip_path = os.path.join(data_dir, "beir", f"{name}.zip")
    if not os.path.exists(zip_path):
        _download(BEIR_URL.format(name=name), zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(os.path.join(data_dir, "beir"))
    # BEIR zips extract to a folder named after the dataset.
    if not os.path.isdir(folder):
        raise RuntimeError(f"BEIR extract for {name} did not produce {folder}")
    return folder


def load_beir(
    name: str, split: str = "test", data_dir: str = DEFAULT_DATA_DIR
) -> Dataset:
    if name not in BEIR_DATASETS:
        raise ValueError(f"unknown BEIR dataset {name!r}; known: {BEIR_DATASETS}")
    folder = _ensure_beir(name, data_dir)

    corpus: Dict[str, str] = {}
    with open(os.path.join(folder, "corpus.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            text = (row.get("title", "") + " " + row.get("text", "")).strip()
            corpus[str(row["_id"])] = text

    all_queries: Dict[str, str] = {}
    with open(os.path.join(folder, "queries.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            all_queries[str(row["_id"])] = row["text"]

    qrels: Dict[str, Dict[str, int]] = {}
    qrels_path = os.path.join(folder, "qrels", f"{split}.tsv")
    with open(qrels_path, encoding="utf-8") as fh:
        header = fh.readline()  # "query-id\tcorpus-id\tscore"
        assert "query-id" in header, f"unexpected qrels header: {header!r}"
        for line in fh:
            qid, did, score = line.rstrip("\n").split("\t")
            if int(score) <= 0:
                continue
            qrels.setdefault(str(qid), {})[str(did)] = int(score)

    # Keep only queries that have judgements in this split.
    queries = {qid: all_queries[qid] for qid in qrels if qid in all_queries}
    task = RetrievalTask(task_id=name, corpus=corpus, queries=queries, qrels=qrels)
    graded = any(r > 1 for rels in qrels.values() for r in rels.values())
    return Dataset(
        name=f"beir/{name}",
        tasks=[task],
        has_time=False,
        label_source="BEIR human relevance judgements (independently authored)",
        notes=[
            f"split={split}",
            f"graded={graded}",
            "no timestamps -> recency is a null signal here",
        ],
    )


# --------------------------------------------------------------------------- #
# LongMemEval
# --------------------------------------------------------------------------- #
_LME_DATE_FMT = "%Y/%m/%d %H:%M"


def _parse_lme_date(s: str) -> Optional[float]:
    """'2023/05/20 (Sat) 02:21' -> unix seconds. Returns None if unparseable."""
    if not s:
        return None
    # Drop the '(Day)' token.
    parts = [p for p in s.split() if not (p.startswith("(") and p.endswith(")"))]
    cleaned = " ".join(parts)
    try:
        dt = datetime.strptime(cleaned, _LME_DATE_FMT).replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _session_text(session: List[Dict[str, str]]) -> str:
    return "\n".join(
        f"{turn.get('role', '')}: {turn.get('content', '')}" for turn in session
    )


def load_longmemeval(
    variant: str = "longmemeval_s", data_dir: str = DEFAULT_DATA_DIR
) -> Dataset:
    """Session-level LongMemEval retrieval: one task per question.

    corpus = that question's ~50 haystack sessions (each session -> one document),
    query = the question, qrels = the evidence sessions (answer_session_ids),
    doc_times = per-session dates, query_times = the question 'now' date.

    Abstention questions (question_id endswith '_abs', no evidence session) are
    excluded from retrieval scoring -- there is no relevant document to rank.
    """
    from huggingface_hub import hf_hub_download

    os.environ.setdefault("HF_HOME", os.path.join(data_dir, "hf"))
    path = hf_hub_download(LONGMEMEVAL_REPO, variant, repo_type="dataset")
    data = json.load(open(path, encoding="utf-8"))

    tasks: List[RetrievalTask] = []
    n_abstain = 0
    for item in data:
        qid = str(item["question_id"])
        relevant = set(str(s) for s in item.get("answer_session_ids") or [])
        if qid.endswith("_abs") or not relevant:
            n_abstain += 1
            continue
        sess_ids = [str(s) for s in item["haystack_session_ids"]]
        sessions = item["haystack_sessions"]
        dates = item.get("haystack_dates") or []

        corpus: Dict[str, str] = {}
        doc_times: Dict[str, float] = {}
        for i, sid in enumerate(sess_ids):
            corpus[sid] = _session_text(sessions[i])
            ts = _parse_lme_date(dates[i]) if i < len(dates) else None
            if ts is not None:
                doc_times[sid] = ts
        qrels = {qid: {sid: 1 for sid in relevant if sid in corpus}}
        if not qrels[qid]:
            continue  # evidence session missing from haystack -> skip
        qtime = _parse_lme_date(item.get("question_date", ""))
        tasks.append(
            RetrievalTask(
                task_id=qid,
                corpus=corpus,
                queries={qid: item["question"]},
                qrels=qrels,
                doc_times=doc_times or None,
                query_times={qid: qtime} if qtime is not None else None,
            )
        )

    return Dataset(
        name=f"longmemeval/{variant}",
        tasks=tasks,
        has_time=True,
        label_source="LongMemEval evidence-session labels (Wu et al., ICLR'25)",
        notes=[
            f"session-level retrieval; {len(tasks)} scored questions",
            f"{n_abstain} abstention/empty-evidence questions excluded",
            "per-session timestamps + question 'now' date -> real recency signal",
        ],
    )


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def load_dataset(name: str, data_dir: str = DEFAULT_DATA_DIR) -> Dataset:
    """`beir/scifact`, `beir/nfcorpus`, `beir/fiqa`, or `longmemeval/longmemeval_s`."""
    if name.startswith("beir/"):
        return load_beir(name.split("/", 1)[1], data_dir=data_dir)
    if name.startswith("longmemeval/"):
        return load_longmemeval(name.split("/", 1)[1], data_dir=data_dir)
    raise ValueError(f"unknown dataset {name!r}")
