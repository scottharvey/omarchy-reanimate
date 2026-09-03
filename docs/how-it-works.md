# How it works

## Rebuilding the layout tree

Hyprland exposes no way to read or write its layout tree, so there is nothing
to serialise. Reanimate infers the tree instead.

The save records each window's rectangle. `infer_tree` looks for a straight
line that cleanly partitions those rectangles into two groups, recurses into
each side, and returns a binary tree of splits with the ratio at each node. If
the rectangles do not form a clean binary partition (which happens with window
groups, or after manual resizing that breaks the invariant), it gives up for
that workspace and says so in the log rather than guessing.

Replaying it relies on one fact about dwindle: when it splits a leaf, the two
children are the original window and the new one. So the earliest-created
window in any subtree is exactly the leaf that was there when the split
happened. `insertion_plan` walks the tree and derives, for every window, which
existing window to focus and which direction to `preselect` before launching
it. Windows are then re-inserted in their original creation order, taken from
Hyprland's monotonically increasing `stableId`.

Structure comes back first; proportions come second. A convergence pass resizes
each tiled window toward its saved size, twice. Each resize redistributes
space to a single sibling subtree, so two passes settle it.

## Finding what a terminal is hosting

A window's `/proc/<pid>/cmdline` names the *terminal*, never what you started
inside it. Save a terminal running herdr and you get `foot`; restore it and you
get an empty shell, with every tab, pane and agent gone.

So the save walks the window's descendants, depth-bounded, looking for
something worth bringing back:

- **herdr** wins over everything. The agents inside its panes are its
  descendants too, but it is herdr that has to be relaunched. It rebuilds its
  own tabs and panes from its session file once it is up.
- **claude** is recorded with its absolute path, because it is installed
  through mise and a restored terminal gets no login shell to put the shims on
  `PATH`. It comes back as `claude --continue`.

The terminal is then relaunched hosting it: `foot herdr` rather than `foot`.
A restored terminal's argv already carries that payload, so the save strips any
existing copy before adding the current one; otherwise every snapshot would
append another (`foot herdr herdr`).

## Agents in herdr panes

herdr restores its own tabs and panes, but every one of them lands at a shell
prompt. Restarting the agents is not its job, so Reanimate records them
separately from the windows: agent kind, its pane, its cwd, and its own name.

Two things are easy to get wrong here:

- In `herdr agent start <NAME> --kind <KIND> --pane <ID>`, the positional is a
  **unique agent label, not the kind**. Passing the kind for all of them means
  the first starts and the rest are rejected with *"agent name claude is
  already used"*.
- `agent start` reports failure for an agent that came up fine and then stopped
  at a prompt, which is exactly what `claude --continue` does on a large
  session, offering to resume from a summary. The exit status is therefore
  ignored; what herdr can see in the pane afterwards is what counts. A blocked
  agent is a success with a note, not a failure.

Only agents actually running at save time are recorded, so a pane you left at a
shell prompt correctly comes back at one.

## Browsers

Chromium collapses every window into one process with one command line, so a
naive `/proc` walk sees several windows and can relaunch only one. Three cases,
handled separately.

**Ask it to die properly.** Chromium writes a complete session only on a
graceful exit. Killed as the machine goes down it is marked as having crashed
and comes back with a single window and stale tabs, no matter what "continue
where you left off" is set to. So `omarchy-reanimate-save --quit-browsers`
(which the Reboot and Shutdown menu entries use) records the layout first, then
sends `SIGTERM` and waits for the browser to finish. Its own session restore
then brings the windows back with the pages that were in them.

**Web apps** (`--app=` windows) are resolved to a URL and relaunched
individually with `omarchy-launch-webapp`, so each lands on its own workspace.
The URL comes from, in order of preference: your explicit `webapp_urls`
overrides; a desktop entry (`omarchy webapp install`, or a Chromium-installed
PWA with `StartupWMClass`); web apps seen running before
(`webapp-classes.json`); URLs harvested from your Hyprland bindings and menu
config; and last, the URL decoded from the window class itself. Chromium does
not restore `--app=` windows itself, so there is no risk of duplicates.

**Plain browser windows** are relaunched through `/usr/bin/chromium`, the
wrapper Omarchy uses, not the raw binary `/proc` reports, which would give the
window a different class. The browser is launched once and every window it
opens is parked on a special workspace, then moved into place one at a time at
its original position in the insertion sequence. Parking is what keeps the
tiling exact: otherwise Chromium's windows arrive in a burst, in its order, and
scramble the tree.

On a cold boot Chromium can take a long time to show its first window and then
restores the rest at once. The restore waits for the first
(`browser_first_window_timeout`), then for quiet (`chromium_settle`). If it
still remembers fewer windows than were saved, the difference is topped up with
`--new-window`, and any straggler is caught for `late_browser_watch` seconds
and moved in with the others.

Tab-to-window mapping for plain windows is Chromium's decision, not ours.
Per-window tab capture would need Chromium's remote debugging port; the
manifest format already has a slot for it.

## When the restore runs

Only at boot. The manifest carries its save time and the restore refuses to
replay anything saved since the machine booted. Those windows are already
open, and replaying them would duplicate every one. This matters because the
Omarchy shell re-runs plugin services whenever a plugin file changes or the
shell restarts.

Both the post-boot hook and the Quickshell service fire around login. They take
an exclusive lock and the restore consumes its manifest, so whichever arrives
second is a no-op. `--force` overrides the boot guard for testing.
