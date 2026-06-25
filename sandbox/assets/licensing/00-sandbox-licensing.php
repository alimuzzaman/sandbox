<?php
/**
 * Sandbox Pro-license activation loader (spec 013).
 *
 * Modular by design: this loader reads the per-instance state file
 * `sandbox-licensing.json` (written by provisioning from the central key store)
 * and includes ONE platform module per supported vendor from
 * `sandbox-licensing/platforms/<name>.php`. Each platform module self-gates on
 * its own key and registers its own activation/interception. Adding a new
 * platform = drop a new file in platforms/ and list it here.
 *
 * Secrets: the keys live in the gitignored runtime state file, never in the
 * repo. With no key set, every platform module no-ops → today's behavior.
 *
 * Dev/staging only. Loaded as an mu-plugin.
 */

if (! defined('ABSPATH')) {
    exit;
}

$sandbox_lic_state = __DIR__ . '/sandbox-licensing.json';
if (! is_file($sandbox_lic_state)) {
    return; // no licensing configured → no-op (additive)
}

$SANDBOX_LICENSING = json_decode((string) file_get_contents($sandbox_lic_state), true);
if (! is_array($SANDBOX_LICENSING)) {
    return;
}

// Registered platform modules, in load order. Each self-gates on its key.
$sandbox_lic_platforms = array('wpdeveloper', 'elementor');

foreach ($sandbox_lic_platforms as $sandbox_lic_platform) {
    $sandbox_lic_file = __DIR__ . '/sandbox-licensing/platforms/' . $sandbox_lic_platform . '.php';
    if (is_file($sandbox_lic_file)) {
        require $sandbox_lic_file; // platform reads $SANDBOX_LICENSING from this scope
    }
}
