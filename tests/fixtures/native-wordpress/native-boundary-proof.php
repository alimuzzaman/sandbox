<?php
/** Managed-native hostile-boundary and exhaustion payload. */

declare(strict_types=1);

function sandbox_native_option(array $arguments, string $name, string $default = ''): string {
    $index = array_search($name, $arguments, true);
    return $index !== false && isset($arguments[$index + 1]) ? (string) $arguments[$index + 1] : $default;
}

function sandbox_native_connect(string $host, int $port, float $timeout = 0.25): bool {
    $errno = 0;
    $error = '';
    $socket = @stream_socket_client("tcp://{$host}:{$port}", $errno, $error, $timeout);
    if (is_resource($socket)) {
        fclose($socket);
        return true;
    }
    return false;
}

function sandbox_native_unix(string $path): bool {
    $errno = 0;
    $error = '';
    $socket = @stream_socket_client("unix://{$path}", $errno, $error, 0.25);
    if (is_resource($socket)) {
        fclose($socket);
        return true;
    }
    return false;
}

function sandbox_native_emit(array $value, int $status = 0): never {
    echo json_encode($value, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), "\n";
    exit($status);
}

function sandbox_native_boundary(array $arguments): never {
    $hostPid = (int) sandbox_native_option($arguments, '--host-pid', '-1');
    $siblingPid = (int) sandbox_native_option($arguments, '--sibling-pid', '-1');
    $siblingRoot = sandbox_native_option($arguments, '--sibling-root', '/nonexistent-sibling');
    $hostHome = sandbox_native_option($arguments, '--host-home', '/nonexistent-host-home');
    $hostIpc = (int) sandbox_native_option($arguments, '--host-ipc', '-1');
    $siblingIpc = (int) sandbox_native_option($arguments, '--sibling-ipc', '-1');
    $hostVeth = sandbox_native_option($arguments, '--host-veth', '');
    $hostVethPort = (int) sandbox_native_option($arguments, '--host-veth-port', '0');
    $siblingAddress = sandbox_native_option($arguments, '--sibling-address', '');
    $siblingPort = (int) sandbox_native_option($arguments, '--sibling-port', '0');
    $sourceProbe = '/workspace/.native-source-write';
    $sourceWrite = @file_put_contents($sourceProbe, 'must-not-land') !== false;
    if ($sourceWrite) {
        @unlink($sourceProbe);
    }
    $escape = @file_get_contents('/workspace/.native-host-escape');
    $hostProcess = $hostPid > 0 ? @file_get_contents("/proc/{$hostPid}/cmdline") : false;
    $siblingProcess = $siblingPid > 0 ? @file_get_contents("/proc/{$siblingPid}/cmdline") : false;
    $hostSignal = $hostPid > 0 && function_exists('posix_kill') ? @posix_kill($hostPid, 0) : false;
    $siblingSignal = $siblingPid > 0 && function_exists('posix_kill') ? @posix_kill($siblingPid, 0) : false;
    $ipcVisible = static function (int $key): bool {
        return $key > 0 && function_exists('msg_queue_exists') && @msg_queue_exists($key);
    };
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
    sandbox_native_emit([
        'source_write' => $sourceWrite,
        'symlink_escape' => $escape !== false,
        'sibling_source_read' => @file_get_contents($siblingRoot . '/sandbox.config.json') !== false,
        'host_home_read' => @scandir($hostHome) !== false,
        'host_control_read' => is_dir('/run/host') || is_readable('/run/systemd/private')
            || is_readable('/run/dbus/system_bus_socket'),
        'host_process_visible' => $hostProcess !== false && str_contains($hostProcess, 'live_native_acceptance'),
        'host_process_signal' => $hostSignal,
        'sibling_process_visible' => $siblingProcess !== false && str_contains($siblingProcess, 'native-boundary-proof.php'),
        'sibling_process_signal' => $siblingSignal,
        'host_ipc_visible' => $ipcVisible($hostIpc),
        'sibling_ipc_visible' => $ipcVisible($siblingIpc),
        'device_open' => @fopen('/dev/mem', 'rb') !== false || @fopen('/dev/kmsg', 'rb') !== false,
        'control_socket_open' => sandbox_native_unix('/run/systemd/private')
            || sandbox_native_unix('/run/dbus/system_bus_socket')
            || sandbox_native_unix('/var/run/docker.sock'),
        'instance_db_socket' => sandbox_native_unix('/var/run/mysqld/mysqld.sock'),
        'raw_socket' => $rawSocket,
        'new_user_namespace' => $userns,
        'metadata_reachable' => sandbox_native_connect('169.254.169.254', 80),
        'private_reachable' => sandbox_native_connect('127.0.0.1', 22),
        'host_veth_reachable' => $hostVeth !== '' && $hostVethPort > 0
            ? sandbox_native_connect($hostVeth, $hostVethPort) : false,
        'sibling_address_reachable' => $siblingAddress !== '' && $siblingPort > 0
            ? sandbox_native_connect($siblingAddress, $siblingPort) : false,
        'host_veth_target' => $hostVeth,
        'host_veth_port' => $hostVethPort,
        'sibling_address_target' => $siblingAddress,
        'public_reachable' => sandbox_native_connect('1.1.1.1', 443),
        'credential_read' => @file_get_contents('/run/credentials/sandbox/db-credential') !== false,
        'effective_uid' => function_exists('posix_geteuid') ? posix_geteuid() : null,
    ]);
}

function sandbox_native_hold(array $arguments): never {
    $seconds = max(1, min(120, (int) ($arguments[1] ?? 30)));
    $key = 0x530000 + (getmypid() % 0xffff);
    $queue = function_exists('msg_get_queue') ? @msg_get_queue($key, 0600) : false;
    echo json_encode(['pid' => getmypid(), 'ipc_key' => $queue === false ? null : $key]), "\n";
    if (function_exists('ob_flush')) {
        @ob_flush();
    }
    flush();
    sleep($seconds);
    if ($queue !== false && function_exists('msg_remove_queue')) {
        @msg_remove_queue($queue);
    }
    exit(0);
}

function sandbox_native_cgroup(string $name): ?string {
    foreach (["/sys/fs/cgroup/{$name}", "/sys/fs/cgroup/system.slice/{$name}"] as $path) {
        $value = @file_get_contents($path);
        if ($value !== false) {
            return trim($value);
        }
    }
    return null;
}

function sandbox_native_cgroup_map(string $name): array {
    $raw = sandbox_native_cgroup($name);
    $values = [];
    foreach (preg_split('/\R/', (string) $raw) ?: [] as $line) {
        $parts = preg_split('/\s+/', trim($line)) ?: [];
        if (count($parts) >= 2 && is_numeric($parts[1])) {
            $values[$parts[0]] = (int) $parts[1];
        }
    }
    return $values;
}

function sandbox_native_io_write_bytes(): int {
    $total = 0;
    foreach (preg_split('/\s+/', (string) sandbox_native_cgroup('io.stat')) ?: [] as $part) {
        if (str_starts_with($part, 'wbytes=')) {
            $total += (int) substr($part, 7);
        }
    }
    return $total;
}

function sandbox_native_line(array $value): void {
    echo json_encode($value, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), "\n";
    if (function_exists('ob_flush')) {
        @ob_flush();
    }
    flush();
}

function sandbox_native_inode_free(string $path): ?int {
    $output = [];
    $status = 1;
    @exec('/usr/bin/df -Pi --output=iavail ' . escapeshellarg($path) . ' 2>/dev/null', $output, $status);
    if ($status !== 0 || count($output) < 2) {
        return null;
    }
    $value = trim((string) end($output));
    return ctype_digit($value) ? (int) $value : null;
}

function sandbox_native_fd_soft_limit(): ?int {
    $limits = @file_get_contents('/proc/self/limits');
    if ($limits !== false && preg_match('/^Max open files\s+(\d+)\s+/m', $limits, $matches)) {
        return (int) $matches[1];
    }
    return null;
}

function sandbox_native_resource_observe(string $resource): never {
    sandbox_native_emit([
        'resource' => $resource,
        'memory_events' => sandbox_native_cgroup_map('memory.events'),
        'pids_events' => sandbox_native_cgroup_map('pids.events'),
        'cpu_stat' => sandbox_native_cgroup_map('cpu.stat'),
        'io_write_bytes' => sandbox_native_io_write_bytes(),
    ]);
}

function sandbox_native_resource(string $resource, array $arguments): never {
    $scratchRoot = '/var/lib/sandbox/tmp';
    $scratch = $scratchRoot . '/sandbox-native-resource-' . getmypid();
    if (!is_dir($scratchRoot) || !is_writable($scratchRoot)) {
        sandbox_native_emit(['phase' => 'blocked', 'resource' => $resource,
            'path' => $scratchRoot, 'reason' => 'quota_backed_scratch_unavailable'], 2);
    }
    if ($resource === 'cpu') {
        $before = sandbox_native_cgroup_map('cpu.stat');
        $cpuMax = sandbox_native_cgroup('cpu.max');
        sandbox_native_line(['phase' => 'started', 'resource' => $resource, 'cpu_max' => $cpuMax,
            'cpu_stat' => $before, 'pcntl' => function_exists('pcntl_fork')]);
        if (!function_exists('pcntl_fork')) {
            sandbox_native_emit(['phase' => 'result', 'resource' => $resource,
                'reason' => 'pcntl_unavailable'], 3);
        }
        $children = [];
        for ($worker = 0; $worker < 4; $worker++) {
            $pid = pcntl_fork();
            if ($pid === 0) {
                $until = microtime(true) + 5.0;
                while (microtime(true) < $until) {
                    hash('sha256', random_bytes(4096), true);
                }
                exit(0);
            }
            if ($pid > 0) {
                $children[] = $pid;
            }
        }
        foreach ($children as $pid) {
            pcntl_waitpid($pid, $status);
        }
        $after = sandbox_native_cgroup_map('cpu.stat');
        sandbox_native_emit(['phase' => 'result', 'resource' => $resource,
            'nr_throttled_delta' => ($after['nr_throttled'] ?? 0) - ($before['nr_throttled'] ?? 0),
            'throttled_usec_delta' => ($after['throttled_usec'] ?? 0) - ($before['throttled_usec'] ?? 0)]);
    } elseif ($resource === 'memory') {
        $memoryMax = sandbox_native_cgroup('memory.max');
        sandbox_native_line(['phase' => 'started', 'resource' => $resource,
            'memory_max' => ctype_digit((string) $memoryMax) ? (int) $memoryMax : null,
            'memory_events' => sandbox_native_cgroup_map('memory.events')]);
        @ini_set('memory_limit', '-1');
        $chunks = [];
        while (true) {
            $chunks[] = str_repeat('m', 16 * 1024 * 1024);
        }
    } elseif ($resource === 'pids') {
        $pidsMax = sandbox_native_cgroup('pids.max');
        $before = sandbox_native_cgroup_map('pids.events');
        $pcntl = function_exists('pcntl_fork') && function_exists('pcntl_waitpid');
        sandbox_native_line(['phase' => 'started', 'resource' => $resource,
            'pids_max' => ctype_digit((string) $pidsMax) ? (int) $pidsMax : null,
            'pids_events' => $before, 'pcntl' => $pcntl]);
        if (!$pcntl || !ctype_digit((string) $pidsMax)) {
            sandbox_native_emit(['phase' => 'result', 'resource' => $resource,
                'reason' => 'pcntl_or_pids_limit_unavailable'], 3);
        }
        $children = [];
        $forkFailures = 0;
        for ($index = 0; $index < ((int) $pidsMax + 64); $index++) {
            $pid = @pcntl_fork();
            if ($pid === -1) {
                $forkFailures++;
                break;
            }
            if ($pid === 0) {
                sleep(10);
                exit(0);
            }
            $children[] = $pid;
        }
        foreach ($children as $pid) {
            @posix_kill($pid, SIGTERM);
            @pcntl_waitpid($pid, $status);
        }
        $after = sandbox_native_cgroup_map('pids.events');
        sandbox_native_emit(['phase' => 'result', 'resource' => $resource,
            'forked' => count($children), 'fork_failures' => $forkFailures,
            'pids_max_events_delta' => ($after['max'] ?? 0) - ($before['max'] ?? 0)]);
    } elseif ($resource === 'runtime') {
        $declared = (int) sandbox_native_option($arguments, '--declared-runtime-seconds', '0');
        sandbox_native_line(['phase' => 'started', 'resource' => $resource,
            'declared_runtime_seconds' => $declared]);
        sleep(86400);
    } elseif ($resource === 'disk') {
        $handle = fopen($scratch, 'wb');
        $chunk = str_repeat('d', 1024 * 1024);
        $bytes = 0;
        sandbox_native_line(['phase' => 'started', 'resource' => $resource,
            'path' => $scratchRoot, 'free_bytes' => disk_free_space($scratchRoot)]);
        $writeFailed = false;
        while (is_resource($handle)) {
            $written = @fwrite($handle, $chunk);
            if ($written !== strlen($chunk)) {
                $writeFailed = true;
                break;
            }
            $bytes += $written;
        }
        if (is_resource($handle)) {
            fclose($handle);
        }
        @unlink($scratch);
        sandbox_native_emit(['phase' => 'result', 'resource' => $resource,
            'path' => $scratchRoot, 'write_failed' => $writeFailed, 'bytes_written' => $bytes]);
    } elseif ($resource === 'inodes') {
        @mkdir($scratch, 0700);
        $before = sandbox_native_inode_free($scratchRoot);
        sandbox_native_line(['phase' => 'started', 'resource' => $resource,
            'path' => $scratchRoot, 'free_inodes' => $before]);
        $created = 0;
        $createFailed = false;
        for ($index = 0; $index < 10000000; $index++) {
            if (@touch("{$scratch}/{$index}") === false) {
                $createFailed = true;
                break;
            }
            $created++;
        }
        $after = sandbox_native_inode_free($scratchRoot);
        foreach (glob("{$scratch}/*") ?: [] as $path) {
            @unlink($path);
        }
        @rmdir($scratch);
        sandbox_native_emit(['phase' => 'result', 'resource' => $resource,
            'path' => $scratchRoot, 'create_failed' => $createFailed, 'created' => $created,
            'inodes_consumed' => is_int($before) && is_int($after) ? $before - $after : 0]);
    } elseif ($resource === 'fds') {
        $soft = sandbox_native_fd_soft_limit();
        sandbox_native_line(['phase' => 'started', 'resource' => $resource,
            'fd_soft_limit' => $soft]);
        $handles = [];
        $openFailed = false;
        for ($index = 0; $index < (($soft ?? 1048576) + 64); $index++) {
            $handle = @fopen('/dev/null', 'rb');
            if ($handle === false) {
                $openFailed = true;
                break;
            }
            $handles[] = $handle;
        }
        foreach ($handles as $handle) {
            fclose($handle);
        }
        sandbox_native_emit(['phase' => 'result', 'resource' => $resource,
            'open_failed' => $openFailed, 'opened' => count($handles)]);
    } elseif ($resource === 'connections') {
        $backendAddress = sandbox_native_option($arguments, '--backend-address', '');
        $backendPort = (int) sandbox_native_option($arguments, '--backend-port', '0');
        $connectionLimit = (int) sandbox_native_option($arguments, '--connection-limit', '0');
        $sockets = [];
        $first = $backendAddress !== '' && $backendPort > 0
            ? @stream_socket_client("tcp://{$backendAddress}:{$backendPort}", $errno, $error, .25)
            : false;
        sandbox_native_line(['phase' => 'started', 'resource' => $resource,
            'backend_address' => $backendAddress, 'backend_port' => $backendPort,
            'connection_limit' => $connectionLimit,
            'backend_connected' => is_resource($first)]);
        if (is_resource($first)) {
            fwrite($first, "GET / HTTP/1.1\r\nHost: localhost\r\n");
            $sockets[] = $first;
        }
        $connectionFailed = false;
        for ($index = count($sockets); $index < max(1, $connectionLimit * 8); $index++) {
            $socket = @stream_socket_client("tcp://{$backendAddress}:{$backendPort}", $errno, $error, .05);
            if ($socket === false) {
                $connectionFailed = true;
                break;
            }
            fwrite($socket, "GET / HTTP/1.1\r\nHost: localhost\r\n");
            $sockets[] = $socket;
        }
        $held = count($sockets);
        foreach ($sockets as $socket) {
            fclose($socket);
        }
        sandbox_native_emit(['phase' => 'result', 'resource' => $resource,
            'connection_failed' => $connectionFailed, 'held_connections' => $held]);
    } elseif ($resource === 'io') {
        $duration = max(10, min(120, (int) sandbox_native_option($arguments, '--duration', '60')));
        $scratch = $scratchRoot . '/sandbox-native-io-durable';
        $before = sandbox_native_io_write_bytes();
        $weight = sandbox_native_cgroup('io.weight');
        sandbox_native_line(['phase' => 'started', 'resource' => $resource,
            'path' => $scratchRoot, 'io_weight' => $weight, 'write_bytes' => $before]);
        $handle = fopen($scratch, 'c+b');
        if (!is_resource($handle)) {
            sandbox_native_emit(['phase' => 'blocked', 'resource' => $resource,
                'reason' => 'io_scratch_open_failed'], 3);
        }
        register_shutdown_function(static function () use ($scratch): void {
            @unlink($scratch);
        });
        if (function_exists('pcntl_async_signals') && function_exists('pcntl_signal')) {
            pcntl_async_signals(true);
            pcntl_signal(SIGTERM, static function () use ($scratch): void {
                @unlink($scratch);
                exit(143);
            });
        }
        $until = microtime(true) + $duration;
        $lastProgress = 0.0;
        $chunk = str_repeat('i', 1024 * 1024);
        while (microtime(true) < $until) {
            rewind($handle);
            for ($index = 0; $index < 16; $index++) {
                fwrite($handle, $chunk);
            }
            fflush($handle);
            if (function_exists('fsync')) {
                fsync($handle);
            }
            if (microtime(true) - $lastProgress >= .5) {
                sandbox_native_line(['phase' => 'progress', 'resource' => $resource,
                    'path' => $scratchRoot,
                    'write_bytes_delta' => sandbox_native_io_write_bytes() - $before]);
                $lastProgress = microtime(true);
            }
        }
        fclose($handle);
        $after = sandbox_native_io_write_bytes();
        @unlink($scratch);
        sandbox_native_emit(['phase' => 'result', 'resource' => $resource,
            'path' => $scratchRoot, 'write_bytes_delta' => $after - $before]);
    } else {
        sandbox_native_emit(['phase' => 'blocked', 'resource' => $resource,
            'error' => 'unknown_resource'], 2);
    }
}

$arguments = $argv ?? [];
$contextPath = '/workspace/.sandbox-native-proof-context.json';
if (count($arguments) <= 2 && is_readable($contextPath)) {
    $context = json_decode((string) file_get_contents($contextPath), true);
    if (is_array($context)) {
        $arguments = [$arguments[0] ?? __FILE__, 'boundary',
            '--host-pid', (string) ($context['host_pid'] ?? -1),
            '--sibling-pid', (string) ($context['sibling_pid'] ?? -1),
            '--sibling-root', (string) ($context['sibling_root'] ?? '/nonexistent-sibling'),
            '--host-home', (string) ($context['host_home'] ?? '/nonexistent-host-home'),
            '--host-ipc', (string) ($context['host_ipc'] ?? -1),
            '--sibling-ipc', (string) ($context['sibling_ipc'] ?? -1),
            '--host-veth', (string) ($context['host_veth'] ?? ''),
            '--host-veth-port', (string) ($context['host_veth_port'] ?? 0),
            '--sibling-address', (string) ($context['sibling_address'] ?? ''),
            '--sibling-port', (string) ($context['sibling_port'] ?? 0),
        ];
    }
}
$mode = $arguments[1] ?? 'boundary';
if ($mode === 'hold') {
    sandbox_native_hold(array_slice($arguments, 1));
}
if ($mode === 'resource') {
    sandbox_native_resource((string) ($arguments[2] ?? ''), array_slice($arguments, 3));
}
if ($mode === 'resource-observe') {
    sandbox_native_resource_observe((string) ($arguments[2] ?? ''));
}
sandbox_native_boundary(array_slice($arguments, 1));
