"""Shared helpers for the Omarchy session save/restore plugin.

Everything that both the save and the restore side need lives here: talking to
Hyprland, reading config, classifying windows, resolving web apps, and the
dwindle layout maths.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

SCHEMA_VERSION = 2

STATE_DIR = Path.home() / ".local/state/omarchy/reanimate"
MANIFEST = STATE_DIR / "session.json"
LOG_FILE = STATE_DIR / "restore.log"
LOCK_FILE = STATE_DIR / "session.lock"
AFTER_FILE = STATE_DIR / "session-after.json"
LEARNED_FILE = STATE_DIR / "webapp-classes.json"
CONFIG_FILE = Path.home() / ".config/omarchy/reanimate.json"

DEFAULT_CONFIG = {
    # Windows whose class matches any of these are never saved. Games and
    # fullscreen video belong here: relaunching them is rarely what you want.
    "exclude_classes": [
        r"^walker$",
        r"^gamescope$",
        r"^steam_app_\d+$",
        r"^xwaylandvideobridge$",
    ],
    "exclude_titles": [],
    "restore_layout": True,
    "restore_geometry": True,
    # How long to wait for a launched app to map its window.
    "map_timeout": 20.0,
    "poll_interval": 0.05,
    "settle_delay": 0.35,
    # Chromium on a cold boot can take a long time to show anything at all, and
    # then restores its windows in a burst. Wait this long for the first one...
    "browser_first_window_timeout": 30.0,
    # ...then consider it finished once no new window has appeared for this long.
    "chromium_settle": 3.0,
    # After the restore, keep an eye out for browser windows that arrive late.
    "late_browser_watch": 10.0,
    # The same browser reports different classes depending on how it was
    # launched (the /usr/bin/chromium wrapper vs. the raw binary), so all of
    # these are treated as one browser.
    "browser_classes": [
        "chromium", "chromium-browser", "Chromium",
        "google-chrome", "google-chrome-stable", "Google-chrome",
        "brave-browser", "Brave-browser", "microsoft-edge", "helium",
    ],
    # Manual class -> URL overrides for web apps nothing else can resolve.
    "webapp_urls": {},
    "park_workspace": "special:reanimate",
    # herdr rebuilds its own tabs and panes when its window comes back, but it
    # leaves every pane at a shell prompt -- restarting the agents is not its
    # job. How long to wait for those panes, and to let them reach a prompt.
    # A browser killed by the reboot loses its session. Give it this long to
    # shut down cleanly once the windows have been recorded.
    "browser_quit_timeout": 20.0,
    "herdr_timeout": 60.0,
    "herdr_settle": 3.0,
    "herdr_agent_timeout": 60000,
}

# Terminals that take their start directory as an argument. The saved argv
# already carries one of these, so it has to be stripped before the recorded
# cwd can win -- otherwise the terminal reopens wherever it was first opened.
TERMINAL_CWD_FLAGS = {
    "foot": ["--working-directory", "-D"],
    "Alacritty": ["--working-directory"],
    "kitty": ["--directory", "--working-directory", "-d"],
    "com.mitchellh.ghostty": ["--working-directory"],
    "org.wezfurlong.wezterm": ["--cwd"],
}
TERMINAL_CLASSES = set(TERMINAL_CWD_FLAGS)

# What to put before a command so a terminal runs it instead of a login shell.
# foot and kitty take the command as plain trailing arguments; foot's -e is
# documented as "ignored (for compatibility with xterm -e)".
TERMINAL_EXEC_PREFIX = {
    "foot": [],
    "kitty": [],
    "Alacritty": ["-e"],
    "com.mitchellh.ghostty": ["-e"],
    "org.wezfurlong.wezterm": ["start", "--"],
}

DESKTOP_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/share/applications"),
]

# Web apps launched straight from a keybinding or menu entry have no desktop
# file, so their URLs are harvested from the configs that launch them.
WEBAPP_CONFIG_DIRS = [
    Path.home() / ".config/hypr",
    Path.home() / ".config/omarchy/extensions",
    Path("/usr/share/omarchy/default/hypr"),
]

WEBAPP_PREFIXES = ("chrome-", "brave-")


# --------------------------------------------------------------------------
# logging + config


def log(channel: str, message: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with LOG_FILE.open("a") as fh:
        fh.write(f"{stamp} {channel}: {message}\n")


def load_config() -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log("config", f"ignoring unreadable {CONFIG_FILE}: {exc}")
            return config
        # Lists replace wholesale so a user can drop a default exclusion.
        config.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})
    return config


def boot_time() -> float:
    """Epoch seconds when this machine booted."""
    import time as _time
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return 0.0
    return _time.time() - uptime


def saved_before_boot(saved_at: str) -> bool:
    """True when a manifest predates the current boot and so deserves a restore.

    A manifest written during this boot describes windows that are already
    open. Restoring it would duplicate every one of them -- which is exactly
    what happens if the shell service re-runs on a plugin reload.
    """
    try:
        saved = datetime.fromisoformat(saved_at).timestamp()
    except ValueError:
        return False
    return saved < boot_time()


# --------------------------------------------------------------------------
# hyprctl


def hyprctl(*args: str) -> str:
    result = subprocess.run(
        ["hyprctl", *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def hyprctl_json(*args: str):
    raw = subprocess.run(
        ["hyprctl", "-j", *args], capture_output=True, text=True, check=False
    ).stdout
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def lua(code: str) -> bool:
    """Run a snippet against Hyprland's Lua API.

    Hyprland 0.56 replaced the old flat dispatchers with a namespaced Lua API,
    so `hyprctl dispatch closewindow address:0x...` is a syntax error now.
    Everything here goes through `hyprctl eval` instead.
    """
    result = subprocess.run(
        ["hyprctl", "eval", code], capture_output=True, text=True, check=False
    )
    if result.returncode != 0 or result.stdout.startswith("error:"):
        log("lua", f"{code} -> {result.stdout.strip() or result.returncode}")
        return False
    return True


def _addr(address: str) -> str:
    return f"'address:{address}'"


def focus_window(address: str) -> bool:
    return lua(f"hl.dispatch(hl.dsp.focus({{window={_addr(address)}}}))")


def focus_workspace(workspace) -> bool:
    return lua(f"hl.dispatch(hl.dsp.focus({{workspace='{workspace}'}}))")


def focus_monitor(name: str) -> bool:
    return lua(f"hl.dispatch(hl.dsp.focus({{monitor='{name}'}}))")


def move_to_workspace(address: str, workspace, follow: bool = True) -> bool:
    return lua(
        f"hl.dispatch(hl.dsp.window.move({{window={_addr(address)},"
        f"workspace='{workspace}',follow={str(follow).lower()}}}))"
    )


def move_exact(address: str, x: int, y: int) -> bool:
    return lua(
        f"hl.dispatch(hl.dsp.window.move({{window={_addr(address)},x={x},y={y}}}))"
    )


def resize_exact(address: str, width: int, height: int) -> bool:
    # Resizing a tiled window adjusts the split ratio it sits under, which is
    # how saved proportions are recovered after the tree is rebuilt.
    return lua(
        f"hl.dispatch(hl.dsp.focus({{window={_addr(address)}}}))"
        f"hl.dispatch(hl.dsp.window.resize({{x={width},y={height}}}))"
    )


def window_at(address: str):
    for client in hyprctl_json("clients"):
        if client["address"] == address:
            return client.get("at", [0, 0])
    return None


def place_floating(address: str, x: int, y: int, width: int, height: int) -> None:
    """Move a floating window so its reported position matches the saved one.

    The move dispatcher and the `at` field Hyprland reports do not share a
    reference point (borders and the reserved area shift it), so the offset is
    measured and corrected rather than assumed.
    """
    resize_exact(address, width, height)
    for _ in range(3):
        move_exact(address, x, y)
        actual = window_at(address)
        if actual is None:
            return
        dx, dy = x - actual[0], y - actual[1]
        if abs(dx) <= 1 and abs(dy) <= 1:
            return
        x, y = x + dx, y + dy


def preselect(direction: str) -> bool:
    if direction not in ("l", "r", "u", "d"):
        return False
    return lua(f"hl.dispatch(hl.dsp.layout('preselect {direction}'))")


def set_fullscreen(address: str, mode: str) -> bool:
    return lua(
        f"hl.dispatch(hl.dsp.focus({{window={_addr(address)}}}))"
        f"hl.dispatch(hl.dsp.window.fullscreen({{mode='{mode}'}}))"
    )


def pin_window(address: str) -> bool:
    return lua(
        f"hl.dispatch(hl.dsp.focus({{window={_addr(address)}}}))"
        f"hl.dispatch(hl.dsp.window.pin())"
    )


def close_window(address: str) -> bool:
    return lua(
        f"hl.dispatch(hl.dsp.focus({{window={_addr(address)}}}))"
        f"hl.dispatch(hl.dsp.window.close())"
    )


def exec_cmd(rules: str, command: str) -> bool:
    """Spawn a command through Hyprland, optionally with window rules.

    The command travels inside a Lua long-bracket string, which needs no
    escaping at all -- and saved command lines are full of quotes and slashes.
    """
    argument = f"[{rules}] {command}" if rules else command
    level = "="
    while f"]{level}]" in argument:
        level += "="
    return lua(f"hl.dispatch(hl.dsp.exec_cmd([{level}[{argument}]{level}]))")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def quit_browsers(clients: list[dict], browser_classes, timeout: float) -> int:
    """Ask every running browser to exit cleanly, and wait until it has.

    Chromium writes a complete session only on a graceful exit. Killed as the
    machine goes down it is marked as having crashed and comes back with a
    single window and stale tabs, whatever "continue where you left off" is
    set to. So the layout is recorded first, then the browser is asked to quit
    while there is still time for it to finish -- and its own session restore
    brings the windows back with the pages that were in them.

    Web app windows share the browser process, so they go too; the restore
    relaunches those by URL, which it does anyway.
    """
    pids = {
        client["pid"]
        for client in clients
        if client.get("pid", 0) > 0
        and (client.get("class") in browser_classes
             or is_webapp(client.get("class", "")))
    }
    if not pids:
        return 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(pid_alive(pid) for pid in pids):
            break
        time.sleep(0.25)
    return len(pids)


def gaps() -> int:
    """Largest configured gap, used as the tolerance for layout inference."""
    widest = 0
    for option in ("general:gaps_in", "general:gaps_out"):
        numbers = re.findall(r"\d+", hyprctl("getoption", option))
        if numbers:
            widest = max(widest, max(int(n) for n in numbers))
    return widest


def current_layout() -> str:
    text = hyprctl("getoption", "general:layout")
    match = re.search(r"str:\s*(\S+)", text)
    return match.group(1) if match else "dwindle"


def addresses() -> set[str]:
    return {c["address"] for c in hyprctl_json("clients")}


# --------------------------------------------------------------------------
# window classification


def compiled(patterns) -> list:
    out = []
    for pattern in patterns:
        try:
            out.append(re.compile(pattern))
        except re.error as exc:
            log("config", f"bad regex {pattern!r}: {exc}")
    return out


def excluded(client: dict, class_res: list, title_res: list) -> bool:
    klass = client.get("class", "")
    title = client.get("title", "")
    return any(r.search(klass) for r in class_res) or any(
        r.search(title) for r in title_res
    )


def stable_id(client: dict) -> int:
    """Window creation counter. Hyprland reports it as a hex string."""
    raw = client.get("stableId", 0)
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw), 16)
    except ValueError:
        return 0


def proc_argv(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    argv = [part for part in raw.decode("utf-8", "replace").split("\0") if part]
    # Chromium rewrites its own argv into a single space-joined blob, so the
    # usual NUL separators are gone and the arguments have to be re-split.
    if len(argv) == 1 and " " in argv[0]:
        try:
            argv = shlex.split(argv[0])
        except ValueError:
            pass
    return argv or None


def proc_cwd(pid: int) -> str:
    try:
        return os.path.realpath(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def shell_cwd(pid: int) -> str:
    """The working directory of a terminal's child shell, not the terminal."""
    try:
        children = subprocess.run(
            ["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False
        ).stdout.split()
    except OSError:
        return ""
    for child in reversed(children):
        directory = proc_cwd(int(child))
        if directory and Path(directory).is_dir():
            return directory
    return ""


def strip_cwd_flags(argv: list[str], klass: str) -> list[str]:
    flags = TERMINAL_CWD_FLAGS.get(klass)
    if not flags:
        return argv
    out: list[str] = []
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if any(arg == f for f in flags):
            skip_next = True
            continue
        if any(arg.startswith(f + "=") for f in flags):
            continue
        out.append(arg)
    return out


def build_command(argv: list[str], klass: str, cwd: str,
                  run: list[str] | None = None) -> str:
    argv = strip_cwd_flags(argv, klass)
    if run:
        prefix = TERMINAL_EXEC_PREFIX.get(klass, ["-e"])
        # A restored terminal is already running its payload, so its argv
        # carries a copy. Drop that before adding the current one, or every
        # save round-trip appends another (`foot herdr herdr`). Compared by
        # basename so an upgraded interpreter path still matches.
        target = Path(run[0]).name
        for index, token in enumerate(argv[1:], start=1):
            if Path(token).name == target:
                argv = argv[:index]
                break
        while argv and prefix and argv[-1] in prefix:
            argv = argv[:-1]
        argv = argv + prefix + run
    command = " ".join(shlex.quote(a) for a in argv)
    if cwd:
        inner = f"cd {shlex.quote(cwd)} && exec {command}"
        command = f"sh -c {shlex.quote(inner)}"
    return command


# --------------------------------------------------------------------------
# herdr
#
# herdr is a terminal workspace manager: the user starts it by typing `herdr`
# in a terminal, so it is a child process of a window rather than a window in
# its own right. Two things follow. The window has to be relaunched running it,
# and the agents that were living in its panes have to be started again.


HERDR_BIN = "herdr"


def proc_children(pid: int) -> list[int]:
    try:
        raw = subprocess.run(
            ["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return []
    return [int(c) for c in raw.split() if c.isdigit()]


def proc_descendants(pid: int, depth: int = 6) -> list[int]:
    """Every process below `pid`. Depth-bounded so nothing can spin forever."""
    found: list[int] = []
    frontier = [pid]
    for _ in range(depth):
        children: list[int] = []
        for parent in frontier:
            children.extend(proc_children(parent))
        if not children:
            break
        found.extend(children)
        frontier = children
    return found


def terminal_payload(pid: int) -> list[str]:
    """What a terminal is hosting that has to come back with it, as an argv.

    A window's own argv only ever names the terminal; whatever the user started
    by typing at the shell sits a level or two below it. Without this the
    terminal is restored as a bare shell and its contents are lost.

    herdr wins over anything else found: the agents inside its panes are its
    descendants too, and it is herdr that has to be relaunched, not them.
    """
    claude: list[str] = []
    for child in proc_descendants(pid):
        argv = proc_argv(child)
        if not argv:
            continue
        name = Path(argv[0]).name
        if name == HERDR_BIN and "server" not in argv[1:]:
            return [HERDR_BIN]
        if name == "claude" and not claude:
            # Keep the absolute path. claude is installed through mise, whose
            # shims reach PATH from an interactive shell profile -- and the
            # restored terminal does not get one.
            claude = [argv[0], "--continue"]
    return claude


def herdr_cli(*args: str, timeout: float = 15.0):
    """Run a herdr subcommand and hand back its `result` object, or None."""
    if not shutil.which(HERDR_BIN):
        return None
    try:
        raw = subprocess.run(
            [HERDR_BIN, *args], capture_output=True, text=True,
            check=False, timeout=timeout,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload.get("result") if isinstance(payload, dict) else None


def herdr_agents() -> list[dict]:
    return (herdr_cli("agent", "list") or {}).get("agents") or []


def herdr_panes() -> list[dict]:
    return (herdr_cli("pane", "list") or {}).get("panes") or []


def herdr_start_agent(name: str, kind: str, pane: str, args: list[str],
                      timeout_ms: int) -> None:
    """Ask herdr to start an agent in a pane.

    `name` is the agent's own label and herdr requires it to be unique across
    the session -- passing the kind for all of them means only the first ever
    starts, and the rest are rejected with "agent name <kind> is already used".

    The result is deliberately ignored: `agent start` reports a failure for an
    agent that launched fine and then stopped at a prompt, which is exactly
    what `claude --continue` does on a large session. The caller checks what
    herdr can actually see in the pane instead.
    """
    command = [HERDR_BIN, "agent", "start", name, "--kind", kind,
               "--pane", pane, "--timeout", str(timeout_ms)]
    if args:
        command += ["--", *args]
    try:
        subprocess.run(command, capture_output=True, text=True,
                       check=False, timeout=timeout_ms / 1000 + 15)
    except (OSError, subprocess.TimeoutExpired):
        pass


# --------------------------------------------------------------------------
# browsers and web apps


def is_browser(klass: str, browser_classes) -> bool:
    return klass in browser_classes


def same_class(want: str, got: str, browser_classes) -> bool:
    """Equal classes, or two aliases of the same browser."""
    if want == got:
        return True
    return want in browser_classes and got in browser_classes


def is_webapp(klass: str) -> bool:
    return klass.startswith(WEBAPP_PREFIXES)


def app_url(argv: list[str]) -> str:
    for arg in argv:
        if arg.startswith("--app="):
            return arg[len("--app="):]
    return ""


def desktop_entries() -> dict[str, dict]:
    """Every desktop entry by id; a user entry shadows a system one."""
    entries: dict[str, dict] = {}
    for directory in reversed(DESKTOP_DIRS):
        if not directory.is_dir():
            continue
        for path in directory.glob("*.desktop"):
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            entry = {"id": path.name, "exec": "", "wmclass": "", "name": ""}
            in_main = False
            for line in text.splitlines():
                if line.startswith("["):
                    in_main = line.strip() == "[Desktop Entry]"
                    continue
                if not in_main:
                    continue
                if line.startswith("Exec=") and not entry["exec"]:
                    entry["exec"] = line[5:].strip()
                elif line.startswith("StartupWMClass="):
                    entry["wmclass"] = line[15:].strip()
                elif line.startswith("Name=") and not entry["name"]:
                    entry["name"] = line[5:].strip()
            if entry["exec"]:
                entries[path.name] = entry
    return entries


def exec_to_command(exec_field: str) -> str:
    """Turn a desktop Exec line into something that can be run as-is."""
    try:
        parts = shlex.split(exec_field)
    except ValueError:
        return ""
    parts = [p for p in parts if not re.fullmatch(r"%[a-zA-Z]", p)]
    return " ".join(shlex.quote(p) for p in parts)


def default_browser_command() -> str:
    """How Omarchy itself launches the browser.

    /usr/bin/chromium is a wrapper script; running the raw binary it points at
    yields a different window class. Relaunch the way the desktop entry does.
    """
    browser = subprocess.run(
        ["xdg-settings", "get", "default-web-browser"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    entry = desktop_entries().get(browser)
    if entry:
        command = exec_to_command(entry["exec"])
        if command:
            return command.split()[0]
    return shutil.which("chromium") or ""


def webapp_slug(url: str) -> str:
    """The slug Chromium builds a --app window's class from: host_path."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.hostname or ""
    path = parsed.path or "/"
    return host + "_" + path.replace("/", "_")


def class_slug(klass: str) -> str:
    """chrome-<slug>-<profile> -> <slug>, or '' for a non web-app class."""
    for prefix in WEBAPP_PREFIXES:
        if klass.startswith(prefix):
            rest = klass[len(prefix):]
            slug, dash, _profile = rest.rpartition("-")
            return slug if dash else rest
    return ""


def url_from_slug(slug: str) -> str:
    """Invert the slug when nothing else knows the URL.

    Lossy only for paths that contain underscores, and ports are not encoded
    in the class at all, so this is the last resort rather than the first.
    """
    host, sep, path = slug.partition("_")
    if not sep or "." not in host:
        return ""
    path = path.replace("_", "/")
    if not path.startswith("/"):
        path = "/" + path
    scheme = "http" if host.endswith(".localhost") or host == "localhost" else "https"
    return f"{scheme}://{host}{path}"


def learned_load() -> dict[str, str]:
    try:
        return json.loads(LEARNED_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def learned_save(table: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LEARNED_FILE.write_text(json.dumps(table, indent=2, sort_keys=True))


_URL_RE = re.compile(r"https?://[^\s\"'`)>,;]+")


def webapp_table(config: dict) -> dict[str, str]:
    """slug -> launch command, from every source that knows about web apps.

    Later sources override earlier ones, so the list runs from least to most
    authoritative: URLs scraped from configs, then classes seen running before,
    then desktop entries, then the user's explicit overrides.
    """
    table: dict[str, str] = {}

    for directory in WEBAPP_CONFIG_DIRS:
        if not directory.is_dir():
            continue
        for path in list(directory.glob("*.conf")) + list(directory.glob("*.lua")) \
                + list(directory.glob("*.jsonc")):
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if "webapp" not in line:
                    continue
                for url in _URL_RE.findall(line):
                    table[webapp_slug(url)] = f"omarchy-launch-webapp {shlex.quote(url)}"

    for slug, url in learned_load().items():
        table[slug] = f"omarchy-launch-webapp {shlex.quote(url)}"

    for entry in desktop_entries().values():
        command = exec_to_command(entry["exec"])
        if not command:
            continue
        if entry["exec"].startswith("omarchy-launch-webapp "):
            url = entry["exec"].split()[1] if len(entry["exec"].split()) > 1 else ""
            if url:
                table[webapp_slug(url)] = command
        elif "--app=" in entry["exec"]:
            url = app_url(shlex.split(entry["exec"]))
            if url:
                table[webapp_slug(url)] = command
        if is_webapp(entry["wmclass"]):
            table[class_slug(entry["wmclass"])] = command

    for key, url in config.get("webapp_urls", {}).items():
        slug = class_slug(key) or key
        table[slug] = f"omarchy-launch-webapp {shlex.quote(url)}"

    return table


def resolve_webapp(klass: str, argv: list[str], ambiguous: bool,
                   table: dict[str, str], learned: dict[str, str]) -> tuple[str, str]:
    """Launch command for a web app window, and where it came from.

    The process's own --app= flag is exact when that process owns just one web
    app window. After a reboot every web app shares the bare browser process,
    which has no such flag, so the lookup table does most of the work then.
    """
    slug = class_slug(klass)
    url = app_url(argv)

    if url and not ambiguous:
        if slug and slug not in learned:
            learned[slug] = url
        if webapp_slug(url) in table:
            return table[webapp_slug(url)], "process"
        return f"omarchy-launch-webapp {shlex.quote(url)}", "process"

    if slug in table:
        return table[slug], "table"

    derived = url_from_slug(slug) if slug else ""
    if derived:
        return f"omarchy-launch-webapp {shlex.quote(derived)}", "derived"

    if url:
        return f"omarchy-launch-webapp {shlex.quote(url)}", "guess"

    return "", "unresolved"


# --------------------------------------------------------------------------
# dwindle layout inference
#
# Hyprland exposes no way to read or write its layout tree, so the tree is
# reconstructed from the saved window rectangles and replayed by inserting
# windows in their original order with an explicit preselect direction.


def infer_tree(nodes: list[dict], tol: int):
    """Rebuild a BSP tree from window rectangles.

    `nodes` are dicts with idx/x/y/w/h. Returns a nested dict of split and leaf
    nodes, or None when the rectangles do not form a clean binary partition.
    """
    if len(nodes) == 1:
        return {"type": "leaf", "idx": nodes[0]["idx"]}

    for axis, pos, size in (("v", "x", "w"), ("h", "y", "h")):
        for line in sorted({n[pos] for n in nodes})[1:]:
            near = [n for n in nodes if n[pos] + n[size] / 2 < line]
            far = [n for n in nodes if n[pos] + n[size] / 2 >= line]
            if not near or not far:
                continue
            if max(n[pos] + n[size] for n in near) > line + tol:
                continue
            if min(n[pos] for n in far) < line - tol:
                continue
            near_tree = infer_tree(near, tol)
            far_tree = infer_tree(far, tol)
            if near_tree is None or far_tree is None:
                continue
            near_extent = max(n[pos] + n[size] for n in near) - min(
                n[pos] for n in near
            )
            far_extent = max(n[pos] + n[size] for n in far) - min(n[pos] for n in far)
            total = near_extent + far_extent
            ratio = near_extent / total if total else 0.5
            return {
                "type": "split",
                "axis": axis,
                "ratio": round(ratio, 4),
                "near": near_tree,
                "far": far_tree,
            }
    return None


def _first_leaf(node) -> int:
    """Index of the earliest-created window in a subtree.

    Windows are stored in creation order, so the smallest index is the leaf
    that was originally split to create this subtree.
    """
    if node["type"] == "leaf":
        return node["idx"]
    return min(_first_leaf(node["near"]), _first_leaf(node["far"]))


def insertion_plan(tree) -> list[dict]:
    """Derive how to rebuild a tree: for each window, what to split and which way.

    When dwindle splits a leaf, the new internal node's two children are the
    original window and the new one. Every later window in either subtree is a
    descendant, so the earliest-created window in a subtree is exactly the leaf
    that was there when the split happened.
    """
    steps: list[dict] = []

    def walk(node) -> None:
        if node["type"] == "leaf":
            return
        near, far = node["near"], node["far"]
        if _first_leaf(near) < _first_leaf(far):
            existing, added = near, far
            direction = "r" if node["axis"] == "v" else "d"
        else:
            existing, added = far, near
            direction = "l" if node["axis"] == "v" else "u"
        steps.append(
            {
                "idx": _first_leaf(added),
                "parent": _first_leaf(existing),
                "direction": direction,
            }
        )
        walk(near)
        walk(far)

    walk(tree)
    return sorted(steps, key=lambda s: s["idx"])
