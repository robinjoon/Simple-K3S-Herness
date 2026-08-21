# 🏗️ AI 에이전트용 K3s + ArgoCD 워크로드 관리 시스템 설계서

## 1. 개요 및 설계 철학
이 시스템은 AI 에이전트가 K3s + ArgoCD 환경에 워크로드를 배포할 때 발생하는 환각(Hallucination) 현상과 규격 이탈을 원천 차단하기 위해 고안되었습니다.

**"AI가 Kubernetes를 이해하게 만들지 말고, 통제된 단일 인터페이스(Contract)만 조작하게 만든다"**는 철학을 바탕으로 합니다.

### 4대 핵심 원칙
1. **No Raw YAML**: 에이전트는 어떠한 경우에도 개별 K8s 매니페스트(Deployment, Ingress 등)를 직접 작성할 수 없습니다.
2. **단일 Base Chart**: 모든 워크로드는 시스템이 제공하는 단일 Helm Chart만을 통해 렌더링됩니다.
3. **JSON Contract**: 에이전트는 YAML 대신 다루기 안전한 JSON 형식의 추상화된 워크로드 모델만 조작합니다.
4. **1 Workload = 1 Namespace**: 워크로드 간의 격리성과 ArgoCD의 깔끔한 리소스 정리를 위해 1:1 매핑을 강제합니다.

---

## 2. 전체 아키텍처 및 디렉터리 구조

### 아키텍처 파이프라인
```text
[AI 에이전트] 
  │ (JSON 도메인 모델 생성/수정)
  ▼
[CLI 도구 (platform.py)] ──▶ (JSON Schema 검증 & helm lint)
  │ 
  ├─▶ workloads/<app>/values.json (워크로드 설정)
  └─▶ argocd/managed/apps/<app>.yaml (ArgoCD Child App)
  │
  ▼ (Git Commit & Push)
[ArgoCD App-of-Apps]
  │
  ▼ (단일 Base Chart 렌더링)
[K3s Cluster (Deployment, Service, Ingress, Certificate...)]
```

### Git 저장소 구조
```text
homelab/
├── chart/                      # 단일 Base Helm Chart (에이전트 수정 불가)
│   ├── Chart.yaml
│   ├── values.yaml             # 기본값
│   ├── values.schema.json      # ★ 엄격한 JSON Schema 검증 룰
│   └── templates/              # Deployment, Service, Ingress, Certificate 등
│
├── platform/
│   └── defaults.json           # 플랫폼 공통 설정 (Traefik, ClusterIssuer 등)
│
├── workloads/                  # 개별 워크로드 설정 (JSON)
│   └── my-api/
│       └── values.json
│
├── argocd/                     # ArgoCD App-of-Apps 매니페스트
│   ├── root.yaml
│   └── managed/
│       ├── project.yaml        # 보안 통제용 AppProject
│       └── apps/
│           └── my-api.yaml     # Child Application
│
├── tools/
│   └── platform.py             # ★ 유일한 에이전트 인터페이스 (CLI)
│
└── skills/
    └── homelab-k3s-workloads/
        └── SKILL.md            # 에이전트 행동 지침
```

---

## 3. 핵심 설계 요소

### 3.1 YAML 대신 JSON 사용 (`values.json`)
- 에이전트가 `platform.py`를 통해 데이터를 다룰 때 외부 의존성(PyYAML 등) 없이 Python 표준 라이브러리만으로 안전하게 파싱 및 병합(Merge)하기 위해 JSON을 사용합니다.
- Helm은 JSON을 완벽하게 지원합니다.

### 3.2 `values.schema.json`을 통한 원천 차단 (Guardrail)
- Python 코드 내에 수많은 `if` 문을 두는 대신, Helm의 기본 기능인 JSON Schema를 활용합니다.
- `additionalProperties: false`를 기본으로 설정하여, 에이전트가 존재하지 않는 필드(`extraObjects`, `rawManifests` 등)를 주입하려고 하면 `helm lint` 단계에서 즉시 실패(Fail-fast) 처리됩니다.

### 3.3 도메인 모델 추상화 (Homelab Workload Contract)
- K8s API 구조를 그대로 노출하지 않습니다.
- 에이전트는 `deployment`, `statefulset`, `daemonset`, `cronjob` 4가지 타입과 직관적인 포트, 인그레스 설정 등 **추상화된 스펙**만 다룹니다.
- Ingress의 TLS 인증서(cert-manager) 발급, StatefulSet의 Headless Service 자동 생성 등 복잡한 작업은 Base Chart 내부에서 자동으로 처리됩니다.

---

## 4. CLI 도구 (`platform.py`) 명세 및 보완책

에이전트는 이 CLI만 사용하여 작업을 수행해야 합니다. K8s SDK나 API 서버 직접 접근은 차단됩니다.

### 기본 명령어
```bash
python3 tools/platform.py doctor             # 플랫폼 상태 및 CLI 정상 작동 확인
python3 tools/platform.py schema             # 허용되는 JSON Schema 구조 확인
python3 tools/platform.py app list           # 현재 워크로드 목록 조회
python3 tools/platform.py app get my-api     # 특정 워크로드 설정 조회 (JSON)
python3 tools/platform.py app validate --all # 전체 워크로드 렌더링 및 유효성 검증
```

### 💡 [보완 적용] 생성 및 패치 (JSON Escaping 오류 방지)
에이전트가 긴 JSON 문자열을 Bash에 직접 입력하다가 따옴표 Escaping 에러를 내는 것을 방지하기 위해, **파일 기반 패치 옵션**을 강력히 권장합니다.

```bash
# 1. 앱 생성
python3 tools/platform.py app create my-api \
  --kind deployment \
  --image ghcr.io/example/my-api:1.4.2

# 2. 앱 설정 수정 (JSON Merge Patch 방식)
# AI는 /tmp/patch.json 에 수정할 JSON 내용을 먼저 저장한 뒤 명령을 실행합니다.
cat << 'INNER_EOF' > /tmp/patch.json
{
  "workload": {
    "replicas": 2
  },
  "ingresses": [
    {
      "name": "public",
      "service": "http",
      "rules": [{"host": "api.example.com", "paths": [{"path": "/"}]}],
      "tls": {"mode": "cert-manager"}
    }
  ]
}
INNER_EOF

python3 tools/platform.py app patch my-api --file /tmp/patch.json
```

---

## 5. 보안 및 권한 제어 (Safety Nets)

### 5.1 ArgoCD `AppProject`를 통한 2차 방어선
- `argocd/managed/project.yaml`에 워크로드 배포용 Project를 엄격하게 정의합니다.
- **Cluster-scoped 리소스 배포 전면 금지** (예: `ClusterRole`, `ClusterIssuer` 등).
- 허용된 Namespace-scoped 리소스(`Deployment`, `Service`, `Ingress`, `ConfigMap` 등)만 화이트리스트 처리하여, 에이전트가 버그로 이상한 매니페스트를 만들더라도 ArgoCD 단에서 배포를 차단합니다.

### 5.2 💡 [보완 적용] Secret 관리 원칙 고정
- **Git 내에 평문 Secret 보관 및 생성을 절대 금지**합니다.
- `values.json`에서는 **기존 Secret 참조(`secretKeyRef`)**만 허용합니다.
- 새로운 Secret 생성이 필요할 경우, 이는 AI 시스템 밖(클러스터 관리자)에서 사전에 생성되어야 하며, AI는 해당 Secret의 존재 유무를 확인한 뒤 참조만 구성하도록 정책을 강제합니다.

---

## 6. AI 에이전트 스킬 지침 (`SKILL.md`)

AI 에이전트가 이 생태계에 접근할 때 강제로 주입해야 할 시스템 프롬프트(SKILL) 규약입니다.

```markdown
# Skill: homelab-k3s-workloads

## 1. Description
Creates, inspects, modifies, validates and removes workloads in this homelab K3s GitOps repository using `tools/platform.py`. 
Use for tasks involving workload deployment, containers, services, ingress, TLS, configuration, RBAC or resource settings.

## 2. 작업 순서 (Workflow Protocol)
1. 작업 시작 전 `platform.py doctor` 실행
2. 기존 워크로드 수정 시 `app get`으로 현재 JSON 스펙 확인
3. 필요한 기능이 있을 경우 `platform.py schema`로 지원 여부 확인
4. 변경은 **반드시** `app create` 및 `app patch --file <json-file>` 사용
5. 변경 후 `app validate`로 이상 유무 검증
6. `app render`로 최종 생성될 매니페스트 확인 후 Git 커밋

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
```
