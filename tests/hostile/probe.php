<?php
// Executed through every managed-native PHP path; success means isolation failed.
$targets = ['/etc/shadow', '/proc/1/root/etc/shadow', '/run/docker.sock'];
foreach ($targets as $target) {
    if (@file_get_contents($target) !== false) { fwrite(STDERR, "ESCAPE:$target\n"); exit(70); }
}
exit(0);
