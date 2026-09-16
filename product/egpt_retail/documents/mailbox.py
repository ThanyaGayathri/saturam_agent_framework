"""The mailbox: messages in over IMAP, exceptions out over SMTP (K15's Receive and Notify).

Configuration comes from the slot value (``source mailbox`` is the configured source's own
record: host, user, an app password) or, when a field is absent, from the ``GMAIL_*``
environment the demo's ``gmail.env`` carries. Nothing here deletes or moves mail: a received
message is flagged ``\\Seen`` (``mark_seen``, on by default) and that flag is the only write
the Receive stage makes to the mailbox -- ``BODY.PEEK`` is used for the fetch so that a fetch
that then fails to parse leaves the message unseen for the next poll.

The IMAP and SMTP calls are synchronous by nature of the standard library; the executors call
them through ``asyncio.to_thread``. Both take an injectable transport so the unit tests and
the offline cookbook never open a socket.
"""

from __future__ import annotations

import base64
import email
import re
import email.utils
import imaplib
import os
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "MailboxConfig",
    "MessageFilter",
    "Attachment",
    "Message",
    "mailbox_from",
    "filter_from",
    "fetch_messages",
    "parse_message",
    "send_mail",
]


@dataclass(frozen=True)
class MailboxConfig:
    user: str
    password: str
    imap_host: str = "imap.gmail.com"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    folder: str = "INBOX"

    @property
    def configured(self) -> bool:
        return bool(self.user and self.password)


@dataclass(frozen=True)
class MessageFilter:
    unseen_only: bool = True
    subject_contains: Optional[str] = None
    since_days: Optional[int] = 7
    max_messages: int = 50
    #: Skip messages whose sender is in the mailbox's own domain (our own summaries and
    #: acknowledgements, for one).
    not_own_domain: bool = False
    #: Skip messages that carry no attachment. Decided once bodies are read: a headers-only
    #: listing cannot tell and keeps them; the Screen stage refuses them on a run.
    has_attachment: bool = False


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    size: int
    data: bytes

    def as_dict(self, *, with_data: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
        }
        if with_data:
            out["data_b64"] = base64.b64encode(self.data).decode("ascii")
        return out


@dataclass(frozen=True)
class Message:
    id: str
    sender: str
    subject: str
    received_at: str
    body_text: str
    attachments: List[Attachment] = field(default_factory=list)
    #: The From header when it differs from ``sender`` (Reply-To won); empty otherwise.
    from_address: str = ""
    #: The message size in bytes, known on a headers-only listing.
    size: int = 0

    @property
    def sender_domain(self) -> str:
        return self.sender.rsplit("@", 1)[-1].lower() if "@" in self.sender else ""

    def as_dict(self, *, with_data: bool = True) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "sender_domain": self.sender_domain,
            "subject": self.subject,
            "received_at": self.received_at,
            "from_address": self.from_address,
            "size": self.size,
            "body_text": self.body_text,
            "attachments": [a.as_dict(with_data=with_data) for a in self.attachments],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Message":
        attachments = []
        for a in raw.get("attachments") or []:
            data = a.get("data")
            if data is None and a.get("data_b64"):
                data = base64.b64decode(a["data_b64"])
            attachments.append(
                Attachment(
                    filename=str(a.get("filename") or "attachment"),
                    content_type=str(
                        a.get("content_type") or "application/octet-stream"
                    ),
                    size=int(a.get("size") or len(data or b"")),
                    data=data or b"",
                )
            )
        return cls(
            id=str(raw.get("id") or ""),
            sender=str(raw.get("sender") or raw.get("from") or ""),
            subject=str(raw.get("subject") or ""),
            received_at=str(raw.get("received_at") or ""),
            body_text=str(raw.get("body_text") or ""),
            attachments=attachments,
            from_address=str(raw.get("from_address") or ""),
        )


def mailbox_from(value: Any) -> MailboxConfig:
    """A ``MailboxConfig`` from the slot value (a mapping from the configured source) with the
    ``GMAIL_*`` environment filling any field the mapping leaves out."""
    raw: Mapping[str, Any] = value if isinstance(value, Mapping) else {}
    env = os.environ
    return MailboxConfig(
        user=str(raw.get("user") or raw.get("address") or env.get("GMAIL_USER", "")),
        password=str(
            raw.get("password")
            or raw.get("app_password")
            or env.get("GMAIL_APP_PASSWORD", "")
        ),
        imap_host=str(
            raw.get("imap_host") or env.get("GMAIL_IMAP_HOST", "imap.gmail.com")
        ),
        smtp_host=str(
            raw.get("smtp_host") or env.get("GMAIL_SMTP_HOST", "smtp.gmail.com")
        ),
        smtp_port=int(str(raw.get("smtp_port") or env.get("GMAIL_SMTP_PORT", "587"))),
        folder=str(raw.get("folder") or "INBOX"),
    )


def filter_from(value: Any) -> MessageFilter:
    """A ``MessageFilter`` from the slot value: a mapping, a bare subject phrase, or nothing."""
    if isinstance(value, Mapping):
        return MessageFilter(
            unseen_only=bool(value.get("unseen_only", True)),
            subject_contains=(str(value["subject_contains"]).strip() or None)
            if value.get("subject_contains")
            else None,
            since_days=int(value["since_days"])
            if value.get("since_days") is not None
            else 7,
            max_messages=int(value.get("max_messages") or 50),
            not_own_domain=bool(value.get("not_own_domain", False)),
            has_attachment=bool(value.get("has_attachment", False)),
        )
    if isinstance(value, str) and value.strip():
        return MessageFilter(subject_contains=value.strip())
    return MessageFilter()


def _bytes_payload(part: Any) -> bytes:
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, bytes) else b""


def parse_message(uid: str, raw: bytes) -> Message:
    """One RFC 822 message into the shape the Screen stage reads."""
    parsed = email.message_from_bytes(raw)
    # Reply-To first: a purchase order is often sent by a procurement system from a no-reply
    # address with the buyer in Reply-To, and the buyer is who the customer lookup is about.
    # From is kept alongside on the message (``from_address``) so the provenance shows both.
    from_address = email.utils.parseaddr(parsed.get("From", ""))[1].lower()
    reply_to = email.utils.parseaddr(parsed.get("Reply-To", ""))[1].lower()
    sender = reply_to or from_address
    received = parsed.get("Date", "")
    try:
        received_at = (
            email.utils.parsedate_to_datetime(received)
            .astimezone(timezone.utc)
            .isoformat()
        )
    except (TypeError, ValueError):
        received_at = datetime.now(timezone.utc).isoformat()
    body_parts: List[str] = []
    attachments: List[Attachment] = []
    for part in parsed.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if filename or "attachment" in disposition:
            payload = _bytes_payload(part)
            attachments.append(
                Attachment(
                    filename=filename or "attachment",
                    content_type=part.get_content_type(),
                    size=len(payload),
                    data=payload,
                )
            )
        elif part.get_content_type() == "text/plain":
            payload = _bytes_payload(part)
            body_parts.append(
                payload.decode(part.get_content_charset() or "utf-8", "replace")
            )
    return Message(
        id=str(parsed.get("Message-ID") or uid).strip(),
        sender=sender,
        from_address=from_address,
        subject=str(parsed.get("Subject") or "").strip(),
        received_at=received_at,
        body_text="\n".join(body_parts).strip(),
        attachments=attachments,
    )


def _fetched_items(parts: Sequence[Any]) -> List[Tuple[bytes, bytes]]:
    """``(meta, raw)`` per message from imaplib's FETCH response: the tuples carry the data,
    the bare ``b")"`` separators between them do not."""
    return [
        (p[0] if isinstance(p[0], bytes) else bytes(str(p[0]), "ascii"), p[1])
        for p in parts
        if isinstance(p, tuple) and len(p) > 1 and isinstance(p[1], bytes)
    ]


def _domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().lower() if "@" in address else ""


def _search_criteria(message_filter: MessageFilter) -> List[str]:
    criteria: List[str] = ["UNSEEN"] if message_filter.unseen_only else ["ALL"]
    if message_filter.since_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=message_filter.since_days)
        criteria += ["SINCE", since.strftime("%d-%b-%Y")]
    if message_filter.subject_contains:
        criteria += ["SUBJECT", f'"{message_filter.subject_contains}"']
    return criteria


def fetch_messages(
    config: MailboxConfig,
    message_filter: MessageFilter,
    *,
    mark_seen: bool = True,
    imap_factory: Any = None,
    headers_only: bool = False,
) -> List[Message]:
    """New messages matching the filter, newest last. ``imap_factory(host)`` returns an
    object with ``imaplib.IMAP4_SSL``'s ``login``/``select``/``search``/``fetch``/``store``/
    ``logout``; the default opens the real socket."""
    if not config.configured:
        raise ValueError(
            "mailbox has no user/password: set the source or GMAIL_USER/GMAIL_APP_PASSWORD"
        )
    factory = imap_factory or imaplib.IMAP4_SSL
    client = factory(config.imap_host)
    try:
        client.login(config.user, config.password)
        client.select(config.folder)
        status, data = client.search(None, *_search_criteria(message_filter))
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        uids = [
            u.decode() if isinstance(u, bytes) else str(u)
            for u in (data[0] or b"").split()
            if u
        ][-message_filter.max_messages :]
        messages: List[Message] = []
        # A listing (K19) reads headers only: forty messages with PDFs is twenty seconds
        # of bodies nobody asked for. The size stands in for the attachment names.
        what = "(RFC822.SIZE BODY.PEEK[HEADER])" if headers_only else "(BODY.PEEK[])"
        # One FETCH per batch, not per message: each round trip to Gmail is about a second,
        # and a listing of forty is a listing, not a twenty-second wait.
        batch = 40 if headers_only else 8
        for start in range(0, len(uids), batch):
            chunk = uids[start : start + batch]
            status, parts = client.fetch(",".join(chunk), what)
            if status != "OK" or not parts:
                continue
            for meta, raw in _fetched_items(parts):
                uid_match = re.match(rb"\s*(\d+)\s", meta)
                uid = uid_match.group(1).decode() if uid_match else chunk[0]
                message = parse_message(uid, raw)
                if message_filter.not_own_domain and _domain_of(
                    message.sender
                ) == _domain_of(config.user):
                    continue
                if (
                    message_filter.has_attachment
                    and not headers_only
                    and not message.attachments
                ):
                    continue
                if headers_only:
                    size_match = re.search(rb"RFC822.SIZE (\d+)", meta)
                    message = Message(
                        id=message.id,
                        sender=message.sender,
                        subject=message.subject,
                        received_at=message.received_at,
                        body_text="",
                        attachments=[],
                        from_address=message.from_address,
                        size=int(size_match.group(1)) if size_match else 0,
                    )
                messages.append(message)
                if mark_seen and not headers_only:
                    client.store(uid, "+FLAGS", "\\Seen")
        return messages
    finally:
        try:
            client.logout()
        except Exception:  # pragma: no cover - best effort
            pass


def send_mail(
    config: MailboxConfig,
    *,
    to: Sequence[str],
    subject: str,
    body: str,
    smtp_factory: Any = None,
) -> Dict[str, Any]:
    """Send one plain-text message. ``smtp_factory(host, port)`` returns an object with
    ``smtplib.SMTP``'s ``starttls``/``login``/``send_message``/``quit``."""
    if not config.configured:
        raise ValueError(
            "mailbox has no user/password: set the source or GMAIL_USER/GMAIL_APP_PASSWORD"
        )
    message = EmailMessage()
    message["From"] = config.user
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(body)
    factory = smtp_factory or smtplib.SMTP
    client = factory(config.smtp_host, config.smtp_port)
    try:
        client.starttls()
        client.login(config.user, config.password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:  # pragma: no cover - best effort
            pass
    return {
        "to": list(to),
        "subject": subject,
        "message_id": str(message.get("Message-ID") or ""),
    }
