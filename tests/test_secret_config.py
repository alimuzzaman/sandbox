from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


def _compose_descriptor(*, secrets=None):
    document = {
        "kind": "compose",
        "compose": {
            "file": "compose.yaml",
            "service": "web",
            "internal_port": 80,
            "health_path": "/",
        },
    }
    if secrets is not None:
        document["secrets"] = secrets
    return document


class TestSecretConfigNormalization(unittest.TestCase):
    def test_defaults_are_empty(self):
        from sandbox.config.secrets import normalize_secret_config

        self.assertEqual(
            normalize_secret_config({"_secrets_raw": {
                "project": {}, "machine_override": {},
            }}),
            {"sources": {}, "useProfiles": {}},
        )

    def test_normalizes_source_modes_and_fixed_use_profile_defaults(self):
        from sandbox.config.secrets import normalize_secret_config

        result = normalize_secret_config({
            "root": "/tmp/project",
            "_secrets_raw": {
                "project": {
                    "sources": {
                        "project-env": {
                            "path": "config/.env.fixture",
                            "mcpModes": ["use", "keys", "metadata", "validate", "masked"],
                        },
                    },
                    "useProfiles": {
                        "provider-status": {
                            "source": "project-env",
                            "key": "API_TOKEN",
                            "argv": ["trusted-provider-cli", "status"],
                            "destination": "API_TOKEN",
                            "mcp": True,
                        },
                    },
                },
                "machine_override": {},
            },
        })

        self.assertEqual(result, {
            "sources": {
                "project-env": {
                    "path": "config/.env.fixture",
                    "mcpModes": ["keys", "metadata", "validate", "masked", "use"],
                },
            },
            "useProfiles": {
                "provider-status": {
                    "source": "project-env",
                    "key": "API_TOKEN",
                    "argv": ["trusted-provider-cli", "status"],
                    "destination": "API_TOKEN",
                    "timeoutSeconds": 300,
                    "maxOutputBytes": 1_048_576,
                    "mcp": True,
                },
            },
        })

    def test_aliases_are_lowercase_slugs_and_personal_is_reserved(self):
        from sandbox.config.secrets import normalize_secret_config

        for alias in ("Project-Env", "project_env", "-project", "project-", "personal"):
            with self.subTest(alias=alias), self.assertRaisesRegex(ValueError, "alias"):
                normalize_secret_config({"_secrets_raw": {
                    "project": {"sources": {alias: {"path": ".env.fixture"}}},
                    "machine_override": {},
                }})

        with self.assertRaisesRegex(ValueError, "profile name"):
            normalize_secret_config({"_secrets_raw": {
                "project": {
                    "sources": {"project-env": {"path": ".env.fixture"}},
                    "useProfiles": {"Provider_Status": {
                        "source": "project-env", "key": "API_TOKEN",
                        "argv": ["provider-cli"], "destination": "API_TOKEN",
                    }},
                },
                "machine_override": {},
            }})

    def test_source_paths_are_project_relative_dot_env_files(self):
        from sandbox.config.secrets import normalize_secret_config

        for path in (
            "/tmp/.env.fixture", "../.env.fixture", "config/../../.env.fixture",
            "config/secrets.env", ".env.fixture/value", ".env.fixture/", "",
            ".env.fixture\n",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "path"):
                normalize_secret_config({"root": "/tmp/project", "_secrets_raw": {
                    "project": {"sources": {"project-env": {"path": path}}},
                    "machine_override": {},
                }})

    def test_source_modes_are_explicit_bounded_and_known(self):
        from sandbox.config.secrets import normalize_secret_config

        for modes in (
            None, "keys", ["keys", "keys"], ["read"], ["keys", 1],
        ):
            with self.subTest(modes=modes), self.assertRaisesRegex(ValueError, "mcpModes"):
                normalize_secret_config({"_secrets_raw": {
                    "project": {"sources": {
                        "project-env": {"path": ".env.fixture", "mcpModes": modes},
                    }},
                    "machine_override": {},
                }})

    def test_unknown_configuration_keys_fail_closed(self):
        from sandbox.config.secrets import normalize_secret_config

        cases = (
            {"surprise": True},
            {"sources": {"project-env": {"path": ".env.fixture", "surprise": True}}},
            {
                "sources": {"project-env": {"path": ".env.fixture"}},
                "useProfiles": {"status": {
                    "source": "project-env", "key": "API_TOKEN",
                    "argv": ["provider-cli"], "destination": "API_TOKEN",
                    "surprise": True,
                }},
            },
        )
        for project in cases:
            with self.subTest(project=project), self.assertRaisesRegex(ValueError, "unknown"):
                normalize_secret_config({"_secrets_raw": {
                    "project": project, "machine_override": {},
                }})

    def test_use_profile_references_and_fixed_argv_are_validated(self):
        from sandbox.config.secrets import normalize_secret_config

        base = {
            "sources": {"project-env": {"path": ".env.fixture"}},
            "useProfiles": {"status": {
                "source": "project-env", "key": "API_TOKEN",
                "argv": ["provider-cli", "status"], "destination": "API_TOKEN",
            }},
        }
        invalid_changes = (
            {"source": "missing"}, {"key": "not-a-key"}, {"key": "A" * 129},
            {"argv": []}, {"argv": "provider-cli"}, {"argv": ["provider-cli", ""]},
            {"argv": ["provider-cli\n"]}, {"destination": "not-a-key"},
        )
        for changes in invalid_changes:
            project = json.loads(json.dumps(base))
            project["useProfiles"]["status"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_secret_config({"_secrets_raw": {
                    "project": project, "machine_override": {},
                }})

    def test_local_profile_can_reference_builtin_personal_without_configuring_it(self):
        from sandbox.config.secrets import normalize_secret_config

        result = normalize_secret_config({"_secrets_raw": {
            "project": {"useProfiles": {"personal-status": {
                "source": "personal", "key": "API_TOKEN",
                "argv": ["provider-cli", "status"], "destination": "API_TOKEN",
            }}},
            "machine_override": {},
        }})

        self.assertEqual(result["sources"], {})
        self.assertEqual(
            result["useProfiles"]["personal-status"]["source"], "personal",
        )
        self.assertFalse(result["useProfiles"]["personal-status"]["mcp"])

        with self.assertRaisesRegex(ValueError, "use.*mcpModes"):
            normalize_secret_config({"_secrets_raw": {
                "project": {"useProfiles": {"personal-status": {
                    "source": "personal", "key": "API_TOKEN",
                    "argv": ["provider-cli"], "destination": "API_TOKEN",
                    "mcp": True,
                }}},
                "machine_override": {},
            }})

    def test_dangerous_use_destinations_are_rejected(self):
        from sandbox.config.secrets import normalize_secret_config

        dangerous = (
            "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
            "NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME", "PERL5OPT", "RUBYOPT",
            "BASH_ENV", "ENV", "SHELLOPTS", "PS4", "PROMPT_COMMAND",
            "GIT_ASKPASS", "SSH_ASKPASS",
        )
        for destination in dangerous:
            with self.subTest(destination=destination), self.assertRaisesRegex(
                ValueError, "dangerous",
            ):
                normalize_secret_config({"_secrets_raw": {
                    "project": {
                        "sources": {"project-env": {"path": ".env.fixture"}},
                        "useProfiles": {"status": {
                            "source": "project-env", "key": "API_TOKEN",
                            "argv": ["provider-cli"], "destination": destination,
                        }},
                    },
                    "machine_override": {},
                }})

    def test_use_profile_bounds_and_mcp_authorization(self):
        from sandbox.config.secrets import normalize_secret_config

        for field, value in (
            ("timeoutSeconds", 0), ("timeoutSeconds", 1801),
            ("timeoutSeconds", True), ("maxOutputBytes", 0),
            ("maxOutputBytes", 1_048_577), ("maxOutputBytes", True),
            ("mcp", "yes"),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                normalize_secret_config({"_secrets_raw": {
                    "project": {
                        "sources": {"project-env": {"path": ".env.fixture"}},
                        "useProfiles": {"status": {
                            "source": "project-env", "key": "API_TOKEN",
                            "argv": ["provider-cli"], "destination": "API_TOKEN",
                            field: value,
                        }},
                    },
                    "machine_override": {},
                }})

        with self.assertRaisesRegex(ValueError, "use.*mcpModes"):
            normalize_secret_config({"_secrets_raw": {
                "project": {
                    "sources": {"project-env": {
                        "path": ".env.fixture", "mcpModes": ["keys"],
                    }},
                    "useProfiles": {"status": {
                        "source": "project-env", "key": "API_TOKEN",
                        "argv": ["provider-cli"], "destination": "API_TOKEN",
                        "mcp": True,
                    }},
                },
                "machine_override": {},
            }})

    def test_project_and_machine_layers_can_add_but_not_replace_entries(self):
        from sandbox.config.secrets import normalize_secret_config

        result = normalize_secret_config({"_secrets_raw": {
            "project": {"sources": {"project-env": {"path": ".env.project"}}},
            "machine_override": {
                "sources": {"machine-env": {"path": ".env.machine"}},
                "useProfiles": {"local-status": {
                    "source": "project-env", "key": "API_TOKEN",
                    "argv": ["provider-cli"], "destination": "API_TOKEN",
                }},
            },
        }})
        self.assertEqual(set(result["sources"]), {"project-env", "machine-env"})
        self.assertEqual(set(result["useProfiles"]), {"local-status"})

        for category, entry in (
            ("sources", {"path": ".env.other"}),
            ("useProfiles", {
                "source": "project-env", "key": "API_TOKEN",
                "argv": ["other-cli"], "destination": "API_TOKEN",
            }),
        ):
            project = {"sources": {"project-env": {"path": ".env.project"}}}
            if category == "useProfiles":
                project[category] = {"duplicate": {
                    "source": "project-env", "key": "API_TOKEN",
                    "argv": ["provider-cli"], "destination": "API_TOKEN",
                }}
            machine = {category: {
                "project-env" if category == "sources" else "duplicate": entry,
            }}
            with self.subTest(category=category), self.assertRaisesRegex(
                ValueError, "duplicate",
            ):
                normalize_secret_config({"_secrets_raw": {
                    "project": project, "machine_override": machine,
                }})


class TestSecretConfigProviders(unittest.TestCase):
    def test_wordpress_provider_carries_project_and_machine_layers(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sandbox.config.json").write_text(json.dumps({
                "secrets": {"sources": {
                    "project-env": {"path": ".env.project", "mcpModes": ["keys"]},
                }},
            }))
            (root / "sandbox.config.override.json").write_text(json.dumps({
                "secrets": {"sources": {
                    "machine-env": {"path": ".env.machine"},
                }},
            }))

            result = resolve_project_config(
                root, legacy_loader=mock.Mock(return_value={}),
            )

        self.assertEqual(set(result["secrets"]["sources"]), {
            "project-env", "machine-env",
        })
        self.assertNotIn("_secrets_raw", result)

    def test_compose_provider_carries_project_and_label_layers(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compose.yaml").write_text("services: {}\n")
            (root / "sandbox.config.json").write_text(json.dumps(
                _compose_descriptor(secrets={"sources": {
                    "project-env": {"path": ".env.project"},
                }}),
            ))
            (root / "sandbox.config.preview.json").write_text(json.dumps({
                "secrets": {"sources": {
                    "preview-env": {"path": ".env.preview"},
                }},
            }))

            result = resolve_project_config(
                root, label="preview", legacy_loader=mock.Mock(),
            )

        self.assertEqual(set(result["secrets"]["sources"]), {
            "project-env", "preview-env",
        })
        self.assertNotIn("_secrets_raw", result)


if __name__ == "__main__":
    unittest.main()
