#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
WORKLOADS_DIR = BASE_DIR / "workloads"
ARGOCD_APPS_DIR = BASE_DIR / "argocd" / "managed" / "apps"
CHART_DIR = BASE_DIR / "chart"
DEFAULTS_FILE = BASE_DIR / "platform" / "defaults.json"
SCHEMA_FILE = CHART_DIR / "values.schema.json"

DNS_1123_LABEL = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
RESERVED_NAMESPACES = {
    "argocd", "cnpg-system", "database-system", "default", "kube-node-lease",
    "kube-public", "kube-system",
}


def run_cmd(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)


def fail(message):
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_app_name(app_name):
    if not DNS_1123_LABEL.fullmatch(app_name) or len(app_name) > 63:
        fail("App name must be a DNS-1123 label (lowercase letters, numbers, and hyphens; 63 characters or fewer).")
    if app_name in RESERVED_NAMESPACES:
        fail(f"App name {app_name!r} is a reserved namespace.")


def parse_image(image):
    if not image or "@" in image:
        fail("Image digests are not supported; use a tagged image instead.")
    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon > last_slash:
        repository, tag = image[:last_colon], image[last_colon + 1:]
        if not repository or not tag:
            fail("Image must include a repository and a non-empty tag.")
        return repository, tag
    return image, "latest"


def dict_merge(dct, merge_dct):
    for key, value in merge_dct.items():
        if key in dct and isinstance(dct[key], dict) and isinstance(value, dict):
            dict_merge(dct[key], value)
        else:
            dct[key] = value


def load_json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Could not read JSON from {path}: {exc}")
    if not isinstance(data, dict):
        fail(f"JSON object expected in {path}.")
    return data


def ensure_workload_values(values, app_name):
    if "platform" in values:
        fail("platform is managed by platform/defaults.json and cannot be overridden by a workload.")
    metadata = values.get("metadata")
    if not isinstance(metadata, dict):
        fail("metadata must be an object.")
    if metadata.get("name") != app_name or metadata.get("namespace") != app_name:
        fail("metadata.name and metadata.namespace must both match the app name.")


def helm_value_files(values_file):
    return ["-f", str(DEFAULTS_FILE), "-f", str(values_file)]


def lint_values(values):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(values, tmp)
            tmp_path = Path(tmp.name)
        return run_cmd(["helm", "lint", str(CHART_DIR), *helm_value_files(tmp_path)])
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def lint_file(values_file):
    return run_cmd(["helm", "lint", str(CHART_DIR), *helm_value_files(values_file)])


def print_command_failure(prefix, result):
    print(prefix, file=sys.stderr)
    if result.stdout:
        print(result.stdout, file=sys.stderr, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")


def values_file_for(app_name):
    validate_app_name(app_name)
    values_file = WORKLOADS_DIR / app_name / "values.json"
    if not values_file.is_file():
        fail(f"Workload {app_name} not found.")
    return values_file


def application_yaml(app_name):
    return f"""apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {app_name}
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: homelab-workloads
  source:
    repoURL: https://github.com/robinjoon/Simple-K3S-Herness.git
    targetRevision: main
    path: chart
    helm:
      releaseName: {app_name}
      valueFiles:
        - ../platform/defaults.json
        - ../workloads/{app_name}/values.json
  destination:
    server: https://kubernetes.default.svc
    namespace: {app_name}
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
"""


def app_create(args):
    app_name = args.name
    validate_app_name(app_name)
    wl_dir = WORKLOADS_DIR / app_name
    app_yaml_file = ARGOCD_APPS_DIR / f"{app_name}.yaml"
    if wl_dir.exists() or app_yaml_file.exists():
        fail(f"Workload {app_name} already exists.")

    repository, tag = parse_image(args.image)
    values = {
        "contractVersion": 1,
        "metadata": {"name": app_name, "namespace": app_name},
        "workload": {
            "kind": args.kind,
            "replicas": 1,
            "containers": [{"name": "app", "image": {"repository": repository, "tag": tag}}],
        },
    }
    if args.db_name:
        values["database"] = {"name": args.db_name}
    if args.file:
        dict_merge(values, load_json(args.file))
    ensure_workload_values(values, app_name)

    result = lint_values(values)
    if result.returncode != 0:
        print_command_failure("Validation failed during create.", result)
        raise SystemExit(1)

    wl_dir.mkdir(parents=True)
    (wl_dir / "values.json").write_text(json.dumps(values, indent=2) + "\n")
    ARGOCD_APPS_DIR.mkdir(parents=True, exist_ok=True)
    app_yaml_file.write_text(application_yaml(app_name))
    print(f"Created {app_name} successfully.")


def app_patch(args):
    app_name = args.name
    values_file = values_file_for(app_name)
    current_values = load_json(values_file)
    dict_merge(current_values, load_json(args.file))
    ensure_workload_values(current_values, app_name)

    result = lint_values(current_values)
    if result.returncode != 0:
        print_command_failure("Validation failed.", result)
        raise SystemExit(1)

    values_file.write_text(json.dumps(current_values, indent=2) + "\n")
    print(f"Patched {app_name} successfully.")


def app_validate(args):
    if args.all:
        app_names = app_names_in_workloads()
        if not app_names:
            print("No workloads found.")
            return
    else:
        if not args.name:
            fail("Provide a workload name or use --all.")
        app_names = [args.name]

    failures = []
    for app_name in app_names:
        values_file = values_file_for(app_name)
        ensure_workload_values(load_json(values_file), app_name)
        result = lint_file(values_file)
        if result.returncode != 0:
            failures.append(app_name)
            print_command_failure(f"Validation failed for {app_name}.", result)
        else:
            print(f"Validation successful for {app_name}.")
    if failures:
        raise SystemExit(1)


def app_get(args):
    print(values_file_for(args.name).read_text(), end="")


def app_list(_args):
    for app_name in app_names_in_workloads():
        print(app_name)


def app_names_in_workloads():
    if not WORKLOADS_DIR.is_dir():
        return []
    return sorted(path.name for path in WORKLOADS_DIR.iterdir() if (path / "values.json").is_file())


def app_render(args):
    app_name = args.name
    values_file = values_file_for(app_name)
    ensure_workload_values(load_json(values_file), app_name)
    result = run_cmd(["helm", "template", app_name, str(CHART_DIR), *helm_value_files(values_file)])
    if result.returncode != 0:
        print_command_failure(f"Render failed for {app_name}.", result)
        raise SystemExit(1)
    print(result.stdout, end="")


def doctor(_args):
    missing = [str(path) for path in (CHART_DIR, DEFAULTS_FILE, SCHEMA_FILE) if not path.exists()]
    if missing:
        fail(f"Required platform files are missing: {', '.join(missing)}")
    result = run_cmd(["helm", "version", "--short"])
    if result.returncode != 0:
        print_command_failure("Helm is not available.", result)
        raise SystemExit(1)
    print(f"Platform ready ({result.stdout.strip()}).")


def schema(_args):
    workload_schema = load_json(SCHEMA_FILE)
    workload_schema["properties"].pop("platform", None)
    print(json.dumps(workload_schema, indent=2) + "\n", end="")


def main():
    parser = argparse.ArgumentParser(description="Manage homelab workloads through the supported JSON contract.")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    parser_doctor = subparsers.add_parser("doctor", help="Check local platform prerequisites")
    parser_doctor.set_defaults(func=doctor)
    parser_schema = subparsers.add_parser("schema", help="Print the workload JSON schema")
    parser_schema.set_defaults(func=schema)
    parser_list = subparsers.add_parser("list", help="List workloads")
    parser_list.set_defaults(func=app_list)

    parser_create = subparsers.add_parser("create", help="Create a workload")
    parser_create.add_argument("name")
    parser_create.add_argument("--kind", choices=("deployment",), default="deployment")
    parser_create.add_argument("--image", required=True)
    parser_create.add_argument("--db-name", help="Database name in the shared PostgreSQL instance")
    parser_create.add_argument("--file", help="JSON file with initial configuration")
    parser_create.set_defaults(func=app_create)

    parser_get = subparsers.add_parser("get", help="Print workload values")
    parser_get.add_argument("name")
    parser_get.set_defaults(func=app_get)
    parser_patch = subparsers.add_parser("patch", help="Merge a JSON patch into workload values")
    parser_patch.add_argument("name")
    parser_patch.add_argument("--file", required=True)
    parser_patch.set_defaults(func=app_patch)
    parser_validate = subparsers.add_parser("validate", help="Validate one workload or all workloads")
    parser_validate.add_argument("name", nargs="?")
    parser_validate.add_argument("--all", action="store_true")
    parser_validate.set_defaults(func=app_validate)
    parser_render = subparsers.add_parser("render", help="Render a workload manifest")
    parser_render.add_argument("name")
    parser_render.set_defaults(func=app_render)

    args = parser.parse_args()
    if args.cmd == "validate" and args.all and args.name:
        parser.error("validate accepts either a workload name or --all, not both")
    args.func(args)


if __name__ == "__main__":
    main()
