<?php
/**
 * Plugin Name: Sandbox Native Proof
 * Description: Live-only managed-native hostile-boundary probe.
 * Version: 1.1.0
 */

defined('ABSPATH') || exit;

function sandbox_native_proof_socket(string $target): bool {
    $errorNumber = 0;
    $errorMessage = '';
    $socket = @stream_socket_client($target, $errorNumber, $errorMessage, 0.25, STREAM_CLIENT_CONNECT);
    if (is_resource($socket)) {
        fclose($socket);
        return true;
    }
    return false;
}

function sandbox_native_proof_context(): array {
    $context = get_option('sandbox_native_proof_context', []);
    if (is_string($context)) {
        $decoded = json_decode($context, true);
        $context = is_array($decoded) ? $decoded : [];
    }
    return is_array($context) ? $context : [];
}

function sandbox_native_proof_observe(): array {
    $context = sandbox_native_proof_context();
    $hostPid = (int) ($context['host_pid'] ?? -1);
    $siblingPid = (int) ($context['sibling_pid'] ?? -1);
    $siblingIpc = (int) ($context['sibling_ipc'] ?? -1);
    $hostIpc = (int) ($context['host_ipc'] ?? -1);
    $siblingRoot = (string) ($context['sibling_root'] ?? '/nonexistent-sibling');
    $hostHome = (string) ($context['host_home'] ?? '/nonexistent-host-home');
    $hostVeth = (string) ($context['host_veth'] ?? '');
    $hostVethPort = (int) ($context['host_veth_port'] ?? 0);
    $siblingAddress = (string) ($context['sibling_address'] ?? '');
    $siblingPort = (int) ($context['sibling_port'] ?? 0);
    $sourceProbe = '/workspace/.native-source-write';
    $sourceWrite = @file_put_contents($sourceProbe, 'must-not-land') !== false;
    if ($sourceWrite) {
        @unlink($sourceProbe);
    }
    $hostProcess = $hostPid > 0 ? @file_get_contents("/proc/{$hostPid}/cmdline") : false;
    $siblingProcess = $siblingPid > 0 ? @file_get_contents("/proc/{$siblingPid}/cmdline") : false;
    $public = wp_remote_get('https://example.com/', ['timeout' => 2]);
    $metadata = wp_remote_get('http://169.254.169.254/', ['timeout' => 1]);
    $rawSocket = false;
    if (function_exists('socket_create')) {
        $raw = @socket_create(AF_INET, SOCK_RAW, 1);
        $rawSocket = $raw !== false;
        if ($raw !== false) {
            @socket_close($raw);
        }
    }
    $userns = false;
    if (is_executable('/usr/bin/unshare')) {
        $output = [];
        $status = 1;
        @exec('/usr/bin/unshare --user --map-root-user /usr/bin/true 2>/dev/null', $output, $status);
        $userns = $status === 0;
    }
    return [
        'source_write' => $sourceWrite,
        'symlink_escape' => @file_get_contents('/workspace/.native-host-escape') !== false,
        'sibling_source_read' => @file_get_contents($siblingRoot . '/sandbox.config.json') !== false,
        'host_home_read' => @scandir($hostHome) !== false,
        'host_control_read' => is_dir('/run/host') || is_readable('/run/systemd/private')
            || is_readable('/run/dbus/system_bus_socket'),
        'host_process_visible' => $hostProcess !== false && str_contains($hostProcess, 'live_native_acceptance'),
        'host_process_signal' => $hostPid > 0 && function_exists('posix_kill') ? @posix_kill($hostPid, 0) : false,
        'sibling_process_visible' => $siblingProcess !== false && str_contains($siblingProcess, 'native-boundary-proof.php'),
        'sibling_process_signal' => $siblingPid > 0 && function_exists('posix_kill') ? @posix_kill($siblingPid, 0) : false,
        'host_ipc_visible' => $hostIpc > 0 && function_exists('msg_queue_exists')
            ? @msg_queue_exists($hostIpc) : false,
        'sibling_ipc_visible' => $siblingIpc > 0 && function_exists('msg_queue_exists')
            ? @msg_queue_exists($siblingIpc) : false,
        'device_open' => @fopen('/dev/mem', 'rb') !== false || @fopen('/dev/kmsg', 'rb') !== false,
        'control_socket_open' => sandbox_native_proof_socket('unix:///run/systemd/private')
            || sandbox_native_proof_socket('unix:///run/dbus/system_bus_socket')
            || sandbox_native_proof_socket('unix:///var/run/docker.sock'),
        'instance_db_socket' => sandbox_native_proof_socket('unix:///var/run/mysqld/mysqld.sock'),
        'raw_socket' => $rawSocket,
        'new_user_namespace' => $userns,
        'metadata_reachable' => ! is_wp_error($metadata),
        'private_reachable' => sandbox_native_proof_socket('tcp://127.0.0.1:22'),
        'host_veth_reachable' => $hostVeth !== '' && $hostVethPort > 0
            ? sandbox_native_proof_socket("tcp://{$hostVeth}:{$hostVethPort}") : false,
        'sibling_address_reachable' => $siblingAddress !== '' && $siblingPort > 0
            ? sandbox_native_proof_socket("tcp://{$siblingAddress}:{$siblingPort}") : false,
        'host_veth_target' => $hostVeth,
        'host_veth_port' => $hostVethPort,
        'sibling_address_target' => $siblingAddress,
        'public_reachable' => ! is_wp_error($public),
        'credential_read' => @file_get_contents('/run/credentials/sandbox/db-credential') !== false,
        'effective_uid' => function_exists('posix_geteuid') ? posix_geteuid() : null,
    ];
}

add_action('rest_api_init', static function (): void {
    register_rest_route('sandbox-native-proof/v1', '/isolation', [
        'methods' => 'GET',
        'callback' => static fn () => rest_ensure_response(sandbox_native_proof_observe()),
        'permission_callback' => '__return_true',
    ]);
});

add_action('template_redirect', static function (): void {
    if (! isset($_GET['sandbox-native-proof'])) {
        return;
    }
    $encoded = base64_encode(wp_json_encode(sandbox_native_proof_observe()));
    nocache_headers();
    header('Content-Type: text/html; charset=UTF-8');
    echo '<!doctype html><html><head><title>sandbox-native-proof:'
        . esc_html($encoded) . '</title></head><body>managed native proof</body></html>';
    exit;
});

add_action('sandbox_native_proof_cron', static function (): void {
    update_option('sandbox_native_proof_cron_result', sandbox_native_proof_observe(), false);
});

register_activation_hook(__FILE__, static function (): void {
    update_option('sandbox_native_proof_activation_result', sandbox_native_proof_observe(), false);
});
