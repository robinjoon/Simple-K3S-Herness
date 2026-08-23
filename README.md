# Simple-K3S-Herness (AI-Driven GitOps System)

K3s 홈랩에서 AI 에이전트가 제한된 JSON 계약과 CLI만으로 애플리케이션을 배포하도록 하는 소형 GitOps 하네스입니다. 에이전트는 Kubernetes YAML이나 Argo CD Application을 직접 작성하지 않고, `tools/platform.py`를 통해 `workloads/<app>/values.json`을 관리합니다.

## 설계 범위

- 공식 워크로드 종류는 `Deployment` 하나입니다.
- 앱마다 네임스페이스를 하나씩 사용합니다. 현재 계약은 앱 생성 시 앱 이름과 네임스페이스를 동일하게 만듭니다.
- 공통 Helm Chart가 Deployment, Service, ConfigMap, Ingress, cert-manager Certificate, 선택적 CNPG Database를 렌더링합니다. Private registry는 기존 Secret을 `imagePullSecrets`로 참조할 수 있습니다.
- PostgreSQL은 `database-system`의 CloudNativePG Cluster 하나와 공유 `defaultuser` 계정을 사용합니다. 앱별로 분리되는 것은 논리적 database 이름뿐이며, 앱별 DB 인스턴스·계정·Secret·HA를 만들지 않습니다.
- Argo CD App-of-Apps가 Git 변경을 동기화하고 prune/self-heal을 수행합니다.

## 저장소 구조

```text
argocd/               # Root/Application 및 AppProject
chart/                # 유일한 공통 Helm Chart와 JSON Schema
infrastructure/       # CNPG, Reflector 등 공통 인프라
platform/             # 모든 앱에 먼저 적용되는 플랫폼 공통 Helm 기본값
skills/               # AI 에이전트 작업 지침
tools/platform.py     # 유일한 워크로드 관리 CLI
workloads/            # CLI가 생성한 values.json
```

## 초기 연동

Argo CD와 CNPG/Reflector를 클러스터에 설치한 뒤 Root Application을 등록합니다.

```bash
kubectl apply -f argocd/root.yaml
```

CNPG가 `database-system` 네임스페이스에 생성한 `shared-db-app` Secret은 Reflector가 앱 네임스페이스로 복제할 수 있도록 한 번 표시해야 합니다.

```bash
kubectl annotate secret shared-db-app -n database-system \
  reflector.v1.k8s.emberstack.com/reflection-allowed="true" \
  reflector.v1.k8s.emberstack.com/reflection-auto-enabled="true"
```

이 Secret은 공유 PostgreSQL 계정의 접속 정보입니다. 앱별 Secret을 발급하는 기능은 범위에 포함하지 않습니다.

## CLI 사용법

AI 에이전트는 아래 flat 명령만 사용합니다. `app create` 같은 중첩 명령이나 `delete` 명령은 제공하지 않습니다.

```bash
python3 tools/platform.py doctor
python3 tools/platform.py schema
python3 tools/platform.py list
python3 tools/platform.py create my-api --image ghcr.io/my-org/my-api:v1.0.0 --db-name my_api_db
python3 tools/platform.py get my-api
python3 tools/platform.py patch my-api --file /tmp/patch.json
python3 tools/platform.py validate my-api
python3 tools/platform.py validate --all
python3 tools/platform.py render my-api
```

`--db-name`을 지정하면 공유 `shared-db` Cluster 안에 해당 논리적 database를 선언합니다. 환경변수, ConfigMap, Secret 참조, 볼륨 마운트, Service, Ingress, cert-manager TLS는 JSON 계약이 허용하는 범위에서 설정할 수 있습니다. 평문 Secret 값은 Git에 저장하지 않습니다.

Private registry를 사용할 때는 `kubernetes.io/dockerconfigjson` 타입 Secret을 앱 네임스페이스에 미리 만든 뒤 이름만 참조합니다.

```json
{
  "workload": {
    "imagePullSecrets": [
      {"name": "registry-credentials"}
    ]
  }
}
```

CLI와 Chart는 registry Secret을 생성하거나 인증정보를 values에 저장하지 않습니다.

`platform/defaults.json`은 CLI와 Argo CD가 앱 values보다 먼저 적용하며, 앱 계약으로 덮어쓸 수 없습니다.

변경 후 검증하고 Git에 커밋·푸시하면 Argo CD가 클러스터에 반영합니다. CLI의 검증은 계약과 Helm 렌더링을 확인하는 단계이며, 실제 클러스터 상태나 모든 Kubernetes 운영 조건을 보장하지는 않습니다.
