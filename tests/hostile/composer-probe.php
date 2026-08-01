<?php
foreach (['/etc/shadow', '/proc/1/root/etc/shadow', '/run/docker.sock'] as $target) {
    if (@file_get_contents($target) !== false) { exit(70); }
}
