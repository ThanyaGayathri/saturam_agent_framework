"""Import and seed portable component definitions (EGPT-2.8).

``egpt.registry.io`` owns the bundle format and every write, but it is core and so cannot build
an ``Agent`` -- which is exactly what the EGPT-2.2 validator needs in order to prove a config
hydrates. This module is the other side of that seam: it binds the validator and hands it in.

The binding matters more than it looks. The closure below makes the *same*
``resolve_db_reference`` + ``validate_component_config`` pair of calls the components router
makes on ``POST /components``, in the same order, so "an imported definition passes the same
checks as an API write" is literally true rather than approximately true. Skipping the db
resolution would be the subtle failure: a config naming a db that does not exist here would
validate, persist, and then quietly lose its db the first time something hydrated it.

Seeding is import with a different default, not a second mechanism. A template file is a
bundle, so ``seed_from_directory`` is a loop over ``import_component`` -- which is what makes
re-running a seed converge instead of duplicating.
"""

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from egpt.components.spec import validate_component_config
from egpt.db.base import BaseDb
from egpt.db.utils import resolve_db_reference
from egpt.registry import Registry
from egpt.registry.io import ComponentIOError, ImportResult, dump_document, export_component, import_document
from egpt.registry.policy import CapabilityPolicy
from egpt.utils.log import log_info

__all__ = [
    "default_templates_dir",
    "dump_document",
    "export_component",
    "import_component",
    "seed_from_directory",
]

#: Seeding is re-run on every onboarding and after every template change, so its default has to
#: be the one that converges. Importing a bundle from somewhere else is a different act -- there
#: the library default (`error`) is right, because a surprise id collision is news.
_SEED_ON_CONFLICT = "new_version"


def default_templates_dir() -> Path:
    """The starter templates that ship with the package.

    Resolved through ``importlib.resources`` rather than ``__file__`` so it works the same from
    a checkout and from an installed wheel -- the whole reason the templates are package data.
    """
    from importlib.resources import files

    return Path(str(files("egpt.registry") / "templates"))


def import_component(
    db: BaseDb,
    document: Union[str, bytes, Mapping[str, Any], Path],
    *,
    tenant_id: Optional[str] = None,
    registry: Optional[Registry] = None,
    policy: Optional[CapabilityPolicy] = None,
    **import_kwargs: Any,
) -> List[ImportResult]:
    """Import a definition bundle into a target tenant, validating it on the way in.

    Args:
        db: The target database. Must be a sync ``BaseDb``; component methods do not exist on
            the async backends.
        document: A bundle -- a path, JSON text, or an already-parsed mapping.
        tenant_id: The tenant the imported rows are stamped with.
        registry: The application's instance catalog. Supplying one is what lets a config's
            ``db``/tool/schema references resolve; without it those checks are skipped rather
            than failed, per the EGPT-2.2 contract.
        policy: The EGPT-2.3 capability policy in force, if any.
        **import_kwargs: Forwarded to ``egpt.registry.io.import_document`` -- ``on_conflict``,
            ``on_deleted``, ``id_map``, ``id_prefix``, ``new_component_id``, ``db_id``,
            ``drop_db``, ``stage``, ``actor``, ``audit_note``, ``dry_run``.

    Returns:
        One ``ImportResult`` per component in the bundle, children first.

    Raises:
        ComponentIOError: the bundle is unreadable, an id collides, or a link dangles.
        ConfigValidationError: a config is not a legal definition.
    """
    if not isinstance(db, BaseDb):
        raise ComponentIOError("Async databases are not yet supported for component import. Use a sync database.")

    if isinstance(document, Path):
        document = _read_document(document)

    return import_document(
        db,
        document,
        tenant_id=tenant_id,
        validate=_validator_for(db, registry=registry, policy=policy, tenant_id=tenant_id),
        **import_kwargs,
    )


def seed_from_directory(
    db: BaseDb,
    templates_dir: Optional[Union[str, Path]] = None,
    *,
    tenant_id: Optional[str] = None,
    registry: Optional[Registry] = None,
    policy: Optional[CapabilityPolicy] = None,
    templates: Optional[Sequence[Union[str, Path]]] = None,
    stage: Optional[str] = "published",
    on_conflict: str = _SEED_ON_CONFLICT,
    on_deleted: str = "error",
    id_prefix: Optional[str] = None,
    db_id: Optional[str] = None,
    drop_db: bool = False,
    actor: Optional[str] = None,
    audit_note: Optional[str] = None,
    dry_run: bool = False,
) -> List[ImportResult]:
    """Load every template bundle in a directory into one tenant, idempotently.

    Re-running is the point, so the outcome per component is one of: created (it was absent),
    a new version (its definition changed), or nothing at all (it already matches -- no row and
    no audit record). Two templates that both declare the same component converge on the second
    one rather than colliding, which is what lets a team bundle re-declare the agents it needs.

    Args:
        db: The target database.
        templates_dir: Directory of ``*.json`` bundles. Defaults to the packaged templates.
        tenant_id: The tenant to seed. Pass a real one: ``None`` writes untenanted rows that
            only an untenanted reader can see.
        registry: The instance catalog, so ``db`` references in the templates resolve.
        policy: The capability policy to validate against.
        templates: Specific files, instead of globbing ``templates_dir``.
        stage: Stage to write at. ``"published"`` by default -- a seeded draft is not served.
        on_conflict: Defaults to ``new_version``; see the module docstring.
        on_deleted: ``error`` (default) or ``skip`` when an id is held by a soft-deleted row.
        id_prefix: Prefix every seeded id. The answer to seeding the same corpus into a second
            tenant, since ``component_id`` is a global primary key.
        db_id: Point each template's ``db`` block at this database id.
        drop_db: Strip the ``db`` block, letting hydration attach the caller's database.
        actor: Principal recorded on the audit rows.
        audit_note: Reason recorded on the audit rows.
        dry_run: Resolve and validate everything, write nothing.

    Returns:
        The results for every component across every file, in application order.
    """
    paths = _template_paths(templates_dir, templates)
    if not paths:
        raise ComponentIOError(f"No template bundles (*.json) found in {templates_dir or default_templates_dir()}.")

    results: List[ImportResult] = []
    for path in paths:
        log_info(f"Seeding {path.name}")
        results.extend(
            import_component(
                db,
                _read_document(path),
                tenant_id=tenant_id,
                registry=registry,
                policy=policy,
                on_conflict=on_conflict,
                on_deleted=on_deleted,
                id_prefix=id_prefix,
                db_id=db_id,
                drop_db=drop_db,
                stage=stage,
                actor=actor,
                audit_note=audit_note,
                dry_run=dry_run,
            )
        )
    return results


def _template_paths(
    templates_dir: Optional[Union[str, Path]],
    templates: Optional[Sequence[Union[str, Path]]],
) -> List[Path]:
    if templates:
        return [Path(template) for template in templates]
    directory = Path(templates_dir) if templates_dir is not None else default_templates_dir()
    if not directory.is_dir():
        raise ComponentIOError(f"Templates directory not found: {directory}")
    # Sorted so a seed run is reproducible and its output diffable.
    return sorted(directory.glob("*.json"))


def _read_document(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ComponentIOError(f"Could not read {path}: {exc}") from exc


def _validator_for(
    db: BaseDb,
    *,
    registry: Optional[Registry],
    policy: Optional[CapabilityPolicy],
    tenant_id: Optional[str],
) -> Any:
    """Bind the EGPT-2.2 validator to this database, registry, policy and tenant.

    The db resolution ahead of the validation is not optional garnish -- it is what turns an
    unresolvable ``db.id`` into a field-level rejection at import time instead of a config that
    stores fine and loses its database later.
    """

    def validate(
        config: Dict[str, Any],
        component_type: str,
        *,
        component_id: Optional[str] = None,
        links: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        resolved, unresolved_db_id = resolve_db_reference(config, os_db=db, registry=registry)
        return validate_component_config(
            resolved,
            component_type,
            db=db,
            registry=registry,
            component_id=component_id,
            unresolved_db_id=unresolved_db_id,
            links=links,
            policy=policy,
            tenant_id=tenant_id,
        )

    return validate
