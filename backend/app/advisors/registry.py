"""Advisor registry.

Loads AdvisorManifest artifacts from disk and compiles each into the compact
AdvisorRuntimeProfile that is actually sent to Claude. Runs with no API key.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from app.domain.advisor import AdvisorManifest, AdvisorOrigin, AdvisorRuntimeProfile

BUILTIN_DIR = Path(__file__).parent / "builtin"
DEFAULT_CUSTOM_DIR = Path(__file__).parent / "custom"


class AdvisorNotFound(KeyError):
    pass


class AdvisorRegistry:
    """Thread-safe in-memory registry of advisor manifests and runtime profiles."""

    def __init__(self, builtin_dir: Path = BUILTIN_DIR, custom_dir: Path = DEFAULT_CUSTOM_DIR):
        self._builtin_dir = builtin_dir
        self._custom_dir = custom_dir
        self._lock = threading.RLock()
        self._manifests: dict[str, AdvisorManifest] = {}
        self._runtime: dict[str, AdvisorRuntimeProfile] = {}
        self.reload()

    # --- loading ---------------------------------------------------------------------

    def reload(self) -> None:
        with self._lock:
            self._manifests.clear()
            self._runtime.clear()
            for directory, origin in (
                (self._builtin_dir, AdvisorOrigin.builtin),
                (self._custom_dir, AdvisorOrigin.custom),
            ):
                if directory.exists():
                    for manifest_path in sorted(directory.glob("*/manifest.json")):
                        self._load_one(manifest_path, origin)

    def _load_one(self, manifest_path: Path, origin: AdvisorOrigin) -> None:
        data = json.loads(manifest_path.read_text())
        data.setdefault("origin", origin.value)
        manifest = AdvisorManifest.model_validate(data)
        if manifest.advisor_id != manifest_path.parent.name:
            raise ValueError(
                f"advisor_id '{manifest.advisor_id}' does not match directory "
                f"'{manifest_path.parent.name}'"
            )
        self._manifests[manifest.advisor_id] = manifest
        self._runtime[manifest.advisor_id] = manifest.to_runtime_profile()

    # --- registration ----------------------------------------------------------------

    def register(self, manifest: AdvisorManifest, *, persist: bool = True) -> AdvisorRuntimeProfile:
        """Add (or replace) an advisor. Custom advisors are written under `custom/`."""
        with self._lock:
            runtime = manifest.to_runtime_profile()
            self._manifests[manifest.advisor_id] = manifest
            self._runtime[manifest.advisor_id] = runtime

            if persist and manifest.origin is AdvisorOrigin.custom:
                target = self._custom_dir / manifest.advisor_id
                target.mkdir(parents=True, exist_ok=True)
                (target / "manifest.json").write_text(
                    manifest.model_dump_json(indent=2, exclude_none=False)
                )
                (target / "SKILL.md").write_text(render_skill_md(manifest))
            return runtime

    # --- access ----------------------------------------------------------------------

    def manifest(self, advisor_id: str) -> AdvisorManifest:
        try:
            return self._manifests[advisor_id]
        except KeyError as exc:
            raise AdvisorNotFound(advisor_id) from exc

    def runtime_profile(self, advisor_id: str) -> AdvisorRuntimeProfile:
        try:
            return self._runtime[advisor_id]
        except KeyError as exc:
            raise AdvisorNotFound(advisor_id) from exc

    def all_manifests(self) -> list[AdvisorManifest]:
        return sorted(self._manifests.values(), key=lambda m: m.advisor_id)

    def all_runtime_profiles(self) -> list[AdvisorRuntimeProfile]:
        return [self._runtime[m.advisor_id] for m in self.all_manifests()]

    def ids(self) -> list[str]:
        return sorted(self._manifests)

    def __len__(self) -> int:
        return len(self._manifests)

    def __contains__(self, advisor_id: object) -> bool:
        return advisor_id in self._manifests


def render_skill_md(manifest: AdvisorManifest) -> str:
    """Long-form, human-readable artifact. Kept for traceability — never sent at runtime."""

    def section(title: str, items: list[str]) -> str:
        if not items:
            return ""
        body = "\n".join(f"- {i}" for i in items)
        return f"\n## {title}\n\n{body}\n"

    evidence = "\n".join(
        f"- {e.label}" + (f" — {e.source}" if e.source else "") + (f" ({e.year})" if e.year else "")
        for e in manifest.evidence
    )
    return (
        f"# {manifest.display_name}\n\n"
        f"**Subject:** {manifest.subject}\n\n"
        f"**Edge:** {manifest.one_line}\n"
        + section("Mental models", manifest.mental_models)
        + section("Heuristics", manifest.heuristics)
        + section("Reasoning rules", manifest.reasoning_rules)
        + section("Blind spots", manifest.blind_spots)
        + section("Honest boundaries", manifest.honest_boundaries)
        + (f"\n## Evidence\n\n{evidence}\n" if evidence else "")
        + f"\n## Provenance\n\n{manifest.provenance or 'Unspecified.'}\n"
        + (
            f"\nDistilled at: {manifest.distilled_at.isoformat()}\n"
            if manifest.distilled_at
            else ""
        )
    )


_default_registry: AdvisorRegistry | None = None
_default_lock = threading.Lock()


def get_registry() -> AdvisorRegistry:
    """Process-wide registry. Contains no credentials — safe to share across requests."""
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            _default_registry = AdvisorRegistry()
        return _default_registry
