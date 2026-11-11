# Research

Accepted initial evidence report: /Users/alim/Sites/git/.scratch/sandbox/shared-server-installation/plan.txt.
Named server metadata shows all persistent candidate parents share /dev/sda2 ext4,
223G available, so path choice follows authority/purpose rather than assumed disk
advantages. /run is separate noexec tmpfs. No content/secret scan or host mutation.

FHS /var/lib private application state, /srv exposed service content, /usr/local
administrator-installed software, /etc policy, /run ephemeral files justify the
accepted split. /opt alternative requires coherent /etc/opt and /var/opt treatment.
Sources: https://specifications.freedesktop.org/fhs/latest/varLib.html,
https://specifications.freedesktop.org/fhs/latest/srv.html,
https://specifications.freedesktop.org/fhs/latest/usrLocal.html,
https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.exec.xml.

Do not let StateDirectory recursively re-own mixed workload data on service start.
Existing per-user home migration removes verified old data and regenerates derived
paths; it cannot silently become the managed retained-source migration. Existing
SSH staging checks process euid and service units use personal homes. Preserve
checks and add lifecycle authority; chmod/symlinks are insufficient.

Existing authenticated /resources host_memory_status suffices for immediate joined
marker/runtime/machine adoption without server update. Genuine installation IDs
need new explicit lifecycle initialization, never hash of personal context. Existing
runtime compatibility can admit differing hashes via its supported live verdict;
no weakening hash/pin checks and no automatic service migration from hash alone.

085 worker release/listener machinery and086 actor/grant/credential consumption
remain sibling-owned integrations. This feature publishes identity and extension
ports; capability truth requires their actually assembled handler/verifier state.
