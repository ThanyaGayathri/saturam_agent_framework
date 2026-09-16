"""PA03 -- a second document-to-record use case, built from configuration alone: return
requests that arrive by email become Salesforce Cases (K21).

The point of the exercise is what is *not* here. The executors, the extraction agent, the
resolver, the guardrails, the mapping interpreter and the writer are the ones PA02 ran for
purchase orders. What changes is data: a different extraction template (``return_request``),
a different mapping (a Case per document, no child), two guardrails suggested from the
template's own fields, and -- the K21 change -- a Resolve stage that resolves only the roles
this mapping reads (the sender's account and contact) instead of every role the purchase
order needed. No ship-to is asked for, no product is matched, and a return with no address
on it is not an exception.

Three ways to run it:

    .venv/bin/python cookbook/94_retail/PA03_return_request_cases.py
        Offline. Cases are generated from the template and the sample master data
        (``documents/golden.py``), injected as messages, the expected values stand in for
        the model, every write is a dry run. Reports where each one landed against what it
        was expected to do.

    ... --write-samples [--dir ~/Downloads/northwind-samples/returns]
        Writes the same cases as PDFs a person can upload on the rehearse screen, with
        ``expected.json`` (the outcome and header values to key in as expected output) and
        a README. Generated from the org's own accounts, contacts and products when
        salesforce.env resolves, so the senders are real customers of the org.

    ... --seed-mailbox [--only RR-1001,RR-1002]
        Sends those cases to the Gmail inbox in gmail.env as emails, PDF attached, with the
        customer's contact in Reply-To. Subjects start "Northwind returns:", so the order
        use case's filter ("Northwind sample") never sees them.

Credentials come from gmail.env and salesforce.env at the repo root (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "product"))

from egpt.workflow.types import StepInput  # noqa: E402

from egpt_retail.documents.golden import (  # noqa: E402
    case_to_message,
    generate_cases,
    score_case,
)
from egpt_retail.documents.guardrails import suggest_rules  # noqa: E402
from egpt_retail.documents.mailbox import mailbox_from  # noqa: E402
from egpt_retail.documents.mapping import mapping_from  # noqa: E402
from egpt_retail.documents.roles import roles_needed  # noqa: E402
from egpt_retail.documents.samples import sample_master_data  # noqa: E402
from egpt_retail.documents.templates import template_from  # noqa: E402
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

SEED_PREFIX = "Northwind returns: "
DEFAULT_DIR = Path.home() / "Downloads" / "northwind-samples" / "returns"

#: The mapping the form would assemble over the org's Case object: what the rehearse screen
#: suggests when the parent is Case, edited only to fix the origin.
CASE_MAPPING: Dict[str, Any] = {
    "id": "case_per_return_request",
    "label": "Case per return request",
    "target": "salesforce",
    "object": "Case",
    "record": "case",
    "fields": [
        {"target": "AccountId", "source": ["customer.id"], "required": True},
        {"target": "ContactId", "source": ["contact.id"], "required": False},
        {
            "target": "Subject",
            "source": ["rma_reference"],
            "required": True,
            "transform": "text:255",
        },
        {"target": "Description", "source": ["reason"], "transform": "text:32000"},
        {"target": "Origin", "source": ["=Email"]},
    ],
    "child": None,
    "status_field": "Status",
    "status_values": {"draft": "New", "active": "Working"},
    "rollback_action": "delete",
}


def _load_env(*names: str) -> None:
    for name in names:
        path = REPO_ROOT / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key and not key.startswith("#"):
                os.environ.setdefault(key, value)


def _master_data(prefer_org: bool) -> Dict[str, Any]:
    """The org's master data when salesforce.env resolves, else the sample rows."""
    if prefer_org:
        _load_env("salesforce.env")
        try:
            from egpt_retail.documents.salesforce import client_from, load_master_data
            from egpt_retail.usecases.credentials import resolve_credentials

            client = client_from(
                resolve_credentials(
                    {"credentialRef": "env:salesforce"}, kind="salesforce"
                )
            )
            try:
                master = load_master_data(client)
            finally:
                client.close()
            print(
                f"  master data from the org: {len(master.get('customer') or [])} accounts, "
                f"{len(master.get('contact') or [])} contacts"
            )
            return master
        except Exception as exc:  # no org reachable: the sample set is honest enough
            print(f"  org not reachable ({exc}); using the sample master data")
    return sample_master_data()


def build_cases(master: Dict[str, Any]) -> List[Dict[str, Any]]:
    template = template_from("return_request")
    rules = [
        r
        for r in suggest_rules(template)
        if r.kind in {"sender_known", "duplicate_reference"}
    ]
    needed = roles_needed(mapping_from(CASE_MAPPING), rules)
    cases = generate_cases(template, master, rules, roles=needed)
    seen: Dict[str, int] = {}
    for case in cases:
        stem = case["filename"].removesuffix(".pdf")
        seen[stem] = seen.get(stem, 0) + 1
        resent = seen[stem] > 1
        # The resent copy shares the reference (that is the duplicate) but not the id or
        # the subject, so it is carried as its own message and seeded as its own email.
        case["id"] = f"{stem}-resent" if resent else stem
        case["filename"] = f"{case['id']}.pdf"
        case["subject"] = f"Return request {stem.split('-', 1)[1]}" + (
            " (resent)" if resent else ""
        )
    return cases


def _rules_as_dicts() -> List[Dict[str, Any]]:
    template = template_from("return_request")
    return [
        r.as_dict()
        for r in suggest_rules(template)
        if r.kind in {"sender_known", "duplicate_reference"}
    ]


def offline() -> int:
    master = sample_master_data()
    cases = build_cases(master)
    template = template_from("return_request")
    by_id = {c["id"]: c for c in cases}

    def extractor(document: Dict[str, Any]) -> Dict[str, Any]:
        case = by_id[document["message"]["id"][1:-7]]
        expected = case["expected"]["extracted"]
        return {
            **{name: expected.get(name) for name, _ in template.header},
            "reason": "Two drums arrived dented; contents unusable.",
            "lines": [
                {
                    "line_no": 1,
                    "product": "Returned item",
                    "quantity": 2,
                    "condition": "damaged",
                }
            ],
        }

    options = {
        "messages": [case_to_message(c) for c in cases],
        "document type": "pdf",
        "extraction template": template.id,
        "master data": master,
        "guardrails": _rules_as_dicts(),
        "target objects": CASE_MAPPING,
        "created as": "draft",
        "per-run cap": "50 records",
        "write target": {"type": "salesforce"},
        "recipient": "returns@northwind.example",
        "rollback": "delete",
        "extractor": extractor,
        "dry_run": True,
        "source mailbox": {"user": "returns@northwind.example", "password": "sample"},
    }

    async def run() -> Any:
        previous: Any = None
        for step in (
            receive_messages,
            screen_documents,
            resolve_entities,
            apply_hard_limits,
            build_record,
            apply_per_run_cap,
            write_to_target,
            record_rollback_point,
            send_email,
        ):
            out = await step(
                StepInput(previous_step_content=previous, additional_data=options)
            )
            previous = out.content
        return previous

    result = asyncio.run(run())
    batch = result.get("batch", result)
    print(f"roles resolved: {', '.join(batch.get('roles_needed') or [])}")
    print(f"reference field: {batch.get('reference_field')}\n")
    documents = {d["message"]["id"]: d for d in batch["documents"]}
    exceptions = {e["message_id"]: e for e in batch["exceptions"]}
    failures = 0
    for case in cases:
        message_id = f"<{case['id']}@cases>"
        verdict = score_case(
            case,
            document=documents.get(message_id),
            exception=exceptions.get(message_id),
        )
        status = verdict["status"].upper()
        failures += status != "PASS"
        record = (documents.get(message_id) or {}).get("record") or {}
        print(
            f"  {status:<4} {case['label']:<28} expected {verdict['expected_outcome']:<20} "
            f"got {verdict['outcome']:<20} "
            + (
                f"-> {record.get('object')} {json.dumps(record.get('fields'))}"
                if record
                else ""
            )
        )
    print(f"\n{len(cases) - failures} of {len(cases)} cases landed as expected")
    return 1 if failures else 0


def write_samples(directory: Path) -> int:
    master = _master_data(prefer_org=True)
    cases = build_cases(master)
    directory.mkdir(parents=True, exist_ok=True)
    expected: Dict[str, Any] = {}
    for case in cases:
        (directory / case["filename"]).write_bytes(
            base64.b64decode(case["document_b64"])
        )
        expected[case["filename"]] = {
            "label": case["label"],
            "send_as": case["sender"],
            "subject": case["subject"],
            "expected": case["expected"],
        }
    (directory / "expected.json").write_text(json.dumps(expected, indent=2))
    lines = [
        "# Northwind return requests -- sample set for the Case use case",
        "",
        "Upload any of these on the rehearse screen (Upload documents), then key in the",
        "expected output from expected.json: the outcome (created, or the exception class)",
        "and the header values. `send_as` is the sender to give the upload, so the sender",
        "account resolves the way the email would.",
        "",
        "| File | Send as | Expected outcome |",
        "|---|---|---|",
    ]
    for name, row in expected.items():
        lines.append(f"| {name} | {row['send_as']} | {row['expected']['outcome']} |")
    (directory / "README.md").write_text("\n".join(lines) + "\n")
    print(f"{len(cases)} PDFs, expected.json and README.md written to {directory}")
    for name, row in expected.items():
        print(f"  {name:<14} {row['expected']['outcome']:<20} as {row['send_as']}")
    return 0


def _already_seeded(mailbox: Any) -> set:
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


def seed_mailbox(only: Optional[List[str]]) -> int:
    """Send the cases to the inbox, one email each, the customer's contact in Reply-To.
    Idempotent on subject; Gmail's temporary refusals are retried after a pause."""
    _load_env("gmail.env")
    mailbox = mailbox_from(None)
    if not mailbox.configured:
        print("gmail.env is missing: GMAIL_USER and GMAIL_APP_PASSWORD are needed")
        return 2
    cases = build_cases(_master_data(prefer_org=True))
    present = _already_seeded(mailbox)

    def connect() -> smtplib.SMTP:
        client = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port)
        client.starttls()
        client.login(mailbox.user, mailbox.password)
        return client

    client = connect()
    sent = skipped = 0
    try:
        for case in cases:
            if only and case["id"] not in only:
                continue
            if case["subject"] in present:
                skipped += 1
                continue
            mail = EmailMessage()
            mail["From"] = mailbox.user
            mail["To"] = mailbox.user
            mail["Reply-To"] = case["sender"]
            mail["Subject"] = SEED_PREFIX + case["subject"]
            mail.set_content(
                "Please find our return request attached.\n\n"
                f"({case['label']}; sent as {case['sender']})"
            )
            mail.add_attachment(
                base64.b64decode(case["document_b64"]),
                maintype="application",
                subtype="pdf",
                filename=case["filename"],
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
                    print(f"  Gmail refused temporarily ({code}); waiting {wait}s")
                    try:
                        client.quit()
                    except Exception:
                        pass
                    time.sleep(wait)
                    client = connect()
            sent += 1
            print(
                f"  sent {case['subject']:<24} {case['expected']['outcome']:<20} reply-to {case['sender']}"
            )
            time.sleep(2)
    finally:
        try:
            client.quit()
        except Exception:
            pass
    print(f"\n{sent} emails sent to {mailbox.user}, {skipped} already there.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write-samples", action="store_true")
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    parser.add_argument("--seed-mailbox", action="store_true")
    parser.add_argument(
        "--only", default=None, help="comma-separated ids, RR-1001,RR-1002"
    )
    args = parser.parse_args()
    only = [x.strip() for x in args.only.split(",")] if args.only else None
    if args.write_samples:
        return write_samples(Path(args.dir).expanduser())
    if args.seed_mailbox:
        return seed_mailbox(only)
    return offline()


if __name__ == "__main__":
    raise SystemExit(main())
