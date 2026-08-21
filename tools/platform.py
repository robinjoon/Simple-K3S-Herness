#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
WORKLOADS_DIR = BASE_DIR / "workloads"
ARGOCD_APPS_DIR = BASE_DIR / "argocd" / "managed" / "apps"
CHART_DIR = BASE_DIR / "chart"

def run_cmd(cmd, cwd=None):
    res = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    return res

def app_create(args):
    app_name = args.name
    wl_dir = WORKLOADS_DIR / app_name
    
    if wl_dir.exists():
        print(f"Error: Workload {app_name} already exists.")
        sys.exit(1)
        
    wl_dir.mkdir(parents=True)
    
    values = {
        "contractVersion": 1,
        "metadata": {
            "name": app_name,
            "namespace": app_name
        },
        "workload": {
            "kind": args.kind,
            "replicas": 1,
            "containers": [
                {
                    "name": "app",
                    "image": {
                        "repository": args.image.split(':')[0],
                        "tag": args.image.split(':')[1] if ':' in args.image else "latest"
                    }
                }
            ]
        }
    }
    if args.db_name:
        values["database"] = {
            "name": args.db_name
        }
        
    if getattr(args, 'file', None):
        with open(args.file, 'r') as f:
            user_data = json.load(f)
        dict_merge(values, user_data)
        
    # Validate before saving
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        json.dump(values, tmp)
        tmp_path = tmp.name
        
    res = run_cmd(["helm", "lint", str(CHART_DIR), "-f", tmp_path])
    if res.returncode != 0:
        print("Validation failed during create!")
        print(res.stdout)
        print(res.stderr)
        # Cleanup created dir on failure
        import shutil
        shutil.rmtree(wl_dir)
        sys.exit(1)
        
    val_file = wl_dir / "values.json"
    val_file.write_text(json.dumps(values, indent=2))
    
    # Create ArgoCD app
    app_yaml = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": app_name,
            "namespace": "argocd",
            "finalizers": ["resources-finalizer.argocd.argoproj.io"]
        },
        "spec": {
            "project": "homelab-workloads",
            "source": {
                "repoURL": "https://github.com/robinjoon/Simple-K3S-Herness.git", # Example repo
                "targetRevision": "main",
                "path": "chart",
                "helm": {
                    "releaseName": app_name,
                    "valueFiles": [f"../workloads/{app_name}/values.json"]
                }
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": app_name
            },
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["CreateNamespace=true"]
            }
        }
    }
    
    ARGOCD_APPS_DIR.mkdir(parents=True, exist_ok=True)
    app_yaml_file = ARGOCD_APPS_DIR / f"{app_name}.yaml"
    
    # Quick dump for yaml
    yaml_str = f"""apiVersion: argoproj.io/v1alpha1
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
    app_yaml_file.write_text(yaml_str)
    print(f"Created {app_name} successfully.")

def dict_merge(dct, merge_dct):
    for k, v in merge_dct.items():
        if (k in dct and isinstance(dct[k], dict)
                and isinstance(merge_dct[k], dict)):
            dict_merge(dct[k], merge_dct[k])
        else:
            dct[k] = merge_dct[k]

def app_patch(args):
    app_name = args.name
    val_file = WORKLOADS_DIR / app_name / "values.json"
    if not val_file.exists():
        print(f"Error: Workload {app_name} not found.")
        sys.exit(1)
        
    with open(args.file, 'r') as f:
        patch_data = json.load(f)
        
    current_data = json.loads(val_file.read_text())
    dict_merge(current_data, patch_data)
    
    # Save to temp and validate
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        json.dump(current_data, tmp)
        tmp_path = tmp.name
        
    res = run_cmd(["helm", "lint", str(CHART_DIR), "-f", tmp_path])
    if res.returncode != 0:
        print("Validation failed!")
        print(res.stdout)
        print(res.stderr)
        sys.exit(1)
        
    val_file.write_text(json.dumps(current_data, indent=2))
    print(f"Patched {app_name} successfully.")

def app_validate(args):
    app_name = args.name
    val_file = WORKLOADS_DIR / app_name / "values.json"
    if not val_file.exists():
        print(f"Error: Workload {app_name} not found.")
        sys.exit(1)
        
    res = run_cmd(["helm", "lint", str(CHART_DIR), "-f", str(val_file)])
    if res.returncode != 0:
        print(f"Validation failed for {app_name}")
        print(res.stdout)
        print(res.stderr)
        sys.exit(1)
    print(f"Validation successful for {app_name}")

def app_get(args):
    app_name = args.name
    val_file = WORKLOADS_DIR / app_name / "values.json"
    if not val_file.exists():
        print(f"Error: Workload {app_name} not found.")
        sys.exit(1)
    print(val_file.read_text())

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    
    parser_create = subparsers.add_parser("create")
    parser_create.add_argument("name")
    parser_create.add_argument("--kind", default="deployment")
    parser_create.add_argument("--image", required=True)
    parser_create.add_argument("--db-name", help="DB name to create/connect")
    parser_create.add_argument("--file", help="JSON file to apply initial configuration all at once")
    
    parser_patch = subparsers.add_parser("patch")
    parser_patch.add_argument("name")
    parser_patch.add_argument("--file", required=True)
    
    parser_validate = subparsers.add_parser("validate")
    parser_validate.add_argument("name")
    
    parser_get = subparsers.add_parser("get")
    parser_get.add_argument("name")
    
    args = parser.parse_args()
    
    if args.cmd == "create":
        app_create(args)
    elif args.cmd == "patch":
        app_patch(args)
    elif args.cmd == "validate":
        app_validate(args)
    elif args.cmd == "get":
        app_get(args)

if __name__ == "__main__":
    main()
