#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sudo install -m 0644 "$project_root/udev/70-lighttime-zhiyun.rules" /etc/udev/rules.d/70-lighttime-zhiyun.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --action=change
printf '%s\n' 'USB rule installed. Reconnect the G60 if the active-user ACL has not appeared.'
