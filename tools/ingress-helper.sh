#!/bin/sh
# Fixed privileged helper for attributable system ingress fragments.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

fail() { echo "ingress-helper: $1" >&2; exit 65; }
usage() {
    echo "usage: ingress-helper.sh check-candidate ROOT FILE ADAPTER ROUTE_ID" >&2
    echo "       ingress-helper.sh install" >&2
    echo "       ingress-helper.sh validate-current ADAPTER" >&2
    echo "       ingress-helper.sh prepare ROOT FILE ADAPTER ROUTE_ID" >&2
    echo "       ingress-helper.sh activate ROOT ADAPTER ROUTE_ID" >&2
    echo "       ingress-helper.sh observe ADAPTER ROUTE_ID" >&2
    echo "       ingress-helper.sh rollback ROOT ADAPTER ROUTE_ID" >&2
    echo "       ingress-helper.sh cleanup ROOT ADAPTER ROUTE_ID EXPECTED_SHA256" >&2
    exit 64
}
require_root() { [ "$(id -u)" -eq 0 ] || fail "this verb requires root"; }
valid_route() { printf '%s\n' "$1" | grep -Eq '^[a-f0-9]{64}$' || fail "invalid route id"; }
valid_digest() { printf '%s\n' "$1" | grep -Eq '^[a-f0-9]{64}$' || fail "invalid digest"; }
valid_adapter() {
    case "$1" in system-nginx|system-apache|system-caddy|traefik) ;; *) fail "invalid adapter" ;; esac
}
extension() {
    case "$1" in system-caddy) echo caddy ;; traefik) echo yml ;; *) echo conf ;; esac
}
validate_schema() {
    adapter=$1 file=$2 route=$3
    case "$adapter" in
        system-nginx)
            [ "$(wc -l < "$file" | tr -d ' ')" -eq 10 ] || fail "nginx candidate line count is invalid"
            grep -Eq '^    listen (\[[0-9a-fA-F:]+\]|[0-9.]+):[0-9]{1,5};$' "$file" || fail "nginx listen is invalid"
            grep -Eq '^    server_name ([a-z0-9-]+\.)+[a-z0-9-]+( \*\.([a-z0-9-]+\.)+[a-z0-9-]+)?;$' "$file" || fail "nginx hostname is invalid"
            grep -Eq '^        proxy_pass http://(\[[0-9a-fA-F:]+\]|127\.[0-9.]+):[0-9]{1,5};$' "$file" || fail "nginx backend is invalid"
            grep -Ev '^(# sandbox-ingress v1 route=[a-f0-9]{64}|server \{|    listen (\[[0-9a-fA-F:]+\]|[0-9.]+):[0-9]{1,5};|    server_name ([a-z0-9-]+\.)+[a-z0-9-]+( \*\.([a-z0-9-]+\.)+[a-z0-9-]+)?;|    location / \{|        proxy_pass http://(\[[0-9a-fA-F:]+\]|127\.[0-9.]+):[0-9]{1,5};|        proxy_set_header Host \$host;|        proxy_set_header X-Forwarded-Proto \$scheme;|    \}|\})$' "$file" \
                | grep -q . && fail "nginx candidate contains a forbidden directive" || true ;;
        system-apache)
            lines=$(wc -l < "$file" | tr -d ' '); [ "$lines" -eq 7 ] || [ "$lines" -eq 8 ] || fail "Apache candidate line count is invalid"
            grep -Eq '^<VirtualHost (\[[0-9a-fA-F:]+\]|[0-9.]+):[0-9]{1,5}>$' "$file" || fail "Apache listener is invalid"
            grep -Eq '^    ServerName ([a-z0-9-]+\.)+[a-z0-9-]+$' "$file" || fail "Apache hostname is invalid"
            grep -Eq '^    ProxyPass / http://(\[[0-9a-fA-F:]+\]|127\.[0-9.]+):[0-9]{1,5}/$' "$file" || fail "Apache backend is invalid"
            grep -Ev '^(# sandbox-ingress v1 route=[a-f0-9]{64}|<VirtualHost (\[[0-9a-fA-F:]+\]|[0-9.]+):[0-9]{1,5}>|    ServerName ([a-z0-9-]+\.)+[a-z0-9-]+|    ServerAlias \*\.([a-z0-9-]+\.)+[a-z0-9-]+|    ProxyPreserveHost On|    ProxyPass / http://(\[[0-9a-fA-F:]+\]|127\.[0-9.]+):[0-9]{1,5}/|    ProxyPassReverse / http://(\[[0-9a-fA-F:]+\]|127\.[0-9.]+):[0-9]{1,5}/|</VirtualHost>)$' "$file" \
                | grep -q . && fail "Apache candidate contains a forbidden directive" || true ;;
        system-caddy)
            [ "$(wc -l < "$file" | tr -d ' ')" -eq 4 ] || fail "Caddy candidate line count is invalid"
            grep -Eq '^http://([a-z0-9-]+\.)+[a-z0-9-]+(, \*\.([a-z0-9-]+\.)+[a-z0-9-]+)? \{$' "$file" || fail "Caddy hostname is invalid"
            grep -Eq '^    reverse_proxy (\[[0-9a-fA-F:]+\]|127\.[0-9.]+):[0-9]{1,5}$' "$file" || fail "Caddy backend is invalid"
            grep -Ev '^(# sandbox-ingress v1 route=[a-f0-9]{64}|http://([a-z0-9-]+\.)+[a-z0-9-]+(, \*\.([a-z0-9-]+\.)+[a-z0-9-]+)? \{|    reverse_proxy (\[[0-9a-fA-F:]+\]|127\.[0-9.]+):[0-9]{1,5}|\})$' "$file" \
                | grep -q . && fail "Caddy candidate contains a forbidden directive" || true ;;
        traefik)
            [ "$(wc -l < "$file" | tr -d ' ')" -eq 11 ] || fail "Traefik candidate line count is invalid"
            grep -Eq '^      rule: "Host\(`([a-z0-9-]+\.)+[a-z0-9-]+`\)( \|\| HostRegexp\(`\{subdomain:\.\+\}\.([a-z0-9-]+\.)+[a-z0-9-]+`\))?"$' "$file" || fail "Traefik hostname rule is invalid"
            grep -Eq '^          - url: "http://(\[[0-9a-fA-F:]+\]|127\.[0-9.]+):[0-9]{1,5}"$' "$file" || fail "Traefik backend is invalid"
            grep -Ev '^(# sandbox-ingress v1 route=[a-f0-9]{64}|http:|  routers:|    sandbox-[a-f0-9]{20}:|      rule: "Host\(`([a-z0-9-]+\.)+[a-z0-9-]+`\)( \|\| HostRegexp\(`\{subdomain:\.\+\}\.([a-z0-9-]+\.)+[a-z0-9-]+`\))?"|      service: sandbox-[a-f0-9]{20}|  services:|      loadBalancer:|        servers:|          - url: "http://(\[[0-9a-fA-F:]+\]|127\.[0-9.]+):[0-9]{1,5}")$' "$file" \
                | grep -q . && fail "Traefik candidate contains a forbidden key" || true ;;
    esac
}
destination() {
    adapter=$1 route=$2
    case "$adapter" in
        system-nginx) echo "/etc/nginx/conf.d/90-sandbox-$route.conf" ;;
        system-apache) echo "/etc/apache2/conf-enabled/90-sandbox-$route.conf" ;;
        system-caddy) echo "/etc/caddy/conf.d/90-sandbox-$route.caddy" ;;
        traefik) echo "/etc/traefik/dynamic/90-sandbox-$route.yml" ;;
    esac
}
canonical_root() {
    [ -d "$1" ] || fail "network root does not exist"
    [ ! -L "$1" ] || fail "network root must not be a symlink"
    (cd "$1" && pwd -P) || fail "could not resolve network root"
}
check_candidate() {
    [ "$#" -eq 4 ] || usage
    root=$(canonical_root "$1") file=$2 adapter=$3 route=$4
    valid_adapter "$adapter"; valid_route "$route"
    [ -f "$file" ] && [ ! -L "$file" ] || fail "candidate must be a regular non-symlink"
    directory=$(cd "$(dirname "$file")" && pwd -P) || fail "candidate directory is invalid"
    canonical="$directory/$(basename "$file")"
    expected="$root/ingress/candidates/$adapter/$route.$(extension "$adapter")"
    [ "$canonical" = "$expected" ] || fail "candidate path is outside its fixed owned location"
    expected_uid=${SUDO_UID:-$(id -u)}
    [ "$(stat -c '%u' -- "$canonical")" = "$expected_uid" ] || fail "candidate owner mismatch"
    mode=$(stat -c '%a' -- "$canonical")
    [ $((0$mode & 022)) -eq 0 ] || fail "candidate must not be group/world writable"
    [ "$(wc -c < "$canonical")" -le 65536 ] || fail "candidate is too large"
    [ "$(sed -n '1p' "$canonical")" = "# sandbox-ingress v1 route=$route" ] \
        || fail "candidate ownership header mismatch"
    validate_schema "$adapter" "$canonical" "$route"
    printf '%s\n' "$canonical"
}
surface_ready() {
    adapter=$1
    [ "$(uname -s)" = Linux ] || fail "system fragment helper is not proven on this platform"
    case "$adapter" in
        system-nginx) [ -d /etc/nginx/conf.d ] || fail "nginx conf.d is unavailable" ;;
        system-apache) [ -d /etc/apache2/conf-enabled ] || fail "Apache conf-enabled is unavailable" ;;
        system-caddy)
            [ -d /etc/caddy/conf.d ] || fail "Caddy fragment directory is unavailable"
            grep -Eq '^[[:space:]]*import[[:space:]]+(/etc/caddy/)?conf\.d/\*' /etc/caddy/Caddyfile \
                || fail "Caddyfile does not import the owned fragment directory" ;;
        traefik)
            [ -d /etc/traefik/dynamic ] || fail "Traefik dynamic directory is unavailable"
            grep -Rqs '/etc/traefik/dynamic' /etc/traefik/traefik.y*ml \
                || fail "Traefik file provider is not enabled for the owned directory" ;;
    esac
}
validate_current() {
    adapter=$1; surface_ready "$adapter"
    case "$adapter" in
        system-nginx) nginx -t ;;
        system-apache)
            if command -v apache2ctl >/dev/null; then apache2ctl configtest; else apachectl configtest; fi ;;
        system-caddy) caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile ;;
        traefik) traefik check-config --configFile=/etc/traefik/traefik.yml ;;
    esac
}
reload_service() {
    case "$1" in
        system-nginx) systemctl reload nginx.service ;;
        system-apache) systemctl reload apache2.service ;;
        system-caddy) systemctl reload caddy.service ;;
        traefik) : ;; # enabled file provider watches the directory atomically
    esac
}
atomic_install() {
    source=$1 destination=$2
    temporary="$destination.new.$$"
    trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
    install -o root -g root -m 0644 -- "$source" "$temporary"
    mv -f -- "$temporary" "$destination"
    trap - EXIT HUP INT TERM
}
restore_prior() {
    transaction=$1 destination=$2
    if [ -f "$transaction/prior" ]; then atomic_install "$transaction/prior" "$destination"
    else rm -f -- "$destination"; fi
}

verb=${1:-}; [ "$#" -gt 0 ] || usage; shift
case "$verb" in
    check-candidate) check_candidate "$@" >/dev/null; echo candidate-ok ;;
    install)
        [ "$#" -eq 0 ] || usage; require_root
        [ ! -L "$0" ] || fail "helper source must not be a symlink"
        source_dir=$(cd "$(dirname "$0")" && pwd -P)
        install -d -o root -g root -m 0755 /usr/local/libexec
        install -o root -g root -m 0755 "$source_dir/$(basename "$0")" /usr/local/libexec/sandbox-ingress-helper ;;
    validate-current)
        [ "$#" -eq 1 ] || usage; valid_adapter "$1"; require_root; validate_current "$1" ;;
    prepare)
        [ "$#" -eq 4 ] || usage
        root=$(canonical_root "$1"); candidate=$(check_candidate "$@"); adapter=$3; route=$4
        require_root; surface_ready "$adapter"; destination=$(destination "$adapter" "$route")
        [ ! -L "$destination" ] || fail "owned destination became a symlink"
        transaction="$root/ingress/transactions/$route"
        [ ! -L "$transaction" ] || fail "transaction path became a symlink"
        rm -rf -- "$transaction"; install -d -o root -g root -m 0700 "$transaction"
        if [ -e "$destination" ]; then
            [ "$(sed -n '1p' "$destination")" = "# sandbox-ingress v1 route=$route" ] \
                || fail "foreign destination collision"
            install -o root -g root -m 0600 "$destination" "$transaction/prior"
        fi
        candidate_digest=$(sha256sum "$candidate" | cut -d' ' -f1)
        install -o root -g root -m 0600 "$candidate" "$transaction/staged"
        [ "$(sha256sum "$transaction/staged" | cut -d' ' -f1)" = "$candidate_digest" ] \
            || fail "candidate changed while staging"
        validate_schema "$adapter" "$transaction/staged" "$route"
        atomic_install "$transaction/staged" "$destination"
        if ! validate_current "$adapter"; then restore_prior "$transaction" "$destination"; fail "candidate config validation failed"; fi
        restore_prior "$transaction" "$destination"
        validate_current "$adapter" >/dev/null || fail "restored config validation failed"
        ;;
    activate)
        [ "$#" -eq 3 ] || usage
        root=$(canonical_root "$1"); adapter=$2; route=$3; valid_adapter "$adapter"; valid_route "$route"; require_root
        transaction="$root/ingress/transactions/$route"; destination=$(destination "$adapter" "$route")
        [ -f "$transaction/staged" ] && [ ! -L "$transaction/staged" ] || fail "prepared candidate is unavailable"
        atomic_install "$transaction/staged" "$destination"
        if ! validate_current "$adapter" || ! reload_service "$adapter"; then
            restore_prior "$transaction" "$destination"; validate_current "$adapter" >/dev/null || true; reload_service "$adapter" || true
            fail "activation failed and prior state was restored"
        fi
        sha256sum "$destination" | cut -d' ' -f1 ;;
    observe)
        [ "$#" -eq 2 ] || usage; adapter=$1; route=$2; valid_adapter "$adapter"; valid_route "$route"; require_root
        destination=$(destination "$adapter" "$route"); [ -f "$destination" ] && [ ! -L "$destination" ] || fail "owned route unavailable"
        [ "$(sed -n '1p' "$destination")" = "# sandbox-ingress v1 route=$route" ] || fail "route ownership header mismatch"
        sha256sum "$destination" | cut -d' ' -f1 ;;
    rollback)
        [ "$#" -eq 3 ] || usage
        root=$(canonical_root "$1"); adapter=$2; route=$3; valid_adapter "$adapter"; valid_route "$route"; require_root
        transaction="$root/ingress/transactions/$route"; destination=$(destination "$adapter" "$route")
        [ -d "$transaction" ] && [ ! -L "$transaction" ] || fail "rollback transaction unavailable"
        restore_prior "$transaction" "$destination"; validate_current "$adapter"; reload_service "$adapter" ;;
    cleanup)
        [ "$#" -eq 4 ] || usage
        root=$(canonical_root "$1"); adapter=$2; route=$3; expected=$4
        valid_adapter "$adapter"; valid_route "$route"; valid_digest "$expected"; require_root
        destination=$(destination "$adapter" "$route"); [ -e "$destination" ] || exit 0
        [ -f "$destination" ] && [ ! -L "$destination" ] || fail "owned route is not a regular file"
        [ "$(sed -n '1p' "$destination")" = "# sandbox-ingress v1 route=$route" ] || fail "foreign route collision"
        [ "$(sha256sum "$destination" | cut -d' ' -f1)" = "$expected" ] || fail "owned route drifted"
        transaction="$root/ingress/transactions/$route"; install -d -o root -g root -m 0700 "$transaction"
        install -o root -g root -m 0600 "$destination" "$transaction/cleanup-prior"
        rm -f "$destination"
        if ! validate_current "$adapter" || ! reload_service "$adapter"; then
            atomic_install "$transaction/cleanup-prior" "$destination"; reload_service "$adapter" || true; fail "cleanup failed and route was restored"
        fi ;;
    *) usage ;;
esac
