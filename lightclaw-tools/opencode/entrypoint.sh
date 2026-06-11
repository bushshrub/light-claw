#!/bin/sh
set -e
# Copy host opencode config to writable location on every container start.
# This ensures API key changes are picked up without rebuilding the image,
# while preventing the container from modifying the host config directory.
if [ -d /run/opencode-config-host ]; then
    cp -r /run/opencode-config-host/. "$HOME/.config/opencode/"
fi
exec opencode "$@"
