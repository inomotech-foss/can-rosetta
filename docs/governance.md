# Repository governance

This document describes the branch-protection and merge-queue configuration for
`inomotech-foss/can-rosetta`. The workflow files and templates live in the repo,
but the protection rules themselves are account-level settings that only a repo
admin can apply through the GitHub API or UI. The exact commands are given below
so the intended state is reproducible and reviewable.

This mirrors the sibling project `inomotech-foss/paperplane`, adapted for a
Python + Swift monorepo: a single merge-queue-compatible CI workflow, Renovate
for dependency updates, `CODEOWNERS`-based required review, and linear history.

## Intended state

- **Protected branch:** `main`.
- **Linear history:** required (no merge commits; squash/rebase only).
- **Merge queue:** enabled on `main` (GitHub native merge queue). CI runs against
  the temporary `merge_group` ref before a PR is merged; this is why every CI
  workflow also triggers on `merge_group:`.
- **Required review:** at least 1 approving review, and **require review from Code
  Owners** (see `/CODEOWNERS`). Dismiss stale approvals on new commits.
- **Required conversation resolution:** on.
- **Required status checks** (must pass before merge / to leave the queue) — these
  are the job names produced by `.github/workflows/ci.yml`:
  - `server (py3.10)`
  - `server (py3.12)`
  - `edge / autopi (py3.10)`
  - `edge / autopi (py3.12)`
  - `schema + sample-session validation`
  - `companion / ios (project generation)`

  > The image build (`.github/workflows/build-image.yml`) runs on push to `main`
  > and tags, not on PRs, so it is intentionally **not** a required PR check.

- **Enforce for admins:** recommended on.
- **Force pushes / deletions:** disabled.

## Applying it (admin only)

Requires `gh` authenticated as a repo admin. Set the repo once:

```bash
REPO=inomotech-foss/can-rosetta
```

### 1. Branch protection + required status checks

```bash
gh api -X PUT "repos/$REPO/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      { "context": "server (py3.10)" },
      { "context": "server (py3.12)" },
      { "context": "edge / autopi (py3.10)" },
      { "context": "edge / autopi (py3.12)" },
      { "context": "schema + sample-session validation" },
      { "context": "companion / ios (project generation)" }
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true
  },
  "required_linear_history": true,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "restrictions": null
}
JSON
```

### 2. Enable the merge queue on `main`

The merge queue is configured through the ruleset/branch settings API. Via the
UI: **Settings -> Branches -> Add branch ruleset -> Require merge queue**. Via
the API (rulesets):

```bash
gh api -X POST "repos/$REPO/rulesets" --input - <<'JSON'
{
  "name": "main-merge-queue",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "rules": [
    { "type": "merge_queue",
      "parameters": {
        "merge_method": "SQUASH",
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 5,
        "max_entries_to_merge": 5,
        "min_entries_to_merge": 1,
        "check_response_timeout_minutes": 60,
        "min_entries_to_merge_wait_minutes": 5
      }
    }
  ]
}
JSON
```

### 3. Repo defaults

```bash
# Squash merges only, linear history, auto-delete merged branches.
gh api -X PATCH "repos/$REPO" \
  -F allow_squash_merge=true \
  -F allow_merge_commit=false \
  -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true
```

## Verifying

```bash
gh api "repos/$REPO/branches/main/protection" | jq '.required_status_checks.checks'
gh api "repos/$REPO/rulesets" | jq '.[].name'
```

## Dependency updates

Renovate (`.github/renovate.json`) opens grouped PRs (Python, GitHub Actions,
Docker, deploy manifests) on a Monday schedule. Because branch protection is on,
Renovate PRs go through the same CI + merge queue as human PRs.
