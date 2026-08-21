---
name: homelab-k3s-workloads
description: >
  Creates, inspects, modifies, validates and removes workloads in this
  homelab K3s GitOps repository using tools/platform.py.
  Use for tasks involving workload deployment, containers, services,
  ingress, TLS, persistence, configuration, RBAC or resource settings.
---

# Skill: homelab-k3s-workloads

## 1. Description
Creates, inspects, modifies, validates and removes workloads in this homelab K3s GitOps repository using `tools/platform.py`. 
Use for tasks involving workload deployment, containers, services, ingress, TLS, configuration, RBAC or resource settings.

## 2. 작업 순서 (Workflow Protocol)
1. 작업 시작 전 `platform.py doctor` (또는 `platform.py --help`) 실행
2. 기존 워크로드 수정 시 `platform.py app get <app_name>`으로 현재 JSON 스펙 확인
3. 생성은 **반드시** `platform.py app create <app_name> --kind <kind> --image <image>` 사용 (DB가 필요한 경우 `--db-name <db_name>`을 반드시 포함할 것)
4. 변경은 **반드시** 파일 기반 패치 사용 (`platform.py app patch <app_name> --file <json-file>`)
5. 변경 후 `platform.py app validate <app_name>`으로 이상 유무 검증

## 3. 엄격한 금지 사항 (CRITICAL PROHIBITIONS) 🚨
- **NEVER** create another Helm chart.
- **NEVER** create Kubernetes YAML files for an individual workload.
- **NEVER** directly modify `workloads/*/values.json` without CLI.
- **NEVER** directly modify `argocd/managed/apps/*.yaml`.
- **NEVER** use `kubectl apply` or `helm install/upgrade`.
- **NEVER** use the Argo CD CLI to create or modify Applications.
- **NEVER** inject arbitrary pod specs, extraObjects, or raw manifests.

## 4. 미지원 기능 처리 (Handling Unsupported Capabilities)
사용자가 GPU 할당 등 `platform.py`나 `schema`에서 지원하지 않는 리소스를 요청할 경우:
- 임의로 YAML을 우회 생성하여 적용하려 하지 마십시오 (Do not work around).
- 즉시 작업을 중지(STOP)하고 "현재 Homelab Workload Contract에서 해당 기능을 지원하지 않으므로 Base Chart 확장이 필요합니다"라고 사용자에게 보고하십시오.

## 5. 고급 설정 스펙 (ConfigMap, Env, Volumes) 작성 규약
패치(`patch`)를 통해 환경변수나 설정 파일을 주입할 때는 K8s 원시 리소스(YAML) 형식이 아닌, 아래의 **JSON Contract** 구조를 반드시 준수하십시오.
특히 ConfigMap의 `data`는 반드시 단순한 **키-값(Key-Value) 쌍의 객체(Object)** 여야 합니다.

**[올바른 JSON 패치 작성 예시]**
```json
{
  "configMaps": [
    {
      "name": "env-config",
      "data": {
        "LOG_LEVEL": "info",
        "ENABLE_FEATURE_X": "true"
      }
    },
    {
      "name": "file-config",
      "data": {
        "nginx.conf": "server { listen 80; ... }"
      }
    }
  ],
  "workload": {
    "volumes": [
      {
        "name": "nginx-conf-vol",
        "configMap": { "name": "file-config" }
      }
    ],
    "containers": [
      {
        "name": "app",
        "env": [
          {
            "name": "DB_PASSWORD",
            "secretKeyRef": { "name": "shared-db-app", "key": "password" }
          }
        ],
        "envFrom": [
          { "configMapRef": { "name": "env-config" } }
        ],
        "volumeMounts": [
          {
            "name": "nginx-conf-vol",
            "mountPath": "/etc/nginx/nginx.conf",
            "subPath": "nginx.conf",
            "readOnly": true
          }
        ]
      }
    ]
  }
}
```
