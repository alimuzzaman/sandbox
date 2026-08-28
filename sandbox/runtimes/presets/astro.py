from __future__ import annotations

import json
import re
from pathlib import Path

from sandbox.config.init_files import (
    atomic_write_init_file, validate_init_destination,
)


def propose_astro(root: str | Path) -> dict:
    """Inspect package metadata and write only explicit, reviewable preset files.

    Detection never imports the project or runs npm/package scripts. The
    generated Compose command is executed only after the user invokes ensure.
    """
    root = Path(root).expanduser().resolve()
    package_path = root / "package.json"
    if not package_path.is_file():
        raise ValueError("Astro initialization requires package.json")
    document = json.loads(package_path.read_text())
    scripts = document.get("scripts") if isinstance(document, dict) else None
    if not isinstance(scripts, dict) or not isinstance(scripts.get("dev"), str) or not scripts["dev"].strip():
        raise ValueError("Astro initialization requires an explicit package.json scripts.dev command")
    manager = "npm"
    install = "npm ci" if (root / "package-lock.json").is_file() else "npm install"
    if (root / "pnpm-lock.yaml").is_file():
        manager, install = "pnpm", "pnpm install --frozen-lockfile"
    elif (root / "yarn.lock").is_file():
        manager, install = "yarn", "yarn install --frozen-lockfile"
    command = f"{manager} run dev -- --host 0.0.0.0"
    port = 4321
    config = root / "astro.config.mjs"
    if config.is_file():
        match = re.search(r"\bport\s*:\s*(\d{2,5})", config.read_text())
        if match:
            port = int(match.group(1))
    compose = root / "sandbox.compose.yml"
    compose_payload = (
        "services:\n"
        "  web:\n"
        "    image: node:22-alpine\n"
        "    working_dir: /app\n"
        "    volumes:\n"
        "      - .:/app\n"
        "    command: [\"sh\", \"-lc\", "
        f"\"{install} && {command}\"]\n"
    )
    descriptor = {
        "kind": "compose", "framework": "astro",
        "compose": {"file": compose.name, "service": "web", "internal_port": port, "health_path": "/"},
        "preset": {"package_manager": manager, "install_command": install, "dev_command": command},
    }
    descriptor_path = root / "sandbox.config.json"
    validate_init_destination(root, compose)
    validate_init_destination(root, descriptor_path)
    atomic_write_init_file(root, compose, compose_payload)
    atomic_write_init_file(
        root, descriptor_path, json.dumps(descriptor, indent=2) + "\n"
    )
    return descriptor
