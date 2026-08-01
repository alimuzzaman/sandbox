# Pre-live managed-native proof gate

Date: 2026-08-01  
Branch: `latest`  
Baseline commit: `0bac9cd`

## Result

The managed-native implementation remains `implemented_unproven` and `adoptable: false`.
No package, root image, machine, network rule, host service, or project data was mutated.

`./sb native preflight --json` ran against the current Ubuntu 24.04/systemd 255 host. The
platform, systemd version, cgroup v2, user namespaces, nft binary, ext4 tooling, PID 1,
and AppArmor gates passed. The operation correctly returned `blocked` because
`systemd-nspawn`, `machinectl`, `bwrap`, `debootstrap`, delegated cgroups, a live private
network/default-drop ruleset, and effective payload seccomp have not been proven.

`./sb native install-plan --web-server nginx --json` and the Apache variant resolved exact
host and fresh-image dependency closures from the configured signed Ubuntu Noble archive
and security sources. The plans were observational only and produced distinct confirmation
digests. The image simulation used an empty dpkg status database, so installed host packages
could not disappear from the future image closure.

## Simulated and unprivileged suites

```text
python3 -m unittest discover -s tests -p 'test_native*.py'   -> 22 passed
python3 -m unittest discover -s tests -p 'test_managed*.py'  -> 20 passed
python3 -m unittest discover -s tests -p 'test_isolation*.py' -> 22 passed
runtime/config/transport/composition boundary selection       -> 45 passed
git diff --check                                               -> passed
```

The hostile PHP, shell, activation, Composer, and PHPUnit fixtures exist but were not called
live because no verified OS-container boundary exists yet. Executing them on the host would
violate the no-fallback requirement. Live evidence must install prerequisites through the
confirmed package transaction, create a bounded image, start the minimal nspawn boundary,
apply default-deny networking, verify effective policy, and only then invoke every fixture.
