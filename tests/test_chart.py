import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CHART_DIR = REPOSITORY_ROOT / "chart"
PLATFORM_DEFAULTS = REPOSITORY_ROOT / "platform" / "defaults.json"
NOTION_BLOG_VALUES = REPOSITORY_ROOT / "workloads" / "notion-blog" / "values.json"


def render_chart(values, release="sample"):
    with tempfile.TemporaryDirectory() as directory:
        values_path = Path(directory) / "values.json"
        values_path.write_text(json.dumps(values))
        return subprocess.run(
            [
                "helm", "template", release, str(CHART_DIR),
                "-f", str(PLATFORM_DEFAULTS), "-f", str(values_path),
            ],
            text=True,
            capture_output=True,
        )


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
                }, {
                    "name": "sidecar",
                    "image": {"repository": "busybox", "tag": "1.36"},
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

        result = render_chart(values)

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
        self.assertIn(
            '- name: DB_HOST\n              value: "shared-db-rw.database-system.svc.cluster.local"',
            manifests,
        )
        self.assertEqual(manifests.count("- name: DB_HOST"), 2)

    @unittest.skipUnless(shutil.which("helm"), "helm CLI is required")
    def test_does_not_inject_database_host_without_database(self):
        values = {
            "contractVersion": 1,
            "metadata": {"name": "sample", "namespace": "sample"},
            "workload": {
                "kind": "deployment",
                "containers": [{
                    "name": "app",
                    "image": {"repository": "nginx", "tag": "1.27"},
                }],
            },
        }

        result = render_chart(values)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("- name: DB_HOST", result.stdout)

    @unittest.skipUnless(shutil.which("helm"), "helm CLI is required")
    def test_rejects_database_host_environment_override(self):
        values = {
            "contractVersion": 1,
            "metadata": {"name": "sample", "namespace": "sample"},
            "workload": {
                "kind": "deployment",
                "containers": [{
                    "name": "app",
                    "image": {"repository": "nginx", "tag": "1.27"},
                    "env": [{"name": "DB_HOST", "value": "shared-db-rw"}],
                }],
            },
            "database": {"name": "sample_db"},
        }

        result = render_chart(values)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DB_HOST is managed by the platform", result.stderr)

    @unittest.skipUnless(shutil.which("helm"), "helm CLI is required")
    def test_rejects_platform_database_host_override(self):
        values = {
            "contractVersion": 1,
            "metadata": {"name": "sample", "namespace": "sample"},
            "workload": {
                "kind": "deployment",
                "containers": [{
                    "name": "app",
                    "image": {"repository": "nginx", "tag": "1.27"},
                }],
            },
            "database": {"name": "sample_db"},
            "platform": {"database": {"host": "shared-db-rw"}},
        }

        result = render_chart(values)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/platform/database/host", result.stderr)

    @unittest.skipUnless(shutil.which("helm"), "helm CLI is required")
    def test_rejects_unsafe_database_secret_consumption(self):
        unsafe_fields = (
            ({
                "env": [{
                    "name": "PGHOST",
                    "secretKeyRef": {"name": "shared-db-app", "key": "host"},
                }],
            }, {}),
            ({"envFrom": [{"secretRef": {"name": "shared-db-app"}}]}, {}),
            ({"volumeMounts": [{"name": "database", "mountPath": "/database"}]}, {
                "volumes": [{
                    "name": "database",
                    "secret": {"secretName": "shared-db-app"},
                }],
            }),
        )

        for container_fields, workload_fields in unsafe_fields:
            with self.subTest(container_fields=container_fields, workload_fields=workload_fields):
                values = {
                    "contractVersion": 1,
                    "metadata": {"name": "sample", "namespace": "sample"},
                    "workload": {
                        "kind": "deployment",
                        "containers": [{
                            "name": "app",
                            "image": {"repository": "nginx", "tag": "1.27"},
                            **container_fields,
                        }],
                        **workload_fields,
                    },
                    "database": {"name": "sample_db"},
                }

                result = render_chart(values)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("shared-db-app may only supply port, username, and password", result.stderr)

    @unittest.skipUnless(shutil.which("helm"), "helm CLI is required")
    def test_notion_blog_uses_the_managed_database_host(self):
        values = json.loads(NOTION_BLOG_VALUES.read_text())
        env_names = [
            env["name"]
            for container in values["workload"]["containers"]
            for env in container.get("env", [])
        ]

        self.assertNotIn("DB_HOST", env_names)

        result = render_chart(values, release="notion-blog")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("- name: DB_HOST"), 1)
        self.assertIn(
            '- name: DB_HOST\n              value: "shared-db-rw.database-system.svc.cluster.local"',
            result.stdout,
        )
        self.assertLess(result.stdout.index("- name: DB_HOST"), result.stdout.index("- name: DB_PORT"))
        self.assertLess(
            result.stdout.index("- name: DB_PORT"),
            result.stdout.index("- name: SPRING_DATASOURCE_URL"),
        )

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
