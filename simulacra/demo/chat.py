from __future__ import annotations

import re

from .runs import AppConfig, ChatMessage, ProjectState


def infer_app_config(prompt: str, existing: AppConfig | None = None) -> AppConfig:
	cfg = existing or AppConfig()
	lower = prompt.lower()

	if "vendor" in lower or "risk" in lower or "diligence" in lower:
		cfg.title = "Vendor Risk Command Center"
		cfg.subtitle = "Third-party diligence · live risk posture"
	elif "sales" in lower or "revenue" in lower:
		cfg.title = "Sales Analytics"
		cfg.subtitle = "Internal revenue view"
	else:
		cfg.title = "Data Explorer"
		cfg.subtitle = "Built from your data room"

	if "region" in lower or "country" in lower:
		cfg.group_by = "theme"
	if "sort" in lower and "asc" in lower:
		cfg.sort_direction = "asc"
	if "sort" in lower and "desc" in lower:
		cfg.sort_direction = "desc"
	if "alphabet" in lower:
		cfg.sort_column = "vendor"
		cfg.sort_direction = "asc"
	if "disable search" in lower or "no search" in lower:
		cfg.search_enabled = False
	if "enable search" in lower or "add search" in lower:
		cfg.search_enabled = True
	if "group by vendor" in lower:
		cfg.group_by = "vendor"
	if "group by theme" in lower or "group by region" in lower:
		cfg.group_by = "theme"

	# title override: "call it X" / "rename to X"
	m = re.search(r"(?:call it|rename to|title)\s+[\"']?([^\"'\n]+)[\"']?", prompt, re.I)
	if m:
		cfg.title = m.group(1).strip()[:80]

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
