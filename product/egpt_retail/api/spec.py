"""Load and bundle the OpenAPI contract.

The contract is authored as fragments -- one ``paths/<area>.yaml`` and one
``schemas/<area>.yaml`` per contract area -- so Lane K can review the semantic fragment
without reading the other fifteen hundred lines. Every tool downstream (the model generator,
the validator, the stub server) wants one document, so this module bundles the fragments into
one: every path item is inlined, every external schema is hoisted into
``components.schemas`` under its own name, and every ``$ref`` is rewritten to point there.

The bundle is deterministic and is committed beside the source
(``egpt-retail.v1.bundled.json``). ``scripts/check_api_model_drift.py`` rebuilds it and
fails if the committed copy differs, which is what keeps the YAML the source of truth in
practice rather than in principle.

Fragment conventions:

* ``egpt-retail.v1.yaml`` carries ``info``, ``servers``, ``tags``, ``security`` and
  ``components.securitySchemes``, plus ``x-egpt-path-fragments``: the ordered list of path
  fragment files to merge.
* ``paths/<area>.yaml`` is a mapping of path -> path item.
* ``schemas/<area>.yaml`` is a mapping of schema name -> schema.
* A ``$ref`` to a schema is written relative to the file it appears in, e.g.
  ``'../schemas/kpis.yaml#/Kpi'`` from a path fragment, ``'./common.yaml#/Error'`` from a
  schema fragment. Schema names are global: two files defining the same name is an error,
  not a shadowing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, MutableMapping, Optional, Tuple

import yaml

__all__ = [
    "SPEC_DIR",
    "SPEC_PATH",
    "BUNDLE_PATH",
    "SpecError",
    "bundle",
    "abundle",
    "load_bundle",
    "aload_bundle",
    "write_bundle",
    "awrite_bundle",
]

SPEC_DIR = Path(__file__).resolve().parent / "openapi"
SPEC_PATH = SPEC_DIR / "egpt-retail.v1.yaml"
BUNDLE_PATH = SPEC_DIR / "egpt-retail.v1.bundled.json"


class SpecError(ValueError):
    """The contract source is malformed in a way the bundler cannot repair."""


def _read_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise SpecError(f"{path}: expected a mapping at the top level")
    return loaded


def _split_ref(ref: str) -> Tuple[str, str]:
    """``'../schemas/kpis.yaml#/Kpi'`` -> ``('../schemas/kpis.yaml', '/Kpi')``."""
    if "#" in ref:
        file_part, pointer = ref.split("#", 1)
    else:
        file_part, pointer = ref, ""
    return file_part, pointer


def _resolve_pointer(document: Dict[str, Any], pointer: str, origin: str) -> Any:
    node: Any = document
    for raw in [segment for segment in pointer.split("/") if segment]:
        segment = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or segment not in node:
            raise SpecError(f"{origin}: pointer {pointer!r} does not resolve")
        node = node[segment]
    return node


class _Bundler:
    """One bundling run. Holds the hoisted schemas and the files already loaded."""

    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path
        self.root_dir = root_path.parent
        self.schemas: Dict[str, Any] = {}
        self._schema_origin: Dict[str, Path] = {}
        self._cache: Dict[Path, Dict[str, Any]] = {}

    def _load(self, path: Path) -> Dict[str, Any]:
        path = path.resolve()
        if path not in self._cache:
            self._cache[path] = _read_yaml(path)
        return self._cache[path]

    def _hoist_schema(self, file_path: Path, pointer: str) -> str:
        """Register the schema at ``file_path#pointer`` under its own name and return the name."""
        name = pointer.rstrip("/").split("/")[-1]
        if not name:
            raise SpecError(
                f"{file_path}: a schema $ref must point at a named schema, got {pointer!r}"
            )
        previous = self._schema_origin.get(name)
        if previous is not None and previous != file_path.resolve():
            raise SpecError(
                f"schema name {name!r} is defined in both {previous} and {file_path}; schema names are global"
            )
        if name in self.schemas:
            return name
        document = self._load(file_path)
        schema = _resolve_pointer(document, pointer, str(file_path))
        # Reserve the name before walking, so a self-referential schema terminates.
        self._schema_origin[name] = file_path.resolve()
        self.schemas[name] = None
        self.schemas[name] = self._walk(schema, file_path)
        return name

    def _walk(self, node: Any, current_file: Path) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and isinstance(node["$ref"], str):
                ref = node["$ref"]
                file_part, pointer = _split_ref(ref)
                if not file_part:
                    if pointer.startswith("/components/schemas/"):
                        # Already a bundled schema ref; nothing to rewrite.
                        return dict(node)
                    if pointer.startswith("/x-egpt-"):
                        # A fragment-local reusable piece (a parameter, a response). Inlined,
                        # so the bundle carries no fragment-only vocabulary.
                        document = self._load(current_file)
                        resolved = _resolve_pointer(
                            document, pointer, str(current_file)
                        )
                        return self._walk(resolved, current_file)
                    raise SpecError(
                        f"{current_file}: internal $ref {ref!r} must target components/schemas or a fragment-local x-egpt-* block"
                    )
                target = (current_file.parent / file_part).resolve()
                if target.parent.name == "schemas" or "/schemas/" in target.as_posix():
                    name = self._hoist_schema(target, pointer)
                    rewritten = dict(node)
                    rewritten["$ref"] = f"#/components/schemas/{name}"
                    return rewritten
                # Anything else (a path item, a parameter) is inlined.
                document = self._load(target)
                resolved = _resolve_pointer(document, pointer, str(target))
                return self._walk(resolved, target)
            return {key: self._walk(value, current_file) for key, value in node.items()}
        if isinstance(node, list):
            return [self._walk(item, current_file) for item in node]
        return node

    def run(self) -> Dict[str, Any]:
        root = self._load(self.root_path)
        fragments = root.get("x-egpt-path-fragments")
        if not isinstance(fragments, list) or not fragments:
            raise SpecError(
                f"{self.root_path}: x-egpt-path-fragments must list at least one path fragment"
            )

        paths: Dict[str, Any] = {}
        for fragment in fragments:
            fragment_path = (self.root_dir / fragment).resolve()
            document = self._load(fragment_path)
            for route, item in document.items():
                if route.startswith("x-"):
                    # Fragment-local reusable blocks, inlined wherever they are referenced.
                    continue
                if not route.startswith("/"):
                    raise SpecError(
                        f"{fragment_path}: {route!r} is neither a path nor an x-egpt-* block"
                    )
                if route in paths:
                    raise SpecError(
                        f"{fragment_path}: path {route!r} is already declared by another fragment"
                    )
                paths[route] = self._walk(item, fragment_path)

        bundled: Dict[str, Any] = {
            key: value for key, value in root.items() if key != "x-egpt-path-fragments"
        }
        bundled = self._walk(bundled, self.root_path)
        bundled["paths"] = paths
        components: MutableMapping[str, Any] = dict(bundled.get("components") or {})
        components["schemas"] = {
            name: self.schemas[name] for name in sorted(self.schemas)
        }
        bundled["components"] = components
        return bundled


def bundle(spec_path: Path = SPEC_PATH) -> Dict[str, Any]:
    """Bundle the fragments into one OpenAPI document."""
    return _Bundler(spec_path).run()


async def abundle(spec_path: Path = SPEC_PATH) -> Dict[str, Any]:
    """Async variant. The file reads happen off the event loop."""
    return await asyncio.to_thread(bundle, spec_path)


def _dump(document: Dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def write_bundle(spec_path: Path = SPEC_PATH, bundle_path: Path = BUNDLE_PATH) -> Path:
    """Bundle and write the committed copy. Returns the path written."""
    bundle_path.write_text(_dump(bundle(spec_path)), encoding="utf-8")
    return bundle_path


async def awrite_bundle(
    spec_path: Path = SPEC_PATH, bundle_path: Path = BUNDLE_PATH
) -> Path:
    """Async variant."""
    return await asyncio.to_thread(write_bundle, spec_path, bundle_path)


def load_bundle(bundle_path: Path = BUNDLE_PATH) -> Dict[str, Any]:
    """Read the committed bundle. This is what the stub server and the tests consume."""
    loaded = json.loads(bundle_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SpecError(f"{bundle_path}: expected a mapping at the top level")
    return loaded


async def aload_bundle(bundle_path: Path = BUNDLE_PATH) -> Dict[str, Any]:
    """Async variant."""
    return await asyncio.to_thread(load_bundle, bundle_path)


def bundle_matches_committed(
    spec_path: Path = SPEC_PATH, bundle_path: Path = BUNDLE_PATH
) -> Tuple[bool, Optional[str]]:
    """Whether the committed bundle equals a fresh one. Used by the drift check."""
    fresh = _dump(bundle(spec_path))
    if not bundle_path.exists():
        return False, f"{bundle_path} does not exist; run ./scripts/gen_api_models.sh"
    committed = bundle_path.read_text(encoding="utf-8")
    if fresh != committed:
        return False, f"{bundle_path} is stale; run ./scripts/gen_api_models.sh"
    return True, None
