"""Design brief — structured aesthetics/IA for Prime builder tasks."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_BRIEF: dict[str, Any] = {
	"product_name": "Vendor Risk Command Center",
	"one_liner": "Monitor vendor findings and risk scores",
	"audience": "internal risk / ops",
	"aesthetic": {
		"direction": "dense-ops",
		"density": "compact",
		"color_mode": "dark",
		"palette": {
			"background": "#0B0F0E",
			"surface": "#141A18",
			"text": "#E8EEE9",
			"accent": "#3D8B6E",
			"danger": "#C44B4B",
		},
		"typography": {
			"display": "IBM Plex Sans",
			"body": "IBM Plex Sans",
		},
		"shape": "sharp",
		"chrome": "no-cards",
		"motion": "subtle",
	},
	"information_architecture": {
		"primary_view": "overview",
		"must_have": ["KPI strip", "findings table", "vendor leaderboard"],
		"must_not": ["emoji", "purple glow", "generic Inter-on-white", "rounded pills"],
	},
	"copy_tone": "precise",
	"references": [],
	"user_notes": "",
}


def default_brief(*, prompt: str = "") -> dict[str, Any]:
	brief = copy.deepcopy(DEFAULT_BRIEF)
	if prompt:
		# Light title guess from prompt first clause
		title = prompt.strip().split(".")[0][:60].strip()
		if len(title) > 8:
			brief["product_name"] = title
			brief["one_liner"] = prompt.strip()[:120]
	return brief


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
	out = copy.deepcopy(base)
	for key, value in patch.items():
		if isinstance(value, dict) and isinstance(out.get(key), dict):
			out[key] = _deep_merge(out[key], value)
		elif value is not None:
			out[key] = copy.deepcopy(value)
	return out


def merge_brief(current: dict[str, Any] | None, patch: dict[str, Any] | None) -> dict[str, Any]:
	base = current if current else default_brief()
	if not patch:
		return copy.deepcopy(base)
	return _deep_merge(base, patch)


def merge_notes_from_message(brief: dict[str, Any], message: str) -> dict[str, Any]:
	"""Heuristic NL → brief patches for common aesthetic requests."""
	out = copy.deepcopy(brief)
	lower = message.lower()
	aesthetic = out.setdefault("aesthetic", {})

	if any(w in lower for w in ("darker", "dark mode", "dark theme")):
		aesthetic["color_mode"] = "dark"
	if any(w in lower for w in ("lighter", "light mode", "light theme")):
		aesthetic["color_mode"] = "light"
	if any(w in lower for w in ("dense", "tighter", "compact")):
		aesthetic["density"] = "dense" if "dense" in lower else "compact"
	if any(w in lower for w in ("spacious", "comfortable", "airy")):
		aesthetic["density"] = "comfortable"
	if any(w in lower for w in ("no card", "without card", "flat", "no-cards")):
		aesthetic["chrome"] = "no-cards"
	if "minimal" in lower:
		aesthetic["direction"] = "soft-minimal"
	if any(w in lower for w in ("ops", "utilitarian", "command")):
		aesthetic["direction"] = "dense-ops" if "ops" in lower or "command" in lower else "utilitarian"
	if "editorial" in lower:
		aesthetic["direction"] = "editorial"

	hexes = re.findall(r"#[0-9a-fA-F]{6}", message)
	if hexes:
		palette = aesthetic.setdefault("palette", {})
		palette["accent"] = hexes[0]

	notes = (out.get("user_notes") or "").strip()
	if message.strip() and message.strip() not in notes:
		out["user_notes"] = f"{notes}\n{message.strip()}".strip() if notes else message.strip()
	return out


def write_brief(project_id: str, brief: dict[str, Any]) -> list[Path]:
	from .runs import project_dir

	root = project_dir(project_id)
	paths = [
		root / "work" / "design_brief.json",
		root / "app" / "public" / "design_brief.json",
	]
	written: list[Path] = []
	payload = json.dumps(brief, indent=2)
	for path in paths:
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(payload)
		written.append(path)
	return written


def update_project_brief(project_id: str, patch: dict[str, Any] | None = None):
	from .runs import load_state, save_state

	state = load_state(project_id)
	state.design_brief = merge_brief(state.design_brief, patch)
	write_brief(project_id, state.design_brief)
	save_state(state)
	return state


def brief_to_prime_block(brief: dict[str, Any], *, delta_note: str = "") -> str:
	lines = [
		"## Design brief (OBEY over template defaults)",
		"Read `public/design_brief.json` if present. Do not invent conflicting aesthetics.",
		"```json",
		json.dumps(brief, indent=2)[:3500],
		"```",
	]
	if delta_note:
		lines.append(f"## Design delta\n{delta_note}")
	aes = brief.get("aesthetic") or {}
	ia = brief.get("information_architecture") or {}
	lines.append(
		"## Done when\n"
		f"- Palette/typography match brief ({aes.get('color_mode')}, {aes.get('density')}, chrome={aes.get('chrome')})\n"
		f"- must_have present: {', '.join(ia.get('must_have') or [])}\n"
		f"- must_not absent: {', '.join(ia.get('must_not') or [])}\n"
		"- App stays interactive (filters/tabs/detail), TypeScript valid, stay inside app/"
	)
	return "\n".join(lines)


def apply_brief_css_tokens(app_dir: Path, brief: dict[str, Any]) -> None:
	"""Seed CSS variables from brief so Prime starts on-brand."""
	css_path = app_dir / "src" / "styles.css"
	if not css_path.exists():
		return
	palette = (brief.get("aesthetic") or {}).get("palette") or {}
	if not palette:
		return
	css = css_path.read_text()
	token_block = (
		"  /* design_brief tokens */\n"
		f"  --bg: {palette.get('background', '#0B0F0E')};\n"
		f"  --surface: {palette.get('surface', '#141A18')};\n"
		f"  --text: {palette.get('text', '#E8EEE9')};\n"
		f"  --accent: {palette.get('accent', '#3D8B6E')};\n"
		f"  --danger: {palette.get('danger', '#C44B4B')};\n"
	)
	if "/* design_brief tokens */" in css:
		css = re.sub(
			r"/\* design_brief tokens \*/.*?--danger:[^;]+;",
			token_block.strip(),
			css,
			count=1,
			flags=re.DOTALL,
		)
	elif ":root" in css:
		css = css.replace(":root {", ":root {\n" + token_block, 1)
	else:
		css = f":root {{\n{token_block}}}\n\n" + css
	css_path.write_text(css)
