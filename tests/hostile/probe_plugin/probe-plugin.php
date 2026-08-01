<?php
/** Plugin Name: Managed Native Isolation Probe */
register_activation_hook(__FILE__, static function (): void {
    foreach (['/etc/shadow', '/proc/1/root/etc/shadow', '/run/docker.sock'] as $target) {
        if (@file_get_contents($target) !== false) {
            throw new RuntimeException('managed-native isolation escape: ' . $target);
        }
    }
});
