from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr


__all__ = ['ACTIVE', 'ASKPASS_HELPER', 'CLI_VENV', 'COMPOSE', 'COMPOSE_DIR', 'CONFIG', 'CONFIG_LOCAL', 'CONNECT_TARGETS', 'DOMAIN_RE', 'ENTRY', 'FOCUS', 'HERD_BIN_DIR', 'HERD_CLI_DEFAULT', 'HERD_DB_HOST', 'HERD_DB_PASSWORD', 'HERD_DB_PORT', 'HERD_DB_USER', 'HOSTS_HELPER', 'INTROSPECT_PHP', 'LAUNCHD_PLIST', 'MCP_DIR', 'MCP_SERVER_NAME', 'MCP_VENV', 'MULTISITE_MARKER', 'PLUGINS_DIR', 'PROJECT_MCP_JSON', 'PROXY_BIND_IP', 'PROXY_CADDYFILE', 'PROXY_CERTS_DIR', 'PROXY_COMPOSE', 'PROXY_DIR', 'PROXY_HELPER', 'PROXY_PROJECT', 'PROXY_SUDOERS', 'PROXY_TLD', 'ROOT', 'SECRETS_ENV', 'SEEDS_DIR', 'SERVERS', 'SNAPSHOTS_DIR', 'SUDOERS_FILE', 'TESTS_DB_NAME', 'TEST_SUITE_DIR', 'TEST_TOOLS_DIR', 'TOOLS_DIR', 'TOOLS_VENV', 'WP_DIR', '_BASE_WP_CONFIG', '_CLAUDE_PRICES', '_COMPOSER_PHAR_URL', '_HTTPS_OFFER_MARKER', '_JobStream', '_PHPUNIT_PHAR_URL', '_POLYFILLS_REPO', '_POLYFILLS_TAG', '_RunResult', '_WEB_BUILDERS', '_WEB_CSS_CACHE', '_WEB_JS_CACHE', '_WEB_PAGE', '_WEB_STREAM', '_WPDEVELOP_REPO', '_active_project_name', '_assign_domains_to_all', '_autologin_mu_plugin', '_build_instance_block', '_build_mcp_entry', '_ca_installed', '_ca_trusted_macos', '_caddy_block', '_cert_paths', '_certs_changed_since_proxy_start', '_claude_projects_dir', '_cli_image', '_compose_no_follow_logs', '_config_extra_php', '_connect_fluentboards', '_connect_github', '_convert_multisite', '_core', '_cost_for', '_curses_suspended', '_cwd_instance', '_dash_draw', '_dash_flash', '_dash_pick', '_dash_prompt', '_dash_run', '_derive_instance_name', '_distinct_tlds', '_dns_flush', '_docker_preflight', '_download', '_ensure_litespeed_htaccess', '_ensure_proxy_up', '_ensure_test_tools', '_ensure_tests_db', '_ensure_url_proxy', '_ensure_wp_test_suite', '_env_config_lines', '_extra_vol_lines', '_force_symlink', '_gh_cli_orgs', '_gh_cli_user', '_git_q', '_global_link_dir', '_herd', '_herd_cli', '_herd_db_name', '_herd_domain', '_herd_isolate', '_herd_isolated_php', '_herd_php', '_herd_php_bin', '_herd_tests_db', '_herd_wp_cmd', '_host_php', '_host_wp', '_hosts_edit', '_hosts_passwordless', '_https_offer_declined', '_install_alias_launchd', '_instance_reachable', '_instance_running', '_is_herd_instance', '_is_server', '_job_snapshot', '_lo0_alias_present', '_local_yaml', '_make_venv', '_merged_wp_config', '_mint_cert', '_multisite_mode', '_next_free_port', '_norm_tld', '_offer_install', '_onboard_instance', '_php_literal', '_php_squote', '_pick_instance_ports', '_pin_db_creds_in_config', '_pin_wp_constants_in_config', '_pkg_manager', '_pkg_slug', '_plugins_home', '_port_busy_by_other', '_price_tier', '_prompt', '_provision_herd', '_provision_test_harness', '_proxy_container_running', '_proxy_started_at', '_proxy_sudoers_installed', '_refresh_env_local', '_relax_perms_for_uid_switch', '_resolve_port_conflicts', '_resolve_setup_tld', '_resolver_present', '_run_cmd_capture', '_run_tests', '_run_tests_herd', '_sandbox_proxy_active', '_secure_at_create', '_server_runtime', '_set_https_offer_declined', '_site_host', '_stale_mcp_servers', '_start_job', '_sudo', '_sudo_env', '_tld', '_valet_available', '_valet_proxy_active', '_valet_tld', '_valid_domain', '_valid_server', '_wait_http', '_wait_reachable', '_warn_version_drift', '_web_apache', '_web_css', '_web_do_action', '_web_image', '_web_job_seq', '_web_jobs_lock', '_web_js', '_web_list_seeds', '_web_list_snapshots', '_web_litespeed', '_web_lock', '_web_nginx', '_web_services', '_wildcard_san', '_wire_project_plugins', '_wire_project_themes', '_wp_debug_env', '_wpcli_service', '_write_env_local', '_write_local_yaml', '_write_mail_muplugin', '_write_multisite_htaccess', '_write_ssl_muplugin', '_write_wp_tests_config', '_write_wp_tests_config_herd', 'active_project_file', 'apply_config', 'claude_usage', 'collect_instance_rows', 'compose', 'compose_file', 'deep_merge', 'die', 'domains_ready', 'ensure_instance', 'ensure_pyyaml', 'ensure_tools_venv', 'expand', 'find_modern_python', 'focus_file', 'info', 'load_config', 'mcp_server_name', 'ok', 'plugins_dir', 'project_name', 'proxy_available', 'proxy_setup', 'proxy_teardown', 'regen_caddyfile', 'register_claude_user_scope', 'reload_proxy', 'render_compose', 'render_proxy_compose', 'resolve_instances', 'run', 'save_local_app_password', 'save_local_autologin_token', 'site_url', 'snapshots_dir', 'valet_proxy_add', 'valet_proxy_remove', 'wp_dir', 'wpcli', 'write_claude_mcp_config', 'write_compose_files', 'write_env_for_compose']


__all__ += ['BRIDGE_PORT', 'save_local_bridge_token', '_write_snapshot_muplugin',
            '_bridge_handle', '_bridge_token_for', '_ensure_bridge_server',
            '_valid_snapshot_name', '_bridge_port_up']


BRIDGE_PORT = 8765


ROOT = Path(__file__).resolve().parent.parent.parent


# --- Per-user base for ALL machine-state (spec 009) ------------------------- #
# Single swappable base; every runtime/config/secret path derives from it. The
# `sb` CLI, sandbox_core, and the MCP server resolve this identically (same env,
# same default) so they never disagree about where state lives. Replicated here
# (rather than imported from sandbox_core) to avoid import-order fragility at
# module load — keep the three copies in lockstep.

def _sandbox_base() -> Path:
    return Path(os.environ.get("SANDBOX_HOME", "~/sandbox")).expanduser().resolve()


def _resolve_runtime_dir() -> Path:
    """$SANDBOX_HOME/runtime, with a backward-compat fallback to the pre-009
    in-repo runtime until `sb migrate` runs (FR-015). SANDBOX_RUNTIME wins."""
    explicit = os.environ.get("SANDBOX_RUNTIME")
    if explicit:
        return Path(explicit)
    new = _sandbox_base() / "runtime"
    legacy = ROOT / "runtime"
    if not (new / "registry.json").exists() and (legacy / "registry.json").exists():
        return legacy
    return new


BASE = _sandbox_base()


RUNTIME_DIR = _resolve_runtime_dir()


def ensure_base() -> Path:
    """Create the base + runtime dir on demand (idempotent)."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return BASE


ENTRY = ROOT / "sb"


CONFIG = ROOT / "sandbox.yml"


def _base_file(name: str, legacy: Path) -> Path:
    """Resolve a per-machine file under the base, falling back to its pre-009
    legacy location until migration moves it (FR-015). Returns the base path for
    fresh writes when neither exists, so reads and writes stay co-located."""
    new = BASE / name
    if new.exists():
        return new
    if legacy.exists():
        return legacy
    return new


# Per-machine config + secrets, consolidated under the base (spec 009). Legacy
# locations (repo root) are read as a fallback until `sb migrate` relocates them.
CONFIG_LOCAL = _base_file("sandbox.local.yml", ROOT / "sandbox.local.yml")

LOCAL_YML = CONFIG_LOCAL  # spec-009 canonical name (alias of CONFIG_LOCAL)

CONFIG_FILE = _base_file(
    "config.json", Path.home() / ".config" / "sandbox" / "config.json")

__all__ += ['BASE', 'RUNTIME_DIR', 'ensure_base', 'LOCAL_YML', 'CONFIG_FILE',
            'ENV_LOCAL']


COMPOSE = ROOT / "docker-compose.yml"


COMPOSE_DIR = RUNTIME_DIR / "compose"


ACTIVE = ROOT / ".active-project"


FOCUS = ROOT / ".focus"


WP_DIR = RUNTIME_DIR / "wp"


PLUGINS_DIR = WP_DIR / "wp-content" / "plugins"


SNAPSHOTS_DIR = RUNTIME_DIR / "snapshots"


SEEDS_DIR = RUNTIME_DIR / "seeds"


# Shared plugin/theme/core download cache (one dir for ALL instances — see the
# `wp-cli` / `wp-http` subdirs). Bind-mounted into every instance's tiers.
DL_CACHE_DIR = RUNTIME_DIR / "dl-cache"
__all__ += ['DL_CACHE_DIR']


MCP_DIR = ROOT / "mcp" / "wp-server"


MCP_VENV = MCP_DIR / ".venv"


CLI_VENV = ROOT / ".cli-venv"


TOOLS_DIR = ROOT / "tools"


TOOLS_VENV = RUNTIME_DIR / ".venv-tools"


_WEB_STREAM = [False]


SERVERS = ("apache", "nginx", "litespeed", "herd")


HERD_CLI_DEFAULT = (Path.home() / "Library" / "Application Support"
                    / "Herd" / "bin" / "herd")


HERD_BIN_DIR = Path(os.environ.get(
    "SANDBOX_HERD_BIN_DIR",
    str(Path.home() / "Library" / "Application Support" / "Herd" / "bin")))


HERD_DB_HOST = os.environ.get("SANDBOX_HERD_DB_HOST", "127.0.0.1")


HERD_DB_PORT = os.environ.get("SANDBOX_HERD_DB_PORT", "3306")


HERD_DB_USER = os.environ.get("SANDBOX_HERD_DB_USER", "root")


HERD_DB_PASSWORD = os.environ.get("SANDBOX_HERD_DB_PASSWORD", "")


MCP_SERVER_NAME = "sandbox"


HOSTS_HELPER = TOOLS_DIR / "hosts-helper.sh"


SUDOERS_FILE = Path("/etc/sudoers.d/sandbox-hosts")


DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


PROXY_DIR       = RUNTIME_DIR / "proxy"


PROXY_CERTS_DIR = PROXY_DIR / "certs"


PROXY_CADDYFILE = PROXY_DIR / "Caddyfile"


PROXY_COMPOSE   = PROXY_DIR / "proxy.yml"


PROXY_HELPER    = TOOLS_DIR / "proxy-helper.sh"


ASKPASS_HELPER  = TOOLS_DIR / "askpass.sh"


PROXY_SUDOERS   = Path("/etc/sudoers.d/sandbox-proxy")


PROXY_PROJECT   = "sandbox-proxy"


PROXY_BIND_IP   = "127.0.0.77"


PROXY_TLD       = "tst"


LAUNCHD_PLIST   = Path("/Library/LaunchDaemons/com.sandbox.lo0alias.plist")


_HTTPS_OFFER_MARKER = RUNTIME_DIR / ".https-offer-declined"


_BASE_WP_CONFIG = {
    # Keep WordPress installs non-interactive in the disposable local stack.
    # The web container also repairs wp-content ownership during bootstrap;
    # without both guarantees WordPress falls back to FTP/SSH credentials.
    "FS_METHOD": "direct",
    "WP_DEBUG_LOG": True,
    "WP_DEBUG_DISPLAY": False,
    "SCRIPT_DEBUG": True,
    "WP_ENVIRONMENT_TYPE": "local",
}


MULTISITE_MARKER = ".sandbox-multisite"


_SNAPSHOT_MU_TEMPLATE = r'''<?php
/**
 * Sandbox Snapshots - local dev only. Generated by ./sb; regenerated on recreate.
 * Tools -> Sandbox Snapshots: take/restore/list/delete instance snapshots via the
 * host `sb web` bridge. Never ships to / affects a real site (sandbox-only guard).
 */
if ( ! defined( 'ABSPATH' ) ) { return; }
define( 'SANDBOX_BRIDGE_URL', '%URL%' );
define( 'SANDBOX_BRIDGE_TOKEN', '%TOKEN%' );
define( 'SANDBOX_INSTANCE', '%INSTANCE%' );

add_action( 'admin_menu', function () {
	add_management_page(
		'Sandbox Snapshots', 'Sandbox Snapshots',
		'manage_options', 'sandbox-snapshots', 'sandbox_snapshots_render'
	);
} );

/** Server-side proxy to the host bridge (nonce + capability enforced here). */
function sandbox_snapshots_bridge( $method, $path, $body = null ) {
	$args = array(
		'method'  => $method,
		'timeout' => 30,
		'headers' => array(
			'Authorization' => 'Bearer ' . SANDBOX_BRIDGE_TOKEN,
			'Content-Type'  => 'application/json',
		),
	);
	if ( null !== $body ) { $args['body'] = wp_json_encode( $body ); }
	$url = SANDBOX_BRIDGE_URL . '/api/instance/' . rawurlencode( SANDBOX_INSTANCE ) . $path;
	$res = wp_remote_request( $url, $args );
	if ( is_wp_error( $res ) ) {
		return array( 'ok' => false, 'error' =>
			'Sandbox bridge not reachable (' . $res->get_error_message() .
			'). Start it on the host: run `./sb up ' . SANDBOX_INSTANCE .
			'` (or `./sb web`).' );
	}
	$code = wp_remote_retrieve_response_code( $res );
	$data = json_decode( wp_remote_retrieve_body( $res ), true );
	if ( ! is_array( $data ) ) { $data = array( 'ok' => false, 'error' => 'bad bridge response' ); }
	$data['_status'] = $code;
	return $data;
}

add_action( 'wp_ajax_sandbox_snap', function () {
	if ( ! current_user_can( 'manage_options' )
		|| ! check_ajax_referer( 'sandbox_snapshots', 'nonce', false ) ) {
		wp_send_json( array( 'ok' => false, 'error' => 'unauthorized' ), 403 );
	}
	$op = isset( $_POST['op'] ) ? sanitize_text_field( wp_unslash( $_POST['op'] ) ) : '';
	$name = isset( $_POST['name'] ) ? sanitize_text_field( wp_unslash( $_POST['name'] ) ) : '';
	// No client-side name validation: the host bridge is the trust boundary —
	// it slugifies free-form names on create ("snapshot 2" -> "snapshot-2") and
	// validates them on restore/delete. A blanket reject here would block names
	// the bridge would happily accept.
	if ( 'list' === $op ) {
		wp_send_json( sandbox_snapshots_bridge( 'GET', '/snapshots' ) );
	} elseif ( 'take' === $op ) {
		wp_send_json( sandbox_snapshots_bridge( 'POST', '/snapshot', array( 'name' => $name, 'force' => ! empty( $_POST['force'] ), 'db_only' => ! empty( $_POST['db_only'] ) ) ) );
	} elseif ( 'restore' === $op ) {
		wp_send_json( sandbox_snapshots_bridge( 'POST', '/restore', array( 'name' => $name ) ) );
	} elseif ( 'delete' === $op ) {
		wp_send_json( sandbox_snapshots_bridge( 'DELETE', '/snapshot/' . rawurlencode( $name ) ) );
	} elseif ( 'job' === $op ) {
		$jid = isset( $_POST['job_id'] ) ? sanitize_text_field( wp_unslash( $_POST['job_id'] ) ) : '';
		wp_send_json( sandbox_snapshots_bridge( 'GET', '/job/' . rawurlencode( $jid ) ) );
	}
	wp_send_json( array( 'ok' => false, 'error' => 'unknown op' ), 400 );
} );

function sandbox_snapshots_render() {
	if ( ! current_user_can( 'manage_options' ) ) { wp_die( 'Forbidden' ); }
	$nonce = wp_create_nonce( 'sandbox_snapshots' );
	echo '<div class="wrap"><h1>Sandbox Snapshots &mdash; <code>' . esc_html( SANDBOX_INSTANCE ) . '</code></h1>';
	echo '<p>Capture or roll back this instance\'s database + uploads (runs on the sandbox host).</p>';
	echo '<p><input type="text" id="sbx-name" class="regular-text" placeholder="snapshot name (optional)"> ';
	echo '<button class="button button-primary" id="sbx-take">Take snapshot</button> ';
	echo '<label><input type="checkbox" id="sbx-force"> overwrite</label> ';
	echo '<label title="Capture the database only (skip the uploads archive)"><input type="checkbox" id="sbx-dbonly"> DB only</label></p>';
	echo '<p><button class="button" id="sbx-reset">Reset to fresh install</button> <span class="description">Restores the post-install database baseline; uploads are kept.</span></p>';
	echo '<div id="sbx-msg" style="margin:8px 0"></div>';
	echo '<table class="widefat striped" id="sbx-table"><thead><tr><th>Name</th><th>Size</th><th>Type</th><th>Meta</th><th></th></tr></thead><tbody></tbody></table>';
	echo '</div>';
	$ajax = esc_url( admin_url( 'admin-ajax.php' ) );
	?>
<script>
(function(){
  var AJAX=<?php echo wp_json_encode( $ajax ); ?>, NONCE=<?php echo wp_json_encode( $nonce ); ?>;
  var msg=document.getElementById('sbx-msg'), tb=document.querySelector('#sbx-table tbody');
  function call(op, extra){ var d=new URLSearchParams(Object.assign({action:'sandbox_snap',nonce:NONCE,op:op},extra||{}));
    return fetch(AJAX,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:d}).then(function(r){return r.json();}); }
  function say(t,err){ msg.textContent=t; msg.style.color=err?'#b32d2e':'#2271b1'; }
  function poll(jid){ return call('job',{job_id:jid}).then(function(j){
    if(j.status==='succeeded'){say('Done.');return refresh();}
    if(j.status==='failed'){say('Failed: '+(j.detail||''),true);return;}
    say('Working… ('+(j.status||'running')+')'); return new Promise(function(res){setTimeout(res,1500);}).then(function(){return poll(jid);}); }); }
  function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function refresh(){ return call('list').then(function(r){ tb.innerHTML='';
    (r.snapshots||[]).forEach(function(s){ var tr=document.createElement('tr'); var n=esc(s.name);
      var mode=s.mode||((/mode=([\w-]+)/.exec(s.meta||'')||[])[1])||'';
      tr.innerHTML='<td>'+n+'</td><td>'+(parseInt(s.size_kb)||0)+' KB</td><td>'+esc(mode)+'</td><td>'+esc(s.meta)+'</td>'+
        '<td><button class="button" data-r="'+n+'">Restore</button> <button class="button" data-d="'+n+'">Delete</button></td>';
      tb.appendChild(tr); }); }); }
  document.getElementById('sbx-take').onclick=function(){ var n=document.getElementById('sbx-name').value;
    say('Taking snapshot…'); call('take',{name:n,force:document.getElementById('sbx-force').checked?1:'',db_only:document.getElementById('sbx-dbonly').checked?1:''}).then(function(r){
      if(r.job_id){return poll(r.job_id);} say(r.error||'error',true); }); };
  document.getElementById('sbx-reset').onclick=function(){
    if(!confirm('Reset this database to its fresh-install baseline? Current posts, settings, and users will be replaced; uploads are kept.')){return;}
    say('Resetting to fresh install…'); call('reset').then(function(r){
      if(r.job_id){return poll(r.job_id);} say(r.error||'error',true); }); };
  tb.addEventListener('click',function(e){ var r=e.target.getAttribute('data-r'), d=e.target.getAttribute('data-d');
    if(r&&confirm('Restore '+r+'? This REPLACES the current DB + uploads.')){ say('Restoring…');
      call('restore',{name:r}).then(function(x){ if(x.job_id){return poll(x.job_id);} say(x.error||'error',true); }); }
    if(d&&confirm('Delete snapshot '+d+'?')){ call('delete',{name:d}).then(function(){refresh();}); } });
  refresh();
})();
</script>
	<?php
}
'''


PROJECT_MCP_JSON = ROOT / ".mcp.json"


SECRETS_ENV = _base_file(".env.local", ROOT / ".env.local")

ENV_LOCAL = SECRETS_ENV  # spec-009 canonical name (alias of SECRETS_ENV)


CONNECT_TARGETS = {
    "fb": "fluentboards", "fluentboards": "fluentboards",
    "gh": "github", "github": "github",
    "cloudflare": "cloudflare",
}


INTROSPECT_PHP = {
"blocks": r"""<?php
$reg = WP_Block_Type_Registry::get_instance();
$out = [];
foreach ($reg->get_all_registered() as $name => $b) {
    $out[] = [
        'name'       => $name,
        'title'      => $b->title ?? '',
        'category'   => $b->category ?? '',
        'attributes' => $b->attributes ?? [],
        'supports'   => $b->supports ?? [],
        'dynamic'    => !empty($b->render_callback),
        'parent'     => $b->parent ?? null,
        'ancestor'   => $b->ancestor ?? null,
    ];
}
echo wp_json_encode(['count' => count($out), 'blocks' => $out], JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
""",

"widgets": r"""<?php
if (!class_exists('\Elementor\Plugin')) {
    echo wp_json_encode(['error' => 'Elementor not active']);
    return;
}
$mgr = \Elementor\Plugin::$instance->widgets_manager;
$out = [];
foreach ($mgr->get_widget_types() as $name => $w) {
    // get_controls() / get_title() avoid get_config()'s full bootstrap which
    // sometimes faults outside an editor request context.
    $controls = [];
    try { $raw = $w->get_controls(); } catch (\Throwable $e) { $raw = []; }
    foreach ((array)$raw as $cname => $c) {
        if (!is_array($c)) continue;
        $entry = ['type' => $c['type'] ?? null];
        if (isset($c['default']))   $entry['default']  = $c['default'];
        if (isset($c['options']))   $entry['options']  = is_array($c['options']) ? array_keys($c['options']) : $c['options'];
        if (isset($c['label']))     $entry['label']    = is_string($c['label']) ? wp_strip_all_tags($c['label']) : '';
        if (isset($c['fields']))    $entry['fields']   = array_keys((array)$c['fields']);
        if (!empty($c['condition'])) $entry['condition'] = $c['condition'];
        if (!empty($c['classes']))  $entry['classes']  = $c['classes'];   // surfaces Pro-only flags
        $controls[$cname] = $entry;
    }
    try { $title = $w->get_title(); } catch (\Throwable $e) { $title = ''; }
    try { $cats  = $w->get_categories(); } catch (\Throwable $e) { $cats = []; }
    $out[] = [
        'name'       => $name,
        'title'      => is_string($title) ? wp_strip_all_tags($title) : '',
        'categories' => $cats,
        'controls'   => $controls,
    ];
}
echo wp_json_encode(['count' => count($out), 'widgets' => $out], JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
""",

"shortcodes": r"""<?php
$out = [];
foreach ($GLOBALS['shortcode_tags'] ?? [] as $tag => $cb) {
    $callback = '';
    if (is_string($cb))         $callback = $cb;
    elseif (is_array($cb))      $callback = (is_object($cb[0]) ? get_class($cb[0]) : (string)$cb[0]) . '::' . $cb[1];
    elseif ($cb instanceof Closure) $callback = 'Closure';
    $out[] = ['tag' => $tag, 'callback' => $callback];
}
echo wp_json_encode(['count' => count($out), 'shortcodes' => $out], JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
""",
}


TEST_SUITE_DIR = RUNTIME_DIR / "test-suite"


TEST_TOOLS_DIR = RUNTIME_DIR / "test-tools"


_WPDEVELOP_REPO = "https://github.com/WordPress/wordpress-develop.git"


_PHPUNIT_PHAR_URL = "https://phar.phpunit.de/phpunit-9.phar"


_COMPOSER_PHAR_URL = "https://getcomposer.org/composer-stable.phar"


_POLYFILLS_REPO = "https://github.com/Yoast/PHPUnit-Polyfills.git"


_POLYFILLS_TAG = "2.0.1"


TESTS_DB_NAME = "wp_tests"


_web_lock = threading.Lock()


_web_jobs: dict = {}


_web_job_seq = [0]


_web_jobs_lock = threading.Lock()


_CLAUDE_PRICES = {
    "opus":   {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.50},
    "sonnet": {"in": 3.0,  "out": 15.0, "cw": 3.75,  "cr": 0.30},
    "haiku":  {"in": 0.80, "out": 4.0,  "cw": 1.00,  "cr": 0.08},
}


_WEB_CSS_CACHE = [None]


_WEB_JS_CACHE = [None]


_WEB_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sandbox</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Vendored, pre-built Tailwind CSS (no CDN, works offline). Rebuild with
     scripts/build-web-css.sh after changing classes in this page. -->
<style>__SANDBOX_WEB_CSS__</style>
<style>
  /* ---- desktop-app feel (overrides on top of Tailwind) ---- */
  html, body { -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
  * { transition: background-color .14s ease, border-color .14s ease, color .12s ease,
        box-shadow .14s ease, transform .08s ease; }
  ::-webkit-scrollbar { width: 9px; height: 9px; }
  ::-webkit-scrollbar-thumb { background: #8884; border-radius: 9999px; border: 2px solid transparent;
    background-clip: padding-box; }
  ::-webkit-scrollbar-thumb:hover { background: #8888; background-clip: padding-box; }
  .spin { animation: sp 0.7s linear infinite; }
  @keyframes sp { to { transform: rotate(360deg); } }

  /* Flatten the pill buttons into crisp desktop controls + give them depth.
     Targets the action buttons rendered with rounded-full / rounded in the JS. */
  button[disabled] { opacity: .4; cursor: default; }
  main button:not([disabled]):active, footer button:not([disabled]):active { transform: translateY(0.5px); }
  /* pill action buttons → desktop radius + subtle shadow */
  .rounded-full { border-radius: 7px !important; }
  main .rounded-full, footer .rounded-full, aside .rounded-full {
    box-shadow: 0 1px 0 rgba(0,0,0,.03); }
  /* primary (accent) buttons get a soft raised shadow */
  .bg-accent { box-shadow: 0 1px 2px rgba(37,99,235,.35), inset 0 1px 0 rgba(255,255,255,.12); }

  /* sidebar rows: tighter, app-like selection */
  #list button { border-radius: 7px; }

  /* console drawer: terminal vibe */
  #conBody { background:
    linear-gradient(180deg, rgba(255,255,255,.015), transparent 120px); }

  /* fade-in for panel content swaps */
  #detail > * { animation: fadein .18s ease; }
  @keyframes fadein { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }
</style></head>
<body class="font-sans bg-page dark:bg-page-dark text-ink dark:text-ink-dark antialiased h-screen overflow-hidden flex flex-col">

<!-- Desktop title bar (window chrome) -->
<div class="h-9 shrink-0 flex items-center px-3.5 gap-2 border-b border-brd dark:border-brd-dark
     bg-neutral-100/80 dark:bg-neutral-900/80 backdrop-blur select-none">
  <span class="w-3 h-3 rounded-full" style="background:#ff5f57"></span>
  <span class="w-3 h-3 rounded-full" style="background:#febc2e"></span>
  <span class="w-3 h-3 rounded-full" style="background:#28c840"></span>
  <div class="flex-1 text-center text-[12px] font-medium text-neutral-500 dark:text-neutral-400">
    Sandbox — WordPress dev environments</div>
  <span class="w-12"></span>
</div>

<div class="flex flex-1 min-h-0">
  <!-- Sidebar: instance list (Local-style) -->
  <aside class="w-60 shrink-0 border-r border-brd dark:border-brd-dark
                bg-neutral-100/60 dark:bg-neutral-950 flex flex-col">
    <button onclick="goHome()" title="What is this?"
      class="h-12 px-3.5 flex items-center gap-2 border-b border-brd dark:border-brd-dark
             w-full hover:bg-neutral-200/50 dark:hover:bg-neutral-900 text-left">
      <div class="w-5 h-5 rounded-md bg-accent flex items-center justify-center
                  text-white text-[12px] font-bold">S</div>
      <span class="font-semibold text-[13px] text-neutral-900 dark:text-neutral-50">Sandbox</span>
      <span id="runcount" class="ml-auto text-[11px] text-neutral-400"></span>
    </button>
    <div class="px-3 pt-3 pb-1 text-[11px] font-medium uppercase tracking-wide
                text-neutral-400">Instances</div>
    <nav id="list" class="flex-1 overflow-auto px-2 pb-2 space-y-0.5"></nav>
    <div class="p-2 border-t border-brd dark:border-brd-dark space-y-0.5">
      <button id="newBtn" class="w-full text-[13px] px-3 py-2 rounded
        text-neutral-600 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800
        flex items-center gap-2">
        <span class="text-[15px] leading-none">+</span> New instance</button>
      <button id="termBtn" class="w-full text-[13px] px-3 py-2 rounded text-left
        text-neutral-500 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800
        flex items-center gap-2">
        <span class="text-[13px] leading-none font-mono">›_</span> Terminal</button>
      <button id="usageBtn" class="w-full text-[13px] px-3 py-2 rounded text-left
        text-neutral-500 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800
        flex items-center gap-2">
        <span class="text-[13px] leading-none">◴</span> Claude usage</button>
      <button id="helpBtn" class="w-full text-[13px] px-3 py-2 rounded text-left
        text-neutral-500 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800
        flex items-center gap-2">
        <span class="text-[14px] leading-none">?</span> Using Claude</button>
    </div>
  </aside>

  <!-- Detail panel -->
  <div class="flex-1 flex flex-col min-w-0">
    <main id="detail" class="flex-1 overflow-auto"></main>
    <!-- Footer bar -->
    <footer class="h-12 shrink-0 border-t border-brd dark:border-brd-dark
       bg-app dark:bg-card-dark px-5 flex items-center gap-3 text-[12.5px]">
      <span id="footstat" class="text-neutral-500 dark:text-neutral-400"></span>
      <div class="ml-auto flex items-center gap-2">
        <button id="startAll" class="px-3 py-1 rounded border border-brd dark:border-neutral-700
          text-neutral-700 dark:text-neutral-300 hover:bg-neutral-50 dark:hover:bg-neutral-800">
          Start all</button>
        <button id="stopAll" class="px-3 py-1 rounded border border-red-200 dark:border-red-900/60
          text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40">
          Stop all</button>
      </div>
    </footer>
  </div>

  <!-- Console: right-side drawer, slides in. Width animates 0 → 26rem. -->
  <div id="console" class="shrink-0 w-0 overflow-hidden border-l border-neutral-800
       bg-neutral-950 flex flex-col transition-[width] duration-300 ease-out">
    <div class="w-[26rem] flex flex-col h-full">
      <div class="h-14 px-4 flex items-center gap-2 border-b border-neutral-800 shrink-0">
        <span id="conDot" class="w-2 h-2 rounded-full bg-neutral-500"></span>
        <span id="conTitle" class="text-neutral-200 text-[13px] font-medium truncate flex-1">Activity</span>
        <button id="conClose" class="text-neutral-500 hover:text-neutral-200 text-[18px] leading-none">×</button>
      </div>
      <pre id="conBody" class="flex-1 overflow-auto px-4 py-3 text-[11.5px] leading-relaxed
        font-mono text-neutral-300 whitespace-pre-wrap"></pre>
      <!-- Interactive terminal input (runs in the selected instance's container) -->
      <div id="conInputRow" class="hidden shrink-0 border-t border-neutral-800 flex items-center gap-1.5 px-3 py-2">
        <span class="text-emerald-400 font-mono text-[12px]">›</span>
        <input id="conInput" spellcheck="false" autocomplete="off"
          placeholder="wp plugin list   ·   or any shell command"
          class="flex-1 min-w-0 bg-transparent text-neutral-100 font-mono text-[12px] outline-none placeholder:text-neutral-600">
      </div>
    </div>
  </div>
</div>

<!-- Modal -->
<div id="modal" class="hidden fixed inset-0 z-50 flex items-center justify-center
     bg-black/40 backdrop-blur-sm p-4">
  <div class="bg-app dark:bg-card-dark border border-brd dark:border-brd-dark rounded-lg
       shadow-xl w-full max-w-md p-5 flex flex-col gap-3.5 max-h-[85vh]">
    <h2 id="mTitle" class="text-[15px] font-semibold text-neutral-900 dark:text-neutral-50"></h2>
    <p id="mDesc" class="text-[13px] text-neutral-500 dark:text-neutral-400 leading-snug"></p>
    <div id="mFields" class="flex flex-col gap-2.5 overflow-y-auto -mx-1 px-1"></div>
    <div class="flex justify-end gap-2 pt-1">
      <button id="mCancel" class="px-3 py-1.5 rounded border border-brd dark:border-neutral-700
         text-[13px] text-neutral-600 dark:text-neutral-300
         hover:bg-neutral-50 dark:hover:bg-neutral-800">Cancel</button>
      <button id="mOk" class="px-3 py-1.5 rounded text-[13px] text-white
         bg-accent border border-accent hover:bg-blue-700">Confirm</button>
    </div>
  </div>
</div>

<div id="toasts" class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 items-end"></div>

<script>__SANDBOX_WEB_JS__</script></body></html>"""
