"""Safe hosted target components shared by manifests and receipt admission."""
import re

TARGET_PART_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
COMPOSE_PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
