#!/bin/sh
# Fixed privileged helper for one live-proven, receipt-authorized Caddy surface.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

fail() { echo "ingress-helper: $1" >&2; exit 65; }
usage() {
    echo "usage: ingress-helper install ROOT USER" >&2
    echo "       ingress-helper authorize ROOT system-caddy ROUTE OWNER HOST BACKEND BACKEND_PORT LISTEN LISTEN_PORT SHA256 PID START EXE_SHA256 SOCKET_IDS OBSERVATION_SHA256" >&2
    echo "       ingress-helper authorization-status ROOT system-caddy ROUTE OWNER HOST BACKEND BACKEND_PORT LISTEN LISTEN_PORT SHA256 PID START EXE_SHA256 SOCKET_IDS OBSERVATION_SHA256" >&2
    echo "       ingress-helper preflight ROOT system-caddy PID START EXE_SHA256 SOCKET_IDS LISTEN LISTEN_PORT" >&2
    echo "       ingress-helper validate-current ROOT system-caddy" >&2
    echo "       ingress-helper prepare ROOT system-caddy ROUTE OWNER HOST BACKEND BACKEND_PORT LISTEN LISTEN_PORT SHA256 PID START EXE_SHA256 SOCKET_IDS OBSERVATION_SHA256" >&2
    echo "       ingress-helper activate ROOT system-caddy ROUTE" >&2
    echo "       ingress-helper observe ROOT system-caddy ROUTE" >&2
    echo "       ingress-helper rollback ROOT system-caddy ROUTE" >&2
    echo "       ingress-helper cleanup ROOT system-caddy ROUTE SHA256" >&2
    exit 64
}

require_root() { [ "$(id -u)" -eq 0 ] || fail "this verb requires root"; }
valid_adapter() { [ "$1" = system-caddy ] || fail "adapter is not live-proven"; }
valid_route() { printf '%s\n' "$1" | grep -Eq '^[a-f0-9]{64}$' || fail "invalid route id"; }
valid_digest() { printf '%s\n' "$1" | grep -Eq '^[a-f0-9]{64}$' || fail "invalid digest"; }
valid_hostname() {
    printf '%s\n' "$1" | grep -Eq '^([a-z0-9-]+\.)+[a-z0-9-]+$' \
        || fail "invalid hostname binding"
}
valid_port() {
    printf '%s\n' "$1" | grep -Eq '^[0-9]{1,5}$' || fail "invalid port"
    [ "$1" -ge 1 ] && [ "$1" -le 65535 ] || fail "invalid port"
}
valid_listen_address() {
    # The listen endpoint is the incumbent's OWN socket, which may be a
    # wildcard; the rendered fragment restricts such a site to loopback
    # clients. Anything routable is still refused.
    python3 - "$1" <<'LISTEN_PY' || exit 65
import ipaddress
import sys
try:
    value = ipaddress.ip_address(sys.argv[1])
except ValueError:
    print("ingress-helper: invalid address", file=sys.stderr)
    raise SystemExit(1)
if not (value.is_loopback or value.is_unspecified):
    print("ingress-helper: listen address is neither loopback nor the incumbent wildcard",
          file=sys.stderr)
    raise SystemExit(1)
LISTEN_PY
}
valid_loopback() {
    python3 - "$1" <<'PY' || exit 65
import ipaddress
import sys
try:
    value = ipaddress.ip_address(sys.argv[1])
except ValueError:
    print("ingress-helper: invalid address", file=sys.stderr)
    raise SystemExit(1)
if not value.is_loopback:
    print("ingress-helper: address is not loopback", file=sys.stderr)
    raise SystemExit(1)
PY
}
file_uid() {
    if stat -c '%u' "$1" >/dev/null 2>&1; then stat -c '%u' "$1"; else stat -f '%u' "$1"; fi
}
file_mode() {
    if stat -c '%a' "$1" >/dev/null 2>&1; then stat -c '%a' "$1"; else stat -f '%Lp' "$1"; fi
}
trusted_directory() {
    path=$1
    [ -d "$path" ] && [ ! -L "$path" ] || fail "trusted directory is unavailable"
    [ "$(file_uid "$path")" -eq 0 ] || fail "trusted directory is not root-owned"
    mode=$(file_mode "$path"); [ $((0$mode & 022)) -eq 0 ] \
        || fail "trusted directory is group/world writable"
}
canonical_root() {
    [ -d "$1" ] || fail "network root does not exist"
    [ ! -L "$1" ] || fail "network root must not be a symlink"
    (cd "$1" && pwd -P) || fail "could not resolve network root"
}
caller_uid() {
    uid=${SUDO_UID:-}
    printf '%s\n' "$uid" | grep -Eq '^[1-9][0-9]*$' || fail "caller identity is unavailable"
    printf '%s\n' "$uid"
}
authorized_root() {
    requested=$(canonical_root "$1"); uid=$(caller_uid)
    trusted_directory /etc/sandbox-ingress
    trusted_directory /etc/sandbox-ingress/owners
    policy="/etc/sandbox-ingress/owners/$uid.root"
    [ -f "$policy" ] && [ ! -L "$policy" ] || fail "ingress owner policy is unavailable"
    [ "$(file_uid "$policy")" -eq 0 ] || fail "ingress owner policy is not root-owned"
    [ "$(sed -n '1p' "$policy")" = "$requested" ] || fail "network root is outside the installed scope"
    [ "$(file_uid "$requested")" = "$uid" ] || fail "network root owner changed"
    printf '%s\n' "$requested"
}
valid_owner() {
    owner=$1; uid=$(caller_uid); project=${owner%::*}; label=${owner##*::}
    [ "$project" != "$owner" ] || fail "invalid owner binding"
    printf '%s\n' "$label" | grep -Eq '^[A-Za-z0-9._-]+$' || fail "invalid owner label"
    [ -d "$project" ] && [ ! -L "$project" ] || fail "owner project is unavailable"
    canonical=$(cd "$project" && pwd -P) || fail "owner project is invalid"
    [ "$owner" = "$canonical::$label" ] || fail "owner binding is not canonical"
    [ "$(file_uid "$canonical")" = "$uid" ] || fail "owner project belongs to another user"
}
expected_route() { printf '%s\0%s\0%s' "$1" "$2" "$3" | sha256sum | cut -d' ' -f1; }
plan_digest() { printf '%s\0' "$@" | sha256sum | cut -d' ' -f1; }
render_candidate() {
    output=$1 route=$2 hostname=$3 backend=$4 backend_port=$5 listen=$6
    rendered_backend=$backend; case "$backend" in *:*) rendered_backend="[$backend]" ;; esac
    # Root renders the fragment itself and compares digests, so this must match
    # the unprivileged renderer byte for byte -- including the loopback-client
    # restriction used when the incumbent listens on a wildcard.
    loopback_only=no
    case "$listen" in 0.0.0.0|::) loopback_only=yes ;; esac
    {
        printf '# sandbox-ingress v1 route=%s\n' "$route"
        printf 'http://%s {\n' "$hostname"
        printf '    bind %s\n' "$listen"
        if [ "$loopback_only" = yes ]; then
            printf '    @loopback remote_ip 127.0.0.0/8 ::1\n'
            printf '    handle @loopback {\n'
            printf '        reverse_proxy %s:%s\n' "$rendered_backend" "$backend_port"
            printf '    }\n'
            printf '    handle {\n'
            printf '        respond 403\n'
            printf '    }\n'
        else
            printf '    reverse_proxy %s:%s\n' "$rendered_backend" "$backend_port"
        fi
        printf '}\n'
    } >"$output"
    chown root:root "$output"; chmod 0600 "$output"
}
verify_caddy_authority() {
    pid=$1 start=$2 executable_digest=$3 socket_ids=$4 listen=$5 listen_port=$6
    printf '%s\n' "$pid" "$start" | grep -Eq '^[0-9]+$' || fail "Caddy process identity is invalid"
    [ "$pid" -gt 1 ] || fail "Caddy process identity is invalid"
    valid_digest "$executable_digest"
    printf '%s\n' "$socket_ids" | grep -Eq '^[0-9]+(,[0-9]+)*$' || fail "Caddy socket identity is invalid"
    service_pid=$(systemctl show -p MainPID --value caddy.service 2>/dev/null)
    [ "$pid" = "$service_pid" ] || fail "selected listener is not owned by caddy.service"
    [ -r "/proc/$pid/stat" ] && [ "$(sed -n '1p' "/proc/$pid/comm")" = caddy ] \
        || fail "selected Caddy process disappeared"
    observed_start=$(sed -n '1p' "/proc/$pid/stat" | awk '{print $22}')
    [ "$start" = "$observed_start" ] || fail "selected Caddy process was replaced"
    executable=$(readlink -f "/proc/$pid/exe") || fail "Caddy executable identity is unavailable"
    case "$executable" in
        /usr/bin/caddy|/usr/sbin/caddy|/usr/local/bin/caddy|/usr/local/sbin/caddy) ;;
        *) fail "Caddy executable is outside system binary roots" ;;
    esac
    [ -f "$executable" ] && [ ! -L "$executable" ] || fail "Caddy executable is not a regular file"
    [ "$(file_uid "$executable")" -eq 0 ] || fail "Caddy executable is not root-owned"
    mode=$(file_mode "$executable"); [ $((0$mode & 022)) -eq 0 ] \
        || fail "Caddy executable is group/world writable"
    [ "$(sha256sum "$executable" | cut -d' ' -f1)" = "$executable_digest" ] \
        || fail "Caddy executable digest changed"
    python3 - "$pid" "$socket_ids" "$listen" "$listen_port" <<'PY' || exit 65
import ipaddress
import os
import socket
import sys

pid, raw_ids, requested_address, requested_port = sys.argv[1:]
wanted = set(raw_ids.split(","))
owned = set()
for name in os.listdir(f"/proc/{pid}/fd"):
    try:
        target = os.readlink(f"/proc/{pid}/fd/{name}")
    except OSError:
        continue
    if target.startswith("socket:[") and target.endswith("]"):
        owned.add(target[8:-1])
if not wanted or not wanted.issubset(owned):
    print("ingress-helper: selected socket is not owned by caddy.service", file=sys.stderr)
    raise SystemExit(1)

def decode(raw, family):
    data = bytes.fromhex(raw)
    if family == socket.AF_INET:
        return socket.inet_ntop(family, data[::-1])
    normalized = b"".join(data[i:i + 4][::-1] for i in range(0, 16, 4))
    return socket.inet_ntop(family, normalized)

found = {}
for path, family in (("/proc/net/tcp", socket.AF_INET), ("/proc/net/tcp6", socket.AF_INET6)):
    with open(path, encoding="ascii") as stream:
        next(stream, None)
        for line in stream:
            columns = line.split()
            if len(columns) < 10 or columns[3] != "0A" or columns[9] not in wanted:
                continue
            raw_address, raw_port = columns[1].rsplit(":", 1)
            found[columns[9]] = (decode(raw_address, family), int(raw_port, 16))
if set(found) != wanted:
    print("ingress-helper: selected Caddy listener set changed", file=sys.stderr)
    raise SystemExit(1)
requested = (str(ipaddress.ip_address(requested_address)), int(requested_port))
if requested not in set(found.values()):
    print("ingress-helper: authorized listen endpoint is not the selected Caddy socket", file=sys.stderr)
    raise SystemExit(1)
# The incumbent may listen on a wildcard; the rendered fragment restricts
# such a site to loopback clients. A routable-only socket is still refused.
if any(not (ipaddress.ip_address(address).is_loopback
            or ipaddress.ip_address(address).is_unspecified) or port != 80
       for address, port in found.values()):
    print("ingress-helper: selected Caddy socket is not loopback or wildcard HTTP", file=sys.stderr)
    raise SystemExit(1)
PY
}
validate_plan() {
    [ "$#" -eq 15 ] || usage
    root=$(authorized_root "$1"); adapter=$2; route=$3; owner=$4; hostname=$5
    backend=$6; backend_port=$7; listen=$8; listen_port=$9; expected=${10}
    pid=${11}; start=${12}; executable_digest=${13}; socket_ids=${14}; observation=${15}
    valid_adapter "$adapter"; valid_route "$route"; valid_owner "$owner"; valid_hostname "$hostname"
    valid_loopback "$backend"; valid_port "$backend_port"
    valid_listen_address "$listen"; [ "$listen_port" -eq 80 ] || fail "only exact loopback HTTP is proven"
    valid_digest "$expected"
    valid_digest "$observation"
    verify_caddy_authority "$pid" "$start" "$executable_digest" "$socket_ids" "$listen" "$listen_port"
    [ "$(expected_route "$adapter" "$owner" "$hostname")" = "$route" ] \
        || fail "route id does not match owner and hostname"
    temporary=$(mktemp /run/sandbox-ingress-plan.XXXXXX)
    trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
    render_candidate "$temporary" "$route" "$hostname" "$backend" "$backend_port" "$listen"
    [ "$(sha256sum "$temporary" | cut -d' ' -f1)" = "$expected" ] \
        || fail "content digest does not match root rendering"
    rm -f -- "$temporary"; trap - EXIT HUP INT TERM
    [ "$root" = "$1" ] || fail "network root must be canonical"
}
authorization_path() {
    uid=$(caller_uid); route=$1; plan=$2; valid_route "$route"; valid_digest "$plan"
    printf '/etc/sandbox-ingress/authorizations/%s/%s/%s.receipt\n' "$uid" "$route" "$plan"
}
applied_path() {
    uid=$(caller_uid); route=$1; valid_route "$route"
    printf '/etc/sandbox-ingress/applied/%s/%s.receipt\n' "$uid" "$route"
}
require_receipt_file() {
    receipt=$1
    trusted_directory /etc/sandbox-ingress
    trusted_directory "$(dirname "$receipt")"
    [ -f "$receipt" ] && [ ! -L "$receipt" ] || fail "root authorization receipt is unavailable"
    [ "$(file_uid "$receipt")" -eq 0 ] || fail "authorization receipt is not root-owned"
    mode=$(file_mode "$receipt"); [ "$mode" = 400 ] || [ "$mode" = 444 ] \
        || fail "authorization receipt permissions are invalid"
}
require_plan_receipt() {
    validate_plan "$@"
    expected_plan=$(plan_digest "$@")
    receipt=$(authorization_path "$3" "$expected_plan"); require_receipt_file "$receipt"
    [ "$(sed -n '1p' "$receipt")" = "$expected_plan" ] || fail "authorization plan mismatch"
    [ "$(sed -n '2p' "$receipt")" = "${10}" ] || fail "authorization content mismatch"
    [ "$(wc -l < "$receipt" | tr -d ' ')" -eq 2 ] || fail "authorization receipt schema is invalid"
}
require_applied_receipt() {
    root=$(authorized_root "$1"); valid_adapter "$2"; valid_route "$3"
    [ "$root" = "$1" ] || fail "network root must be canonical"
    receipt=$(applied_path "$3"); require_receipt_file "$receipt"
    valid_digest "$(sed -n '1p' "$receipt")"; valid_digest "$(sed -n '2p' "$receipt")"
    [ "$(wc -l < "$receipt" | tr -d ' ')" -eq 2 ] || fail "authorization receipt schema is invalid"
}
hostname_unclaimed() {
    hostname=$1 route=$2; valid_hostname "$hostname"; valid_route "$route"
    expected_exact=0; applied=$(applied_path "$route"); destination=$(destination "$route")
    if [ -e "$applied" ]; then
        require_receipt_file "$applied"
        [ -f "$destination" ] && [ ! -L "$destination" ] || fail "applied route file is unavailable"
        [ "$(sed -n '1p' "$destination")" = "# sandbox-ingress v1 route=$route" ] \
            || fail "applied route marker mismatch"
        [ "$(sha256sum "$destination" | cut -d' ' -f1)" = "$(sed -n '2p' "$applied")" ] \
            || fail "applied route drifted"
        expected_exact=1
    fi
    adapted=$(mktemp /run/sandbox-ingress-adapt.XXXXXX)
    trap 'rm -f -- "$adapted"' EXIT HUP INT TERM
    caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile >"$adapted"
    [ "$(wc -c < "$adapted")" -le 1048576 ] || fail "adapted Caddy config is too large"
    python3 - "$adapted" "$hostname" "$expected_exact" <<'PY' || exit 65
import json
import sys

path, requested, raw_expected = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    value = json.load(stream)
hosts = []
stack = [value]
while stack:
    item = stack.pop()
    if isinstance(item, dict):
        for key, child in item.items():
            if key == "host" and isinstance(child, list):
                hosts.extend(host for host in child if isinstance(host, str))
            stack.append(child)
    elif isinstance(item, list):
        stack.extend(item)
exact = sum(host == requested for host in hosts)
wildcard = any(host.startswith("*.") and requested.endswith(host[1:]) for host in hosts)
if exact != int(raw_expected) or wildcard:
    print("ingress-helper: hostname is already claimed by incumbent Caddy policy", file=sys.stderr)
    raise SystemExit(1)
PY
    rm -f -- "$adapted"; trap - EXIT HUP INT TERM
}
surface_ready() {
    [ "$(uname -s)" = Linux ] || fail "system Caddy helper is not proven on this platform"
    trusted_directory /etc/caddy
    trusted_directory /etc/caddy/conf.d
    [ -f /etc/caddy/Caddyfile ] && [ ! -L /etc/caddy/Caddyfile ] \
        || fail "Caddyfile is not a regular root policy file"
    [ "$(file_uid /etc/caddy/Caddyfile)" -eq 0 ] || fail "Caddyfile is not root-owned"
    mode=$(file_mode /etc/caddy/Caddyfile); [ $((0$mode & 022)) -eq 0 ] \
        || fail "Caddyfile is group/world writable"
    # Accept the fragment-directory import with or without a file-glob suffix:
    # Caddy's own packaging ships `import /etc/caddy/conf.d/*.caddy`, which the
    # bare-`*` pattern rejected as a foreign policy surface -- so the documented
    # conformance host failed its own preflight.
    grep -Eq '^[[:space:]]*import[[:space:]]+(/etc/caddy/)?conf\.d/\*(\.[A-Za-z0-9]+)?[[:space:]]*$' /etc/caddy/Caddyfile \
        || fail "Caddyfile does not import the owned fragment directory"
    grep -E '^[[:space:]]*import[[:space:]]+' /etc/caddy/Caddyfile \
        | grep -Ev '^[[:space:]]*import[[:space:]]+(/etc/caddy/)?conf\.d/\*(\.[A-Za-z0-9]+)?[[:space:]]*$' \
        | grep -q . && fail "Caddyfile imports an unproven policy surface" || true
    python3 - /etc/caddy <<'PY' || exit 65
import os
import stat
import sys

root = sys.argv[1]
count = 0
for directory, names, files in os.walk(root, followlinks=False):
    for name in (*names, *files):
        path = os.path.join(directory, name)
        details = os.lstat(path)
        if stat.S_ISLNK(details.st_mode):
            print("ingress-helper: Caddy policy contains a symlink", file=sys.stderr)
            raise SystemExit(1)
    for name in files:
        path = os.path.join(directory, name)
        details = os.stat(path, follow_symlinks=False)
        count += 1
        if count > 1024 or not stat.S_ISREG(details.st_mode) or details.st_uid != 0 \
                or details.st_mode & 0o022:
            print("ingress-helper: Caddy policy file integrity is invalid", file=sys.stderr)
            raise SystemExit(1)
PY
    [ "$(systemctl show -p LoadState --value caddy.service 2>/dev/null)" = loaded ] \
        || fail "Caddy system service is unavailable"
    [ "$(systemctl show -p ActiveState --value caddy.service 2>/dev/null)" = active ] \
        || fail "Caddy system service is not active"
    pid=$(systemctl show -p MainPID --value caddy.service 2>/dev/null)
    printf '%s\n' "$pid" | grep -Eq '^[1-9][0-9]*$' || fail "Caddy MainPID is unavailable"
    [ "$pid" -gt 1 ] && [ -r "/proc/$pid/comm" ] || fail "Caddy process identity is unavailable"
    [ "$(sed -n '1p' "/proc/$pid/comm")" = caddy ] || fail "Caddy service process identity mismatch"
    executable=$(readlink -f "/proc/$pid/exe") || fail "Caddy executable identity is unavailable"
    trusted_directory "$(dirname "$executable")"
    [ "$(basename "$executable")" = caddy ] || fail "Caddy executable identity mismatch"
    [ -f "$executable" ] && [ ! -L "$executable" ] || fail "Caddy executable is not regular"
    [ "$(file_uid "$executable")" -eq 0 ] || fail "Caddy executable is not root-owned"
    mode=$(file_mode "$executable"); [ $((0$mode & 022)) -eq 0 ] \
        || fail "Caddy executable is group/world writable"
}
validate_current() { surface_ready; caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; }
destination() { printf '/etc/caddy/conf.d/90-sandbox-%s.caddy\n' "$1"; }
transaction_dir() {
    uid=$(caller_uid); route=$1; valid_route "$route"; base="/run/sandbox-ingress/$uid"
    install -d -o root -g root -m 0700 /run/sandbox-ingress "$base"
    trusted_directory /run/sandbox-ingress; trusted_directory "$base"
    printf '%s/%s\n' "$base" "$route"
}
atomic_install() {
    source=$1 destination=$2; temporary="$destination.new.$$"
    trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
    install -o root -g root -m 0644 -- "$source" "$temporary"
    mv -f -- "$temporary" "$destination"; trap - EXIT HUP INT TERM
}
restore_prior() {
    transaction=$1 destination=$2
    if [ -f "$transaction/prior" ]; then atomic_install "$transaction/prior" "$destination"
    else rm -f -- "$destination"; fi
}
reload_service() { systemctl reload caddy.service; }

verb=${1:-}; [ "$#" -gt 0 ] || usage; shift
case "$verb" in
    install)
        [ "$#" -eq 2 ] || usage; require_root
        root=$(canonical_root "$1"); user=$2
        printf '%s\n' "$user" | grep -Eq '^[A-Za-z_][A-Za-z0-9_-]*\$?$' || fail "invalid user"
        uid=$(id -u "$user") || fail "unknown user"; [ "$uid" -gt 0 ] || fail "root cannot own a grant"
        [ "$(file_uid "$root")" = "$uid" ] || fail "network root must belong to the granted user"
        [ ! -L "$0" ] || fail "helper source must not be a symlink"
        source_dir=$(cd "$(dirname "$0")" && pwd -P)
        install -d -o root -g root -m 0755 /usr/local/libexec
        installed=/usr/local/libexec/sandbox-ingress-helper
        install -o root -g root -m 0755 "$source_dir/$(basename "$0")" "$installed"
        install -d -o root -g root -m 0755 /etc/sandbox-ingress /etc/sandbox-ingress/owners \
            /etc/sandbox-ingress/authorizations /etc/sandbox-ingress/applied
        policy="/etc/sandbox-ingress/owners/$uid.root"; temporary="$policy.new.$$"
        printf '%s\n' "$root" >"$temporary"; chown root:root "$temporary"; chmod 0444 "$temporary"; mv -f "$temporary" "$policy"
        install -d -o root -g root -m 0700 "/etc/sandbox-ingress/authorizations/$uid" \
            "/etc/sandbox-ingress/applied/$uid"
        sudoers="/etc/sudoers.d/sandbox-ingress-$uid"; temporary="$sudoers.new.$$"; alias="SANDBOX_INGRESS_$uid"
        {
            printf 'Cmnd_Alias %s = %s authorization-status *, %s preflight *, %s validate-current *, %s prepare *, %s activate *, %s observe *, %s rollback *, %s cleanup *, %s listeners\n' \
                "$alias" "$installed" "$installed" "$installed" "$installed" "$installed" "$installed" "$installed" "$installed" "$installed"
            printf '%s ALL=(root) NOPASSWD: %s\n' "$user" "$alias"
        } >"$temporary"
        chown root:root "$temporary"; chmod 0440 "$temporary"
        visudo -cf "$temporary" >/dev/null || { rm -f "$temporary"; fail "generated sudoers rule is invalid"; }
        mv -f "$temporary" "$sudoers" ;;
    authorize)
        [ "$#" -eq 15 ] || usage; require_root; validate_plan "$@"; validate_current >/dev/null
        hostname_unclaimed "$5" "$3"
        plan=$(plan_digest "$@"); directory="/etc/sandbox-ingress/authorizations/$(caller_uid)/$3"
        install -d -o root -g root -m 0700 "$directory"; trusted_directory "$directory"
        receipt=$(authorization_path "$3" "$plan")
        if [ -e "$receipt" ]; then
            require_receipt_file "$receipt"
            [ "$(sed -n '1p' "$receipt")" = "$plan" ] && [ "$(sed -n '2p' "$receipt")" = "${10}" ] \
                || fail "immutable authorization receipt collision"
        else
            temporary="$receipt.new.$$"; { printf '%s\n' "$plan" "${10}"; } >"$temporary"
            chown root:root "$temporary"; chmod 0400 "$temporary"; mv -f "$temporary" "$receipt"
        fi ;;
    authorization-status)
        [ "$#" -eq 15 ] || usage; require_root; require_plan_receipt "$@" ;;
    preflight)
        [ "$#" -eq 8 ] || usage; require_root; authorized_root "$1" >/dev/null; valid_adapter "$2"
        pid=$3; start=$4; executable_digest=$5; socket_ids=$6; listen=$7; listen_port=$8
        validate_current >/dev/null
        verify_caddy_authority "$pid" "$start" "$executable_digest" \
            "$socket_ids" "$listen" "$listen_port" ;;
    validate-current)
        [ "$#" -eq 2 ] || usage; require_root; authorized_root "$1" >/dev/null; valid_adapter "$2"; validate_current ;;
    prepare)
        [ "$#" -eq 15 ] || usage; require_root; require_plan_receipt "$@"; validate_current >/dev/null
        hostname_unclaimed "$5" "$3"
        route=$3; hostname=$5; backend=$6; backend_port=$7; listen=$8
        destination=$(destination "$route"); [ ! -L "$destination" ] || fail "owned destination became a symlink"
        transaction=$(transaction_dir "$route")
        if [ -e "$transaction" ]; then
            [ -d "$transaction" ] && [ ! -L "$transaction" ] && [ "$(file_uid "$transaction")" -eq 0 ] || fail "transaction path is invalid"
            rm -f -- "$transaction/prior" "$transaction/prior-applied" \
                "$transaction/staged" "$transaction/authorization" "$transaction/cleanup-prior"
            rmdir "$transaction" || fail "transaction directory contains unexpected state"
        fi
        install -d -o root -g root -m 0700 "$transaction"
        if [ -e "$destination" ]; then
            applied=$(applied_path "$route"); require_receipt_file "$applied"
            [ "$(sed -n '1p' "$destination")" = "# sandbox-ingress v1 route=$route" ] || fail "foreign destination collision"
            [ "$(sha256sum "$destination" | cut -d' ' -f1)" = "$(sed -n '2p' "$applied")" ] \
                || fail "prior applied route drifted"
            install -o root -g root -m 0600 "$destination" "$transaction/prior"
            install -o root -g root -m 0600 "$applied" "$transaction/prior-applied"
        fi
        render_candidate "$transaction/staged" "$route" "$hostname" "$backend" "$backend_port" "$listen"
        [ "$(sha256sum "$transaction/staged" | cut -d' ' -f1)" = "${10}" ] || fail "root rendering drifted"
        printf '%s\n' "$(plan_digest "$@")" "${10}" >"$transaction/authorization"
        chown root:root "$transaction/authorization"; chmod 0600 "$transaction/authorization"
        atomic_install "$transaction/staged" "$destination"
        if ! validate_current; then restore_prior "$transaction" "$destination"; fail "candidate config validation failed"; fi
        restore_prior "$transaction" "$destination"; validate_current >/dev/null || fail "restored config validation failed" ;;
    activate)
        [ "$#" -eq 3 ] || usage; require_root; authorized_root "$1" >/dev/null; valid_adapter "$2"; valid_route "$3"
        route=$3; transaction=$(transaction_dir "$route"); destination=$(destination "$route")
        [ -f "$transaction/staged" ] && [ ! -L "$transaction/staged" ] || fail "prepared candidate is unavailable"
        [ -f "$transaction/authorization" ] && [ ! -L "$transaction/authorization" ] \
            || fail "prepared authorization is unavailable"
        plan=$(sed -n '1p' "$transaction/authorization"); expected=$(sed -n '2p' "$transaction/authorization")
        receipt=$(authorization_path "$route" "$plan"); require_receipt_file "$receipt"
        [ "$(sed -n '2p' "$receipt")" = "$expected" ] || fail "prepared authorization was revoked"
        [ "$(sha256sum "$transaction/staged" | cut -d' ' -f1)" = "$expected" ] || fail "staged route no longer matches receipt"
        atomic_install "$transaction/staged" "$destination"
        if ! validate_current || ! reload_service; then
            restore_prior "$transaction" "$destination"; validate_current >/dev/null || true; reload_service || true
            fail "activation failed and prior state was restored"
        fi
        applied=$(applied_path "$route"); temporary="$applied.new.$$"
        printf '%s\n' "$plan" "$expected" >"$temporary"
        chown root:root "$temporary"; chmod 0400 "$temporary"; mv -f "$temporary" "$applied"
        sha256sum "$destination" | cut -d' ' -f1 ;;
    listeners)
        # Read-only privileged attribution for the required ports. An
        # unprivileged caller cannot read /proc/<pid>/fd for a root-owned
        # incumbent, so system Caddy -- the documented conformance target --
        # was permanently "unidentified" and could never be selected. Emits one
        # fixed-shape line per listening socket and mutates nothing.
        [ "$#" -eq 0 ] || usage; require_root
        ss -lntpH 2>/dev/null | while read -r _state _recvq _sendq local _peer users; do
            address=${local%:*}; port=${local##*:}
            case "$port" in ''|*[!0-9]*) continue ;; esac
            pid=${users#*pid=}; pid=${pid%%,*}
            case "$pid" in ''|*[!0-9]*) pid="" ;; esac
            command=""; executable=""; start=""; executable_digest=""
            if [ -n "$pid" ] && [ -r "/proc/$pid/comm" ]; then
                command=$(tr -d "\n" < "/proc/$pid/comm")
                executable=$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)
                # Field 22 of /proc/<pid>/stat: start time in clock ticks. Part
                # of the process identity an adapter must pin, so a pid reused
                # by another process cannot pass as the same owner.
                start=$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || true)
                if [ -n "$executable" ] && [ -f "$executable" ]; then
                    executable_digest=$(sha256sum "$executable" 2>/dev/null | cut -d' ' -f1 || true)
                fi
            fi
            printf '%s %s %s %s %s %s %s\n' "$address" "$port" "${pid:--}" "${command:--}" "${executable:--}" "${start:--}" "${executable_digest:--}"
        done ;;
    observe)
        [ "$#" -eq 3 ] || usage; require_root; require_applied_receipt "$@"
        route=$3; destination=$(destination "$route")
        [ -f "$destination" ] && [ ! -L "$destination" ] || fail "owned route unavailable"
        [ "$(sed -n '1p' "$destination")" = "# sandbox-ingress v1 route=$route" ] || fail "route ownership header mismatch"
        sha256sum "$destination" | cut -d' ' -f1 ;;
    rollback)
        [ "$#" -eq 3 ] || usage; require_root; authorized_root "$1" >/dev/null; valid_adapter "$2"; valid_route "$3"
        route=$3; transaction=$(transaction_dir "$route"); destination=$(destination "$route")
        [ -d "$transaction" ] && [ ! -L "$transaction" ] || fail "rollback transaction unavailable"
        restore_prior "$transaction" "$destination"
        applied=$(applied_path "$route")
        if [ -f "$transaction/prior-applied" ]; then
            temporary="$applied.new.$$"
            install -o root -g root -m 0400 "$transaction/prior-applied" "$temporary"
            mv -f -- "$temporary" "$applied"
        else rm -f -- "$applied"; fi
        validate_current; reload_service ;;
    cleanup)
        [ "$#" -eq 4 ] || usage; require_root; require_applied_receipt "$1" "$2" "$3"
        route=$3; expected=$4; valid_digest "$expected"; applied=$(applied_path "$route")
        [ "$(sed -n '2p' "$applied")" = "$expected" ] || fail "cleanup digest is outside applied ownership"
        destination=$(destination "$route"); transaction=$(transaction_dir "$route")
        if [ -e "$destination" ]; then
            [ -f "$destination" ] && [ ! -L "$destination" ] || fail "owned route is not a regular file"
            [ "$(sed -n '1p' "$destination")" = "# sandbox-ingress v1 route=$route" ] || fail "foreign route collision"
            [ "$(sha256sum "$destination" | cut -d' ' -f1)" = "$expected" ] || fail "owned route drifted"
            install -d -o root -g root -m 0700 "$transaction"
            install -o root -g root -m 0600 "$destination" "$transaction/cleanup-prior"
            rm -f -- "$destination"
            if ! validate_current || ! reload_service; then
                atomic_install "$transaction/cleanup-prior" "$destination"; reload_service || true
                fail "cleanup failed and route was restored"
            fi
        fi
        plan=$(sed -n '1p' "$applied"); authorization=$(authorization_path "$route" "$plan")
        rm -f -- "$applied" "$authorization" "$transaction/prior" \
            "$transaction/prior-applied" "$transaction/staged" \
            "$transaction/authorization" "$transaction/cleanup-prior"
        rmdir "$(dirname "$authorization")" 2>/dev/null || true
        rmdir "$transaction" 2>/dev/null || true ;;
    *) usage ;;
esac
