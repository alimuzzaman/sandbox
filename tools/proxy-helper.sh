#!/bin/sh
# Retired privileged compatibility entry point.
#
# Older Sandbox releases may have left an exact NOPASSWD sudoers rule for
# this repository path. Keeping this executable as an unconditional refusal
# makes that stale rule non-exploitable while `sb domains setup` removes it.
echo "proxy-helper: retired; use proof-scoped resolver and ingress helpers" >&2
exit 65
