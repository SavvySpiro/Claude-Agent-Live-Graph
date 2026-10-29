"""Open `claude attach <id>` for an agent in a terminal window."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

# {cmd} is replaced by the shell-quoted command; {title} by a window title.
TERMINALS = [
    ("gnome-terminal", "gnome-terminal --title {title} -- bash -lc {cmd}"),
    ("kitty", "kitty --title {title} bash -lc {cmd}"),
    ("wezterm", "wezterm start -- bash -lc {cmd}"),
    ("alacritty", "alacritty --title {title} -e bash -lc {cmd}"),
    ("konsole", "konsole -p tabtitle={title} -e bash -lc {cmd}"),
    ("xfce4-terminal", "xfce4-terminal --title {title} -x bash -lc {cmd}"),
    ("x-terminal-emulator", "x-terminal-emulator -e bash -lc {cmd}"),
    ("xterm", "xterm -T {title} -e bash -lc {cmd}"),
]


def attach_command(short_id: str) -> str:
    return f"claude attach {shlex.quote(short_id)}"


def open_in_terminal(short_id: str, title: str, template: str | None = None) -> str:
    """Launch a terminal attached to the agent. Returns a human-readable result."""
    inner = attach_command(short_id)
    # Keep the window open if attach exits with an error, so the message is readable.
    inner_keep = f"{inner} || {{ echo; read -rp 'press enter to close'; }}"
    if sys.platform == "darwin" and not template:
        script = f'tell application "Terminal" to do script "{inner}"\nactivate application "Terminal"'
        subprocess.Popen(["osascript", "-e", script])
        return "opened Terminal.app"
    if not template:
        for exe, tpl in TERMINALS:
            if shutil.which(exe):
                template = tpl
                break
    if not template:
        raise RuntimeError("no terminal emulator found; pass --terminal 'yourterm -e {cmd}'")
    argv = shlex.split(template.format(cmd=shlex.quote(inner_keep), title=shlex.quote(title)))
    # Detach from the server so closing the terminal never touches it.
    subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, env={**os.environ})
    return f"opened {argv[0]}"
