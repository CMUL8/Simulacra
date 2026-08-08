"""Design brief — structured aesthetics/IA for builder tasks."""

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

# Full palettes so style chips change more than accent
DIRECTION_PALETTES: dict[str, dict[str, str]] = {
	"soft-minimal": {
		"background": "#F4F1EC",
		"surface": "#FFFcf8",
		"text": "#1C1917",
		"accent": "#3D8B6E",
		"danger": "#B91C1C",
	},
	"dense-ops": {
		"background": "#0B0F0E",
		"surface": "#141A18",
		"text": "#E8EEE9",
		"accent": "#3D8B6E",
		"danger": "#C44B4B",
	},
	"editorial": {
		"background": "#F7F6F2",
		"surface": "#FFFFFF",
		"text": "#111111",
		"accent": "#1A1A1A",
		"danger": "#8B1E1E",
	},
	"branded-custom": {
		"background": "#FFF8F4",
		"surface": "#FFFFFF",
		"text": "#1A1210",
		"accent": "#FF6B4A",
		"danger": "#C44B4B",
	},
	"utilitarian": {
		"background": "#0E1116",
		"surface": "#161B22",
		"text": "#E6EDF3",
		"accent": "#58A6FF",
		"danger": "#F85149",
	},
}


def default_brief(*, prompt: str = "") -> dict[str, Any]:
	brief = copy.deepcopy(DEFAULT_BRIEF)
	if prompt:
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


def resolve_palette(brief: dict[str, Any]) -> dict[str, str]:
	"""Complete palette from direction + explicit overrides (accent wins)."""
	aes = brief.get("aesthetic") or {}
	direction = str(aes.get("direction") or "dense-ops")
	base = dict(DIRECTION_PALETTES.get(direction) or DIRECTION_PALETTES["dense-ops"])
	mode = str(aes.get("color_mode") or "")
	if mode == "light" and direction == "dense-ops":
		base = dict(DIRECTION_PALETTES["soft-minimal"])
	elif mode == "dark" and direction in ("soft-minimal", "editorial", "branded-custom"):
		base = dict(DIRECTION_PALETTES["dense-ops"])
	user = aes.get("palette") or {}
	for key in ("background", "surface", "text", "accent", "danger"):
		if user.get(key):
			base[key] = str(user[key])
	return base


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


def apply_brief_css_tokens(app_dir: Path, brief: dict[str, Any]) -> bool:
	"""Write CSS variables from brief into src/styles.css. Returns True if file changed."""
	css_path = app_dir / "src" / "styles.css"
	if not css_path.exists():
		return False
	palette = resolve_palette(brief)
	aes = brief.get("aesthetic") or {}
	density = str(aes.get("density") or "compact")
	pad = {"comfortable": "18px", "dense": "10px", "compact": "12px"}.get(density, "12px")
	css = css_path.read_text()
	before = css
	token_block = (
		"  /* design_brief tokens */\n"
		f"  --bg: {palette['background']};\n"
		f"  --surface: {palette['surface']};\n"
		f"  --panel: {palette['surface']};\n"
		f"  --panel-2: {palette['surface']};\n"
		f"  --text: {palette['text']};\n"
		f"  --accent: {palette['accent']};\n"
		f"  --accent-dim: color-mix(in srgb, {palette['accent']} 18%, transparent);\n"
		f"  --danger: {palette['danger']};\n"
		f"  --pad: {pad};\n"
	)
	if "/* design_brief tokens */" in css:
		css = re.sub(
			r"/\* design_brief tokens \*/.*?(?=\n  /\*|\n}|$)",
			token_block.rstrip() + "\n",
			css,
			count=1,
			flags=re.DOTALL,
		)
	elif ":root" in css:
		css = css.replace(":root {", ":root {\n" + token_block, 1)
	else:
		css = f":root {{\n{token_block}}}\n\n" + css
	# Retarget hardcoded template cyan accent so chips visibly retheme
	css = css.replace("#22d3ee", palette["accent"])
	css = css.replace("rgba(34, 211, 238, 0.12)", f"color-mix(in srgb, {palette['accent']} 18%, transparent)")
	if css != before:
		css_path.write_text(css)
		return True
	return False


def apply_brief_to_dist(app_dir: Path, brief: dict[str, Any]) -> bool:
	"""Patch live dist CSS vars so style chips show without a full rebuild."""
	dist = app_dir / "dist"
	if not dist.is_dir():
		return False
	palette = resolve_palette(brief)
	changed = False
	for css_path in dist.rglob("*.css"):
		text = css_path.read_text()
		orig = text
		for var, key in (
			("--bg:", "background"),
			("--surface:", "surface"),
			("--panel:", "surface"),
			("--text:", "text"),
			("--accent:", "accent"),
			("--danger:", "danger"),
		):
			text = re.sub(
				rf"({re.escape(var)}\s*)([^;]+)(;)",
				rf"\g<1>{palette[key]}\g<3>",
				text,
				count=1,
			)
		text = text.replace("#22d3ee", palette["accent"])
		if text != orig:
			css_path.write_text(text)
			changed = True
	brief_path = dist / "design_brief.json"
	brief_path.write_text(json.dumps(brief, indent=2))
	cfg = dist / "config.json"
	if cfg.is_file():
		try:
			data = json.loads(cfg.read_text())
			if brief.get("product_name"):
				data["title"] = str(brief["product_name"])[:80]
			if brief.get("one_liner"):
				data["subtitle"] = str(brief["one_liner"])[:120]
			cfg.write_text(json.dumps(data, indent=2))
			changed = True
		except json.JSONDecodeError:
			pass
	return changed


def update_project_brief(project_id: str, patch: dict[str, Any] | None = None):
	from .runs import load_state, project_dir, save_state

	state = load_state(project_id)
	state.design_brief = merge_brief(state.design_brief, patch)
	aes = state.design_brief.setdefault("aesthetic", {})
	aes["palette"] = resolve_palette(state.design_brief)
	write_brief(project_id, state.design_brief)
	app_dir = project_dir(project_id) / "app"
	applied = False
	if app_dir.is_dir():
		applied = apply_brief_css_tokens(app_dir, state.design_brief) or applied
		if (app_dir / "dist").is_dir():
			apply_brief_to_dist(app_dir, state.design_brief)
			applied = True
	state.prime = {
		**state.prime,
		"style_applied": applied,
		"style_hint": "Styles applied to draft preview" if applied else "Styles saved — applied on next preview",
	}
	save_state(state)
	return state


def brief_to_prime_block(brief: dict[str, Any], *, delta_note: str = "") -> str:
	palette = resolve_palette(brief)
	lines = [
		"## Design brief (OBEY — this is the product differentiator)",
		"Aesthetics and taste matter more than stock layout. Impress the user.",
		"Read `public/design_brief.json` if present.",
		"```json",
		json.dumps({**brief, "aesthetic": {**(brief.get("aesthetic") or {}), "palette": palette}}, indent=2)[:3500],
		"```",
		"## Palette (use these exact hex values in CSS)",
		json.dumps(palette, indent=2),
	]
	if delta_note:
		lines.append(f"## Design delta\n{delta_note}")
	aes = brief.get("aesthetic") or {}
	ia = brief.get("information_architecture") or {}
	lines.append(
		"## Done when\n"
		f"- CSS vars + hardcoded accents match palette ({palette.get('accent')})\n"
		f"- Density/chrome match ({aes.get('color_mode')}, {aes.get('density')}, chrome={aes.get('chrome')})\n"
		f"- must_have present: {', '.join(ia.get('must_have') or [])}\n"
		f"- must_not absent: {', '.join(ia.get('must_not') or [])}\n"
		"- Hero viz + KPI strip feel custom (not the stock cyan template)\n"
		"- App stays interactive (filters/tabs/detail), TypeScript valid, stay inside app/"
	)
	return "\n".join(lines)
