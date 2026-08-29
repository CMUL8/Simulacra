# Missions workplace internal rollout

This runbook enables the new Missions workplace for an explicitly named
internal tenant. The setting is server-owned: it is not a customer preference,
request field, URL option, or browser-stored value.

## Safety contract

- The rollout is off when `SIMULACRA_WORKPLACE_INTERNAL_TENANTS` is unset or
  empty.
- A valid value is one or more exact tenant IDs separated by commas, with no
  spaces: `tenant_internal` or `tenant_internal,tenant_second`.
- Any whitespace, empty member, duplicate, uppercase character, path syntax,
  or invalid identifier fails closed. Deployment preflight rejects it and the
  application enables no tenant if it is nevertheless present.
- A valid allowlisted tenant receives the complete workplace capability set as
  one release unit. All other tenants continue to use the legacy experience.
- Disabling the rollout changes routing only. It does not delete Missions,
  conversation, work, source, evidence, trajectory, approval, output, staged
  source, or bootstrap-recovery records.

## Before enabling

1. Complete the W7 backend, console, browser, accessibility, visual, and
   deterministic worker gates in `docs/MISSIONS_WORKPLACE_EXECUTION_PLAN.md`.
2. Confirm the exact authenticated tenant ID. Do not infer it from a human's
   email, workspace display name, or URL.
3. Complete the preview-origin configuration and security checks before
   enabling a tenant. The control and preview origins must be HTTPS, same-site,
   and use distinct hostnames, with the preview exchange secret configured.
4. Run deployment preflight with the final environment and retain the result.
5. Take the normal durable-data backup and record the current release version.

## Enable one internal tenant

For the repository's local and current Railway internal demo tenant, use:

```text
SIMULACRA_WORKPLACE_INTERNAL_TENANTS=default
```

For a different environment, replace `default` with the exact authenticated
tenant ID. To enable multiple internal tenants, use a comma-separated value
without spaces.

Restart or redeploy the API after changing the server environment. Do not add
this variable to frontend build variables or expose its value through an API.

## Verify the internal rollout

1. Sign in as a human in the allowlisted tenant. Confirm Missions, Needs you,
   Work, and Settings load and that New Mission uses the recoverable creation
   flow.
2. Create a Mission with a source file, reload during creation, and confirm the
   same Mission opens without duplicate work.
3. Add an agent and a second human, assign work, review the returned evidence,
   and verify one exact output.
4. Sign in to a non-allowlisted tenant and confirm it remains on the legacy
   experience.
5. Confirm no model, provider, runtime, host, path, raw tool output, or raw
   exception appears in normal UI or public responses.
6. Record health, smoke, journey, and screenshot evidence for the release.

## Roll back safely

Roll back the capability first, before rolling back code:

```text
SIMULACRA_WORKPLACE_INTERNAL_TENANTS=
```

Restart or redeploy the API, then verify the allowlisted tenant receives the
legacy reads and can still open its existing durable Mission data. Stop any
release-specific background delivery process only if the release manifest says
mixed-version consumption is unsafe. Do not delete or rewrite workplace
collections, bootstrap journals, staged sources, conversations, work,
trajectories, evidence, approvals, or verified outputs.

If flag-off verification passes, proceed with the ordinary code rollback. If it
does not pass, keep the flags off, preserve all durable data, and investigate
before changing schemas or records.
