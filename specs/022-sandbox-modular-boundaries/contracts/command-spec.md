# Contract: Command Specification

Each command declares name, aliases, owner, category/order, parser builder, handler, scope, required capability, destructive-confirmation metadata, and optional compatibility identifier.

The composer loads one explicit built-in manifest, sorts deterministically, rejects duplicate names/aliases, builds subparsers, performs shared project/instance resolution and capability preflight, and then invokes the handler. Every current command is represented by a feature-owned specification or named legacy bridge. New parser/routing definitions cannot be added directly to the central CLI module.
