import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import platform


class PlatformTest(unittest.TestCase):
    def test_parse_image_handles_registry_port_and_rejects_digest(self):
        self.assertEqual(
            platform.parse_image("registry.local:5000/team/api:1.2.3"),
            ("registry.local:5000/team/api", "1.2.3"),
        )
        self.assertEqual(platform.parse_image("nginx"), ("nginx", "latest"))
        with self.assertRaises(SystemExit):
            platform.parse_image("nginx@sha256:abc")

    def test_validate_app_name_rejects_reserved_and_invalid_names(self):
        for app_name in ("kube-system", "registry-system", "Uppercase", "ends-"):
            with self.assertRaises(SystemExit):
                platform.validate_app_name(app_name)

    def test_create_lints_with_defaults_first_and_preserves_registry_port(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workloads = root / "workloads"
            apps = root / "apps"
            defaults = root / "defaults.json"
            chart = root / "chart"
            defaults.write_text("{}")
            chart.mkdir()
            calls = []

            def fake_run(cmd, cwd=None):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")

            args = argparse.Namespace(
                name="my-api",
                kind="deployment",
                image="registry.local:5000/team/api:1.2.3",
                db_name=None,
                file=None,
            )
            with patch.multiple(
                platform,
                WORKLOADS_DIR=workloads,
                ARGOCD_APPS_DIR=apps,
                DEFAULTS_FILE=defaults,
                CHART_DIR=chart,
            ), patch.object(platform, "run_cmd", fake_run):
                platform.app_create(args)

            values = json.loads((workloads / "my-api" / "values.json").read_text())
            self.assertEqual(values["workload"]["containers"][0]["image"], {
                "repository": "registry.local:5000/team/api",
                "tag": "1.2.3",
            })
            self.assertEqual(calls[0][:5], ["helm", "lint", str(chart), "-f", str(defaults)])
            application = (apps / "my-api.yaml").read_text()
            self.assertIn("../platform/defaults.json", application)
            self.assertIn(
                'simple-k3s-harness.dev/workload: "true"',
                application,
            )

    def test_patch_cannot_change_metadata_identity(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            values_file = root / "workloads" / "my-api" / "values.json"
            values_file.parent.mkdir(parents=True)
            values_file.write_text(json.dumps({"metadata": {"name": "my-api", "namespace": "my-api"}}))
            patch_file = root / "patch.json"
            patch_file.write_text(json.dumps({"metadata": {"namespace": "other"}}))
            with patch.object(platform, "WORKLOADS_DIR", root / "workloads"):
                with self.assertRaises(SystemExit):
                    platform.app_patch(argparse.Namespace(name="my-api", file=patch_file))

    def test_patch_cannot_override_platform_defaults(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            values_file = root / "workloads" / "my-api" / "values.json"
            values_file.parent.mkdir(parents=True)
            values_file.write_text(json.dumps({"metadata": {"name": "my-api", "namespace": "my-api"}}))
            patch_file = root / "patch.json"
            patch_file.write_text(json.dumps({"platform": {"ingress": {"className": "other"}}}))
            with patch.object(platform, "WORKLOADS_DIR", root / "workloads"):
                with self.assertRaises(SystemExit):
                    platform.app_patch(argparse.Namespace(name="my-api", file=patch_file))

    def test_render_uses_defaults_before_workload_values(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            values_file = root / "workloads" / "my-api" / "values.json"
            values_file.parent.mkdir(parents=True)
            values_file.write_text(json.dumps({
                "metadata": {"name": "my-api", "namespace": "my-api"},
            }))
            defaults = root / "defaults.json"
            defaults.write_text("{}")
            chart = root / "chart"
            chart.mkdir()
            calls = []

            def fake_run(cmd, cwd=None):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "kind: Deployment\n", "")

            with patch.multiple(platform, WORKLOADS_DIR=root / "workloads", DEFAULTS_FILE=defaults, CHART_DIR=chart), patch.object(platform, "run_cmd", fake_run):
                platform.app_render(argparse.Namespace(name="my-api"))

            self.assertEqual(
                calls[0],
                ["helm", "template", "my-api", str(chart), "-f", str(defaults), "-f", str(values_file)],
            )


if __name__ == "__main__":
    unittest.main()
