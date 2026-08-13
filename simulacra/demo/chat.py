from __future__ import annotations

import re

from .design_brief import is_stock_vendor_name, title_from_prompt
from .runs import AppConfig, ChatMessage, ProjectState


def infer_app_config(prompt: str, existing: AppConfig | None = None) -> AppConfig:
	"""Derive app title/layout from the user prompt — never invent Vendor Risk branding."""
	cfg = existing or AppConfig()
	lower = prompt.lower()
	stock_titles = {
		"",
		"Data Explorer",
		"Data App",
		"Custom App",
		"Untitled",
		"Vendor Risk Command Center",
		"Vendor Risk Dashboard",
		"Vendor Risk",
	}
	cur = (existing.title if existing else "") or ""
	placeholder = existing is None or cur.strip() in stock_titles or is_stock_vendor_name(cur)

	if any(w in lower for w in ("game", "quiz", "flashcard", "learn", "learning", "training", "tutorial")):
		if placeholder:
			cfg.title = "Learning Game"
		cfg.subtitle = "Practice with your source material"
	elif "sales" in lower or "revenue" in lower:
		if placeholder:
			cfg.title = title_from_prompt(prompt) or "Sales Analytics"
		if not cfg.subtitle or cfg.subtitle in ("Built from your sources", "Data Explorer", "Built with Simulacra"):
			cfg.subtitle = "Internal revenue view"
	elif placeholder:
		# Topic comes from the prompt — including real vendor-risk asks.
		# Never hardcode "Vendor Risk Command Center" as a product name.
		cfg.title = title_from_prompt(prompt) or "Custom App"
		if not cfg.subtitle or cfg.subtitle in (
			"Built from your sources",
			"Data Explorer",
			"Built with Simulacra",
			"Monitor vendor findings and risk scores",
			"Third-party diligence · live risk posture",
			"Chat with the agent — Build when ready",
		):
			cfg.subtitle = "From your sources"

	if "region" in lower or "country" in lower:
		cfg.group_by = "theme"
	if "sort" in lower and "asc" in lower:
		cfg.sort_direction = "asc"
	if "sort" in lower and "desc" in lower:
		cfg.sort_direction = "desc"
	if "alphabet" in lower:
		cfg.sort_direction = "asc"
	if "disable search" in lower or "no search" in lower:
		cfg.search_enabled = False
	if "enable search" in lower or "add search" in lower:
		cfg.search_enabled = True
	if "group by vendor" in lower:
		cfg.group_by = "vendor"
	if "group by theme" in lower:
		cfg.group_by = "theme"
	if "group by region" in lower:
		cfg.group_by = "region"

	# title override: "call it X" / "rename to X"
	m = re.search(r"(?:call it|rename to|title)\s+[\"']?([^\"'\n]+)[\"']?", prompt, re.I)
	if m:
		cfg.title = m.group(1).strip()[:80]

	# Absolute ban: stock Vendor Risk name never sticks unless the user typed it verbatim
	if is_stock_vendor_name(cfg.title) and "vendor risk command center" not in lower:
		cfg.title = title_from_prompt(prompt) or "Custom App"

	return cfg


def apply_follow_up(state: ProjectState, message: str) -> tuple[AppConfig, str]:
	state.chat.append(ChatMessage(role="user", content=message))
	cfg = infer_app_config(message, state.app_config)
	reply_parts: list[str] = []

	if cfg.search_enabled != state.app_config.search_enabled:
		reply_parts.append("Search toggled.")
	if cfg.group_by != state.app_config.group_by:
		reply_parts.append(f"Grouped by {cfg.group_by or 'none'}.")
	if cfg.title != state.app_config.title:
		reply_parts.append(f"Renamed app to “{cfg.title}”.")
	if cfg.sort_direction != state.app_config.sort_direction or cfg.sort_column != state.app_config.sort_column:
		reply_parts.append(f"Sorted by {cfg.sort_column} ({cfg.sort_direction}).")

	if not reply_parts:
		reply_parts.append("Updated the app layout from your instructions. Refresh the preview.")

	reply = " ".join(reply_parts)
	state.chat.append(ChatMessage(role="assistant", content=reply))
	state.app_config = cfg
	return cfg, reply
