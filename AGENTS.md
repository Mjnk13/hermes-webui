# Agent instructions for Hermes WebUI

This file is the shared entry point for AI assistants working in this
repository. Keep it project-specific and safe to publish. Do not put personal
machine setup, private network details, credentials, tokens, or local-only
workflow notes here.

## Read first

Before making changes, read:

1. `README.md`
2. `CONTRIBUTING.md`
3. `docs/CONTRACTS.md`
4. `CHANGELOG.md`

For architecture, testing, or setup work, also read the matching reference:

- `ARCHITECTURE.md` for design constraints and current module layout
- `TESTING.md` for local verification commands and manual test guidance
- `docs/onboarding.md` for first-run onboarding behavior
- `docs/troubleshooting.md` for diagnostic flows
- `docs/rfcs/README.md` for larger RFCs and state/durability contracts

For UI or UX work, read `docs/UIUX-GUIDE.md` and `DESIGN.md` before
changing layout, interaction flow, themes, chat rendering, or composer chrome.

## Onboarding and reinstall support

If the task involves install, reinstall, bootstrap, first-run onboarding,
provider setup, local model server setup, Docker onboarding, WSL onboarding, or
support for a failed first run, read `docs/onboarding-agent-checklist.md`
before running commands or inspecting logs.

Follow that checklist's safety rules:

- use isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR` for trials unless the
  human explicitly asks to use real state
- do not delete or overwrite a real `~/.hermes` directory without explicit
  approval
- do not print API keys, OAuth tokens, cookies, full `.env` files, full
  `auth.json` files, or password hashes
- collect non-secret status and log evidence before recommending a fix

## Contribution style

- Keep one logical change per PR; split unrelated refactors or cleanup.
- Read `docs/CONTRACTS.md` and the linked contract/RFC for the touched
  subsystem before editing.
- For local pytest runs, use `./scripts/test.sh` instead of bare `python3`,
  `python -m pytest`, or `pytest`. The script creates/uses the repo `.venv`,
  pins execution to Python 3.11-3.13, and installs missing dev test dependencies.
  `HERMES_WEBUI_TEST_PYTHON` selects the supported base interpreter used to
  create or rebuild `.venv`; it must not install test dependencies into a
  system/Homebrew interpreter directly.
  If a direct pytest invocation reports an unsupported interpreter, rerun through
  `./scripts/test.sh` before debugging product code.
- Prefer the existing Python + vanilla JavaScript structure. Do not add
  dependencies, build tools, frameworks, or long-lived processes without clear
  justification and a rollback story.
- Update docs when changing setup, onboarding, runtime behavior, architecture,
  testing guidance, or user-facing workflows.
- Do not edit `CHANGELOG.md` in ordinary contributor PRs. The release workflow
  owns changelog updates through release commits. If a change is release-note
  worthy, include concise release-note wording in the PR body instead.
- For UI or UX changes, include before/after evidence and test relevant
  desktop, narrow, and mobile states.
- For behavior changes, add or update automated tests where practical and list
  the manual verification performed.
- For runtime, streaming, recovery, replay, compression, or sidebar metadata
  changes, name the state layer being mutated and prove the relevant invariant.
- For Docker build changes in `docker_init.bash`, mirror directory exclusions
  in both the `rsync` and `cp -a` paths — `/opt/hermes` may contain subdirectories
  with restricted permissions (e.g. `.playwright/`).

## Before you open a PR — the change guidelines

Read [`docs/GUIDELINES.md`](docs/GUIDELINES.md) in full before non-trivial work. It is the
distilled set of habits that get a change merged in one review round instead of several. The
compressed form:

1. **Fix the class, not the instance.** A bug usually has siblings — other call sites, backends,
   companion endpoints, layouts, exit paths. Find them all and fix the shared chokepoint, or name
   the ones you left out of scope.
2. **Trace one authoritative value end-to-end** (`input → normalize → decision → action → persist →
   cleanup`); the code that *decides* and the code that *acts* must use the same resolved value.
3. **When you can't confirm something, fail closed and say so.** Never take the permissive branch on
   uncertainty; never report a failure as success. "Unknown" is not "allowed."
4. **Enumerate the state-space before editing** — entry point, backend, item count (0/1/many), every
   lifecycle exit (success/error/cancel/replace/teardown), auth on/off, concurrency, hostile input —
   and cover each or mark it out of scope. Most redo rounds are one un-considered dimension.
5. **Assume inputs and check-then-use gaps are adversarial** — validate at the point of use (hold a
   handle, don't re-resolve a path), scope caches by complete identity, handle crafted input.
6. **A test must fail before your fix and pass after it.** Assert observable behavior, not a source
   string or a mock of the thing under test; use multiple items if selection is what's being tested.
7. **Name the owner of every piece of state and prove it's released on every exit** (success, error,
   cancel, replace, shrink, teardown) — not just the happy path.
8. **Fallbacks/defaults are contracts — extend the mechanism, don't copy it.** Editing N parallel
   blocks identically means you missed a chokepoint (e.g. new copy goes in the `en` locale only).
9. **The diff is the task and nothing else.** Extras go in the PR description, not the diff; run the
   affected + neighboring tests before opening.
10. **A visible control costs attention on every visit** — place it by frequency of use and by where
    mainstream chat apps put the equivalent, not by where your diff already is; verify with
    before/after images at desktop and narrow widths.

Show the work in the PR body: the siblings you found, proof the test failed before the fix, the
verification run, before/after images for visible changes, and an explicit list of what you could
not verify.

## Local state and secrets

Hermes WebUI can read and write real agent state, sessions, workspaces,
credentials, and cron data. Treat local validation as potentially destructive
unless you have confirmed the active state directories.

Prefer isolated trial state for experiments:

```bash
HERMES_HOME=/tmp/hermes-webui-agent-home \
HERMES_WEBUI_STATE_DIR=/tmp/hermes-webui-agent-state \
HERMES_WEBUI_PORT=8789 \
python3 bootstrap.py
```

Do not include private machine instructions in this tracked file. Use a
git-ignored local note for personal workflow details.

# Custom Fork Maintenance Rules

This repository is a long-term custom fork of Hermes WebUI.

## Branch ownership

- The original Hermes WebUI repository currently uses `upstream/master` as its
  default source branch.
- Local `main` and `origin/main` are clean mirrors of `upstream/master`.
- `custom` contains the maintained custom implementation.
- All custom development must occur on `custom` or branches created from
  `custom`.
- Do not add custom product implementation directly to `main`.
- If the upstream default branch changes, verify its symbolic `HEAD` and update
  these rules before changing the mirror source. Do not assume a rename.

## Mandatory rule loading

Before performing any of the following operations, read this file completely:

- Rebase
- Merge from upstream
- Upstream synchronization
- Branch reset
- History rewrite
- Force push
- Conflict resolution
- Cleanup involving custom files

Treat these rules as mandatory.

## SSH identity

Use the personal GitHub SSH alias:

`git@github.com-personal:<owner>/<repository>.git`

For this fork, the required remotes are:

- `origin`: `git@github.com-personal:Mjnk13/hermes-webui.git`
- `upstream`: `git@github.com-personal:nesquena/hermes-webui.git`

Treat `upstream` as read-only. Never push commits, branches, or tags to the
original repository and never create remote branches there. All remote writes
for this custom fork must target the personal `origin` only.

Do not replace remotes with:

- HTTPS GitHub URLs
- `git@github.com:<owner>/<repository>.git`
- A company GitHub SSH alias

Do not modify global Git or SSH configuration.

## Main protection

Never run `git reset --hard upstream/master` while checked out on:

- `custom`
- A custom feature branch
- A branch containing custom implementation

A hard reset to upstream is allowed only on the clean mirror branch `main`,
after all required backups have been verified. Use the verified upstream
default branch rather than assuming its name.

## Hidden and local file protection

Do not use `git diff` alone to determine whether the repository has local
changes.

Before every upstream update or rebase, inspect:

- Staged changes
- Unstaged changes
- Untracked files
- Ignored files
- `assume-unchanged` files
- `skip-worktree` files
- Sparse-checkout state
- `.git/info/exclude`
- Global Git excludes
- Git attributes and content filters
- Local configuration files such as `config.yaml` and `.env`

A file may be absent from `git diff` while still containing important local
changes.

Use:

```bash
git status --porcelain=v2 --untracked-files=all
git status --ignored --untracked-files=all
git ls-files -v
git ls-files --debug
```

Do not automatically stage, commit, reset, or delete local configuration files.

Files such as `config.yaml` and `.env` may contain local settings or secrets.
Preserve them locally before changing branches, but do not commit or push them
unless the repository explicitly requires them and their contents are confirmed
safe.

Do not automatically clear `assume-unchanged` or `skip-worktree`. Investigate
and report the reason first. Never clear either flag repository-wide without
reviewing every affected path.

## Required backup before upstream updates

Before each upstream update:

1. Verify the current branch.
2. Verify the complete working-tree and index state.
3. Audit hidden and local files.
4. Create a local snapshot in `.git/custom-fork-safety/<timestamp>/`.
5. Create a dated backup branch from `custom`.
6. Create a dated annotated backup tag.
7. Create a Git bundle.
8. Push only the safe committed-history backup references to `origin`.
9. Record the pre-rebase custom HEAD.
10. Fetch both `origin` and `upstream`.

Do not push local configuration snapshots or secrets.

## Conflict resolution

During a rebase:

- Analyze each conflict semantically.
- Preserve the intended custom behavior.
- Integrate compatible upstream changes.
- Adapt custom code when upstream architecture has changed.
- Do not resolve the whole repository using only `ours`.
- Do not resolve the whole repository using only `theirs`.
- Do not skip or drop a custom commit merely to complete the rebase.
- Stop for review when product behavior or architecture is ambiguous.
- Abort the rebase when safe preservation cannot be guaranteed.

Do not use repository-wide conflict-resolution commands such as:

```bash
git checkout --ours .
git checkout --theirs .
git restore --ours .
git restore --theirs .
```

Use targeted resolution per file or conflict section.

## Push policy

Use:

`git push --force-with-lease`

Never use:

`git push --force`

Fetch the remote before every force-with-lease push and verify that no
unexpected remote commit will be overwritten.

## Post-rebase verification

After every rebase:

- Compare the result against the backup branch.
- Verify that each original custom change has an equivalent patch.
- Check for accidentally deleted custom files.
- Check local-only files separately.
- Check for unresolved conflict markers.
- Run the repository's documented validation commands.
- Confirm that `AGENTS.md` remains present.
- Confirm that `main` remains a clean upstream mirror.
- Do not push until validation succeeds.

Do not automatically delete backup branches, tags, bundles, or local safety
snapshots.

Do not open or merge a pull request unless explicitly requested.

## Reusable upstream-update workflow

Follow this workflow for every future upstream update:

1. Read `AGENTS.md` completely.
2. Run `ssh -T git@github.com-personal` and semantically confirm that GitHub
   authenticated as `Mjnk13`; GitHub's successful no-shell response may use a
   non-zero exit code.
3. Verify that `origin` and `upstream` both use `github.com-personal` and match
   the URLs documented above.
4. Confirm that the current repository is the Hermes WebUI custom fork.
5. Verify that no rebase, merge, cherry-pick, revert, bisect, or sequencer
   operation is already in progress.
6. Audit staged, unstaged, untracked, ignored, `skip-worktree`, and
   `assume-unchanged` files.
7. Investigate the tracking, ignore source, attributes, filters, content
   differences, and sensitivity of local files such as `config.yaml` and `.env`.
8. Create and verify `.git/custom-fork-safety/<timestamp>/`, preserving relative
   paths and checksums without printing secret contents.
9. Create `backup/custom-before-upstream-update-<timestamp>` from `custom`.
10. Create an annotated `backup-custom-before-upstream-update-<timestamp>` tag.
11. Create and verify a Git bundle in the local safety snapshot.
12. Push only the safe committed-history backup branch and tag to `origin`.
13. Fetch `origin` and `upstream`, including tags and prune state.
14. Determine the upstream default branch from `upstream/HEAD`; currently it is
    `upstream/master`.
15. Update only clean local `main` to the verified upstream default commit.
16. Compare `origin/main...main` and stop if an unexpected fork commit would be
    overwritten.
17. Push `origin/main` using `--force-with-lease` only when necessary.
18. Switch back to `custom`.
19. Read `AGENTS.md` completely again.
20. Re-audit repository and local-only state and verify every safety reference.
21. Rebase `custom` onto `main`, pausing for semantic per-file conflict
    resolution.
22. Restore or migrate local-only configuration safely. Do not blindly replace
    a new upstream config schema with an old local file.
23. Compare the result with the backup using `git range-diff`, changed-file
    comparison, patch equivalence, and semantic inspection.
24. Check for accidental deletions, staged secrets, and unresolved conflict
    markers.
25. Run the repository's documented lint, tests, and relevant production build
    commands using its existing package managers and scripts.
26. Show a final report containing identities, remotes, safety references,
    branch SHAs, range-diff, local-file audit, conflicts, and validation results.
27. Fetch `origin` again and verify that `origin/custom` has not gained an
    unexpected commit.
28. Push `custom` using `git push --force-with-lease origin custom` only after
    all checks pass.

Stop immediately when:

- The working tree contains unexplained changes.
- A hidden or local file has not been preserved.
- `config.yaml`, `.env`, or another configuration file has an unknown tracking
  state.
- SSH identity verification fails or resolves to an unexpected account.
- A remote points to a company account or does not use `github.com-personal`.
- A required snapshot, branch, tag, or bundle cannot be created and verified.
- The personal fork cannot be verified.
- Unexpected remote commits would be overwritten.
- Conflict resolution is ambiguous.
- Validation fails.
- Custom code preservation cannot be demonstrated.

Do not run destructive commands merely to make the workflow complete.
