#!/usr/bin/env python3
"""Audit merged PRs for the "wrong merge order" problem: a PR merged into a non-main
feature branch whose content never made it into `main` because the feature branch was
merged to main before (or without) the stacked PR being retargeted.

Usage:
    python scripts/pr_merge_order_audit.py [--repo OWNER/NAME] [--out-dir DIR]

Writes two CSVs into --out-dir (default: current directory):
    merged_prs.csv          -- one row per merged PR (extraction)
    merge_order_audit.csv   -- merged_prs.csv plus on_main / note columns (analysis)

Requires `gh` (authenticated) and `git`, run from inside a clone of the repo with an
`origin` remote.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

ISSUE_LINK_RE = re.compile(
    r"(?i)\b(?:clos(?:e|es|ed)|fix(?:e|es|ed)?|resolves?|part of|related(?: to)?)\b"
    r"[:\s]*((?:(?:[\w.-]+/[\w.-]+)?#\d+(?:\s*,\s*)?)+)"
)
ISSUE_NUM_RE = re.compile(r"(?:[\w.-]+/[\w.-]+)?#\d+")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def fetch_merged_prs(repo: str) -> list[dict]:
    result = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--limit",
            "2000",
            "--json",
            "number,title,baseRefName,headRefName,mergedAt,mergeCommit,url,body",
        ]
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit("gh pr list failed")
    return json.loads(result.stdout)


def extract_issue_links(body: str) -> str:
    found: list[str] = []
    for match in ISSUE_LINK_RE.finditer(body or ""):
        for num in ISSUE_NUM_RE.findall(match.group(1)):
            if num not in found:
                found.append(num)
    return ";".join(found)


def write_extraction_csv(prs: list[dict], path: Path) -> list[dict]:
    rows = []
    for pr in prs:
        merge_commit = (pr.get("mergeCommit") or {}).get("oid", "")
        row = {
            "pr_number": pr["number"],
            "title": pr["title"],
            "source_branch": pr["headRefName"],
            "target_branch": pr["baseRefName"],
            "merged_at": pr["mergedAt"],
            "merge_commit": merge_commit,
            "issue_links": extract_issue_links(pr.get("body") or ""),
            "url": pr["url"],
        }
        rows.append(row)
    rows.sort(key=lambda r: r["merged_at"])
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    return rows


def ensure_local(sha: str) -> bool:
    if not sha:
        return False
    have = run(["git", "cat-file", "-e", f"{sha}^{{commit}}"])
    if have.returncode == 0:
        return True
    fetched = run(["git", "fetch", "--no-tags", "origin", sha])
    return fetched.returncode == 0


def is_ancestor_of_main(sha: str) -> str:
    """Returns 'yes', 'no', or 'unknown' (commit unreachable, e.g. branch deleted+GC'd)."""
    if not ensure_local(sha):
        return "unknown"
    check = run(["git", "merge-base", "--is-ancestor", sha, "origin/main"])
    if check.returncode == 0:
        return "yes"
    if check.returncode == 1:
        return "no"
    return "unknown"


def added_files_survive_on_main(sha: str) -> str:
    """Content-based fallback for a base branch that was *squash*-merged into main:
    a squash merge produces a single, standalone commit on main, so the original PR's
    merge commit (living only on the now-orphaned feature branch) is never a literal git
    ancestor of main even though its changes did land. Heuristic: does every file this PR
    newly *added* still exist in origin/main? Returns 'yes', 'no', or 'inconclusive' (the
    PR added no new files, so this check can't say anything either way).
    """
    parents = run(["git", "log", "--format=%P", "-1", sha]).stdout.split()
    if not parents:
        return "inconclusive"
    diff = run(["git", "diff", "--name-status", parents[0], sha])
    added = [
        line.split("\t", 1)[1]
        for line in diff.stdout.splitlines()
        if line.startswith("A\t")
    ]
    if not added:
        return "inconclusive"
    for path in added:
        if run(["git", "cat-file", "-e", f"origin/main:{path}"]).returncode != 0:
            return "no"
    return "yes"


def write_analysis_csv(rows: list[dict], path: Path) -> list[dict]:
    run(["git", "fetch", "origin", "main"])
    analyzed = []
    for row in rows:
        row = dict(row)
        if row["target_branch"] == "main":
            row["on_main"] = "yes"
            row["note"] = ""
        else:
            status = is_ancestor_of_main(row["merge_commit"])
            if status == "no" and ensure_local(row["merge_commit"]):
                fallback = added_files_survive_on_main(row["merge_commit"])
                if fallback == "yes":
                    row["on_main"] = "yes"
                    row["note"] = (
                        "not a git ancestor of main (base branch was likely squash-merged), "
                        "but this PR's added files are present on main -- content landed"
                    )
                    analyzed.append(row)
                    continue
            row["on_main"] = status
            if status == "no":
                row["note"] = (
                    "STRANDED: merged off main, commit not reachable from main"
                )
            elif status == "unknown":
                row["note"] = (
                    "commit unreachable locally (branch likely deleted); check manually"
                )
            else:
                row["note"] = (
                    "merged off main but content reached main via a later merge"
                )
        analyzed.append(row)

    fieldnames = list(analyzed[0].keys()) if analyzed else []
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(analyzed)
    return analyzed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="Saturam-Inc/saturam-agent-framework")
    parser.add_argument("--out-dir", default=".")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching merged PRs for {args.repo} ...")
    prs = fetch_merged_prs(args.repo)
    print(f"Found {len(prs)} merged PRs.")

    extraction_path = out_dir / "merged_prs.csv"
    rows = write_extraction_csv(prs, extraction_path)
    print(f"Wrote {extraction_path}")

    print("Checking ancestry against origin/main for non-main-target PRs ...")
    analysis_path = out_dir / "merge_order_audit.csv"
    analyzed = write_analysis_csv(rows, analysis_path)
    print(f"Wrote {analysis_path}")

    off_main = [r for r in analyzed if r["target_branch"] != "main"]
    stranded = [r for r in off_main if r["on_main"] == "no"]
    unknown = [r for r in off_main if r["on_main"] == "unknown"]

    print()
    print(f"PRs merged into a non-main branch: {len(off_main)}")
    print(f"  -> stranded (not reachable from main): {len(stranded)}")
    print(f"  -> unknown (commit unreachable, branch likely deleted): {len(unknown)}")
    if stranded:
        print()
        print("Stranded PRs:")
        for r in stranded:
            print(
                f"  #{r['pr_number']} {r['source_branch']} -> {r['target_branch']}  {r['url']}"
            )
    if unknown:
        print()
        print("Unknown (verify manually, e.g. via GitHub compare view):")
        for r in unknown:
            print(
                f"  #{r['pr_number']} {r['source_branch']} -> {r['target_branch']}  {r['url']}"
            )


if __name__ == "__main__":
    main()
