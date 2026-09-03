#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

omarchy-hook-install post-boot hooks/post-boot.d/11-reanimate 2>/dev/null || {
  mkdir -p ~/.config/omarchy/hooks/post-boot.d
  install -m 755 hooks/post-boot.d/11-reanimate ~/.config/omarchy/hooks/post-boot.d/
}

mkdir -p ~/.config/systemd/user
install -m 644 systemd/omarchy-reanimate-save.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now omarchy-reanimate-save.timer

mkdir -p ~/.local/bin
for cmd in save restore show diff; do
  ln -sf "$PWD/bin/omarchy-reanimate-$cmd" ~/.local/bin/"omarchy-reanimate-$cmd"
done

echo "Installed. Periodic saves every 2 minutes are now running."
echo
echo "Optional but recommended: merge extensions/omarchy-menu-snippet.jsonc into"
echo "~/.config/omarchy/extensions/omarchy-menu.jsonc, then run: omarchy menu refresh"
echo "That makes Reboot and Shutdown snapshot the session first."
