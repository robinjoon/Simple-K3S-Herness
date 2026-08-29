import argparse
import copy
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import release


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "release-workload-image.yml"


class ReleaseTest(unittest.TestCase):
    def values(self):
        return {
            "contractVersion": 1,
            "metadata": {"name": "my-api", "namespace": "my-api"},
            "workload": {
                "kind": "deployment",
                "containers": [{
                    "name": "app",
                    "image": {
                        "repository": "registry.example.test/apps/my-api",
                        "tag": "sha-old",
                    },
                    "ports": [{"name": "http", "containerPort": 8080}],
                    "env": [{"name": "LOG_LEVEL", "value": "info"}],
                }],
            },
        }

    def run_release(self, values, tag="sha-new"):
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        workloads = Path(tmp_dir.name) / "workloads"
        values_file = workloads / "my-api" / "values.json"
        values_file.parent.mkdir(parents=True)
        values_file.write_text(json.dumps(values, indent=2) + "\n")
        args = argparse.Namespace(name="my-api", container="app", tag=tag)
        lint_ok = subprocess.CompletedProcess([], 0, "", "")
        return workloads, values_file, args, lint_ok

    def test_set_image_tag_preserves_every_other_value(self):
        original = self.values()
        workloads, values_file, args, lint_ok = self.run_release(original)

        with patch.object(release.platform, "WORKLOADS_DIR", workloads), \
                patch.object(release.platform, "lint_values", return_value=lint_ok) as lint:
            release.set_image_tag(args)

        updated = json.loads(values_file.read_text())
        expected = copy.deepcopy(original)
        expected["workload"]["containers"][0]["image"]["tag"] = "sha-new"
        self.assertEqual(updated, expected)
        lint.assert_called_once_with(expected)

    def test_set_image_tag_is_idempotent(self):
        original = self.values()
        workloads, values_file, args, lint_ok = self.run_release(original, tag="sha-old")
        before = values_file.read_bytes()

        with patch.object(release.platform, "WORKLOADS_DIR", workloads), \
                patch.object(release.platform, "lint_values", return_value=lint_ok) as lint:
            release.set_image_tag(args)

        self.assertEqual(values_file.read_bytes(), before)
        lint.assert_not_called()

    def test_set_image_tag_rejects_mutable_or_invalid_tags(self):
        for tag in ("latest", "LATEST", "", "bad/tag", "@sha256:abc", "-bad"):
            with self.subTest(tag=tag):
                original = self.values()
                workloads, values_file, args, lint_ok = self.run_release(original, tag=tag)
                before = values_file.read_bytes()

                with patch.object(release.platform, "WORKLOADS_DIR", workloads), \
                        patch.object(release.platform, "lint_values", return_value=lint_ok):
                    with self.assertRaises(SystemExit):
                        release.set_image_tag(args)

                self.assertEqual(values_file.read_bytes(), before)

    def test_set_image_tag_requires_exactly_one_matching_container(self):
        for containers in ([], [
            {"name": "app", "image": {"repository": "example/app", "tag": "one"}},
            {"name": "app", "image": {"repository": "example/app", "tag": "two"}},
        ]):
            with self.subTest(containers=containers):
                original = self.values()
                original["workload"]["containers"] = containers
                workloads, values_file, args, lint_ok = self.run_release(original)
                before = values_file.read_bytes()

                with patch.object(release.platform, "WORKLOADS_DIR", workloads), \
                        patch.object(release.platform, "lint_values", return_value=lint_ok):
                    with self.assertRaises(SystemExit):
                        release.set_image_tag(args)

                self.assertEqual(values_file.read_bytes(), before)

    def test_set_image_tag_does_not_write_when_validation_fails(self):
        original = self.values()
        workloads, values_file, args, _ = self.run_release(original)
        before = values_file.read_bytes()
        lint_failure = subprocess.CompletedProcess([], 1, "", "invalid")

        with patch.object(release.platform, "WORKLOADS_DIR", workloads), \
                patch.object(release.platform, "lint_values", return_value=lint_failure):
            with self.assertRaises(SystemExit):
                release.set_image_tag(args)

        self.assertEqual(values_file.read_bytes(), before)


class ReleaseWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = RELEASE_WORKFLOW.read_text()

    def test_uses_manual_dispatch_and_minimum_repository_permission(self):
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("permissions:\n  contents: write", self.workflow)
        self.assertNotIn("repository_dispatch:", self.workflow)

    def test_serializes_release_writes_without_canceling_queued_requests(self):
        self.assertIn("group: release-workload-image", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertIn("queue: max", self.workflow)

    def test_pins_third_party_actions_to_commit_shas(self):
        action_refs = re.findall(r"(?m)^\s+uses: [^@]+@([^\s]+)", self.workflow)
        self.assertEqual(len(action_refs), 3)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs))

    def test_only_updates_gitops_state_through_the_release_contract(self):
        self.assertIn('python3 tools/release.py "$APP_NAME"', self.workflow)
        self.assertIn('python3 tools/platform.py validate "$APP_NAME"', self.workflow)
        self.assertIn('python3 tools/platform.py render "$APP_NAME"', self.workflow)
        self.assertNotIn("kubectl", self.workflow)
        self.assertNotIn("argocd", self.workflow)
        self.assertNotIn("docker", self.workflow)


if __name__ == "__main__":
    unittest.main()
