from simulacra.demo.runs import load_state, save_state, project_dir
from simulacra.demo.design_brief import title_from_prompt, write_brief, merge_brief
from simulacra.demo.formats import brief_defaults_for
from simulacra.demo.pipeline import start_approve_build

pid = "proj_6c8b4127a8c9"
state = load_state(pid)
print("before", state.artifact_kind, state.phase, state.app_config.title)
title = title_from_prompt(state.prompt or "")
state.artifact_kind = "report"
state.app_config.title = title[:80]
state.app_config.subtitle = (title + " — research brief")[:120]
state.design_brief = merge_brief(state.design_brief or {}, brief_defaults_for("report"))
state.design_brief["product_name"] = title
state.design_brief["one_liner"] = state.app_config.subtitle
state.phase = "plan"
state.status = "planning"
write_brief(pid, state.design_brief)
save_state(state)
room = project_dir(pid) / "inputs" / "data-room"
for name in (
    "design_brief.json",
    "kernel-state.json",
    "kernel_state.json",
    "plan_preview.json",
    "agent_context.json",
):
    p = room / name
    if p.exists():
        p.unlink()
        print("removed", name)
out = start_approve_build(pid, reset_scaffold=True)
print("job", out.get("job_id"), out.get("status"))
print("after", load_state(pid).artifact_kind, load_state(pid).app_config.title)
