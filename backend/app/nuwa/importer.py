"""Importer for externally produced advisor artifacts.

This is the seam for an external Nuwa (or any other distillation system). Point it at a
directory of `manifest.json` files, or hand it a raw dict, and it validates and registers them
using the same deterministic checks the in-process distiller applies.

No LLM, no credentials — importing is free.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.advisors.registry import AdvisorRegistry
from app.domain.advisor import AdvisorManifest, AdvisorOrigin


class ImportError_(ValueError):
    """Raised when an external artifact cannot be trusted as an advisor."""


def validate_external(data: dict[str, Any]) -> AdvisorManifest:
    """Validate a foreign manifest against our schema and safety requirements."""
    payload = dict(data)
    payload.setdefault("origin", AdvisorOrigin.custom.value)
    try:
        manifest = AdvisorManifest.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        raise ImportError_(f"Manifest failed schema validation: {exc}") from exc

    if not manifest.blind_spots:
        raise ImportError_(
            f"'{manifest.advisor_id}' declares no blind spots; refusing to import a persona "
            "with no acknowledged limits."
        )
    if not manifest.honest_boundaries:
        raise ImportError_(
            f"'{manifest.advisor_id}' declares no honest boundaries; refusing to import."
        )
    if all(v == 0.0 for v in manifest.expertise.as_dict().values()):
        raise ImportError_(
            f"'{manifest.advisor_id}' has an all-zero expertise vector and could never be selected."
        )
    if not manifest.mental_models and not manifest.heuristics:
        raise ImportError_(
            f"'{manifest.advisor_id}' has no mental models or heuristics; nothing to reason with."
        )
    return manifest


def import_directory(
    path: Path, registry: AdvisorRegistry, *, persist: bool = True
) -> tuple[list[str], list[str]]:
    """Import every `*/manifest.json` under `path`. Returns (imported_ids, errors)."""
    imported: list[str] = []
    errors: list[str] = []
    for manifest_path in sorted(Path(path).glob("*/manifest.json")):
        try:
            manifest = validate_external(json.loads(manifest_path.read_text()))
            registry.register(manifest, persist=persist)
            imported.append(manifest.advisor_id)
        except Exception as exc:  # noqa: BLE001 - one bad artifact must not stop the batch
            errors.append(f"{manifest_path}: {exc}")
    return imported, errors
