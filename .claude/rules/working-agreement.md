# Rules: Working Agreement

Applied always. How to work in this repo, so nobody has to re-state it each session.

## Act; don't hand back a to-do list

**If a connected tool can do it, do it.** Do not describe a manual procedure for something you can
execute. This repo has live MCP connections to **Roboflow** (projects, versions, exports, batches,
jobs), **Google Drive** (search, read, download), and **Gmail/Calendar**, plus `gh` for GitHub. A
reply that ends in "now go run X" when X was callable is a failure, not a hand-off.

Same for shell work: generate the dataset, run the eval, open the PR. Long jobs go in the
background — report when they land, don't narrate progress.

## Don't ask for permission on ordinary work

Default is **proceed and report**. Cameron does not want to be consulted on routine, reversible
steps, and asking is more expensive than a wrong-but-fixable choice. Make the judgment call, say
what you assumed, keep going.

Check in **only** for actions that are hard to reverse or reach outside the repo:

- Sending real mail (`mail_via_lob.py --send`), publishing anything, posting externally
- Changing sharing/permissions on Cameron's accounts or making private data publicly linkable
- Deleting or overwriting data that isn't reproducible (`tile_index.json`, label sets, `runs/`)
- `git push --force`, history rewrites, anything touching `main` directly
- Spending money (paid API tiers, GPU instances)

Everything else: just do it.

## Name the real blocker

If something can't be done, say precisely *why*, and never dress a capability limit up as a
permission question or vice versa. "I need you to approve X" and "X is technically impossible for
me" are different sentences and lead to different fixes.

Worked example — pulling a 128 MB checkpoint out of Drive is **not** a permissions problem. The
Drive MCP connector's `download_file_content` returns base64 **into the conversation**, so a 128 MB
binary becomes ~170 MB of context and cannot be transferred that way at all. There is no
`set_permissions` tool either, so the link-sharing workaround isn't ours to perform. The fix is a
real transfer path (Drive for Desktop mounted at `/mnt/g`, `rclone`, or a browser download), not an
approval.

## Commits and PRs

- **No Claude attribution.** No `Co-Authored-By: Claude`, no "Generated with Claude Code" trailer,
  in commits or PR bodies.
- Branch off `main`; never commit to `main` directly.
- Commit messages carry the *reasoning*, not just the change — why this number, what it supersedes,
  what it would break. This repo has repeatedly been bitten by stale metrics whose provenance
  nobody recorded (`map50_test=0.263`, the "0.396" SAHI F1, the "~0.65 mAP50" that was never a
  current-labels figure). Write the commit so the next person can't re-derive it wrong.

## Numbers

- State provenance when you record a metric: what measured it, on which split, at which threshold,
  against how many GT objects, and what it supersedes.
- A gate is a **shipping floor**, not a model-selection tool. Do not propose raising a bar because
  a model cleared it — that is moving goalposts. Report the gate result and the anchor comparison
  side by side; they answer different questions.
- Don't validate superiority claims against "the field" without a sourced comparison. Detection
  numbers are confounded by GSD, label convention (whole-array vs per-panel), IoU threshold, and
  task (classification vs instance detection).
