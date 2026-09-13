"""The business hierarchy: every area this deployment holds, with its label and subject areas.

What ``GET /business-areas`` renders and ``PUT /business-areas`` edits. The *areas* are the
``business_area`` vocabulary -- the shipped terms plus the customer's overlay, under the same
G10 rule every other closed set follows (:mod:`egpt_retail.contracts.vocabulary`): an overlay
may add and annotate, never remove or redefine. The *hierarchy* is what a vocabulary term does
not carry: a display label and the subject areas beneath the area. Those live next to the
vocabulary under one ``business_area_hierarchy`` key, in the core file and in an overlay
alike, rather than on :class:`~egpt_retail.contracts.vocabulary.Term` -- a term is one
permitted value in any closed set, and ``grain`` has no subject areas.

Whether an area is *supported* is not decided here. That is K9's graph query
(:mod:`egpt_retail.semantic.areas`), and the router composes the two; this module never
opens a database.

The same G10 rule applies to the annotations. An overlay may add subject areas to a core
area (annotate) and may label an area it adds; it may not relabel a core area, which changes
what every screen calls it and is a redefinition in everything but the description field.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from egpt_retail.contracts.vocabulary import (
    CORE_PATH,
    OverlayConflict,
    Term,
    TermOrigin,
    build_vocabulary_set,
)

__all__ = [
    "HIERARCHY_KEY",
    "VOCABULARY",
    "BusinessAreaHierarchy",
    "BusinessAreaNode",
    "aload_business_area_hierarchy",
    "apply_overlay",
    "aread_overlay",
    "awrite_overlay",
    "build_hierarchy",
    "default_label",
    "load_business_area_hierarchy",
    "read_overlay",
    "write_overlay",
]

#: The closed set the areas are drawn from.
VOCABULARY = "business_area"

#: Top-level key, in the core file and in an overlay, holding ``{area_id: {"label": ...,
#: "subject_areas": [...]}}``. Both fields optional; a missing label is derived from the id.
HIERARCHY_KEY = "business_area_hierarchy"


@dataclass(frozen=True)
class BusinessAreaNode:
    """One area of the hierarchy, as the vocabulary and its annotations describe it."""

    id: str
    label: str
    description: str
    subject_areas: Tuple[str, ...] = ()
    origin: TermOrigin = TermOrigin.CORE


@dataclass(frozen=True)
class BusinessAreaHierarchy:
    """Every area, in vocabulary order, stamped with the vocabulary version it came from."""

    version: str
    areas: Tuple[BusinessAreaNode, ...]

    def __iter__(self) -> Iterator[BusinessAreaNode]:
        return iter(self.areas)

    def get(self, area_id: str) -> Optional[BusinessAreaNode]:
        for area in self.areas:
            if area.id == area_id:
                return area
        return None


def default_label(area_id: str) -> str:
    """``supply_chain`` -> ``Supply Chain``: the label an area gets when nothing declares one."""
    return " ".join(part.capitalize() for part in area_id.split("_") if part)


def _annotations(payload: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    raw = (payload or {}).get(HIERARCHY_KEY) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"{HIERARCHY_KEY!r} must be an object keyed by business area id"
        )
    notes: Dict[str, Dict[str, Any]] = {}
    for area_id, note in raw.items():
        if str(area_id).startswith("_"):
            continue  # `_comment`, the convention the core file already uses
        if not isinstance(note, Mapping):
            raise ValueError(f"{HIERARCHY_KEY}[{area_id!r}] must be an object")
        subject_areas = note.get("subject_areas", ())
        if isinstance(subject_areas, str) or not all(
            isinstance(s, str) and s.strip() for s in subject_areas
        ):
            raise ValueError(
                f"{HIERARCHY_KEY}[{area_id!r}].subject_areas must be a list of non-empty strings"
            )
        notes[str(area_id)] = {
            "label": note.get("label"),
            "subject_areas": tuple(subject_areas),
        }
    return notes


def _union(first: Sequence[str], second: Sequence[str]) -> Tuple[str, ...]:
    merged = list(first)
    for item in second:
        if item not in merged:
            merged.append(item)
    return tuple(merged)


def build_hierarchy(
    core_payload: Mapping[str, Any],
    overlay_payload: Optional[Mapping[str, Any]] = None,
) -> BusinessAreaHierarchy:
    """The hierarchy from two parsed payloads. Pure, and the one place the G10 rules for the
    annotations are applied -- the loader and the writer both come through here, so a payload
    the writer accepted is one the loader will read back.

    Raises :class:`OverlayConflict` when the overlay redefines a core term's description (via
    :func:`~egpt_retail.contracts.vocabulary.merge`) or relabels a core area, and a plain
    ``ValueError`` for a malformed payload -- an annotation naming an area no vocabulary
    defines, or a term whose id is not a valid term value.
    """
    vocabularies = build_vocabulary_set(core_payload, overlay_payload)
    vocabulary = vocabularies[VOCABULARY]
    core_notes = _annotations(core_payload)
    overlay_notes = _annotations(overlay_payload)
    for source, notes in (("core", core_notes), ("overlay", overlay_notes)):
        unknown = sorted(set(notes) - vocabulary.values)
        if unknown:
            raise ValueError(
                f"the {source} {HIERARCHY_KEY} annotates business area(s) {unknown} that "
                f"no {VOCABULARY} term defines"
            )

    areas: List[BusinessAreaNode] = []
    for term in vocabulary.terms:
        core_note = core_notes.get(term.value, {})
        overlay_note = overlay_notes.get(term.value, {})
        if term.origin is TermOrigin.CORE:
            label = core_note.get("label") or default_label(term.value)
            wanted = overlay_note.get("label")
            if wanted and wanted != label:
                raise OverlayConflict(
                    f"overlay relabels core business area {term.value!r} from {label!r} to "
                    f"{wanted!r}. An overlay may add and annotate, never redefine (G10) -- "
                    "raise it as a proposal."
                )
            subject_areas = _union(
                core_note.get("subject_areas", ()),
                overlay_note.get("subject_areas", ()),
            )
        else:
            label = overlay_note.get("label") or default_label(term.value)
            subject_areas = tuple(overlay_note.get("subject_areas", ()))
        areas.append(
            BusinessAreaNode(
                id=term.value,
                label=label,
                description=term.description,
                subject_areas=subject_areas,
                origin=term.origin,
            )
        )
    return BusinessAreaHierarchy(version=vocabularies.version, areas=tuple(areas))


def read_overlay(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    """The overlay file's payload, or ``None`` when no path is configured or the file does
    not exist yet -- a configured path with nothing behind it is an empty overlay, which is
    the state :func:`write_overlay` creates the file from."""
    if path is None or not Path(path).exists():
        return None
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, Mapping):
        raise ValueError(f"overlay {path} must hold a JSON object")
    return dict(payload)


async def aread_overlay(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    """Async variant. The read happens off the event loop."""
    return await asyncio.to_thread(read_overlay, path)


def load_business_area_hierarchy(
    core_path: Path = CORE_PATH, overlay_path: Optional[Path] = None
) -> BusinessAreaHierarchy:
    """The shipped hierarchy, with the customer's overlay applied on top if there is one."""
    core_payload = json.loads(Path(core_path).read_text())
    return build_hierarchy(core_payload, read_overlay(overlay_path))


async def aload_business_area_hierarchy(
    core_path: Path = CORE_PATH, overlay_path: Optional[Path] = None
) -> BusinessAreaHierarchy:
    """Async variant. Reads happen off the event loop."""
    return await asyncio.to_thread(
        load_business_area_hierarchy, core_path, overlay_path
    )


def apply_overlay(
    existing: Optional[Mapping[str, Any]],
    areas: Sequence[BusinessAreaNode],
    core_payload: Mapping[str, Any],
) -> Dict[str, Any]:
    """The overlay payload after ``PUT /business-areas``: everything the overlay already held
    (concepts, relations, other vocabularies) unchanged, with its ``business_area`` terms and
    hierarchy annotations replaced by what ``areas`` describes.

    ``PUT`` replaces the overlay's *own* contribution, never the core: an area the overlay
    added last time and this request omits is dropped; a core area is never removable, so
    omitting one changes nothing. A core area named here is an annotation -- its subject
    areas are added -- and must not redefine the description or the label (G10, raised as
    :class:`OverlayConflict`). A new area must have a valid term id (``ValueError`` otherwise,
    the same validation :class:`~egpt_retail.contracts.vocabulary.Term` applies to the core
    file). The result is run back through :func:`build_hierarchy` before it is returned, so
    what the writer accepts is exactly what the loader reads.
    """
    core = build_hierarchy(core_payload)
    core_by_id = {area.id: area for area in core.areas}
    terms: List[Dict[str, str]] = []
    notes: Dict[str, Dict[str, Any]] = {}
    seen: set = set()
    for area in areas:
        if area.id in seen:
            raise ValueError(f"business area {area.id!r} is listed twice")
        seen.add(area.id)
        Term(value=area.id, description=area.description)  # the id's shape
        core_area = core_by_id.get(area.id)
        if core_area is None:
            terms.append({"value": area.id, "description": area.description})
            notes[area.id] = {
                "label": area.label or default_label(area.id),
                "subject_areas": list(_union((), area.subject_areas)),
            }
            continue
        if area.description and area.description != core_area.description:
            raise OverlayConflict(
                f"overlay redefines core term {area.id!r} in {VOCABULARY!r}. "
                "An overlay may add and annotate, never redefine (G10) -- raise it as a proposal."
            )
        if area.label and area.label != core_area.label:
            raise OverlayConflict(
                f"overlay relabels core business area {area.id!r} from {core_area.label!r} "
                f"to {area.label!r}. An overlay may add and annotate, never redefine (G10) -- "
                "raise it as a proposal."
            )
        incoming = _union((), area.subject_areas)
        additions = [s for s in incoming if s not in core_area.subject_areas]
        if additions:
            notes[area.id] = {"subject_areas": additions}

    payload: Dict[str, Any] = dict(existing or {})
    vocabularies = dict(payload.get("vocabularies") or {})
    if terms:
        vocabularies[VOCABULARY] = terms
    else:
        vocabularies.pop(VOCABULARY, None)
    if vocabularies:
        payload["vocabularies"] = vocabularies
    else:
        payload.pop("vocabularies", None)
    if notes:
        payload[HIERARCHY_KEY] = notes
    else:
        payload.pop(HIERARCHY_KEY, None)

    build_hierarchy(core_payload, payload)
    return payload


def write_overlay(path: Path, payload: Mapping[str, Any]) -> None:
    """Write the overlay atomically: a reader that opens the file mid-write sees the old
    payload or the new one, never a truncated file. The directory must exist.

    Atomic per write, not across writes: two concurrent callers that both read the file
    before either writes will both succeed, and the second write silently wins (a lost
    update, not a torn read). Acceptable for a low-traffic admin/config endpoint; would
    need a lock or a version check if this ever gets a scripted or multi-admin caller.
    """
    target = Path(path)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


async def awrite_overlay(path: Path, payload: Mapping[str, Any]) -> None:
    """Async variant. The write happens off the event loop."""
    await asyncio.to_thread(write_overlay, path, payload)
