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
                            "mcpModes": [
                                "use", "source_info", "keys", "metadata", "validate", "masked",
                            ],
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
                    "mcpModes": [
                        "source_info", "keys", "metadata", "validate", "masked", "use",
                    ],
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

    def test_explicit_structured_formats_require_matching_safe_paths(self):
        from sandbox.config.secrets import normalize_secret_config

        accepted = {
            "json-source": ("config/credentials.json", "json"),
            "ini-source": ("config/credentials.ini", "ini"),
            "properties-source": ("config/.npmrc", "properties"),
            "toml-source": ("config/credentials.toml", "toml"),
            "yaml-source": ("config/kubeconfig.yaml", "yaml"),
            "xml-source": ("config/settings.xml", "xml"),
            "pem-source": ("config/app.pem", "pem"),
            "opaque-source": ("config/workload.jwt", "opaque"),
            "binary-source": ("config/client.pfx", "binary"),
        }
        project = {"sources": {
            alias: {
                "path": path, "format": format_name,
                "mcpModes": ["source_info", "keys", "metadata"],
            }
            for alias, (path, format_name) in accepted.items()
        }}
        result = normalize_secret_config({"root": "/tmp/project", "_secrets_raw": {
            "project": project, "machine_override": {},
        }})
        self.assertEqual(
            {alias: item["format"] for alias, item in result["sources"].items()},
            {alias: format_name for alias, (_, format_name) in accepted.items()},
        )

        for format_name, path in (
            ("json", "config/credentials.yaml"),
            ("yaml", "config/credentials.json"),
            ("pem", "config/key.txt"),
            ("auto", "config/credentials.json"),
        ):
            with self.subTest(format=format_name, path=path), self.assertRaises(ValueError):
                normalize_secret_config({"root": "/tmp/project", "_secrets_raw": {
                    "project": {"sources": {"fixture": {
                        "path": path, "format": format_name,
                    }}},
                    "machine_override": {},
                }})

    def test_structured_mcp_modes_do_not_enable_value_use_or_masking(self):
        from sandbox.config.secrets import normalize_secret_config

        for mode in ("validate", "masked", "use"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "format"):
                normalize_secret_config({"root": "/tmp/project", "_secrets_raw": {
                    "project": {"sources": {"fixture": {
                        "path": "credentials.json", "format": "json",
                        "mcpModes": ["keys", mode],
                    }}},
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


class TestInfisicalSecretConfig(unittest.TestCase):
    def descriptor(self, **changes):
        return {
            "type": "infisical", "endpoint": "https://Vault.Example.invalid:443/",
            "projectId": "synthetic-project", "environment": "dev",
            "path": "/sandbox", "key": "API_TOKEN", "clientId": "controller-one",
            **changes,
        }

    def normalize(self, descriptor, *, profile=None):
        from sandbox.config.secrets import normalize_secret_config

        project = {"sources": {"vault": descriptor}}
        if profile is not None:
            project["useProfiles"] = {"fixed": profile}
        return normalize_secret_config({"secrets": project})

    def test_exact_registration_is_canonical_bounded_and_side_effect_free(self):
        descriptor = self.descriptor(mcpModes=["use", "keys", "source_info"])
        original = json.loads(json.dumps(descriptor))
        with mock.patch("builtins.open", side_effect=AssertionError("file read")), \
             mock.patch("subprocess.run", side_effect=AssertionError("process")), \
             mock.patch("socket.create_connection", side_effect=AssertionError("network")):
            source = self.normalize(descriptor)["sources"]["vault"]
        self.assertEqual(source, {
            **original, "endpoint": "https://vault.example.invalid",
            "mcpModes": ["source_info", "keys", "use"], "timeoutSeconds": 10,
        })
        self.assertEqual(descriptor, original)

    def test_missing_metadata_and_unknown_authority_are_refused(self):
        for field in ("endpoint", "projectId", "environment", "path", "key", "clientId"):
            descriptor = self.descriptor()
            del descriptor[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.normalize(descriptor)
        for field in ("clientSecret", "token", "role", "capacity", "format", "allowImports",
                      "fallback", "proxy", "argv"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unknown"):
                self.normalize(self.descriptor(**{field: "synthetic"}))

    def test_endpoint_requires_https_origin_without_credentials_or_selectors(self):
        for endpoint in (
            "http://vault.example.invalid", "https://user:pass@vault.example.invalid",
            "https://vault.example.invalid/api", "https://vault.example.invalid?key=X",
            "https://vault.example.invalid#X", "https://vault.example.invalid:",
            "https://vault.example.invalid.", "https://vault.example.invalid/\n",
            "https://vault.example.invalid:99999", "https://vault%2eexample.invalid",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.normalize(self.descriptor(endpoint=endpoint))

    def test_provider_path_and_nonsecret_metadata_are_strict(self):
        for path in ("sandbox", "/sandbox/", "/sandbox//item", "/sandbox/../item",
                     "/sandbox/./item", "/sandbox\\item", "/sandbox\n"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.normalize(self.descriptor(path=path))
        for field in ("projectId", "clientId", "environment", "key"):
            for value in ("", "with space", "Bearer synthetic", "ghp_synthetic", "x" * 257):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.normalize(self.descriptor(**{field: value}))

    def test_provider_modes_and_deadlines_cannot_expand_authority(self):
        for modes in (["masked"], ["reveal"], ["browse"], ["keys", "keys"], "keys", None):
            with self.subTest(modes=modes), self.assertRaises(ValueError):
                self.normalize(self.descriptor(mcpModes=modes))
        for timeout in (0, 61, True, "10"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.normalize(self.descriptor(timeoutSeconds=timeout))

    def test_profile_key_is_exact_and_mcp_needs_explicit_use_mode(self):
        profile = {"source": "vault", "key": "API_TOKEN", "argv": ["fixed-child"],
                   "destination": "API_TOKEN", "mcp": True}
        result = self.normalize(self.descriptor(mcpModes=["use"]), profile=profile)
        self.assertEqual(result["useProfiles"]["fixed"]["key"], "API_TOKEN")
        with self.assertRaisesRegex(ValueError, "match"):
            self.normalize(self.descriptor(mcpModes=["use"]),
                           profile={**profile, "key": "OTHER_TOKEN"})
        with self.assertRaisesRegex(ValueError, "mcpModes"):
            self.normalize(self.descriptor(), profile=profile)

    def test_explicit_file_type_and_missing_type_preserve_legacy_normalization(self):
        from sandbox.config.secrets import normalize_secret_config

        source = {"path": ".env.synthetic", "mcpModes": ["masked", "keys", "use"]}
        implicit = normalize_secret_config({"secrets": {"sources": {"fixture": source}}})
        explicit = normalize_secret_config({"secrets": {"sources": {
            "fixture": {**source, "type": "file"},
        }}})
        self.assertEqual(explicit, implicit)
        mixed = normalize_secret_config({"secrets": {"sources": {
            "fixture": source, "vault": self.descriptor(),
        }}})
        self.assertEqual(mixed["sources"]["fixture"], implicit["sources"]["fixture"])
        with self.assertRaises(ValueError):
            self.normalize(self.descriptor(type="unregistered"))


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
