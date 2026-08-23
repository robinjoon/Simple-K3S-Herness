import json
import re
import subprocess
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ZOT_APPLICATION = REPOSITORY_ROOT / "argocd" / "managed" / "apps" / "zot.yaml"
NETWORK_POLICY_APPLICATION = (
    REPOSITORY_ROOT
    / "argocd"
    / "managed"
    / "apps"
    / "registry-network-policy.yaml"
)
NETWORK_POLICY = (
    REPOSITORY_ROOT / "infrastructure" / "registry" / "network-policy.yaml"
)
README = REPOSITORY_ROOT / "README.md"
GITIGNORE = REPOSITORY_ROOT / ".gitignore"
WORKLOAD_NAMESPACE_SELECTOR = "simple-k3s-harness.dev/workload=true"


def fenced_code_blocks(markdown: str) -> list[str]:
    return re.findall(r"(?ms)^```[^\n]*\n(.*?)^```$", markdown)


def code_block_containing(blocks: list[str], marker: str) -> str:
    matches = [block for block in blocks if marker in block]
    if len(matches) != 1:
        raise AssertionError(
            f"Expected exactly one code block containing {marker!r}, found {len(matches)}"
        )
    return matches[0]


def extract_zot_config(application: str) -> dict:
    match = re.search(
        r"(?m)^          config\.json: \|-\n"
        r"(?P<config>(?:^            .*\n?)*)",
        application,
    )
    if match is None:
        raise AssertionError("Zot config.json block was not found")
    return json.loads(textwrap.dedent(match.group("config")))


class ZotApplicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = ZOT_APPLICATION.read_text()
        cls.config = extract_zot_config(cls.application)

    def test_pins_chart_and_single_persistent_runtime(self):
        application = self.application

        self.assertIn("repoURL: https://zotregistry.dev/helm-charts", application)
        self.assertIn("chart: zot", application)
        self.assertIn("targetRevision: 0.1.122", application)
        self.assertIn("replicaCount: 1", application)
        self.assertIn("repository: ghcr.io/project-zot/zot", application)
        self.assertIn("tag: v2.1.20", application)
        self.assertIn("pullPolicy: IfNotPresent", application)
        self.assertIn("service:\n          type: ClusterIP\n          port: 5000", application)
        self.assertIn("persistence: true", application)
        self.assertIn("accessModes:\n            - ReadWriteOnce", application)
        self.assertIn("storageClassName: local-path", application)
        self.assertIn("namespace: registry-system", application)

    def test_exposes_only_the_tls_ingress(self):
        application = self.application

        self.assertIn("ingress:\n          enabled: true", application)
        self.assertIn("className: traefik", application)
        self.assertIn("cert-manager.io/cluster-issuer: letsencrypt-prod", application)
        self.assertIn("host: registry.homelab.robinjoon.xyz", application)
        self.assertIn("secretName: zot-tls", application)
        self.assertNotIn("type: NodePort", application)
        self.assertNotIn("type: LoadBalancer", application)

    def test_ignores_only_kubernetes_defaulted_pvc_typemeta(self):
        application = self.application

        self.assertIn(
            """  ignoreDifferences:
    - group: apps
      kind: StatefulSet
      name: zot
      namespace: registry-system
      jqPathExpressions:
        - ".spec.volumeClaimTemplates[]?.apiVersion"
        - ".spec.volumeClaimTemplates[]?.kind"
""",
            application,
        )
        self.assertIn("- RespectIgnoreDifferences=true", application)

    def test_uses_external_htpasswd_and_single_full_access_acl(self):
        application = self.application
        http = self.config["http"]

        self.assertIn("mountSecret: false", application)
        self.assertIn("secretFiles: {}", application)
        self.assertIn("secretName: zot-auth", application)
        self.assertEqual(
            http["auth"]["htpasswd"]["path"],
            "/etc/zot/auth/htpasswd",
        )

        repository_acl = http["accessControl"]["repositories"]["**"]
        self.assertEqual(
            repository_acl["policies"],
            [
                {
                    "users": ["admin"],
                    "actions": ["read", "create", "update", "delete"],
                },
            ],
        )
        self.assertEqual(repository_acl["defaultPolicy"], [])
        self.assertNotIn("anonymousPolicy", repository_acl)
        self.assertNotIn("adminPolicy", http["accessControl"])

    def test_uses_docker_compatible_secure_sessions(self):
        self.assertEqual(self.config["distSpecVersion"], "1.1.1")
        self.assertEqual(self.config["http"]["compat"], ["docker2s2"])
        self.assertEqual(self.config["http"]["auth"]["failDelay"], 5)
        self.assertIs(self.config["http"]["auth"]["secureSession"], True)

    def test_keeps_persistent_data_and_runs_conservative_maintenance(self):
        storage = self.config["storage"]

        self.assertEqual(storage["rootDirectory"], "/var/lib/registry/data")
        self.assertIs(storage["commit"], True)
        self.assertIs(storage["dedupe"], True)
        self.assertIs(storage["gc"], True)
        self.assertEqual(storage["gcDelay"], "24h")
        self.assertEqual(storage["gcInterval"], "24h")
        self.assertEqual(
            storage["retention"],
            {
                "dryRun": False,
                "delay": "168h",
                "policies": [
                    {
                        "repositories": ["**"],
                        "deleteReferrers": False,
                        "deleteUntagged": True,
                        "keepTags": [{"patterns": [".*"]}],
                    },
                ],
            },
        )

        self.assertEqual(
            self.config["log"]["audit"],
            "/var/lib/registry/zot-audit.log",
        )
        extensions = self.config["extensions"]
        self.assertEqual(extensions["metrics"], {"enable": False})
        self.assertEqual(extensions["search"], {"enable": True})
        self.assertEqual(extensions["ui"], {"enable": True})
        self.assertEqual(
            extensions["scrub"],
            {"enable": True, "interval": "24h"},
        )

    def test_does_not_commit_registry_credentials(self):
        application = self.application

        self.assertNotRegex(application, r"(?im)^\s*(?:user(?:name)?|password):")
        self.assertNotRegex(application, r"\$2[aby]\$\d{2}\$")
        self.assertNotIn("authHeader:", application)
        self.assertNotIn("kind: Secret", application)
        self.assertNotIn("admin:admin", application)
        self.assertNotIn("user:user", application)


class RegistryNetworkPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = NETWORK_POLICY_APPLICATION.read_text()
        cls.policy = NETWORK_POLICY.read_text()

    def test_network_policy_is_managed_by_argocd(self):
        application = self.application

        self.assertIn("kind: Application", application)
        self.assertIn("name: registry-network-policy", application)
        self.assertIn("path: infrastructure/registry", application)
        self.assertIn("namespace: registry-system", application)

    def test_allows_only_kube_system_traefik_on_tcp_5000(self):
        policy = self.policy

        self.assertIn("kind: NetworkPolicy", policy)
        self.assertIn("name: registry-network-policy", policy)
        self.assertRegex(
            policy,
            r"(?s)podSelector:\s+matchLabels:\s+"
            r"app\.kubernetes\.io/instance: zot\s+"
            r"app\.kubernetes\.io/name: zot",
        )
        self.assertRegex(
            policy,
            r"(?s)namespaceSelector:\s+matchLabels:\s+"
            r"kubernetes\.io/metadata\.name: kube-system\s+"
            r"podSelector:\s+matchLabels:\s+"
            r"app\.kubernetes\.io/name: traefik",
        )
        self.assertRegex(policy, r"(?s)ports:\s+- protocol: TCP\s+port: 5000")
        self.assertEqual(policy.count("- from:"), 1)
        self.assertEqual(policy.count("- protocol:"), 1)
        self.assertIn("policyTypes:\n    - Ingress", policy)
        self.assertNotIn("- Egress", policy)


class RegistryReadmeBootstrapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = README.read_text()
        cls.gitignore = GITIGNORE.read_text()
        cls.code_blocks = fenced_code_blocks(cls.readme)

    def assert_uses_workload_selector(self, block: str):
        for annotation in (
            "reflection-allowed-namespaces-selector",
            "reflection-auto-namespaces-selector",
        ):
            self.assertRegex(
                block,
                re.escape(f"reflector.v1.k8s.emberstack.com/{annotation}")
                + r'=(["\'])'
                + re.escape(WORKLOAD_NAMESPACE_SELECTOR)
                + r"\1",
            )

    def test_local_env_defines_the_single_admin_account_and_is_ignored(self):
        block = code_block_containing(
            self.code_blocks,
            "REGISTRY_USERNAME='admin'",
        )

        self.assertIn(
            "REGISTRY_HOST='registry.homelab.robinjoon.xyz'",
            block,
        )
        self.assertIn("REGISTRY_USERNAME='admin'", block)
        self.assertIn("REGISTRY_PASSWORD=''", block)
        self.assertIn("local.env", self.gitignore.splitlines())
        self.assertEqual(
            re.findall(r"(?m)^REGISTRY_USERNAME=.*$", self.readme),
            ["REGISTRY_USERNAME='admin'"],
        )

        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", "local.env"],
            cwd=REPOSITORY_ROOT,
            check=False,
        )
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "local.env"],
            cwd=REPOSITORY_ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(ignored.returncode, 0)
        self.assertNotEqual(tracked.returncode, 0)

    def test_bootstrap_creates_one_admin_account(self):
        block = code_block_containing(
            self.code_blocks,
            "create secret generic zot-auth",
        )

        self.assertIn(
            'htpasswd -nbB "$REGISTRY_USERNAME" "$REGISTRY_PASSWORD"',
            block,
        )
        self.assertIn("--from-literal=htpasswd=", block)
        docker_secret_marker = "create secret docker-registry registry-credentials"
        self.assertIn(docker_secret_marker, block)
        self.assertLess(
            block.index("create secret generic zot-auth"),
            block.index(docker_secret_marker),
        )
        for forbidden in (
            "ZOT_HTPASSWD_FILE",
            "DOCKER_CONFIG",
            "mktemp",
            "--from-file=htpasswd",
            "--from-file=.dockerconfigjson",
        ):
            self.assertNotIn(forbidden, self.readme)
        self.assertNotIn("cluster-pull", self.readme)
        self.assertNotIn("ci-push", self.readme)
        self.assertNotIn("registry-pull-credentials", self.readme)

    def test_reflected_secret_uses_the_same_admin_account(self):
        block = code_block_containing(
            self.code_blocks,
            "create secret docker-registry registry-credentials",
        )

        self.assertIn('--docker-server="$REGISTRY_HOST"', block)
        self.assertIn('--docker-username="$REGISTRY_USERNAME"', block)
        self.assertIn('--docker-password="$REGISTRY_PASSWORD"', block)
        self.assertNotIn("docker login", block)
        self.assertNotIn("DOCKER_CONFIG", block)

    def test_registry_and_database_secrets_use_the_workload_selector(self):
        registry_block = code_block_containing(
            self.code_blocks,
            "annotate secret registry-credentials",
        )
        database_block = code_block_containing(
            self.code_blocks,
            "annotate secret shared-db-app",
        )

        self.assert_uses_workload_selector(registry_block)
        self.assert_uses_workload_selector(database_block)
        self.assertNotIn(
            "reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces=",
            self.readme,
        )
        self.assertNotIn(
            "reflector.v1.k8s.emberstack.com/reflection-auto-namespaces=",
            self.readme,
        )

    def test_workload_values_reference_the_reflected_pull_secret(self):
        block = code_block_containing(self.code_blocks, '"imagePullSecrets"')
        values = json.loads(block)

        self.assertEqual(
            values["workload"]["imagePullSecrets"],
            [{"name": "registry-credentials"}],
        )


if __name__ == "__main__":
    unittest.main()
