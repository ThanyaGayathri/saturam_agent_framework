"""``document_read``: the one tool the extraction role has (K15, ``ROLE_TEMPLATES["extraction"]``).

Reads a PDF's text layer with ``pypdf`` and says honestly when there is none. A scanned
document -- pages that carry an image and no text -- is reported as ``scanned=True`` with
empty text rather than a guess, and the Extract stage turns that into an exception the
person can act on (``SCANNED_DOCUMENT``) instead of a record built from nothing. OCR is a
deliberate non-goal of v1: the platform's ``egpt[pdf]`` extra carries ``rapidocr_onnxruntime``
and the retail image does not, and a fallback that only sometimes exists is exactly the
"works on my machine" a rehearsal cannot vouch for. When OCR lands it lands here, behind the
same ``scanned`` flag, and nothing downstream changes.

Only PDF in v1 (the user's decision: one attachment type, one format). ``read_document``
refuses anything else by content, not by extension -- a ``.pdf`` that does not start with
``%PDF`` is not a PDF.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Dict, List

from egpt.tools import tool

__all__ = ["DocumentText", "read_document", "document_read", "PDF_MAGIC"]

PDF_MAGIC = b"%PDF"


@dataclass(frozen=True)
class DocumentText:
    filename: str
    pages: int
    #: Page texts, one entry per page, in order. Empty strings for pages with no text layer.
    page_texts: List[str]
    #: True when no page carried any text at all.
    scanned: bool

    @property
    def text(self) -> str:
        return "\n\n".join(t for t in self.page_texts if t)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "pages": self.pages,
            "scanned": self.scanned,
            "text": self.text,
        }


def read_document(data: bytes, filename: str = "document.pdf") -> DocumentText:
    """The text layer of one PDF. Raises ``ValueError`` for anything that is not a PDF."""
    if not data.startswith(PDF_MAGIC):
        raise ValueError(f"{filename}: not a PDF (no %PDF header)")
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    texts: List[str] = []
    for page in reader.pages:
        try:
            texts.append((page.extract_text() or "").strip())
        except Exception:  # pragma: no cover - pypdf's own parse failures
            texts.append("")
    return DocumentText(
        filename=filename,
        pages=len(texts),
        page_texts=texts,
        scanned=not any(texts),
    )


@tool(name="document_read")
def document_read(path: str) -> Dict[str, Any]:
    """Read the text of the PDF at ``path``. Returns its page count, whether it is a scanned
    image with no text layer, and the text. Never invents text for a scanned page."""
    with open(path, "rb") as handle:
        return read_document(handle.read(), filename=path.rsplit("/", 1)[-1]).as_dict()
