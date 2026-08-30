#!/bin/sh
# Narrow privileged helper for scoped resolver fragments. Policy stays in Python;
# this file validates fixed values and performs only fixed-path mutations.
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

usage() {
    echo "usage: resolver-helper.sh check-candidate ROOT FILE SUFFIX ADDRESS PORT" >&2
    echo "       resolver-helper.sh install" >&2
    echo "       resolver-helper.sh installed-status" >&2
    echo "       resolver-helper.sh resolved-status" >&2
    echo "       resolver-helper.sh authorize ADAPTER OWNER_SHA256 SUFFIX ADDRESS PORT EXPECTED_SHA256 [PID START UID CONTROL]" >&2
    echo "       resolver-helper.sh authorization-status ADAPTER OWNER_SHA256 SUFFIX ADDRESS PORT EXPECTED_SHA256 [PID START UID CONTROL]" >&2
    echo "       resolver-helper.sh revoke-authorization ADAPTER OWNER_SHA256 SUFFIX ADDRESS PORT EXPECTED_SHA256 [PID START UID CONTROL]" >&2
    echo "       resolver-helper.sh resolved-apply OWNER_SHA256 SUFFIX ADDRESS PORT EXPECTED_SHA256 PID START UID CONTROL" >&2
    echo "       resolver-helper.sh resolved-remove OWNER_SHA256 SUFFIX ADDRESS PORT EXPECTED_SHA256 PID START UID CONTROL" >&2
    echo "       resolver-helper.sh macos-apply OWNER_SHA256 SUFFIX ADDRESS PORT EXPECTED_SHA256" >&2
    echo "       resolver-helper.sh macos-remove OWNER_SHA256 SUFFIX ADDRESS PORT EXPECTED_SHA256" >&2
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
        ::1) ;;
        127.*)
            printf '%s\n' "$1" | awk -F. '
                NF != 4 { exit 1 }
                { for (i=1; i<=4; i++) if ($i !~ /^[0-9]+$/ || $i + 0 > 255) exit 1 }
                $1 + 0 != 127 { exit 1 }
            ' || fail "hosts address must be loopback"
            ;;
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
    case "$1" in
        test|tst|invalid) ;;
        *) fail "suffix is not an approved local-only namespace" ;;
    esac
}

valid_address() {
    case "$1" in
        ::1) ;;
        127.*)
            printf '%s\n' "$1" | awk -F. '
                NF != 4 { exit 1 }
                { for (i=1; i<=4; i++) if ($i !~ /^[0-9]+$/ || $i + 0 > 255) exit 1 }
                $1 + 0 != 127 { exit 1 }
            ' || fail "authority address must be loopback"
            ;;
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

valid_service_identity() {
    identity_pid=$1 identity_start=$2 identity_uid=$3 identity_control=$4
    case "$identity_pid:$identity_start:$identity_uid" in
        *[!0-9:]*|0:*|*:0:*) fail "resolved service identity is invalid" ;;
    esac
    [ "$identity_control" = "/system.slice/systemd-resolved.service" ] \
        || fail "resolved control identity is invalid"
}

resolved_identity_fields() {
    systemctl is-active --quiet systemd-resolved.service \
        || fail "systemd-resolved is not active"
    service_pid=$(systemctl show --property=MainPID --value systemd-resolved.service) \
        || fail "systemd-resolved pid is unavailable"
    case "$service_pid" in ''|*[!0-9]*|0) fail "systemd-resolved pid is invalid" ;; esac
    [ -r "/proc/$service_pid/stat" ] || fail "systemd-resolved process is unavailable"
    service_stat=$(cat "/proc/$service_pid/stat") \
        || fail "systemd-resolved process identity is unavailable"
    service_tail=${service_stat##*) }
    service_start=$(printf '%s\n' "$service_tail" | awk '{print $20}')
    service_uid=$(stat -c '%u' -- "/proc/$service_pid") \
        || fail "systemd-resolved owner identity is unavailable"
    service_control=$(systemctl show --property=ControlGroup --value systemd-resolved.service) \
        || fail "systemd-resolved control identity is unavailable"
    valid_service_identity "$service_pid" "$service_start" "$service_uid" "$service_control"
    confirmed_pid=$(systemctl show --property=MainPID --value systemd-resolved.service) \
        || fail "systemd-resolved pid confirmation is unavailable"
    [ "$confirmed_pid" = "$service_pid" ] \
        || fail "systemd-resolved changed during observation"
    printf '%s %s %s %s\n' "$service_pid" "$service_start" "$service_uid" "$service_control"
}

require_resolved_identity() {
    valid_service_identity "$@"
    [ "$(resolved_identity_fields)" = "$1 $2 $3 $4" ] \
        || fail "systemd-resolved identity changed before mutation"
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

require_root_directory() {
    directory=$1
    [ -d "$directory" ] && [ ! -L "$directory" ] \
        || fail "trusted resolver directory is unavailable"
    if [ "$(uname -s)" = "Darwin" ]; then
        identity=$(stat -f '%u:%Lp' "$directory")
    else
        identity=$(stat -c '%u:%a' -- "$directory")
    fi
    # Deliberately distinct names: POSIX sh has no function scope, and this
    # runs inside verbs that hold the CALLER's `owner` digest. Reusing `owner`
    # here overwrote it with the directory's uid ("0"), so every receipt was
    # written under owner 0 -- unattributable between projects, and impossible
    # to remove later because cleanup looks it up by the real digest.
    directory_owner=${identity%%:*}; directory_mode=${identity#*:}
    [ "$directory_owner" = 0 ] && [ $((0$directory_mode & 022)) -eq 0 ] \
        || fail "trusted resolver directory ownership is invalid"
}

require_root_fragment() {
    fragment=$1
    [ -f "$fragment" ] && [ ! -L "$fragment" ] \
        || fail "owned resolver fragment path is unsafe"
    if [ "$(uname -s)" = "Darwin" ]; then
        identity=$(stat -f '%u:%Lp:%l' "$fragment")
    else
        identity=$(stat -c '%u:%a:%h' -- "$fragment")
    fi
    [ "$identity" = "0:644:1" ] \
        || fail "owned resolver fragment ownership is invalid"
}

require_installed_helper() {
    [ "$0" = "/usr/local/libexec/sandbox-resolver-helper" ] \
        || fail "verb is available only through the installed helper"
    [ ! -L "$0" ] || fail "installed helper must not be a symlink"
    if [ "$(uname -s)" = "Darwin" ]; then
        installed_identity=$(stat -f '%u:%Lp:%l' "$0")
    else
        installed_identity=$(stat -c '%u:%a:%h' -- "$0")
    fi
    [ "$installed_identity" = "0:755:1" ] \
        || fail "installed helper ownership or mode is invalid"
}

ensure_authorization_root() {
    require_root_directory /var
    require_root_directory /var/lib
    for directory in /var/lib/sandbox /var/lib/sandbox/resolver \
        /var/lib/sandbox/resolver/authorizations \
        /var/lib/sandbox/resolver/applied; do
        if [ ! -e "$directory" ]; then
            install -d -o root -g root -m 0700 "$directory"
        fi
        require_root_directory "$directory"
    done
}

require_authorization_root() {
    require_root_directory /var
    require_root_directory /var/lib
    require_root_directory /var/lib/sandbox
    require_root_directory /var/lib/sandbox/resolver
    require_root_directory /var/lib/sandbox/resolver/authorizations
    require_root_directory /var/lib/sandbox/resolver/applied
}

caller_uid() {
    case "${SUDO_UID:-}" in ''|*[!0-9]*) fail "authorized sudo caller is unavailable" ;; esac
    [ "$SUDO_UID" -gt 0 ] || fail "root cannot own a resolver authorization"
    printf '%s\n' "$SUDO_UID"
}

valid_adapter() {
    case "$1:$(uname -s)" in
        resolved:Linux|macos:Darwin) ;;
        *) fail "resolver adapter is unavailable on this platform" ;;
    esac
}

rendered_digest() {
    adapter=$1 suffix=$2 address=$3 port=$4
    case "$adapter" in
        resolved)
            payload=$(printf '# sandbox-resolver v1 suffix=%s\n[Resolve]\nDNS=%s:%s\nDomains=~%s\n' \
                "$suffix" "$address" "$port" "$suffix") ;;
        macos)
            payload=$(printf '# sandbox-resolver v1 suffix=%s\nnameserver %s\nport %s\n' \
                "$suffix" "$address" "$port") ;;
        *) fail "resolver adapter is invalid" ;;
    esac
    if [ "$(uname -s)" = "Darwin" ]; then
        printf '%s\n' "$payload" | shasum -a 256 | cut -d' ' -f1
    else
        printf '%s\n' "$payload" | sha256sum | cut -d' ' -f1
    fi
}

authorization_path() {
    uid=$1 adapter=$2 suffix=$3 owner=$4
    printf '/var/lib/sandbox/resolver/authorizations/%s-%s-%s-%s.receipt\n' \
        "$uid" "$adapter" "$suffix" "$owner"
}

authorization_payload() {
    uid=$1 adapter=$2 owner=$3 suffix=$4 address=$5 port=$6 digest=$7
    shift 7
    if [ "$adapter" = resolved ]; then
        [ "$#" -eq 4 ] || fail "resolved authorization identity is missing"
        valid_service_identity "$@"
        printf 'sandbox-resolver-authorization-v2 uid=%s adapter=%s owner=%s suffix=%s address=%s port=%s digest=%s pid=%s start=%s service_uid=%s control=%s\n' \
            "$uid" "$adapter" "$owner" "$suffix" "$address" "$port" "$digest" \
            "$1" "$2" "$3" "$4"
    else
        printf 'sandbox-resolver-authorization-v1 uid=%s adapter=%s owner=%s suffix=%s address=%s port=%s digest=%s\n' \
            "$uid" "$adapter" "$owner" "$suffix" "$address" "$port" "$digest"
    fi
}

applied_path() {
    uid=$1 adapter=$2 suffix=$3 owner=$4
    printf '/var/lib/sandbox/resolver/applied/%s-%s-%s-%s.receipt\n' \
        "$uid" "$adapter" "$suffix" "$owner"
}

applied_payload() {
    uid=$1 adapter=$2 owner=$3 suffix=$4 address=$5 port=$6 digest=$7
    printf 'sandbox-resolver-applied-v1 uid=%s adapter=%s owner=%s suffix=%s address=%s port=%s digest=%s\n' \
        "$uid" "$adapter" "$owner" "$suffix" "$address" "$port" "$digest"
}

check_applied() {
    adapter=$1 owner=$2 suffix=$3 address=$4 port=$5 expected=$6
    uid=$(caller_uid)
    receipt=$(applied_path "$uid" "$adapter" "$suffix" "$owner")
    payload=$(applied_payload "$uid" "$adapter" "$owner" "$suffix" "$address" "$port" "$expected")
    receipt_matches "$receipt" "$payload" || fail "applied resolver ownership is unavailable"
    printf '%s\n' "$receipt"
}

other_applied_exists() (
    uid=$1 adapter=$2 owner=$3 suffix=$4 address=$5 port=$6 expected=$7
    root=/var/lib/sandbox/resolver/applied
    for candidate in "$root"/*-"$adapter"-"$suffix"-*.receipt; do
        [ -e "$candidate" ] || continue
        [ "$candidate" = "$(applied_path "$uid" "$adapter" "$suffix" "$owner")" ] && continue
        base=${candidate##*/}
        candidate_uid=${base%%-*}
        remainder=${base#"$candidate_uid-$adapter-$suffix-"}
        candidate_owner=${remainder%.receipt}
        case "$candidate_uid" in ''|*[!0-9]*) continue ;; esac
        # SKIP an unparseable neighbour rather than failing the verb. A single
        # malformed or legacy receipt used to abort removal entirely, which left
        # the owned fragment and the authority running with no way to clean up.
        # An unreadable name cannot match this payload, so skipping is safe.
        printf '%s\n' "$candidate_owner" | grep -Eq '^[a-f0-9]{64}$' || continue
        payload=$(applied_payload "$candidate_uid" "$adapter" "$candidate_owner" "$suffix" "$address" "$port" "$expected")
        if receipt_matches "$candidate" "$payload"; then return 0; fi
    done
    return 1
)

receipt_matches() {
    receipt=$1 expected_payload=$2
    [ -f "$receipt" ] && [ ! -L "$receipt" ] || return 1
    if [ "$(uname -s)" = "Darwin" ]; then
        identity=$(stat -f '%u:%Lp:%l' "$receipt")
    else
        identity=$(stat -c '%u:%a:%h' -- "$receipt")
    fi
    [ "$identity" = "0:600:1" ] && [ "$(cat "$receipt")" = "$expected_payload" ]
}

install_receipt() {
    receipt=$1 payload=$2
    receipt_temporary="$receipt.new.$$"
    trap 'rm -f -- "$receipt_temporary"' EXIT HUP INT TERM
    printf '%s\n' "$payload" > "$receipt_temporary"
    chown root:root "$receipt_temporary"; chmod 0600 "$receipt_temporary"
    mv -f -- "$receipt_temporary" "$receipt"
    trap - EXIT HUP INT TERM
}

check_authorization() {
    adapter=$1 owner=$2 suffix=$3 address=$4 port=$5 expected=$6
    shift 6
    valid_adapter "$adapter"; valid_suffix "$suffix"; valid_address "$address"
    valid_port "$port"; valid_digest "$owner"; valid_digest "$expected"
    actual_rendered=$(rendered_digest "$adapter" "$suffix" "$address" "$port")
    [ "$actual_rendered" = "$expected" ] || fail "rendered resolver digest mismatch"
    uid=$(caller_uid)
    require_authorization_root
    receipt=$(authorization_path "$uid" "$adapter" "$suffix" "$owner")
    expected_payload=$(authorization_payload "$uid" "$adapter" "$owner" "$suffix" "$address" "$port" "$expected" "$@")
    receipt_matches "$receipt" "$expected_payload" \
        || fail "exact resolver authorization does not match"
    printf '%s\n' "$receipt"
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
        [ -n "${SUDO_UID:-}" ] && [ -n "${SUDO_USER:-}" ] \
            || fail "install must be invoked by a sudo user"
        case "$SUDO_UID" in ''|*[!0-9]*) fail "invalid sudo user" ;; esac
        [ "$SUDO_UID" -gt 0 ] || fail "refusing a root-owned resolver policy"
        login=$(id -un "$SUDO_UID" 2>/dev/null) || fail "sudo user is unavailable"
        [ "$login" = "$SUDO_USER" ] || fail "sudo identity mismatch"
        install -d -o root -g root -m 0755 /usr/local/libexec
        install -o root -g root -m 0755 -- "$source_path" \
            /usr/local/libexec/sandbox-resolver-helper
        sudoers="/etc/sudoers.d/sandbox-resolver-$SUDO_UID"
        sudoers_temporary="$sudoers.new.$$"
        trap 'rm -f -- "$sudoers_temporary"' EXIT HUP INT TERM
        if [ "$(uname -s)" = "Darwin" ]; then
            allowed="/usr/local/libexec/sandbox-resolver-helper installed-status, /usr/local/libexec/sandbox-resolver-helper authorization-status *, /usr/local/libexec/sandbox-resolver-helper revoke-authorization *, /usr/local/libexec/sandbox-resolver-helper macos-apply *, /usr/local/libexec/sandbox-resolver-helper macos-remove *"
        else
            allowed="/usr/local/libexec/sandbox-resolver-helper installed-status, /usr/local/libexec/sandbox-resolver-helper resolved-status, /usr/local/libexec/sandbox-resolver-helper authorization-status *, /usr/local/libexec/sandbox-resolver-helper revoke-authorization *, /usr/local/libexec/sandbox-resolver-helper resolved-apply *, /usr/local/libexec/sandbox-resolver-helper resolved-remove *"
        fi
        printf '%s ALL=(root) NOPASSWD: %s\n' "$login" "$allowed" > "$sudoers_temporary"
        chown root:root "$sudoers_temporary"
        chmod 0440 "$sudoers_temporary"
        visudo -cf "$sudoers_temporary" >/dev/null \
            || fail "resolver sudo policy validation failed"
        mv -f -- "$sudoers_temporary" "$sudoers"
        trap - EXIT HUP INT TERM
        ;;
    installed-status)
        [ "$#" -eq 0 ] || usage
        require_root
        require_installed_helper
        echo "sandbox-resolver-helper-v2"
        ;;
    authorize)
        case "${1:-}" in resolved) [ "$#" -eq 10 ] || usage ;; *) [ "$#" -eq 6 ] || usage ;; esac
        require_root
        require_installed_helper
        adapter=$1 owner=$2 suffix=$3 address=$4 port=$5 expected=$6
        valid_adapter "$adapter"; valid_suffix "$suffix"; valid_address "$address"
        valid_port "$port"; valid_digest "$owner"; valid_digest "$expected"
        [ "$(rendered_digest "$adapter" "$suffix" "$address" "$port")" = "$expected" ] \
            || fail "rendered resolver digest mismatch"
        uid=$(caller_uid)
        authorization_root=/var/lib/sandbox/resolver/authorizations
        ensure_authorization_root
        receipt=$(authorization_path "$uid" "$adapter" "$suffix" "$owner")
        shift 6
        payload=$(authorization_payload "$uid" "$adapter" "$owner" "$suffix" "$address" "$port" "$expected" "$@")
        if [ -e "$receipt" ]; then
            if [ "$adapter" = resolved ]; then
                legacy_payload=$(printf 'sandbox-resolver-authorization-v1 uid=%s adapter=%s owner=%s suffix=%s address=%s port=%s digest=%s\n' \
                    "$uid" "$adapter" "$owner" "$suffix" "$address" "$port" "$expected")
                if receipt_matches "$receipt" "$legacy_payload"; then
                    install_receipt "$receipt" "$payload"
                    echo "authorized"
                    exit 0
                fi
            fi
            receipt_matches "$receipt" "$payload" \
                || fail "a different resolver authorization already exists"
            echo "unchanged"
            exit 0
        fi
        install_receipt "$receipt" "$payload"
        echo "authorized"
        ;;
    revoke-authorization)
        case "${1:-}" in resolved) [ "$#" -eq 10 ] || usage ;; *) [ "$#" -eq 6 ] || usage ;; esac
        require_root; require_installed_helper
        receipt=$(check_authorization "$@")
        adapter=$1 owner=$2 suffix=$3
        applied=$(applied_path "$(caller_uid)" "$adapter" "$suffix" "$owner")
        [ ! -e "$applied" ] || fail "applied resolver ownership cannot be revoked"
        rm -f -- "$receipt"
        echo "revoked"
        ;;
    authorization-status)
        case "${1:-}" in resolved) [ "$#" -eq 10 ] || usage ;; *) [ "$#" -eq 6 ] || usage ;; esac
        require_root
        require_installed_helper
        check_authorization "$@" >/dev/null
        echo "authorized"
        ;;
    resolved-status)
        [ "$#" -eq 0 ] || usage
        require_root
        require_installed_helper
        [ "$(uname -s)" = "Linux" ] || fail "systemd-resolved is unavailable on this platform"
        set -- $(resolved_identity_fields)
        printf 'sandbox-resolved-service-v1 owner=systemd-resolved:host unit=systemd-resolved.service pid=%s start=%s uid=%s control=%s\n' \
            "$1" "$2" "$3" "$4"
        ;;
    resolved-apply)
        [ "$#" -eq 9 ] || usage
        require_root
        require_installed_helper
        owner=$1 suffix=$2 address=$3 port=$4 expected=$5
        service_pid=$6 service_start=$7 service_uid=$8 service_control=$9
        valid_suffix "$suffix"
        valid_address "$address"
        valid_port "$port"
        check_authorization resolved "$owner" "$suffix" "$address" "$port" "$expected" \
            "$service_pid" "$service_start" "$service_uid" "$service_control" >/dev/null
        uid=$(caller_uid)
        applied=$(applied_path "$uid" resolved "$suffix" "$owner")
        applied_payload_value=$(applied_payload "$uid" resolved "$owner" "$suffix" "$address" "$port" "$expected")
        destination="/etc/systemd/resolved.conf.d/80-sandbox-$suffix.conf"
        require_root_directory /etc
        require_root_directory /etc/systemd
        if [ -e /etc/systemd/resolved.conf.d ]; then
            require_root_directory /etc/systemd/resolved.conf.d
        fi
        require_resolved_identity "$service_pid" "$service_start" "$service_uid" "$service_control"
        install -d -o root -g root -m 0755 /etc/systemd/resolved.conf.d
        require_root_directory /etc/systemd/resolved.conf.d
        temporary="$destination.new.$$"
        trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
        printf '# sandbox-resolver v1 suffix=%s\n[Resolve]\nDNS=%s:%s\nDomains=~%s\n' \
            "$suffix" "$address" "$port" "$suffix" > "$temporary"
        chown root:root "$temporary"
        chmod 0644 "$temporary"
        if [ -e "$destination" ]; then
            require_root_fragment "$destination"
            [ "$(sed -n '1p' "$destination")" = "# sandbox-resolver v1 suffix=$suffix" ] \
                || fail "refusing to replace a foreign resolver fragment"
            candidate_digest=$(sha256sum -- "$temporary" | cut -d' ' -f1)
            current_digest=$(sha256sum -- "$destination" | cut -d' ' -f1)
            [ "$candidate_digest" = "$current_digest" ] \
                || fail "owned resolver fragment changed; clean up before replacing it"
            if ! receipt_matches "$applied" "$applied_payload_value" && \
               ! other_applied_exists "$uid" resolved "$owner" "$suffix" "$address" "$port" "$expected"; then
                fail "refusing to adopt an identical foreign resolver fragment"
            fi
            install_receipt "$applied" "$applied_payload_value"
            rm -f -- "$temporary"
            trap - EXIT HUP INT TERM
            echo "unchanged"
            exit 0
        fi
        [ ! -e "$applied" ] || fail "applied receipt exists without its resolver fragment"
        mv -f -- "$temporary" "$destination"
        trap - EXIT HUP INT TERM
        if ! systemctl reload-or-restart systemd-resolved.service; then
            rm -f -- "$destination"
            systemctl reload-or-restart systemd-resolved.service >/dev/null 2>&1 || true
            fail "systemd-resolved rejected the scoped fragment"
        fi
        if ! install_receipt "$applied" "$applied_payload_value"; then
            rm -f -- "$destination"
            systemctl reload-or-restart systemd-resolved.service >/dev/null 2>&1 || true
            fail "applied resolver receipt could not be persisted; fragment rolled back"
        fi
        echo "applied"
        ;;
    resolved-remove)
        [ "$#" -eq 9 ] || usage
        owner=$1 suffix=$2 address=$3 port=$4 expected=$5
        service_pid=$6 service_start=$7 service_uid=$8 service_control=$9
        valid_suffix "$suffix"
        valid_digest "$expected"
        require_root
        require_installed_helper
        receipt=$(check_authorization resolved "$owner" "$suffix" "$address" "$port" "$expected" \
            "$service_pid" "$service_start" "$service_uid" "$service_control")
        applied=$(check_applied resolved "$owner" "$suffix" "$address" "$port" "$expected")
        uid=$(caller_uid)
        other_status=0
        other_applied_exists "$uid" resolved "$owner" "$suffix" "$address" "$port" "$expected" \
            || other_status=$?
        if [ "$other_status" -eq 0 ]; then
            require_resolved_identity "$service_pid" "$service_start" "$service_uid" "$service_control"
            rm -f -- "$applied" "$receipt"
            echo "retained"
            exit 0
        fi
        [ "$other_status" -eq 1 ] || fail "shared applied resolver receipts are unsafe"
        destination="/etc/systemd/resolved.conf.d/80-sandbox-$suffix.conf"
        if [ ! -e "$destination" ]; then
            require_resolved_identity "$service_pid" "$service_start" "$service_uid" "$service_control"
            rm -f -- "$applied" "$receipt"
            exit 0
        fi
        require_root_fragment "$destination"
        [ "$(sed -n '1p' "$destination")" = "# sandbox-resolver v1 suffix=$suffix" ] \
            || fail "refusing to remove a foreign resolver fragment"
        actual=$(sha256sum -- "$destination" | cut -d' ' -f1)
        [ "$actual" = "$expected" ] || fail "owned resolver fragment drifted"
        require_resolved_identity "$service_pid" "$service_start" "$service_uid" "$service_control"
        backup="$destination.remove.$$"
        cp -p -- "$destination" "$backup"
        trap 'rm -f -- "$backup"' EXIT HUP INT TERM
        require_resolved_identity "$service_pid" "$service_start" "$service_uid" "$service_control"
        rm -f -- "$destination"
        require_resolved_identity "$service_pid" "$service_start" "$service_uid" "$service_control"
        if ! systemctl reload-or-restart systemd-resolved.service; then
            mv -f -- "$backup" "$destination"
            trap - EXIT HUP INT TERM
            systemctl reload-or-restart systemd-resolved.service >/dev/null 2>&1 || true
            fail "systemd-resolved cleanup reload failed; fragment restored"
        fi
        rm -f -- "$backup"
        trap - EXIT HUP INT TERM
        rm -f -- "$applied" "$receipt"
        ;;
    macos-apply)
        [ "$#" -eq 5 ] || usage
        require_root
        require_installed_helper
        owner=$1 suffix=$2 address=$3 port=$4 expected=$5
        valid_suffix "$suffix"
        valid_address "$address"
        valid_port "$port"
        check_authorization macos "$owner" "$suffix" "$address" "$port" "$expected" >/dev/null
        uid=$(caller_uid)
        applied=$(applied_path "$uid" macos "$suffix" "$owner")
        applied_payload_value=$(applied_payload "$uid" macos "$owner" "$suffix" "$address" "$port" "$expected")
        destination="/etc/resolver/$suffix"
        require_root_directory /etc
        if [ -e /etc/resolver ]; then require_root_directory /etc/resolver; fi
        install -d -o root -g wheel -m 0755 /etc/resolver
        require_root_directory /etc/resolver
        temporary="$destination.new.$$"
        trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
        printf '# sandbox-resolver v1 suffix=%s\nnameserver %s\nport %s\n' \
            "$suffix" "$address" "$port" > "$temporary"
        chown root:wheel "$temporary"
        chmod 0644 "$temporary"
        if [ -e "$destination" ]; then
            require_root_fragment "$destination"
            [ "$(sed -n '1p' "$destination")" = "# sandbox-resolver v1 suffix=$suffix" ] \
                || fail "refusing to replace a foreign resolver fragment"
            candidate_digest=$(shasum -a 256 "$temporary" | cut -d' ' -f1)
            current_digest=$(shasum -a 256 "$destination" | cut -d' ' -f1)
            [ "$candidate_digest" = "$current_digest" ] \
                || fail "owned resolver fragment changed; clean up before replacing it"
            if ! receipt_matches "$applied" "$applied_payload_value" && \
               ! other_applied_exists "$uid" macos "$owner" "$suffix" "$address" "$port" "$expected"; then
                fail "refusing to adopt an identical foreign resolver fragment"
            fi
            install_receipt "$applied" "$applied_payload_value"
            rm -f -- "$temporary"
            trap - EXIT HUP INT TERM
            echo "unchanged"
            exit 0
        fi
        [ ! -e "$applied" ] || fail "applied receipt exists without its resolver fragment"
        mv -f -- "$temporary" "$destination"
        trap - EXIT HUP INT TERM
        if ! dscacheutil -flushcache; then
            rm -f -- "$destination"
            dscacheutil -flushcache >/dev/null 2>&1 || true
            fail "macOS resolver cache flush failed"
        fi
        killall -HUP mDNSResponder 2>/dev/null || true
        if ! install_receipt "$applied" "$applied_payload_value"; then
            rm -f -- "$destination"
            dscacheutil -flushcache >/dev/null 2>&1 || true
            killall -HUP mDNSResponder 2>/dev/null || true
            fail "applied resolver receipt could not be persisted; fragment rolled back"
        fi
        echo "applied"
        ;;
    macos-remove)
        [ "$#" -eq 5 ] || usage
        owner=$1 suffix=$2 address=$3 port=$4 expected=$5
        valid_suffix "$suffix"
        valid_digest "$expected"
        require_root
        require_installed_helper
        receipt=$(check_authorization macos "$owner" "$suffix" "$address" "$port" "$expected")
        applied=$(check_applied macos "$owner" "$suffix" "$address" "$port" "$expected")
        uid=$(caller_uid)
        other_status=0
        other_applied_exists "$uid" macos "$owner" "$suffix" "$address" "$port" "$expected" \
            || other_status=$?
        if [ "$other_status" -eq 0 ]; then
            rm -f -- "$applied" "$receipt"
            echo "retained"
            exit 0
        fi
        [ "$other_status" -eq 1 ] || fail "shared applied resolver receipts are unsafe"
        destination="/etc/resolver/$suffix"
        if [ ! -e "$destination" ]; then rm -f -- "$applied" "$receipt"; exit 0; fi
        require_root_fragment "$destination"
        [ "$(sed -n '1p' "$destination")" = "# sandbox-resolver v1 suffix=$suffix" ] \
            || fail "refusing to remove a foreign resolver fragment"
        actual=$(shasum -a 256 -- "$destination" | cut -d' ' -f1)
        [ "$actual" = "$expected" ] || fail "owned resolver fragment drifted"
        backup="$destination.remove.$$"
        cp -p -- "$destination" "$backup"
        trap 'rm -f -- "$backup"' EXIT HUP INT TERM
        rm -f -- "$destination"
        if ! dscacheutil -flushcache; then
            mv -f -- "$backup" "$destination"
            trap - EXIT HUP INT TERM
            dscacheutil -flushcache >/dev/null 2>&1 || true
            fail "macOS resolver cleanup failed; fragment restored"
        fi
        rm -f -- "$backup"
        trap - EXIT HUP INT TERM
        killall -HUP mDNSResponder 2>/dev/null || true
        rm -f -- "$applied" "$receipt"
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
