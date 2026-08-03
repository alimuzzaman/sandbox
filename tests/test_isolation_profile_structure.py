"""Structural guards for the managed AppArmor profile.

Seven separate live failures in this feature had one root cause: a rule named a
profile the kernel could not resolve. `cx` names a child of the CURRENT profile,
so `guest`'s `cx -> bwrap` looked for `guest//bwrap`; `@{profile}` emitted an
AppArmor VARIABLE reference that was never defined, so the peer matched nothing.
Each one surfaced two or three layers away as "permission denied", "Can't mount
proc", or an empty probe, and each cost a full provisioning cycle to find.

These tests read the generated policy the way the kernel does, so an
unresolvable name fails here instead of on a host.
"""

from __future__ import annotations

import re
import unittest

from sandbox.isolation.apparmor import compile_apparmor_profile


MACHINE = "sb-0123456789ab"
ROOT_PROFILE = f"sandbox-native-{MACHINE}"
TRANSITION = re.compile(r"->\s*(?P<target>[A-Za-z0-9_./-]+)\s*,\s*$")
PEER = re.compile(r"peer=(?P<peer>[^,\s]+)")


def _profile_bodies(text):
    """{profile_name: [rule, ...]} with nesting resolved to full names."""
    bodies, stack = {}, []
    for line in text.splitlines():
        stripped = line.strip()
        declaration = re.match(r"^profile\s+(?P<name>\S+)\s", stripped)
        if declaration:
            name = declaration.group("name")
            full = name if not stack else f"{stack[-1]}//{name}"
            stack.append(full)
            bodies.setdefault(full, [])
            continue
        if stripped.startswith("}"):
            if stack:
                stack.pop()
            continue
        if stack and stripped and not stripped.startswith("#"):
            bodies[stack[-1]].append(stripped)
    return bodies


class TestProfileNamesResolve(unittest.TestCase):
    def setUp(self):
        self.text = compile_apparmor_profile(MACHINE, "d" * 64)
        self.bodies = _profile_bodies(self.text)

    def test_the_expected_profiles_exist(self):
        self.assertEqual(set(self.bodies), {
            ROOT_PROFILE, f"{ROOT_PROFILE}//guest", f"{ROOT_PROFILE}//bwrap",
            f"{ROOT_PROFILE}//payload",
        })

    def test_no_rule_references_an_apparmor_variable_as_a_peer(self):
        for name, rules in self.bodies.items():
            for rule in rules:
                for match in PEER.finditer(rule):
                    self.assertFalse(
                        match.group("peer").startswith("@"),
                        f"{name}: `{rule}` names a variable, not a profile",
                    )

    def test_every_peer_names_a_profile_that_exists(self):
        for name, rules in self.bodies.items():
            for rule in rules:
                for match in PEER.finditer(rule):
                    peer = match.group("peer").rstrip(",")
                    self.assertIn(peer, self.bodies,
                                  f"{name}: `{rule}` names an unknown profile")

    def test_every_transition_target_resolves(self):
        """`cx` targets a child of the profile holding the rule; anything else
        must be a fully-qualified name."""
        for name, rules in self.bodies.items():
            for rule in rules:
                if "mount" in rule.split("->")[0]:
                    continue  # mount rules use `->` for their target path
                match = TRANSITION.search(rule)
                if not match:
                    continue
                target = match.group("target").replace("//&", "//")
                mode = rule.split("->")[0].split()[-1]
                resolved = f"{name}//{target}" if mode.endswith("cx") else target
                self.assertIn(
                    resolved, self.bodies,
                    f"{name}: `{rule}` resolves to {resolved}, which does not exist",
                )

    def test_every_profile_that_governs_paths_grants_the_root_entry(self):
        """`/**` matches paths BELOW the root, never the root directory entry."""
        for name, rules in self.bodies.items():
            if not any(rule.startswith("/**") for rule in rules):
                continue
            self.assertIn("/ r,", rules,
                          f"{name}: governs paths but cannot open the root entry")

    def test_the_payload_profile_keeps_its_escape_denials(self):
        payload = self.bodies[f"{ROOT_PROFILE}//payload"]
        # `deny userns,` is a denial, not a grant: assert the payload has no
        # GRANT while requiring the explicit denial.
        self.assertNotIn("\n    userns,", payload)
        self.assertIn("deny userns,", payload)
        self.assertNotIn("mount,", payload)
        self.assertNotIn("capability sys_admin,", payload)
        for denial in ("deny /run/systemd/** rwklmx,", "deny /run/dbus/** rwklmx,"):
            self.assertIn(denial, payload)

    def test_the_helper_and_control_plane_still_agree(self):
        import importlib.util
        from pathlib import Path

        source = Path(__file__).resolve().parents[1] / "tools/native-helper/native-helper.py"
        spec = importlib.util.spec_from_file_location("native_helper_profile", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.compile_apparmor_profile(MACHINE, "d" * 64), self.text)


class TestGuardCatchesTheHistoricalMistakes(unittest.TestCase):
    """A guard nobody has seen fail is a guard nobody should trust. These feed
    it the exact policy text that shipped, and require it to object."""

    SHIPPED_WITH_BUGS = """profile sandbox-native-sb-x flags=(attach_disconnected) {
  / r,
  /** rwklm,
  /usr/lib/systemd/systemd cx -> guest,

  profile guest flags=(attach_disconnected) {
    ptrace (read) peer=@sandbox-native-sb-x,
    /** rwklm,
    /usr/bin/bwrap cx -> bwrap,
  }

  profile bwrap flags=(attach_disconnected) {
    /** rwklm,
    /** cx -> payload,
  }

  profile payload flags=(attach_disconnected) {
    /** rwklm,
  }
}
"""

    def setUp(self):
        self.bodies = _profile_bodies(self.SHIPPED_WITH_BUGS)

    def test_it_rejects_a_variable_reference_peer(self):
        peers = [match.group("peer")
                 for rules in self.bodies.values() for rule in rules
                 for match in PEER.finditer(rule)]
        self.assertTrue(any(peer.startswith("@") for peer in peers))

    def test_it_rejects_a_child_transition_that_resolves_nowhere(self):
        unresolved = []
        for name, rules in self.bodies.items():
            for rule in rules:
                match = TRANSITION.search(rule)
                if not match:
                    continue
                mode = rule.split("->")[0].split()[-1]
                target = match.group("target").replace("//&", "//")
                resolved = f"{name}//{target}" if mode.endswith("cx") else target
                if resolved not in self.bodies:
                    unresolved.append(resolved)
        # guest//bwrap and bwrap//payload: exactly the two that denied every exec.
        self.assertEqual(sorted(unresolved), [
            "sandbox-native-sb-x//bwrap//payload",
            "sandbox-native-sb-x//guest//bwrap",
        ])

    def test_it_rejects_a_path_governing_profile_without_the_root_entry(self):
        missing = [name for name, rules in self.bodies.items()
                   if any(rule.startswith("/**") for rule in rules)
                   and "/ r," not in rules]
        self.assertEqual(len(missing), 3)  # guest, bwrap and payload all lacked it


if __name__ == "__main__":
    unittest.main()
