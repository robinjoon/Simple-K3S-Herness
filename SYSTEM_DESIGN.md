# K3s + Argo CD 워크로드 관리 시스템 설계서

## 1. 목적과 범위

이 저장소는 AI 에이전트가 Kubernetes 매니페스트를 직접 생성하지 않고, 제한된 JSON Contract와 CLI를 통해 K3s 홈랩 앱을 배포하게 하는 GitOps 하네스다.

현재 공식 지원 범위는 단순한 `Deployment` 기반 앱이다. 고가용성, 앱별 데이터베이스 인스턴스, 앱별 PostgreSQL 계정/Secret은 목표가 아니다.

### 핵심 원칙

1. 에이전트는 개별 Kubernetes YAML을 작성하지 않는다.
2. 모든 앱은 하나의 공통 Helm Chart로 렌더링한다.
3. 에이전트가 조작하는 것은 `workloads/<name>/values.json`뿐이다.
4. 앱마다 네임스페이스를 사용한다.
5. PostgreSQL은 공유 Cluster와 공유 `defaultuser`를 사용하고, 논리적 database 이름만 앱별로 나눈다.

## 2. 처리 흐름

```text
AI 에이전트
  │ tools/platform.py (doctor/schema/list/create/get/patch/validate/render)
  ▼
workloads/<app>/values.json + argocd/managed/apps/<app>.yaml
  │ Git push
  ▼
Argo CD Root Application → Child Application → 공통 Helm Chart → K3s
```

Chart가 계약에 따라 다음 리소스를 렌더링한다.

- Deployment
- 선택적 Service, ConfigMap, Ingress
- 선택적 cert-manager Certificate
- `database.name`이 있을 때 CNPG `Database`

워크로드 계약에는 컨테이너 이미지/포트, replicas, 기존 registry Secret을 가리키는 `imagePullSecrets`, 환경변수와 ConfigMap·Secret 참조, 볼륨·마운트, 서비스·Ingress·TLS, 논리적 database 이름이 포함된다. StatefulSet, DaemonSet, CronJob, 임의 raw manifest, existing-secret TLS 모드는 공식 계약이 아니다.

`imagePullSecrets`는 Secret 이름만 받는다. registry 사용자명·비밀번호·토큰은 values에 저장하지 않으며, Secret은 운영자가 앱 네임스페이스에 미리 준비한다.

## 3. 데이터베이스 모델

`infrastructure/shared-db`의 CloudNativePG Cluster 하나가 `database-system`에 배포된다. 앱이 `database.name`을 선언하면 공통 Cluster 안에 CNPG `Database` 리소스를 만들고 owner는 공유 `defaultuser`를 사용한다. 접속 Secret도 공유 `shared-db-app`을 Reflector로 앱 네임스페이스에 복제한다.

따라서 데이터베이스 이름은 앱별로 구분되지만 PostgreSQL 서버, 계정, Secret은 공유된다. 이 단순화는 홈랩 목표에 맞춘 의도적인 선택이며, 계정별 권한 격리나 앱별 인스턴스 분리를 제공하지 않는다.

## 4. CLI 계약

에이전트가 사용할 명령은 flat 형태로 고정한다.

```text
doctor
schema
list
create NAME --image IMAGE [--db-name NAME] [--file JSON]
get NAME
patch NAME --file JSON
validate NAME | validate --all
render NAME
```

에이전트는 CLI를 우회해 values 파일, Argo Application, Helm Chart를 직접 수정하지 않는다. `delete`는 제공하지 않으므로 삭제가 필요하면 운영자가 별도 절차를 수행한다.

검증은 JSON Schema와 Helm lint/렌더링에 초점을 둔다. 이것은 클러스터 API 검증, Secret 존재 확인, 네트워크 연결 확인 또는 무중단 배포 보장이 아니다.

## 5. Argo CD 정책

`homelab-workloads` AppProject는 소스 저장소를 이 저장소 URL(`https://github.com/robinjoon/Simple-K3S-Herness.git`)로 제한한다. 대상 서버는 기본 Kubernetes API 서버이며 앱마다 namespace가 달라 destinations의 `namespace: "*"`는 유지한다. 이는 모든 namespace에 임의로 배포한다는 운영 목표가 아니라, Child Application의 앱별 namespace를 하나의 Project에서 수용하기 위한 설정이다.

워크로드 Project는 Namespace 생성과 공통 Chart가 직접 만드는 Deployment, Service, ConfigMap, Ingress, cert-manager Certificate, CNPG Database를 허용한다. Argo CD 리소스 트리에서 컨트롤러가 만든 하위 리소스를 확인할 수 있도록 ReplicaSet, Pod, Secret, CertificateRequest, Order, Challenge도 허용한다. 이 하위 리소스들은 JSON Contract가 직접 생성하지 않는다.

공유 CNPG Cluster 같은 인프라 리소스는 `default` Project의 인프라 Application과 Root Application이 관리하며, 워크로드 Project에는 Cluster 생성 권한을 주지 않는다. `platform/defaults.json`은 모든 앱 values보다 먼저 병합되고 워크로드 계약에서는 덮어쓸 수 없다.
