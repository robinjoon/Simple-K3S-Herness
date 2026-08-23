import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CHART_DIR = REPOSITORY_ROOT / "chart"
PLATFORM_DEFAULTS = REPOSITORY_ROOT / "platform" / "defaults.json"


class ChartRenderTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("helm"), "helm CLI is required")
    def test_renders_default_workload_resources(self):
        values = {
            "contractVersion": 1,
            "metadata": {"name": "sample", "namespace": "sample"},
            "workload": {
                "kind": "deployment",
                "replicas": 0,
                "imagePullSecrets": [{"name": "registry-credentials"}],
                "containers": [{
                    "name": "app",
                    "image": {"repository": "nginx", "tag": "1.27"},
                    "ports": [{"name": "http", "containerPort": 8080}],
                }],
            },
            "database": {"name": "sample_db"},
            "configMaps": [{"name": "settings", "data": {"MODE": "test"}}],
            "services": [{
                "name": "web",
                "ports": [{"name": "http", "port": 80, "targetPort": "http"}],
            }],
            "ingresses": [{
                "name": "public",
                "service": "web",
                "rules": [{
                    "host": "sample.example.test",
                    "paths": [{"path": "/", "servicePort": "http"}],
                }],
                "tls": {"mode": "cert-manager"},
            }],
        }

        with tempfile.TemporaryDirectory() as directory:
            values_path = Path(directory) / "values.json"
            values_path.write_text(json.dumps(values))
            result = subprocess.run(
                [
                    "helm", "template", "sample", str(CHART_DIR),
                    "-f", str(PLATFORM_DEFAULTS), "-f", str(values_path),
                ],
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        manifests = result.stdout
        self.assertIn("kind: Deployment", manifests)
        self.assertIn("replicas: 0", manifests)
        self.assertIn("imagePullSecrets:\n        - name: registry-credentials", manifests)
        self.assertIn("kind: Service", manifests)
        self.assertIn("name: sample-web", manifests)
        self.assertIn("kind: Ingress", manifests)
        self.assertIn("ingressClassName: traefik", manifests)
        self.assertIn("secretName: sample-public-tls", manifests)
        self.assertIn("name: sample-web\n                port:\n                  name: http", manifests)
        self.assertIn("kind: Certificate", manifests)
        self.assertIn("name: letsencrypt-prod", manifests)
        self.assertIn("kind: ClusterIssuer", manifests)
        self.assertIn("kind: ConfigMap", manifests)
        self.assertIn("name: sample-settings", manifests)
        self.assertIn("MODE: |\n    test", manifests)
        self.assertIn("kind: Database", manifests)
        self.assertIn("namespace: database-system", manifests)
        self.assertIn("name: sample_db", manifests)
        self.assertIn("owner: defaultuser", manifests)

    @unittest.skipUnless(shutil.which("helm"), "helm CLI is required")
    def test_rejects_unsupported_workload_kind(self):
        values = {
            "contractVersion": 1,
            "metadata": {"name": "sample", "namespace": "sample"},
            "workload": {
                "kind": "statefulset",
                "containers": [{
                    "name": "app",
                    "image": {"repository": "nginx", "tag": "1.27"},
                }],
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            values_path = Path(directory) / "values.json"
            values_path.write_text(json.dumps(values))
            result = subprocess.run(
                [
                    "helm", "template", "sample", str(CHART_DIR),
                    "-f", str(PLATFORM_DEFAULTS), "-f", str(values_path),
                ],
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/workload/kind", result.stderr)

    @unittest.skipUnless(shutil.which("helm"), "helm CLI is required")
    def test_rejects_registry_credentials_in_workload_values(self):
        values = {
            "contractVersion": 1,
            "metadata": {"name": "sample", "namespace": "sample"},
            "workload": {
                "kind": "deployment",
                "imagePullSecrets": [{
                    "name": "registry-credentials",
                    "password": "must-not-be-stored-here",
                }],
                "containers": [{
                    "name": "app",
                    "image": {"repository": "registry.example.test/app", "tag": "1.0"},
                }],
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            values_path = Path(directory) / "values.json"
            values_path.write_text(json.dumps(values))
            result = subprocess.run(
                [
                    "helm", "template", "sample", str(CHART_DIR),
                    "-f", str(PLATFORM_DEFAULTS), "-f", str(values_path),
                ],
                text=True,
                capture_output=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/workload/imagePullSecrets/0", result.stderr)


if __name__ == "__main__":
    unittest.main()
