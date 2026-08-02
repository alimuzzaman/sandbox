<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

final class NativeBoundaryTest extends TestCase {
    public function test_hostile_payload_remains_inside_phpunit_boundary(): void {
        $contextPath = dirname(__DIR__) . '/.sandbox-native-proof-context.json';
        self::assertFileIsReadable($contextPath);
        $context = json_decode((string) file_get_contents($contextPath), true, 512, JSON_THROW_ON_ERROR);
        $required = [
            'host_pid' => 'integer', 'sibling_pid' => 'integer',
            'sibling_root' => 'string', 'host_home' => 'string',
            'host_ipc' => 'integer', 'sibling_ipc' => 'integer',
            'host_veth' => 'string', 'host_veth_port' => 'integer',
            'sibling_address' => 'string', 'sibling_port' => 'integer',
        ];
        foreach ($required as $key => $type) {
            self::assertArrayHasKey($key, $context);
            self::assertSame($type, gettype($context[$key]), $key . ' has the wrong type');
        }
        $arguments = ['boundary'];
        foreach ([
            '--host-pid' => 'host_pid', '--sibling-pid' => 'sibling_pid',
            '--sibling-root' => 'sibling_root', '--host-home' => 'host_home',
            '--host-ipc' => 'host_ipc', '--sibling-ipc' => 'sibling_ipc',
            '--host-veth' => 'host_veth', '--host-veth-port' => 'host_veth_port',
            '--sibling-address' => 'sibling_address', '--sibling-port' => 'sibling_port',
        ] as $flag => $key) {
            $arguments[] = $flag;
            $arguments[] = (string) $context[$key];
        }
        $command = escapeshellarg(PHP_BINARY) . ' '
            . escapeshellarg(dirname(__DIR__) . '/native-boundary-proof.php') . ' '
            . implode(' ', array_map('escapeshellarg', $arguments));
        $lines = [];
        $status = 1;
        exec($command, $lines, $status);
        self::assertSame(0, $status);
        $body = json_decode((string) end($lines), true, 512, JSON_THROW_ON_ERROR);
        foreach ([
            'source_write', 'symlink_escape', 'sibling_source_read', 'host_home_read',
            'host_control_read', 'host_process_visible', 'host_process_signal',
            'sibling_process_visible', 'sibling_process_signal', 'host_ipc_visible',
            'sibling_ipc_visible', 'device_open', 'control_socket_open',
            'raw_socket', 'new_user_namespace',
            'metadata_reachable', 'private_reachable', 'host_veth_reachable',
            'sibling_address_reachable', 'public_reachable', 'credential_read',
        ] as $field) {
            self::assertFalse($body[$field], $field . ' escaped the PHPUnit boundary');
        }
        self::assertNotFalse(filter_var($body['host_veth_target'], FILTER_VALIDATE_IP));
        self::assertNotFalse(filter_var($body['sibling_address_target'], FILTER_VALIDATE_IP));
        self::assertGreaterThan(0, $body['host_veth_port']);
        self::assertTrue($body['instance_db_socket'], 'instance-local DB socket is unavailable');
        self::assertSame(33, $body['effective_uid']);
    }
}
