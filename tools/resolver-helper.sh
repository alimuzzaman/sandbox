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
    echo "       resolver-helper.sh macos-apply ROOT FILE SUFFIX ADDRESS PORT" >&2
    echo "       resolver-helper.sh macos-remove SUFFIX EXPECTED_SHA256" >&2
    echo "       resolver-helper.sh hosts-apply HOSTNAME ADDRESS" >&2
    echo "       resolver-helper.sh hosts-remove HOSTNAME ADDRESS" >&2
    exit 64
}

valid_hostname() {
    printf '%s\n' "$1" | grep -Eq '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$' \
        || fail "invalid hostname"
}

valid_host_address() {
    case "$1" in
        127.*|::1) ;;
        *) fail "hosts address must be loopback" ;;
    esac
}

valid_digest() {
    printf '%s\n' "$1" | grep -Eq '^[a-f0-9]{64}$' \
        || fail "expected digest is invalid"
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
    canonical_root=$(cd "$network_root" && pwd -P) \
        || fail "could not resolve network root"
    candidate_directory=$(dirname "$candidate")
    candidate_name=$(basename "$candidate")
    canonical_directory=$(cd "$candidate_directory" && pwd -P) \
        || fail "could not resolve candidate directory"
    canonical_file="$canonical_directory/$candidate_name"
    case "$canonical_file" in
        "$canonical_root"/authority/*) ;;
        *) fail "candidate is outside the authority root" ;;
    esac
    expected_uid=${SUDO_UID:-$(id -u)}
    if [ "$(uname -s)" = "Darwin" ]; then
        actual_uid=$(stat -f '%u' "$canonical_file") \
            || fail "could not inspect candidate owner"
        permissions=$(stat -f '%Lp' "$canonical_file") \
            || fail "could not inspect candidate mode"
    else
        actual_uid=$(stat -c '%u' -- "$canonical_file") \
            || fail "could not inspect candidate owner"
        permissions=$(stat -c '%a' -- "$canonical_file") \
            || fail "could not inspect candidate mode"
    fi
    [ "$actual_uid" = "$expected_uid" ] || fail "candidate owner does not match caller"
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

check_macos_candidate() {
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
    [ "$(wc -l < "$canonical_file" | tr -d ' ')" = 3 ] \
        || fail "candidate must contain exactly three lines"
    [ "$(sed -n '1p' "$canonical_file")" = "# sandbox-resolver v1 suffix=$suffix" ] \
        || fail "candidate ownership header is invalid"
    [ "$(sed -n '2p' "$canonical_file")" = "nameserver $address" ] \
        || fail "candidate nameserver is invalid"
    [ "$(sed -n '3p' "$canonical_file")" = "port $port" ] \
        || fail "candidate port is invalid"
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
    check-macos-candidate)
        check_macos_candidate "$@" >/dev/null
        echo "candidate-ok"
        ;;
    install)
        [ "$#" -eq 0 ] || usage
        require_root
        [ ! -L "$0" ] || fail "helper source must not be a symlink"
        source_directory=$(cd "$(dirname "$0")" && pwd -P) \
            || fail "could not resolve helper source"
        source_path="$source_directory/$(basename "$0")"
        install -d -o root -g root -m 0755 /usr/local/libexec
        install -o root -g root -m 0755 -- "$source_path" \
            /usr/local/libexec/sandbox-resolver-helper
        ;;
    resolved-apply)
        [ "$#" -eq 5 ] || usage
        canonical_file=$(check_candidate "$@")
        require_root
        suffix=$3
        destination="/etc/systemd/resolved.conf.d/80-sandbox-$suffix.conf"
        install -d -o root -g root -m 0755 /etc/systemd/resolved.conf.d
        if [ -e "$destination" ]; then
            [ ! -L "$destination" ] || fail "owned destination became a symlink"
            [ "$(sed -n '1p' "$destination")" = "# sandbox-resolver v1 suffix=$suffix" ] \
                || fail "refusing to replace a foreign resolver fragment"
            candidate_digest=$(sha256sum -- "$canonical_file" | cut -d' ' -f1)
            current_digest=$(sha256sum -- "$destination" | cut -d' ' -f1)
            [ "$candidate_digest" = "$current_digest" ] \
                || fail "owned resolver fragment changed; clean up before replacing it"
            echo "unchanged"
            exit 0
        fi
        temporary="$destination.new.$$"
        trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
        install -o root -g root -m 0644 -- "$canonical_file" "$temporary"
        mv -f -- "$temporary" "$destination"
        trap - EXIT HUP INT TERM
        systemctl reload-or-restart systemd-resolved.service
        echo "applied"
        ;;
    resolved-remove)
        [ "$#" -eq 2 ] || usage
        suffix=$1
        expected=$2
        valid_suffix "$suffix"
        valid_digest "$expected"
        require_root
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
    macos-apply)
        [ "$#" -eq 5 ] || usage
        canonical_file=$(check_macos_candidate "$@")
        require_root
        suffix=$3
        destination="/etc/resolver/$suffix"
        install -d -o root -g wheel -m 0755 /etc/resolver
        if [ -e "$destination" ]; then
            [ ! -L "$destination" ] || fail "owned destination became a symlink"
            [ "$(sed -n '1p' "$destination")" = "# sandbox-resolver v1 suffix=$suffix" ] \
                || fail "refusing to replace a foreign resolver fragment"
            candidate_digest=$(shasum -a 256 "$canonical_file" | cut -d' ' -f1)
            current_digest=$(shasum -a 256 "$destination" | cut -d' ' -f1)
            [ "$candidate_digest" = "$current_digest" ] \
                || fail "owned resolver fragment changed; clean up before replacing it"
            echo "unchanged"
            exit 0
        fi
        temporary="$destination.new.$$"
        trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
        install -o root -g wheel -m 0644 -- "$canonical_file" "$temporary"
        mv -f -- "$temporary" "$destination"
        trap - EXIT HUP INT TERM
        dscacheutil -flushcache
        killall -HUP mDNSResponder 2>/dev/null || true
        echo "applied"
        ;;
    macos-remove)
        [ "$#" -eq 2 ] || usage
        suffix=$1
        expected=$2
        valid_suffix "$suffix"
        valid_digest "$expected"
        require_root
        destination="/etc/resolver/$suffix"
        [ -e "$destination" ] || exit 0
        [ ! -L "$destination" ] || fail "owned destination became a symlink"
        [ "$(sed -n '1p' "$destination")" = "# sandbox-resolver v1 suffix=$suffix" ] \
            || fail "refusing to remove a foreign resolver fragment"
        actual=$(shasum -a 256 -- "$destination" | cut -d' ' -f1)
        [ "$actual" = "$expected" ] || fail "owned resolver fragment drifted"
        rm -f -- "$destination"
        dscacheutil -flushcache
        killall -HUP mDNSResponder 2>/dev/null || true
        ;;
    hosts-apply)
        [ "$#" -eq 2 ] || usage
        hostname=$1
        address=$2
        valid_hostname "$hostname"
        valid_host_address "$address"
        require_root
        begin="# sandbox-resolver-v1 begin $hostname"
        end="# sandbox-resolver-v1 end $hostname"
        [ ! -L /etc/hosts ] || fail "/etc/hosts must not be a symlink"
        if grep -Fxq "$begin" /etc/hosts; then
            awk -v begin="$begin" -v line="$address $hostname" -v end="$end" \
                -v name="$hostname" '
                $0 == begin { if (inside || seen) bad=1; inside=1; seen=1; next }
                $0 == end { if (!inside) bad=1; inside=0; next }
                inside { if ($0 != line || body) bad=1; body=1; next }
                { for (i=2; i<=NF; i++) if ($i == name) foreign=1 }
                END { if (bad || inside || !seen || !body || foreign) exit 65 }
            ' /etc/hosts || fail "owned hosts entry drifted or a foreign entry collides"
            echo "unchanged"
            exit 0
        fi
        awk -v name="$hostname" '
            { for (i=2; i<=NF; i++) if ($i == name) foreign=1 }
            END { exit foreign ? 65 : 0 }
        ' /etc/hosts || fail "refusing to replace a foreign hosts entry"
        temporary="/etc/hosts.sandbox.$$"
        trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
        awk -v begin="$begin" -v end="$end" '
            $0 == begin { skip=1; next }
            $0 == end { skip=0; next }
            !skip { print }
        ' /etc/hosts > "$temporary"
        printf '%s\n%s %s\n%s\n' "$begin" "$address" "$hostname" "$end" >> "$temporary"
        chmod --reference=/etc/hosts "$temporary"
        chown --reference=/etc/hosts "$temporary"
        mv -f -- "$temporary" /etc/hosts
        trap - EXIT HUP INT TERM
        echo "applied"
        ;;
    hosts-remove)
        [ "$#" -eq 2 ] || usage
        hostname=$1
        address=$2
        valid_hostname "$hostname"
        valid_host_address "$address"
        require_root
        begin="# sandbox-resolver-v1 begin $hostname"
        line="$address $hostname"
        end="# sandbox-resolver-v1 end $hostname"
        [ ! -L /etc/hosts ] || fail "/etc/hosts must not be a symlink"
        grep -Fxq "$begin" /etc/hosts || exit 0
        awk -v begin="$begin" -v line="$line" -v end="$end" '
            $0 == begin { getline body; getline close; found=1; next }
            { print }
            END { if (!found || body != line || close != end) exit 65 }
        ' /etc/hosts > "/etc/hosts.sandbox.$$" \
            || { rm -f -- "/etc/hosts.sandbox.$$"; fail "owned hosts entry drifted"; }
        temporary="/etc/hosts.sandbox.$$"
        chmod --reference=/etc/hosts "$temporary"
        chown --reference=/etc/hosts "$temporary"
        mv -f -- "$temporary" /etc/hosts
        ;;
    *) usage ;;
esac
