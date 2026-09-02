# Asking this repo questions with a coding agent (macOS + VS Code + Codex)

For someone who wants to **understand** this project by asking an agent about it, not to run or
change anything. Written 2026-08-31 for a first session on a Mac.

The short version: **you need a clone, a folder open, and Read Only mode. Nothing else.** No
conda, no data download, no credentials. If an agent tells you it cannot do something because of
the sandbox, that is almost always because it tried to install software you do not need.

---

## 1. Get the repo (about 5 minutes)

**Already cloned it?** Skip to the location check below. You do not need to clone again.

In VS Code, press `Cmd+Shift+P`, type `Git: Clone`, and paste:

```
https://github.com/Better-Behavior-Foundation/solar-soiling-ml.git
```

VS Code offers to sign you into GitHub in the browser. That avoids setting up SSH keys. Everyone
in the Better-Behavior-Foundation org already has read access.

### The location check, and do not skip it

**The clone must not live in Documents, Desktop or Downloads.** macOS gates those three
directories with its privacy controls, and the agent's sandbox hits permission denials there that
no Codex setting will fix. If you already cloned into one of them, that alone can be the whole
sandbox problem.

To check where you are, open the integrated terminal in VS Code (`Ctrl+\``) and run:

```bash
pwd
```

If the path contains `/Documents/`, `/Desktop/` or `/Downloads/`, move it and reopen:

```bash
mkdir -p ~/repos && mv "$(pwd)" ~/repos/solar-soiling-ml
```

Then `File > Open Folder` on `~/repos/solar-soiling-ml`. Nothing is lost in the move.

When VS Code asks, **open the cloned folder itself**, not a parent folder and not a single file.
The agent takes its working root from whatever folder is open.

## 2. Put Codex in Read Only

Open the Codex panel and set the mode picker to **Read Only**.

This is the step people get backwards. Read Only still lets the agent read every file, search the
codebase, and answer questions in full. What it blocks is installing packages and writing files,
which is exactly the category of thing that produces "I don't have access to the sandbox." Leaving
it in Read Only means you never see that error.

The agent reads [AGENTS.md](../AGENTS.md) at the repo root on its own, so it starts oriented
without you having to explain the project first.

## 3. Ask

Openers that work well:

- "Read docs/SOILING_STAGE2_GUIDE.md and explain the soiling model to me in plain English."
- "What features does the Stage 2 model use, and where does each one come from?"
- "Walk me from a detected rooftop polygon to a dollar recommendation, naming the file at each step."
- "What are the known limits of the soiling model? Cite the docs."

**Ask it to cite a file and line for every number it gives you.** This is not paranoia. This repo
has stale metrics scattered through older docs, and an agent will quote them with total confidence.
The house rule in [.claude/rules/working-agreement.md](../.claude/rules/working-agreement.md) is
that a number travels with its provenance, and it applies to agents too.

### Which doc wins when two disagree

| Question | Canonical answer lives in |
|---|---|
| How the soiling model works | [SOILING_STAGE2_GUIDE.md](SOILING_STAGE2_GUIDE.md) |
| What the soiling model cannot do | [PVDAQ_LANE_HANDOFF_20260831.md](PVDAQ_LANE_HANDOFF_20260831.md) and [SOILING_LEVEL_INVESTIGATION.md](SOILING_LEVEL_INVESTIGATION.md) |
| Whether the business case closes | [ECONOMICS_GROUNDING_20260809.md](ECONOMICS_GROUNDING_20260809.md), short version in [CRAIG_BRIEF_2026-08-19.md](CRAIG_BRIEF_2026-08-19.md) |
| Where the project stands overall | [Q2_PLAN.md](Q2_PLAN.md) |

Two limits worth knowing before you read any accuracy number, because they change what it means:

- The model scores **0.5313 on an unseen region** against 0.7569 for a random holdout of the same
  size. "Works anywhere" is not supportable today. See the PVDAQ hand-off.
- It cannot rank homes **within** one neighbourhood. The training labels are station-level, so
  there is no within-area signal to learn. See SOILING_LEVEL_INVESTIGATION.

---

## 4. Optional: letting the agent run things (about 30 minutes)

Only needed if you want to reproduce numbers rather than read about them.

Run these **yourself in VS Code's integrated terminal**, not through the agent. The sandbox blocks
network access, so every one of them fails if the agent attempts it. This is the real cause of the
sandbox error, and doing the installs by hand is cleaner than loosening the sandbox.

```bash
brew install miniforge            # only if conda is not already installed
bash setup/setup_conda.sh         # creates the `solar-soiling` env on python 3.10
conda activate solar-soiling
make bootstrap
make test-fast                    # expect 470 passed, 1 skipped, 2 deselected
```

Stop and confirm that is green before going further. It needs no data and no credentials, and it
is the only check that separates a broken environment from missing data.

Then the Stage 2 data, 2.4 MB, using the GitHub access you already have:

```bash
gh release download handoff-v1 --repo Better-Behavior-Foundation/solar-soiling-ml \
    -p 'handoff-stage2.tar' -D /tmp/handoff
tar xf /tmp/handoff/handoff-stage2.tar -C .
make check-data
```

Now a real reproduction, which the agent can run even in Read Only because it only reads local
files:

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/predict/holdout_ci.py
# expect: pooled out-of-year AUC 0.7095, about 60 seconds
```

Full detail on the setup path and its verification tiers is in [ONBOARDING.md](ONBOARDING.md) §1.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Anything path-related, on a first session | Stale session root: the Codex session came up before the folder resolved | **Try this first.** Close and reopen VS Code. Open the folder *before* starting a Codex session, and do not switch folders mid-session. Observed 2026-08-31, cause unconfirmed |
| "No access to the sandbox" | Agent tried to install something | Set mode to Read Only, do installs yourself in the terminal |
| Permission denials nothing fixes (`Operation not permitted`) | Clone is in Documents, Desktop or Downloads | Move it to `~/repos/` |
| `No such file or directory` on the workspace itself | Clone is on a synced/virtual filesystem, or the agent is running in the cloud | See "Read the errno" below |
| Agent looks at the wrong files | A parent folder is open in VS Code | Open the repo folder itself |
| `ERROR: conda not found` | No conda on the machine | `brew install miniforge`, or skip section 4 entirely |
| A script cannot find a file | Data not pulled | `make check-data`, then [DATA.md](../DATA.md) |
| The agent cannot run **anything**, even `pwd` | VS Code opened the folder in Restricted Mode | `Cmd+Shift+P` > `Workspaces: Manage Workspace Trust` > Trust, then reload the window |
| The agent cannot find `AGENTS.md` | A file is open, not a folder, so there is no workspace root | `File > Open Folder` on the repo root |
| Commands fail even after trusting the folder | macOS Seatbelt cannot spawn | Test with `sandbox-exec -p '(version 1)(allow default)' /bin/echo ok`. If that fails, set `sandbox_mode = "danger-full-access"` in `~/.codex/config.toml` |

### Splitting a "cannot run anything" fault in two

The VS Code integrated terminal (`Ctrl+\``) is **not** sandboxed. Run `pwd && head -5 AGENTS.md`
there. If that works, the folder and macOS are fine and the fault is inside the agent's sandbox. If
it fails too, the fault is VS Code or the folder, and workspace trust is the first thing to check.


### Read the errno before changing any sandbox setting

The two failures look identical in a chat transcript and have nothing in common:

| Error | Means | Sandbox settings help? |
|---|---|---|
| `Operation not permitted` | The path exists, the sandbox refused it | Yes |
| `No such file or directory` | The path is not reachable at all | **No.** Loosening the sandbox does nothing |

An agent reporting `No such file or directory` for a workspace path it was told it has usually
means one of two things.

**The clone is on a synced or virtual filesystem.** These look like ordinary folders in Finder and
in the VS Code explorer, but they are file-provider mounts a spawned process often cannot resolve,
and their contents may be undownloaded placeholders:

- iCloud Drive at `~/Library/Mobile Documents/com~apple~CloudDocs/`, which is also where
  Documents and Desktop live once iCloud sync is on
- Google Drive, OneDrive and Dropbox at `~/Library/CloudStorage/`

Run `pwd` in the integrated terminal. If the path contains `Mobile Documents`, `CloudStorage`,
`Dropbox`, or spaces, copy the clone to plain local disk:

```bash
mkdir -p ~/repos && cp -R "$(pwd)" ~/repos/solar-soiling-ml
```

Use `cp` rather than `mv` so nothing is at risk if the sync client is mid-operation, then open the
new folder and delete the original once it works.

**Or the agent is running in the cloud, not on this machine.** The extension reports the local
workspace path while executing in a container that has no clone, so every path is missing. Set the
panel to run locally. To use cloud instead, an org owner must approve the OpenAI GitHub App for the
Better-Behavior-Foundation org under Settings > Third-party Access.
