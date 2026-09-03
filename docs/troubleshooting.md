# Troubleshooting

## Did it come back right?

`omarchy-reanimate-diff` compares a saved manifest against what actually
returned. It matches windows by identity rather than by position, so a
reordering reads as a reordering instead of a huge bogus geometry delta.

```bash
omarchy-reanimate-diff --after   # against the snapshot taken right after the restore
omarchy-reanimate-diff           # against the windows open right now
omarchy-reanimate-diff --json    # machine-readable
```

Every window is reported as one of `ok`, `geometry` (right window, wrong size
or place), `class` (relaunched with a different window class), `missing`, or
`extra`. The exit code is 0 only on a perfect restore, so it can gate a test
script.

Use `--after` when debugging a boot. Every restore writes
`session-after.json` capturing the desktop the moment it finished, because by
the time you sit down to investigate a bad restore you have opened and closed
windows and a live comparison is worthless.

## Reading the log

`~/.local/state/omarchy/reanimate/restore.log`, timestamped. A healthy boot
looks like this:

```
restore: started (8 window(s), saved 2026-09-03T16:33:36+07:00)
restore: browser restored 3 window(s), 3 slot(s) needed
restore: workspace 1: 2 window(s)
restore: workspace 2: 3 window(s)
restore: workspace 3: 3 window(s)
restore: completed
restore: agents: 3 to resume
restore:   resumed claude in pane w2:p1
```

Lines worth knowing:

| line | meaning |
| --- | --- |
| `browser restored N window(s), M slot(s) needed` | If N < M, Chromium did not bring back everything and the difference was topped up with blank windows. Usually means it was killed rather than asked to quit. |
| `layout not inferable on workspace(s) [N]` | The rectangles did not form a clean binary partition, so that workspace gets placement without tiling reconstruction. Window groups and unusual manual resizes do this. |
| `timed out waiting for <class>` | The app did not map a window within `map_timeout`. Slow-starting apps may need it raised. |
| `save: skipped, a restore is in progress` | Normal. The timer fired mid-restore and correctly did nothing. |
| `no free pane for <agent>` | herdr came back with fewer panes than there were agents. |
| `web app(s) with no known URL` | Add them to `webapp_urls`. |

## Testing without rebooting

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

`--force` is needed because the manifest was saved during this boot, and the
restore otherwise refuses to replay it.

## Nothing was restored at all

- Was there a manifest? The restore consumes `session.json` and renames it to
  `session.json.restored`. If you rebooted twice in quick succession, the
  second boot may have had nothing to restore.
- Is the timer running? `systemctl --user status omarchy-reanimate-save.timer`
- Did you install from the marketplace and skip `install.sh`? Then there is no
  hook and no timer — see the README's Install section.

## My browser came back with blank tabs

Reboot from the Omarchy menu rather than a shell. Only the menu entries run
`--quit-browsers`, and Chromium writes a usable session only when it is asked
to exit rather than killed. Check the log for `asked N browser process(es) to
quit`.
