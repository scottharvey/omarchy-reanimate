# Reanimate

Save every window, workspace and tiling split before a reboot, and get the
exact layout back on the next boot — not just the right apps on the right
workspaces, but the same splits at the same ratios.

## What makes this different

Most session tools relaunch your apps onto the right workspace and stop there,
because Hyprland exposes no way to read or write its layout tree. This one
reconstructs the tree from the saved window rectangles, then replays it:
windows are re-inserted one at a time, in their original creation order, with
an explicit `preselect` direction before each insert. A convergence pass then
corrects the split ratios.

Verified pixel-identical on layouts up to six windows with deeply nested and
heavily skewed splits, floating windows included.

## What it restores

- Every window's launch command, rebuilt from `/proc/<pid>/cmdline`
- Workspace and monitor assignment
- **The dwindle tree**: split structure, orientation and ratios
- Floating windows at their exact saved position and size
- Fullscreen and maximized state, pinned windows
- Terminal working directories — the shell's real cwd, not the terminal's
  original one
- Active workspace per monitor, and which window had focus
- **Multiple browser windows**, each to its own workspace

## Browser handling

Chromium collapses every window into one process with one command line, so a
naive `/proc` walk sees several windows and can only relaunch one. This plugin
handles the three cases separately:

- **Web apps** (`--app=` windows) are resolved to their URL and relaunched
  individually with `omarchy-launch-webapp`, so each one lands on its own
  workspace. The URL comes from, in order of preference: your explicit
  `webapp_urls` overrides; a desktop entry (`omarchy webapp install`, or a
  Chromium-installed PWA with `StartupWMClass`); web apps seen running before
  (`~/.local/state/omarchy/webapp-classes.json`); URLs harvested from your
  Hyprland bindings and menu config; and as a last resort, the URL decoded from
  the window class itself. Web apps launched straight from a keybinding with no
  desktop entry are covered by the config scan.
- **Plain browser windows** are relaunched through `/usr/bin/chromium` -- the
  same wrapper Omarchy uses, not the raw binary `/proc` reports, which would
  give the window a different class. The browser is launched once, letting
  Chromium's own session restore repopulate the tabs. Every window it opens is
  parked on a special workspace and then moved into place one at a time, at its
  original position in the insertion sequence. Parking is what keeps the tiling
  exact -- otherwise Chromium's windows arrive in a burst, in its order, and
  scramble the tree.
- On a cold boot Chromium can take a long time to show its first window, then
  restores the rest in a burst. The restore waits for the first
  (`browser_first_window_timeout`), then for quiet (`chromium_settle`). If
  Chromium remembers fewer windows than were saved, the difference is topped up
  with `--new-window`, and any window that arrives late is caught for
  `late_browser_watch` seconds and moved next to the other browser windows.

Tab-to-window mapping for plain windows is Chromium's decision, not ours. Per
window tab capture would need Chromium's remote debugging port; that is a
future upgrade and the manifest format already has a slot for it.

## When the restore runs

Only at boot. The manifest carries its save time, and the restore refuses to
replay anything saved since the machine booted -- those windows are already
open, and replaying them would duplicate every one. This matters because the
Omarchy shell re-runs plugin services whenever a plugin file changes or the
shell restarts. `--force` overrides the guard for testing on a scratch
workspace.

## Install

From the marketplace:

```bash
omarchy plugin add https://github.com/scottharvey/omarchy-reanimate.git --enable
```

`omarchy plugin add` does **not** run `install.sh`. On its own it gives you the
Quickshell service and nothing else — no periodic snapshots, no menu entries.
Run the installer afterwards:

```bash
~/.config/omarchy/plugins/io.github.scottharvey.reanimate/install.sh
```

Or from a clone:

```bash
./install.sh
```

That installs the post-boot hook and a systemd user timer that snapshots the
session every 2 minutes. Then, recommended, merge
`extensions/omarchy-menu-snippet.jsonc` into
`~/.config/omarchy/extensions/omarchy-menu.jsonc` and run `omarchy menu refresh`
so Reboot and Shutdown snapshot the session first. That step edits your menu
config, so it is deliberately manual.

## Remove

```bash
systemctl --user disable --now omarchy-reanimate-save.timer
rm -f ~/.config/systemd/user/omarchy-reanimate-save.{service,timer}
rm -f ~/.config/omarchy/hooks/post-boot.d/11-reanimate
rm -f ~/.local/bin/omarchy-reanimate-{save,restore,show,diff}
omarchy plugin remove io.github.scottharvey.reanimate
```

Then delete the `system.reanimate-save`, `system.reboot` and `system.shutdown`
entries from `~/.config/omarchy/extensions/omarchy-menu.jsonc` if you added
them, and run `omarchy menu refresh`. Saved state lives in
`~/.local/state/omarchy/reanimate/` and can be deleted too.

## Usage

All four commands are linked into `~/.local/bin`, so they are on your PATH.

```bash
omarchy-reanimate-save              # snapshot now
omarchy-reanimate-save --print      # show the manifest without writing it
omarchy-reanimate-show              # readable view of what is currently saved
omarchy-reanimate-restore --dry-run # show what a restore would do
omarchy-reanimate-restore --keep    # restore without consuming the manifest
omarchy-reanimate-restore --force   # replay a manifest saved during this boot (testing)
```

### Debugging a restore

`omarchy-reanimate-diff` compares a saved manifest against what actually came
back. It matches windows by identity rather than by position, so a reordering
reads as a reordering instead of a huge bogus geometry delta.

```bash
omarchy-reanimate-diff --after   # against the snapshot taken right after restore
omarchy-reanimate-diff           # against the windows open right now
omarchy-reanimate-diff --json    # machine-readable, for scripting
```

Use `--after` when debugging a boot. Every restore writes
`~/.local/state/omarchy/reanimate/session-after.json` capturing the desktop the moment it
finished, because by the time you sit down to investigate a bad restore you
have already opened and closed windows and a live comparison is worthless.

The exit code is 0 only on a perfect restore, so it can gate a test script.
Each window is reported as one of: `ok`, `geometry` (right window, wrong size
or place), `class` (relaunched with a different window class), `missing`, or
`extra`.

State lives in `~/.local/state/omarchy/`:

- `session.json` — the manifest, renamed to `.restored` once used
- `session.lock` — guards against two restores racing
- `restore.log` — timestamped save and restore results

## Configuration

Optional, at `~/.config/omarchy/reanimate.json`. See
`config.example.json`. The keys that matter most:

- `exclude_classes` — regexes for windows never to save. Put games and video
  players here; relaunching them after a reboot is rarely what you want. Note
  that setting this key replaces the defaults rather than adding to them.
- `restore_layout` — set false to place windows on workspaces without
  rebuilding the tiling
- `webapp_urls` — `{"chrome-example.com__-Default": "https://example.com/"}`
  for any web app the save reports as unresolved
- `browser_first_window_timeout`, `chromium_settle`, `late_browser_watch` —
  browser timing; see above

## Limitations

This is best-effort relaunch, not process freezing. Only hibernation
(`omarchy hibernation setup`) preserves real application state across a
restart.

- Apps reopen fresh. Unsaved work is gone; editors and browsers restore their
  own session state only if configured to.
- Terminals come back in the right directory with a fresh shell, not your
  running program. Use a multiplexer (`omarchy launch terminal tmux`) if you
  need the session itself back.
- If a web app cannot be resolved to a URL the save says so and falls back to
  a plain browser window; add it to `webapp_urls`.
- Window **groups** are recorded but not rebuilt; grouped windows are excluded
  from layout reconstruction so they cannot corrupt the rest of the tree.
- Only the `dwindle` layout gets tree reconstruction. Under any other layout,
  windows still land on the correct workspaces with correct floating geometry.
- Scratchpad and special workspaces are skipped.
- Restore visits each workspace as it rebuilds it, then returns to the one you
  were on. This is visible on boot and is what makes the insertion order exact.
- A save triggered outside the menu or timer (a plain `systemctl reboot`)
  captures only what the last automatic snapshot saw.

## Requirements

Omarchy with Hyprland 0.56+ (the restore drives the Lua dispatcher API) and
Python 3.11+ from the standard library only. No sudo, no network, no
third-party Python packages.

Optional: [herdr](https://herdr.dev) — if it is running, its window is
relaunched and any agents in its panes are restarted. Nothing here requires it.

Tested on Omarchy 4.0.1 with Hyprland 0.56.2, the `dwindle` layout, `foot` and
Chromium. The terminal entries for kitty, Alacritty, ghostty and wezterm follow
each terminal's documented flags but have not been exercised; other browsers
are untested.

## Testing without a reboot

Use an empty workspace so your real ones are untouched:

```bash
# 1. on an empty workspace, open a few windows and arrange them
# 2. snapshot, then keep only that workspace in the manifest
omarchy-reanimate-save
python3 -c 'import json,os; p=os.path.expanduser("~/.local/state/omarchy/reanimate/session.json"); m=json.load(open(p)); m["workspaces"]=[w for w in m["workspaces"] if w["id"]==9]; json.dump(m,open(p,"w"),indent=2)'
# 3. close every window on that workspace, then
omarchy-reanimate-restore --force
omarchy-reanimate-diff --after
```

`--force` is needed because the manifest was saved during this boot.
