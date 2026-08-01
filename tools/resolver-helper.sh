#!/bin/sh
# Narrow privileged helper for scoped resolver fragments. Policy stays in Python;
# this file validates fixed values and performs only fixed-path mutations.
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

usage() {
    echo "usage: resolver-helper.sh check-candidate ROOT FILE SUFFIX ADDRESS PORT" >&2
    echo "       resolver-helper.sh install" >&2
    echo "       resolver-helper.sh resolved-apply ROOT FILE SUFFIX ADDRESS PORT" >&2
    echo "       resolver-helper.sh resolved-remove SUFFIX EXPECTED_SHA256" >&2
    exit 64
}

fail() {
    echo "resolver-helper: $1" >&2
    exit 65
}

valid_suffix() {
    printf '%s\n' "$1" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$' \
        || fail "invalid local suffix"
    [ "$1" != "local" ] || fail ".local is reserved for mDNS"
}

valid_address() {
    case "$1" in
        127.*|::1) ;;
        *) fail "authority address must be loopback" ;;
    esac
}

valid_port() {
    case "$1" in
        ''|*[!0-9]*) fail "invalid authority port" ;;
    esac
    [ "$1" -ge 1024 ] && [ "$1" -le 65535 ] \
        || fail "authority port must be unprivileged"
}

canonical_candidate() {
    network_root=$1
    candidate=$2
    [ -d "$network_root" ] || fail "network root does not exist"
    [ ! -L "$candidate" ] || fail "candidate must not be a symlink"
    [ -f "$candidate" ] || fail "candidate must be a regular file"
    canonical_root=$(realpath -e -- "$network_root") \
        || fail "could not resolve network root"
    canonical_file=$(realpath -e -- "$candidate") \
        || fail "could not resolve candidate"
    case "$canonical_file" in
        "$canonical_root"/authority/*) ;;
        *) fail "candidate is outside the authority root" ;;
    esac
    expected_uid=${SUDO_UID:-$(id -u)}
    actual_uid=$(stat -c '%u' -- "$canonical_file") \
        || fail "could not inspect candidate owner"
    [ "$actual_uid" = "$expected_uid" ] || fail "candidate owner does not match caller"
    permissions=$(stat -c '%a' -- "$canonical_file") \
        || fail "could not inspect candidate mode"
    [ $((0$permissions & 022)) -eq 0 ] \
        || fail "candidate must not be group/world writable"
    printf '%s\n' "$canonical_file"
}

check_candidate() {
    [ "$#" -eq 5 ] || usage
    network_root=$1
    candidate=$2
    suffix=$3
    address=$4
    port=$5
    valid_suffix "$suffix"
    valid_address "$address"
    valid_port "$port"
    canonical_file=$(canonical_candidate "$network_root" "$candidate")
    [ "$(wc -l < "$canonical_file" | tr -d ' ')" = 4 ] \
        || fail "candidate must contain exactly four lines"
    [ "$(sed -n '1p' "$canonical_file")" = "# sandbox-resolver v1 suffix=$suffix" ] \
        || fail "candidate ownership header is invalid"
    [ "$(sed -n '2p' "$canonical_file")" = "[Resolve]" ] \
        || fail "candidate section is invalid"
    [ "$(sed -n '3p' "$canonical_file")" = "DNS=$address:$port" ] \
        || fail "candidate DNS endpoint is invalid"
    [ "$(sed -n '4p' "$canonical_file")" = "Domains=~$suffix" ] \
        || fail "candidate route-only domain is invalid"
    printf '%s\n' "$canonical_file"
}

require_root() {
    [ "$(id -u)" -eq 0 ] || fail "this verb requires root"
}

verb=${1:-}
[ "$#" -gt 0 ] || usage
shift

case "$verb" in
    check-candidate)
        check_candidate "$@" >/dev/null
        echo "candidate-ok"
        ;;
    install)
        [ "$#" -eq 0 ] || usage
        require_root
        source_path=$(realpath -e -- "$0") || fail "could not resolve helper source"
        [ ! -L "$0" ] || fail "helper source must not be a symlink"
        install -d -o root -g root -m 0755 /usr/local/libexec
        install -o root -g root -m 0755 -- "$source_path" \
            /usr/local/libexec/sandbox-resolver-helper
        ;;
    resolved-apply)
        [ "$#" -eq 5 ] || usage
        require_root
        canonical_file=$(check_candidate "$@")
        suffix=$3
        destination="/etc/systemd/resolved.conf.d/80-sandbox-$suffix.conf"
        install -d -o root -g root -m 0755 /etc/systemd/resolved.conf.d
        if [ -e "$destination" ]; then
            [ ! -L "$destination" ] || fail "owned destination became a symlink"
            [ "$(sed -n '1p' "$destination")" = "# sandbox-resolver v1 suffix=$suffix" ] \
                || fail "refusing to replace a foreign resolver fragment"
        fi
        temporary="$destination.new.$$"
        trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
        install -o root -g root -m 0644 -- "$canonical_file" "$temporary"
        mv -f -- "$temporary" "$destination"
        trap - EXIT HUP INT TERM
        systemctl reload-or-restart systemd-resolved.service
        ;;
    resolved-remove)
        [ "$#" -eq 2 ] || usage
        require_root
        suffix=$1
        expected=$2
        valid_suffix "$suffix"
        printf '%s\n' "$expected" | grep -Eq '^[a-f0-9]{64}$' \
            || fail "expected digest is invalid"
        destination="/etc/systemd/resolved.conf.d/80-sandbox-$suffix.conf"
        if [ ! -e "$destination" ]; then
            exit 0
        fi
        [ ! -L "$destination" ] || fail "owned destination became a symlink"
        [ "$(sed -n '1p' "$destination")" = "# sandbox-resolver v1 suffix=$suffix" ] \
            || fail "refusing to remove a foreign resolver fragment"
        actual=$(sha256sum -- "$destination" | cut -d' ' -f1)
        [ "$actual" = "$expected" ] || fail "owned resolver fragment drifted"
        rm -f -- "$destination"
        systemctl reload-or-restart systemd-resolved.service
        ;;
    *) usage ;;
esac
