# Compose live-stack regression evidence

Date: 2026-08-02
Branch: `latest`
Registered instance: `sandbox`
Runtime: Compose, nginx, WordPress PHP 8.3 FPM

## Result

The existing registered Compose instance remained healthy after the native
runtime composition changes. All runtime operations below went through `./sb`;
no raw Docker command was used.

```text
./sb ensure --project-dir . --json
  existing db/wp containers retained; mailpit and nginx converged healthy

./sb status --project-dir .
  exit 0; db healthy; nginx, WordPress, and Mailpit running
  elapsed 2.09 seconds

./sb wp core version
  exit 0; WordPress 7.0.2
  elapsed 1.41 seconds

./sb test --project-dir . --timeout 300 unit -- \
  --configuration tests/fixtures/pure-unit/phpunit.xml.dist \
  tests/fixtures/pure-unit/tests
  exit 0; OK (1 test, 1 assertion)
  elapsed 2.06 seconds
```

Running `./sb test --project-dir .` without an explicit test target correctly
reported PHPUnit usage because this tooling repository has no root PHP test
configuration. Repeating the operation with the repository's declared pure-unit
fixture proved the Compose test gateway rather than treating the missing test
target as a runtime failure.

The observed status retained the Compose capability envelope and reported only
the pre-existing optional gaps (`logs`, `stop`, WordPress debug, multisite, and
server switch). No native machine, image, policy, network, credential, or route
was selected or created by these operations.
