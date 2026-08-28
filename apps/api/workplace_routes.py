"""Optional workplace route aggregation with strict import failure handling."""

from __future__ import annotations

import importlib

from fastapi import APIRouter


OPTIONAL_SUBROUTER_MODULES: tuple[str, ...] = (
    "apps.api.workplace_summary_routes",
    "apps.api.conversation_routes",
    "apps.api.work_routes",
    "apps.api.file_routes",
    "apps.api.workplace_event_routes",
)

router = APIRouter(tags=["workplace"])


def register_if_present(target_router: APIRouter = router) -> None:
    """Mount present workplace modules, skipping only their absent target file."""
    for module_name in OPTIONAL_SUBROUTER_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            raise
        target_router.include_router(module.router)


register_if_present()
