"""PA02 -- Order Creation Automation: the document-to-record executors over the sample set.

The use-case definition's promise, run for real: sixteen purchase-order emails for Northwind
Trade Supply (``egpt_retail.documents.samples``) through the compiled sequence's own
executors -- receive, screen, extract, resolve, guardrails, build, cap, write, rollback
point, notify -- and a report of where each one landed. Eight clean orders; one exception
per guardrail and per resolution failure; one scanned copy refused before a model is called.

Four ways to run it, each a step closer to the live mailbox and org:

    .venv/bin/python cookbook/94_retail/PA02_order_creation_pipeline.py
        Offline. The sample messages are injected, the expected extraction stands in for
        the model step, master data is the sample rows, every write is a dry run. No
        credentials, no network. This is what the rehearse screen runs.

    ... --model
        Same, but the Extract stage is a real model call (Azure OpenAI, the deployment in
        AZURE_OPENAI_DEPLOYMENT) through the extraction role's own instructions and a
        schema generated from the purchase_order template. Each extraction is scored
        against the expected one, field by field, so a model's slip is visible as a slip.

    ... --seed-mailbox
        Sends the sixteen sample emails, PDFs attached, to the Gmail inbox in gmail.env --
        each with the sample sender in Reply-To, which is how a procurement system's
        no-reply mail carries the buyer, and which is what the customer lookup reads.

    ... --live [--write]
        The real thing: poll the Gmail inbox over IMAP for the seeded messages, load master
        data from the Salesforce org PA01 populated, extract with the model, resolve and
        guard, and -- only with --write -- create Draft Orders in the org and email the
        run's exceptions to --recipient (default: the mailbox itself). Message ids already
        processed (evidence/PA02_processed.json) are skipped, so a second poll carries
        nothing twice. Every record created is written to evidence/PA02_rollback_<run>.json.

    ... --rollback evidence/PA02_rollback_<run>.json
        Deletes the Orders that run created, newest first, through the same client.

Credentials come from gmail.env, salesforce.env and azure_key.env at the repo root (all
gitignored): source them into the environment before the modes that need them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import smtplib
import sys
import time
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "product"))

from egpt.workflow.types import StepInput  # noqa: E402

from egpt_retail.documents.mailbox import mailbox_from  # noqa: E402
from egpt_retail.documents.samples import (  # noqa: E402
    expected_extraction,
    expected_outcome,
    sample_master_data,
    sample_messages,
)
from egpt_retail.documents.templates import template_for  # noqa: E402
from egpt_retail.executors.act import (  # noqa: E402
    apply_hard_limits,
    apply_per_run_cap,
    record_rollback_point,
    write_to_target,
)
from egpt_retail.executors.documents import (  # noqa: E402
    build_record,
    receive_messages,
    resolve_entities,
    screen_documents,
    send_email,
)

EVIDENCE = REPO_ROOT / "cookbook" / "94_retail" / "evidence"
PROCESSED = EVIDENCE / "PA02_processed.json"
SEED_PREFIX = "Northwind sample: "

CHAIN = (
    receive_messages,
    screen_documents,
    resolve_entities,
    apply_hard_limits,
    build_record,
    apply_per_run_cap,
    write_to_target,
    record_rollback_point,
    send_email,
)


def _load_env(*names: str) -> None:
    """KEY=value lines from the named gitignored files into the environment, without
    overriding what is already set."""
    for name in names:
        path = REPO_ROOT / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key, value)


def _sample_id(document_or_row: Dict[str, Any]) -> str:
    message_id = (
        (document_or_row.get("message") or {}).get("id")
        or document_or_row.get("message_id")
        or ""
    )
    return message_id[1:5].upper() if message_id.startswith("<e-") else message_id


def _stand_in_extractor(document: Dict[str, Any]) -> Dict[str, Any]:
    return expected_extraction(_sample_id(document))


def _model_extractor(scores: List[Dict[str, Any]]) -> Any:
    from egpt.agent import Agent
    from egpt.integrations.models.azure import AzureOpenAI

    from egpt_retail.documents.extraction import (
        extract_with_agent,
        extraction_instructions,
        extraction_output_model,
    )

    template = template_for("purchase_order")
    deployment = (
        os.environ.get("EGPT_RETAIL_AZURE_DEPLOYMENT")
        or os.environ["AZURE_OPENAI_DEPLOYMENT"]
    )
    agent = Agent(
        name="order-creation__extraction",
        model=AzureOpenAI(id=deployment),
        instructions=extraction_instructions(template),
        output_schema=extraction_output_model(template),
    )

    def extractor(document: Dict[str, Any]) -> Dict[str, Any]:
        started = time.time()
        out = extract_with_agent(agent, document)
        sample = _sample_id(document)
        row: Dict[str, Any] = {
            "sample": sample,
            "seconds": round(time.time() - started, 1),
        }
        if sample.startswith("E-"):
            expected = expected_extraction(sample)
            checked = (
                "po_number",
                "customer_name",
                "ship_to_address",
                "due_date",
                "order_total",
            )
            row["header_matches"] = sum(
                1
                for k in checked
                if str(out.get(k) or "").strip().lower()
                == str(expected[k] or "").strip().lower()
            )
            row["header_checked"] = len(checked)
            row["line_count_ok"] = len(out.get("lines") or []) == len(expected["lines"])
            row["quantities_ok"] = [
                float(line.get("quantity") or 0) for line in out.get("lines") or []
            ] == [line["quantity"] for line in expected["lines"]]
        scores.append(row)
        return out

    return extractor


async def _run(options: Dict[str, Any]) -> List[Any]:
    outputs: List[Any] = []
    previous: Any = None
    for executor in CHAIN:
        output = await executor(
            StepInput(previous_step_content=previous, additional_data=options)
        )
        outputs.append(output)
        previous = output.content
        if output.stop:
            break
    return outputs


def _report(outputs: List[Any], *, compare: bool) -> Dict[str, Any]:
    print()
    print("Stages")
    print("-" * 78)
    for output in outputs:
        content = output.content if isinstance(output.content, dict) else {}
        batch = content.get("batch", content)
        name = getattr(output, "step_name", None) or ""
        if "messages" in content and "documents" not in content:
            print(
                f"  {name:<22} messages={content.get('count', 0):<3} source={content.get('source')}"
            )
            continue
        documents = batch.get("documents", []) if isinstance(batch, dict) else []
        exceptions = batch.get("exceptions", []) if isinstance(batch, dict) else []
        print(
            f"  {name:<22} carried={len(documents):<3} exceptions={len(exceptions):<3} stop={output.stop}"
        )

    final = outputs[-1].content
    batch = final.get("batch", final)
    outcomes: Dict[str, str] = {}
    for document in batch.get("documents", []):
        outcomes[_sample_id(document)] = (
            "created"
            if (document.get("written") or {}).get("status") == "written"
            else "previewed"
        )
    for row in batch.get("exceptions", []):
        outcomes[_sample_id(row)] = str(row["class"])

    print()
    print("Outcomes")
    print("-" * 78)
    mismatches = 0
    for key in sorted(outcomes):
        line = f"  {key:<34} {outcomes[key]}"
        if compare and key.startswith("E-"):
            expected = expected_outcome(key)
            ok = outcomes[key] == expected or (
                expected == "created" and outcomes[key] == "previewed"
            )
            mismatches += 0 if ok else 1
            line += f"   (expected {expected}){'' if ok else '  <-- MISMATCH'}"
        print(line)
    if compare:
        print()
        print(f"  mismatches against the definition: {mismatches}")
    rollback = batch.get("rollback") or {}
    print()
    print(f"Notify: {final.get('status')} to {final.get('to')}")
    print(
        f"Rollback points: {rollback.get('points', 0)} (persisted: {rollback.get('persisted', False)})"
    )
    if final.get("body"):
        print()
        print("Mail body")
        print("-" * 78)
        for line in str(final["body"]).splitlines():
            print(f"  {line}")
    return {
        "outcomes": outcomes,
        "mismatches": mismatches if compare else None,
        "rollback": rollback,
        "notify": {k: final.get(k) for k in ("status", "to", "subject")},
    }


def _base_options() -> Dict[str, Any]:
    return {
        "document type": "pdf",
        "extraction template": "purchase_order",
        "match thresholds": {
            "address": 0.75,
            "product": 0.85,
            "name": 0.8,
            "ambiguity": 0.05,
        },
        "guardrails": "all",
        "target objects": "order_orderitem",
        "created as": "draft",
        "per-run cap": "50 records",
        "rollback": "Cancel the record and remove its lines",
    }


def offline(use_model: bool) -> int:
    scores: List[Dict[str, Any]] = []
    options = {
        **_base_options(),
        "messages": sample_messages(),
        "source mailbox": {"user": "orders@northwind.example", "password": "sample"},
        "master data": sample_master_data(),
        "write target": {"type": "salesforce"},
        "recipient": "ops@northwind.example",
        "extractor": _model_extractor(scores) if use_model else _stand_in_extractor,
        "dry_run": True,
    }
    return _finish(options, scores, live=False)


def _finish(
    options: Dict[str, Any], scores: List[Dict[str, Any]], *, live: bool
) -> int:
    outputs = asyncio.run(_run(options))
    for executor, output in zip(CHAIN, outputs):
        output.step_name = executor.__name__
    summary = _report(outputs, compare=not live)
    if scores:
        print()
        print("Extraction, scored against the expected extraction")
        print("-" * 78)
        for row in scores:
            print(
                f"  {row['sample']:<8} header {row.get('header_matches', '?')}/{row.get('header_checked', '?')}  lines {'ok' if row.get('line_count_ok') else 'DIFF'}  quantities {'ok' if row.get('quantities_ok') else 'DIFF'}  {row['seconds']}s"
            )
    EVIDENCE.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = {
        "ran_at": stamp,
        "live": live,
        "model": bool(scores),
        **summary,
        "extraction_scores": scores,
    }
    # Rehearsal evidence is committed under a stable name (one offline, one with the
    # model); live runs are per-inbox state and gitignored.
    name = (
        f"PA02_live_{stamp}.json"
        if live
        else f"PA02_rehearsal_{'model' if scores else 'offline'}.json"
    )
    (EVIDENCE / name).write_text(json.dumps(evidence, indent=2, default=str))
    if live:
        points = options.get("rollback_points") or []
        if points:
            path = EVIDENCE / f"PA02_rollback_{stamp}.json"
            path.write_text(json.dumps(points, indent=2, default=str))
            print(f"\nRollback file: {path}")
        seen = set(json.loads(PROCESSED.read_text())) if PROCESSED.exists() else set()
        final = outputs[-1].content
        batch = final.get("batch", final)
        for document in batch.get("documents", []):
            seen.add(document["message"]["id"])
        for row in batch.get("exceptions", []):
            seen.add(row["message_id"])
        PROCESSED.write_text(json.dumps(sorted(seen), indent=2))
    return 0 if (summary["mismatches"] or 0) == 0 else 1


def _already_seeded(mailbox: Any) -> set:
    """The sample subjects already in the inbox, so a rerun after a Gmail throttle sends
    only what is missing rather than a second copy of everything."""
    import email.header
    import imaplib

    subjects: set = set()
    client = imaplib.IMAP4_SSL(mailbox.imap_host)
    try:
        client.login(mailbox.user, mailbox.password)
        client.select(mailbox.folder)
        status, data = client.search(None, "SUBJECT", f'"{SEED_PREFIX.rstrip(": ")}"')
        for uid in (data[0] or b"").split() if status == "OK" else []:
            status, parts = client.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
            for part in parts:
                if isinstance(part, tuple):
                    raw = part[1].decode("utf-8", "replace").partition(":")[2].strip()
                    decoded = "".join(
                        (t.decode(enc or "utf-8") if isinstance(t, bytes) else t)
                        for t, enc in email.header.decode_header(raw)
                    )
                    subjects.add(decoded.replace(SEED_PREFIX, "", 1).strip())
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return subjects


def seed_mailbox() -> int:
    """Send the sample emails to the inbox. Idempotent: subjects already there are skipped,
    and a temporary SMTP refusal (Gmail's 421 rate limit) is retried on a fresh connection
    after a pause rather than aborting the batch part-way."""
    _load_env("gmail.env")
    mailbox = mailbox_from(None)
    if not mailbox.configured:
        print(
            "gmail.env is missing: GMAIL_USER and GMAIL_APP_PASSWORD are needed to seed the inbox"
        )
        return 2
    present = _already_seeded(mailbox)
    if present:
        print(f"  {len(present)} sample emails already in the inbox; sending the rest")

    def connect() -> smtplib.SMTP:
        client = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port)
        client.starttls()
        client.login(mailbox.user, mailbox.password)
        return client

    client = connect()
    sent = 0
    skipped = 0
    try:
        for message in sample_messages():
            if message.subject in present:
                skipped += 1
                continue
            mail = EmailMessage()
            mail["From"] = mailbox.user
            mail["To"] = mailbox.user
            mail["Reply-To"] = message.sender
            mail["Subject"] = SEED_PREFIX + message.subject
            mail.set_content(
                message.body_text + f"\n\n(sample {message.id}; buyer {message.sender})"
            )
            attachment = message.attachments[0]
            mail.add_attachment(
                attachment.data,
                maintype="application",
                subtype="pdf",
                filename=attachment.filename,
            )
            for attempt in range(1, 5):
                try:
                    client.send_message(mail)
                    break
                except (smtplib.SMTPDataError, smtplib.SMTPServerDisconnected) as exc:
                    code = getattr(exc, "smtp_code", 0)
                    if attempt == 4 or not (400 <= int(code or 421) < 500):
                        raise
                    wait = 15 * attempt
                    print(
                        f"  Gmail refused temporarily ({code}); waiting {wait}s and reconnecting"
                    )
                    try:
                        client.quit()
                    except Exception:
                        pass
                    time.sleep(wait)
                    client = connect()
            sent += 1
            print(f"  sent {message.subject:<28} reply-to {message.sender}")
            time.sleep(2)  # Gmail throttles bursts of attachments from one account
    finally:
        try:
            client.quit()
        except Exception:
            pass
    print(
        f"\n{sent} sample emails sent to {mailbox.user}, {skipped} already there. "
        "Give Gmail a minute, then run the use case."
    )
    return 0


def live(write: bool, recipient: Optional[str], use_model: bool) -> int:
    _load_env("gmail.env", "salesforce.env", "azure_key.env")
    mailbox = mailbox_from(None)
    if not mailbox.configured:
        print("gmail.env is missing")
        return 2
    if not os.environ.get("SF_LOGIN_URL"):
        print("salesforce.env is missing")
        return 2
    processed = json.loads(PROCESSED.read_text()) if PROCESSED.exists() else []
    scores: List[Dict[str, Any]] = []
    options = {
        **_base_options(),
        "source mailbox": {
            "user": mailbox.user,
            "password": mailbox.password,
            "imap_host": mailbox.imap_host,
            "smtp_host": mailbox.smtp_host,
        },
        "message filter": {
            "unseen_only": False,
            "subject_contains": SEED_PREFIX.rstrip(": "),
            "since_days": 3,
            "max_messages": 50,
        },
        "processed_ids": processed,
        "mark_seen": False,
        "master data": {"source": "salesforce"},
        "write target": {
            "type": "salesforce",
            "login_url": os.environ["SF_LOGIN_URL"],
            "client_id": os.environ["SF_CLIENT_ID"],
            "client_secret": os.environ["SF_CLIENT_SECRET"],
        },
        "recipient": recipient or mailbox.user,
        "extractor": _model_extractor(scores) if use_model else _stand_in_extractor,
        "dry_run": not write,
        "run_id": f"pa02-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "use_case_id": "order-creation-automation",
    }
    print(
        f"Polling {mailbox.user} for '{SEED_PREFIX.rstrip(': ')}' messages, {len(processed)} already processed; writes {'ON' if write else 'off (dry run)'}"
    )
    return _finish(options, scores, live=True)


def rollback(path: Path) -> int:
    _load_env("salesforce.env")
    from egpt_retail.documents.salesforce import client_from, delete_record

    points = json.loads(path.read_text())
    client = client_from(None)
    try:
        for point in reversed(points):
            delete_record(client, point["object"], point["record_id"])
            print(
                f"  deleted {point['object']} {point['record_id']} (PO {(point.get('content') or {}).get('po_number')})"
            )
    finally:
        client.close()
    print(f"\n{len(points)} record(s) reverted from {path.name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--model",
        action="store_true",
        help="extract with the real model instead of the expected extraction",
    )
    parser.add_argument(
        "--seed-mailbox",
        action="store_true",
        help="send the sixteen sample emails to the Gmail inbox",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="poll the Gmail inbox and resolve against the Salesforce org",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="with --live: create the Orders and send the exception mail for real",
    )
    parser.add_argument(
        "--recipient",
        help="with --live: where the exception mail goes (default: the mailbox)",
    )
    parser.add_argument(
        "--rollback",
        type=Path,
        help="delete the Orders a live run created (evidence/PA02_rollback_*.json)",
    )
    args = parser.parse_args()
    print(f"PA02 -- Order Creation Automation, {date.today().isoformat()}")
    if args.rollback:
        return rollback(args.rollback)
    if args.seed_mailbox:
        return seed_mailbox()
    if args.live:
        _load_env("azure_key.env")
        # Live always extracts with the model: the expected extraction only exists for the
        # sample set, and a live poll may carry anything.
        return live(args.write, args.recipient, use_model=True)
    if args.model:
        _load_env("azure_key.env")
    return offline(args.model)


if __name__ == "__main__":
    sys.exit(main())
