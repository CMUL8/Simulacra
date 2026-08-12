/** Files that belong to the agent/runtime — not the user's data room. */
const INTERNAL_SOURCE =
  /^(design_brief|plan_preview|kernel-state|kernel_state|agent_context|extract_report|gates?_report|run_manifest|session|sources|data_profile)(\.|$)/i;

export function isInternalSourceName(name: string): boolean {
  const base = (name.split("/").pop() || name).trim();
  return INTERNAL_SOURCE.test(base);
}

export function userFacingFiles<T extends { name: string }>(files: T[]): T[] {
  return files.filter((f) => !isInternalSourceName(f.name));
}
