# K3s + Argo CD 워크로드 관리 시스템 설계서

## 1. 목적과 범위

이 저장소는 AI 에이전트가 Kubernetes 매니페스트를 직접 생성하지 않고, 제한된 JSON Contract와 CLI를 통해 K3s 홈랩 앱을 배포하게 하는 GitOps 하네스다.

현재 공식 지원 범위는 단순한 `Deployment` 기반 앱이다. 고가용성, 앱별 데이터베이스 인스턴스, 앱별 PostgreSQL 계정/Secret은 목표가 아니다. 공유 데이터베이스와 자체 컨테이너 레지스트리는 이 계약으로 생성하는 워크로드가 아니라, 별도의 공통 인프라로 관리한다.

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
- `database.name`이 있을 때 모든 컨테이너에 플랫폼 관리 `DB_HOST` FQDN

워크로드 계약에는 컨테이너 이미지/포트, replicas, 기존 registry Secret을 가리키는 `imagePullSecrets`, 환경변수와 ConfigMap·Secret 참조, 볼륨·마운트, 서비스·Ingress·TLS, 논리적 database 이름이 포함된다. Database 워크로드의 `DB_HOST`는 플랫폼 예약 이름이며 워크로드 values에서 직접 정의할 수 없다. StatefulSet, DaemonSet, CronJob, 임의 raw manifest, existing-secret TLS 모드는 공식 계약이 아니다.

`imagePullSecrets`는 Secret 이름만 받는다. 레지스트리 사용자 이름, 비밀번호, 토큰은 values에 저장하지 않으며, 워크로드는 Reflector가 미리 복제한 `registry-credentials` Secret의 이름만 참조한다.

## 3. 데이터베이스 모델

`infrastructure/shared-db`의 CloudNativePG Cluster 하나가 `database-system`에 배포된다. 앱이 `database.name`을 선언하면 공통 Cluster 안에 CNPG `Database` 리소스를 만들고 owner는 공유 `defaultuser`를 사용한다. 접속 Secret도 공유 `shared-db-app`을 Reflector로 앱 네임스페이스에 복제한다.

CNPG가 생성한 Secret의 `host`와 `pgpass`는 같은 네임스페이스의 짧은 Service 이름을 사용하므로 다른 네임스페이스의 복제본에서는 유효하지 않다. `dbname`과 모든 URI 키는 공유 Cluster의 bootstrap database를 가리켜 앱별 논리적 database와 일치하지 않는다. 하네스는 원본 Secret의 `data`를 수정하지 않고, 워크로드가 `database`를 선언하면 `platform/defaults.json`이 관리하는 `shared-db-rw.database-system.svc.cluster.local`을 모든 컨테이너의 첫 번째 환경변수 `DB_HOST`로 주입한다. 이후 환경변수의 `$(DB_HOST)` 확장이 이 값을 사용할 수 있도록 순서를 고정하고, values의 수동 `DB_HOST` 선언은 렌더 단계에서 거부한다. 복제 Secret은 명시적인 `port`, `username`, `password` 참조에만 사용할 수 있으며, 다른 키 참조와 전체 `envFrom`·볼륨 마운트는 렌더 단계에서 거부한다.

CLI가 생성하는 Child Application은 Argo CD `managedNamespaceMetadata`로 앱 네임스페이스에 `simple-k3s-harness.dev/workload=true` 라벨을 붙인다. Reflector는 이 라벨 셀렉터와 일치하는 네임스페이스에만 `shared-db-app`을 자동 복제한다. 따라서 Secret 공유 범위는 모든 네임스페이스가 아니라 하네스가 관리하는 워크로드 네임스페이스로 제한된다.

따라서 데이터베이스 이름은 앱별로 구분되지만 PostgreSQL 서버, 계정, Secret은 공유된다. 이 단순화는 홈랩 목표에 맞춘 의도적인 선택이며, 계정별 권한 격리나 앱별 인스턴스 분리를 제공하지 않는다.

## 4. 컨테이너 레지스트리 모델

자체 컨테이너 레지스트리는 공식 zot Helm Chart를 사용하는 `default` Argo CD Project의 인프라 Application이다. `registry-system` 네임스페이스에 `replicaCount: 1`인 StatefulSet으로 배포하고, 이미지 데이터는 `local-path` StorageClass의 RWO PVC 하나에 저장한다. zot의 Service는 `ClusterIP`으로만 열며 외부 요청은 cert-manager 인증서로 TLS를 종료하는 Traefik Ingress를 거친다. NetworkPolicy는 `kube-system`의 Traefik에서 zot의 `5000/TCP` 포트로 들어오는 요청만 허용한다.

zot에 내장된 htpasswd 인증과 저장소 ACL을 사용하고 익명 접근은 허용하지 않는다. 계정은 `admin` 하나만 사용하며 모든 저장소의 읽기, 생성, 갱신, 삭제를 허용한다.

평문 비밀번호는 Git에서 제외한 `local.env`와 비밀번호 관리 도구에 보관한다. 이 값으로 htpasswd 형식의 `zot-auth`와 같은 `admin` 자격 증명을 담은 `kubernetes.io/dockerconfigjson` 형식의 `registry-credentials`를 `registry-system`에 만든다. Reflector는 `registry-credentials`를 `simple-k3s-harness.dev/workload=true` 셀렉터에 맞는 네임스페이스에만 자동 복제하고, 워크로드 values는 복제된 Secret의 이름만 `imagePullSecrets`로 참조한다. 이 단순화로 인해 해당 Secret을 읽을 수 있는 워크로드는 레지스트리의 이미지를 pull할 뿐 아니라 push, 덮어쓰기, 삭제도 할 수 있으며, 이를 홈랩 단일 운영자 환경의 의도적인 절충으로 받아들인다.

이 구성은 홈랩용 단일 인스턴스이므로 고가용성을 제공하지 않는다. zot 또는 해당 노드가 중단되면 새 Pod의 이미지 pull과 신규 배포가 실패할 수 있지만, 이미 실행 중인 Pod는 이미지를 다시 요청하지 않는 한 계속 동작한다. `local-path` 볼륨의 스냅샷과 외부 백업, 복구 검증은 이 저장소 밖의 운영 책임이며, 노드나 디스크를 잃으면 백업이 없는 이미지는 복구할 수 없다.

`registry.homelab.robinjoon.xyz`가 Traefik 진입점을 가리키도록 하는 DNS 레코드는 외부 접근의 선행 조건이지만 이 저장소에서 생성하지 않는다. Tailscale을 사용한다면 tailnet, 인증 정보, 라우팅, 접근 정책은 Ingress 앞단의 외부 네트워크 계층으로 둔다. 이는 zot 인증과 ACL을 대체하지 않으며, Tailscale을 사용하지 않을 때도 TLS와 zot 접근 제어는 유지한다.

## 5. CLI 계약

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

## 6. Argo CD 정책

`homelab-workloads` AppProject는 소스 저장소를 이 저장소 URL(`https://github.com/robinjoon/Simple-K3S-Herness.git`)로 제한한다. 대상 서버는 기본 Kubernetes API 서버이며 앱마다 namespace가 달라 destinations의 `namespace: "*"`는 유지한다. 이는 모든 namespace에 임의로 배포한다는 운영 목표가 아니라, Child Application의 앱별 namespace를 하나의 Project에서 수용하기 위한 설정이다.

워크로드 Project는 Namespace 생성과 공통 Chart가 직접 만드는 Deployment, Service, ConfigMap, Ingress, cert-manager Certificate, CNPG Database를 허용한다. Argo CD 리소스 트리에서 컨트롤러가 만든 하위 리소스를 확인할 수 있도록 ReplicaSet, Pod, Secret, CertificateRequest, Order, Challenge도 허용한다. 이 하위 리소스들은 JSON Contract가 직접 생성하지 않는다.

공유 CNPG Cluster와 zot 레지스트리 같은 인프라 리소스는 `default` Project의 인프라 Application과 Root Application이 관리하며, 워크로드 Project에는 이 리소스의 생성 권한을 주지 않는다. `platform/defaults.json`은 모든 앱 values보다 먼저 병합되고 워크로드 계약에서는 덮어쓸 수 없다.
