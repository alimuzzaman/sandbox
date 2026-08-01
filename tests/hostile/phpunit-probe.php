<?php
use PHPUnit\Framework\TestCase;
final class ManagedNativeIsolationProbeTest extends TestCase {
    public function testHostPathsAreInvisible(): void {
        $this->assertFalse(is_readable('/proc/1/root/etc/shadow'));
        $this->assertFalse(is_readable('/run/docker.sock'));
    }
}
