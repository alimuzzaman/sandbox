# Contract: Runtime Service

1. Resolve a side-effect-free project descriptor.
2. Resolve canonical project/label identity through the repository.
3. Select exactly one registered adapter.
4. Verify the requested capability from adapter code.
5. Invoke the adapter with injected dependencies.
6. Return a stable result or structured error.

Unsupported kind/capability errors occur before subprocess, HTTP, proxy, filesystem, database, mail, WordPress, or registry-write effects. WordPress uses a compatibility adapter until parity allows separately approved migration.
