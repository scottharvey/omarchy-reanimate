# Configuration

Optional, at `~/.config/omarchy/reanimate.json`. Anything you leave out keeps
its default. A copyable starting point is in
[`config.example.json`](../config.example.json).

## Options

| key | default | what it does |
| --- | --- | --- |
| `exclude_classes` | walker, gamescope, steam apps, xwaylandvideobridge | Regexes for windows never to save. **Setting this replaces the defaults rather than adding to them.** |
| `exclude_titles` | `[]` | Same, matched against window titles. |
| `restore_layout` | `true` | Set false to place windows on workspaces without rebuilding the tiling. |
| `restore_geometry` | `true` | Set false to skip the convergence pass that corrects split ratios. |
| `webapp_urls` | `{}` | `{"chrome-example.com__-Default": "https://example.com/"}` for any web app the save reports as unresolved. |
| `map_timeout` | `20.0` | How long to wait for a launched app to map its window. |
| `poll_interval` | `0.05` | How often to check whether a launched window has appeared. |
| `settle_delay` | `0.35` | Pause after each window maps, before inserting the next. |
| `browser_first_window_timeout` | `30.0` | How long to wait for Chromium's first window on a cold boot. |
| `chromium_settle` | `3.0` | Quiet period after which Chromium is considered finished opening windows. |
| `late_browser_watch` | `10.0` | Keep catching stray browser windows for this long after the restore. |
| `browser_quit_timeout` | `20.0` | How long `--quit-browsers` waits for the browser to shut down. |
| `herdr_timeout` | `60.0` | How long to wait for herdr's panes to appear before giving up on agents. |
| `herdr_settle` | `3.0` | Pause after panes appear, so they reach a shell prompt before agents start. |
| `herdr_agent_timeout` | `60000` | Milliseconds passed to `herdr agent start`. |
| `browser_classes` | chromium, chrome, brave, edge, helium | Window classes treated as the same browser. |
| `park_workspace` | `special:reanimate` | Where browser windows wait before being moved into place. |

Games and video players belong in `exclude_classes`. Relaunching them after a
reboot is rarely what anyone wants.

## Where things live

State, in `~/.local/state/omarchy/reanimate/`:

| file | what it is |
| --- | --- |
| `session.json` | the manifest; renamed to `session.json.restored` once used |
| `session-after.json` | what the desktop looked like the moment a restore finished |
| `session.lock` | guards against two restores racing |
| `restore.log` | timestamped save and restore results |
| `webapp-classes.json` | web app classes seen running, so their URLs can be recovered later |

Installed elsewhere:

| path | what it is |
| --- | --- |
| `~/.config/omarchy/hooks/post-boot.d/11-reanimate` | triggers the restore at boot |
| `~/.config/systemd/user/omarchy-reanimate-save.timer` | the 2-minute snapshot |
| `~/.local/bin/omarchy-reanimate-*` | the four commands |

## Snapshot timing

Saves happen from three places:

- the systemd timer, every 2 minutes
- **Reboot** and **Shutdown** in the Omarchy menu, which also ask the browser
  to quit cleanly
- **Save Session** in the menu, or running `omarchy-reanimate-save` yourself

Only the menu saves on the way out. A bare `systemctl reboot`, the power
button, or a crash falls back to the last timer snapshot.

The timer's `OnBootSec` also means nothing rewrites `session.json` for the
first few minutes after a boot, because the restore consumes it. Lower
`OnBootSec` in the timer unit if that window bothers you.
