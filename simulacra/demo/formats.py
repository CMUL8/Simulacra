"""Artifact formats — data apps, reports, slides, one-pagers.

Same maker loop for every kind: scaffold → Build → Drive → Ship.
Only the template, brief defaults, and builder craft change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .paths import REPO_ROOT

ArtifactKind = Literal["data_app", "report", "slides", "one_pager"]

ARTIFACT_KINDS: tuple[ArtifactKind, ...] = ("data_app", "report", "slides", "one_pager")


@dataclass(frozen=True)
class FormatSpec:
	kind: ArtifactKind
	label: str
	short: str
	template_dir: str
	layout: str
	skill_file: str
	must_have: list[str]
	must_not: list[str]
	primary_view: str
	build_label: str
	placeholder: str
	aesthetic_hint: str


FORMATS: dict[ArtifactKind, FormatSpec] = {
	"data_app": FormatSpec(
		kind="data_app",
		label="Data app",
		short="App",
		template_dir="internal-app",
		layout="explorer",
		skill_file="data_viz.md",
		must_have=[],
		must_not=["emoji", "purple glow"],
		primary_view="overview",
		build_label="Build app",
		placeholder="Interactive explorer for your data",
		aesthetic_hint="Interactive app — you choose filters, tabs, and drill-down if useful.",
	),
	"report": FormatSpec(
		kind="report",
		label="Report",
		short="Report",
		template_dir="report",
		layout="longform_report",
		skill_file="report.md",
		must_have=[],
		must_not=["emoji", "purple glow"],
		primary_view="document",
		build_label="Build report",
		placeholder="A shareable research report",
		aesthetic_hint="Long-form document — you choose sections and narrative.",
	),
	"slides": FormatSpec(
		kind="slides",
		label="Slides",
		short="Slides",
		template_dir="slides",
		layout="deck",
		skill_file="slides.md",
		must_have=[],
		must_not=["emoji", "purple glow"],
		primary_view="deck",
		build_label="Build slides",
		placeholder="A board deck from my sources",
		aesthetic_hint="Full-bleed HTML deck — you choose slide count and story.",
	),
	"one_pager": FormatSpec(
		kind="one_pager",
		label="One-pager",
		short="One-pager",
		template_dir="one-pager",
		layout="one_pager",
		skill_file="one_pager.md",
		must_have=[],
		must_not=["emoji", "purple glow"],
		primary_view="sheet",
		build_label="Build one-pager",
		placeholder="A one-page briefing for tomorrow's review",
		aesthetic_hint="Single printable sheet — you choose hierarchy and density.",
	),
}


def normalize_kind(value: str | None) -> ArtifactKind:
	raw = (value or "data_app").strip().lower().replace("-", "_").replace(" ", "_")
	aliases = {
		"app": "data_app",
		"data": "data_app",
		"dashboard": "data_app",
		"deck": "slides",
		"presentation": "slides",
		"slide": "slides",
		"onepager": "one_pager",
		"brief": "one_pager",
		"memo": "report",
		"document": "report",
	}
	raw = aliases.get(raw, raw)
	if raw in FORMATS:
		return raw  # type: ignore[return-value]
	return "data_app"


def get_format(kind: str | None) -> FormatSpec:
	return FORMATS[normalize_kind(kind)]


def template_path(kind: str | None) -> Path:
	spec = get_format(kind)
	return REPO_ROOT / "templates" / spec.template_dir


def skill_path(kind: str | None) -> Path:
	spec = get_format(kind)
	return Path(__file__).resolve().parent / "skills" / spec.skill_file


def infer_kind_from_prompt(prompt: str) -> ArtifactKind | None:
	"""Optional hint from free text. Explicit UI selection wins."""
	lower = (prompt or "").lower()
	rules: list[tuple[ArtifactKind, tuple[str, ...]]] = [
		("slides", ("slide deck", "board deck", "presentation", "powerpoint", "keynote", "slides")),
		("one_pager", ("one-pager", "one pager", "onepager", "one-page", "one page", "single page brief", "1-pager")),
		("report", ("report", "memo", "write-up", "long-form", "narrative brief", "diligence report")),
		("data_app", ("dashboard", "command center", "explorer", "ops console", "data app")),
	]
	for kind, needles in rules:
		if any(n in lower for n in needles):
			return kind
	return None


def brief_defaults_for(kind: str | None) -> dict[str, Any]:
	spec = get_format(kind)
	return {
		"information_architecture": {
			"primary_view": spec.primary_view,
			"must_have": list(spec.must_have),
			"must_not": list(spec.must_not),
			"artifact_kind": spec.kind,
		},
		"user_notes": f"Format: {spec.label}. {spec.aesthetic_hint}",
	}


def formats_catalog() -> list[dict[str, str]]:
	return [
		{
			"kind": s.kind,
			"label": s.label,
			"short": s.short,
			"placeholder": s.placeholder,
			"build_label": s.build_label,
			"hint": s.aesthetic_hint,
		}
		for s in FORMATS.values()
	]
