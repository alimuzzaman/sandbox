#!/bin/sh
set -eu
for target in /etc/shadow /proc/1/root/etc/shadow /run/docker.sock; do
    [ ! -r "$target" ] || { echo "ESCAPE:$target" >&2; exit 70; }
done
exit 0
