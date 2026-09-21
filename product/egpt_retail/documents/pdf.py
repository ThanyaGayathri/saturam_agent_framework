"""A minimal PDF writer for the sample set (K15, PA02).

The rehearsal needs real PDF bytes -- the Extract stage reads a text layer with ``pypdf``,
and a rasterised page must come back as "scanned" -- and the dev environment carries no
PDF-producing library. Sixty lines of the PDF 1.4 object model cover it: Helvetica text at
a fixed leading, one content stream per page, and for the scanned case a page holding only
an image XObject and no text at all. Generic by design: nothing here knows what a purchase
order is.
"""

from __future__ import annotations

from typing import List, Sequence

__all__ = ["text_pdf", "image_only_pdf"]

_LINES_PER_PAGE = 48
_LEADING = 14
_TOP = 780
_LEFT = 50


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _assemble(objects: Sequence[bytes]) -> bytes:
    """Objects are numbered 1..n in order; the first must be the catalog."""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: List[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def text_pdf(lines: Sequence[str]) -> bytes:
    """A PDF whose text layer is exactly ``lines``, paginated at 48 lines."""
    pages = [
        list(lines[i : i + _LINES_PER_PAGE])
        for i in range(0, max(len(lines), 1), _LINES_PER_PAGE)
    ]
    # 1 catalog, 2 pages, 3 font, then (page, content) pairs.
    objects: List[bytes] = [
        b"",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    page_ids: List[int] = []
    for page_lines in pages:
        page_id = len(objects) + 1
        content_id = page_id + 1
        page_ids.append(page_id)
        stream_parts = [f"BT /F1 10 Tf {_LEFT} {_TOP} Td {_LEADING} TL"]
        for line in page_lines:
            stream_parts.append(f"({_escape(line)}) Tj T*")
        stream_parts.append("ET")
        stream = "\n".join(stream_parts).encode("latin-1", "replace")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ).encode("latin-1")
        )
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode(
        "latin-1"
    )
    return _assemble(objects)


def image_only_pdf(width: int = 8, height: int = 8) -> bytes:
    """A one-page PDF holding a grey image and no text: what a scanned copy looks like."""
    pixels = bytes((x * 31) % 256 for x in range(width * height))
    stream = f"q {612} 0 0 {792} 0 0 cm /Im1 Do Q".encode("latin-1")
    objects: List[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /XObject << /Im1 5 0 R >> >> >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray "
            f"/BitsPerComponent 8 /Length {len(pixels)} >>\nstream\n"
        ).encode("latin-1")
        + pixels
        + b"\nendstream",
    ]
    return _assemble(objects)
