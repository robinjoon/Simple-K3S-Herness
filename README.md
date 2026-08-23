# Simple-K3S-Herness (AI-Driven GitOps System)

K3s 홈랩에서 AI 에이전트가 제한된 JSON 계약과 CLI만으로 애플리케이션을 배포하도록 하는 소형 GitOps 하네스입니다. 에이전트는 Kubernetes YAML이나 Argo CD Application을 직접 작성하지 않고, `tools/platform.py`를 통해 `workloads/<app>/values.json`을 관리합니다.

## 설계 범위

- 공식 워크로드 종류는 `Deployment` 하나입니다.
- 앱마다 네임스페이스를 하나씩 사용합니다. 현재 계약은 앱 생성 시 앱 이름과 네임스페이스를 동일하게 만듭니다.
- 공통 Helm Chart가 Deployment, Service, ConfigMap, Ingress, cert-manager Certificate, 선택적 CNPG Database를 렌더링합니다. 비공개 레지스트리는 기존 Secret을 `imagePullSecrets`로 참조할 수 있습니다.
- PostgreSQL은 `database-system`의 CloudNativePG Cluster 하나와 공유 `defaultuser` 계정을 사용합니다. 앱별로 분리되는 것은 논리적 database 이름뿐이며, 앱별 DB 인스턴스, 계정, Secret, HA를 만들지 않습니다.
- 자체 컨테이너 레지스트리는 일반 워크로드 계약 밖의 공통 인프라입니다. zot을 `registry-system`에 `replicaCount: 1`인 StatefulSet과 RWO PVC로 배포합니다.
- Argo CD App-of-Apps가 Git 변경을 동기화하고 prune/self-heal을 수행합니다.

## 저장소 구조

```text
argocd/               # Root/Application 및 AppProject
chart/                # 유일한 공통 Helm Chart와 JSON Schema
infrastructure/       # CNPG, 레지스트리 NetworkPolicy 등 공통 인프라
platform/             # 모든 앱에 먼저 적용되는 플랫폼 공통 Helm 기본값
skills/               # AI 에이전트 작업 지침
tools/platform.py     # 유일한 워크로드 관리 CLI
workloads/            # CLI가 생성한 values.json
```

## 초기 연동

클러스터에는 Argo CD, Traefik, cert-manager와 `letsencrypt-prod` ClusterIssuer가 먼저 준비되어 있어야 합니다. Root Application은 CNPG, Reflector, 공유 DB, zot과 레지스트리 NetworkPolicy를 설치합니다.

### 1. DNS와 외부 접근 준비

레지스트리 주소는 `registry.homelab.robinjoon.xyz`입니다. 이 이름은 K3s의 모든 노드와 레지스트리를 사용하는 외부 클라이언트에서 Traefik 진입점을 가리켜야 합니다. 다른 주소를 사용하려면 `argocd/managed/apps/zot.yaml`의 Ingress host와 TLS host를 함께 변경하고 Git에 커밋하고 푸시한 뒤 연동을 시작합니다. Root Application은 로컬 파일이 아니라 원격 Git을 읽습니다.

외부 접근은 라우터의 공개 포트 포워딩 대신 Tailscale이나 WireGuard와 split DNS를 사용하는 구성을 권장합니다. `letsencrypt-prod`는 공개 443 포트를 열지 않아도 인증서를 발급할 수 있도록 DNS-01 방식으로 구성합니다. zot의 Service는 `ClusterIP`으로 유지하고 NodePort나 LoadBalancer로 직접 노출하지 않습니다. Tailscale의 tailnet, 라우팅, ACL은 이 저장소 밖의 외부 네트워크 계층이며 zot의 TLS, 인증, 저장소 ACL을 대체하지 않습니다.

### 2. 레지스트리 자격 증명 Secret 생성

저장소 루트의 `local.env`에 레지스트리 주소와 단일 `admin` 계정 정보를 넣습니다. 이 파일은 `.gitignore`에 포함되어 기본 Git 추적 대상에서 제외되므로 강제로 추가하지 않습니다. `REGISTRY_PASSWORD`에는 `openssl rand -hex 32`로 만든 값처럼 셸 인용이 필요 없는 강한 임의 비밀번호를 입력하고, 같은 값을 별도의 비밀번호 관리자에도 보관합니다.

```dotenv
REGISTRY_HOST='registry.homelab.robinjoon.xyz'
REGISTRY_USERNAME='admin'
REGISTRY_PASSWORD=''
```

파일 권한을 제한하고 환경 변수를 불러온 뒤, `registry-system` 네임스페이스에 두 형식의 Secret을 연속으로 만듭니다.

```bash
chmod 600 local.env
source ./local.env
test -n "$REGISTRY_PASSWORD"

kubectl create namespace registry-system --dry-run=client -o yaml |
kubectl apply -f -

kubectl -n registry-system create secret generic zot-auth \
  --from-literal=htpasswd="$(htpasswd -nbB "$REGISTRY_USERNAME" "$REGISTRY_PASSWORD")" \
  --dry-run=client -o yaml |
kubectl apply -f -

kubectl -n registry-system create secret docker-registry registry-credentials \
  --docker-server="$REGISTRY_HOST" \
  --docker-username="$REGISTRY_USERNAME" \
  --docker-password="$REGISTRY_PASSWORD" \
  --dry-run=client -o yaml |
kubectl apply -f -
```

`zot-auth`는 zot 서버가 로그인 검증에 사용하는 htpasswd 형식의 Secret이고, `registry-credentials`는 kubelet이 비공개 이미지를 pull할 때 사용하는 `kubernetes.io/dockerconfigjson` 타입의 Secret입니다. 형식과 사용 주체가 달라 Secret은 두 개지만 둘 다 같은 `admin` 계정과 비밀번호를 담습니다. `admin`은 특별한 예약 이름이 아니며, `argocd/managed/apps/zot.yaml`의 ACL이 이 사용자에게 모든 저장소의 읽기, 생성, 갱신, 삭제를 허용합니다. 사용자 이름을 바꾸려면 `local.env`와 ACL을 함께 바꿔야 합니다.

각 워크로드 네임스페이스에 복제된 `registry-credentials`에는 레지스트리 전체 권한의 `admin` 자격 증명이 담깁니다. 이 Secret을 사용할 수 있는 주체는 이미지를 pull할 뿐 아니라 push, 덮어쓰기, 삭제도 할 수 있습니다. `local.env`는 평문 Secret이므로 공유하거나 커밋하지 않습니다. 위처럼 단순화한 명령은 실행 중 비밀번호를 로컬 프로세스 인자에 잠깐 포함하므로, 여러 사용자가 함께 쓰는 관리 호스트에서는 실행하지 않습니다.

### 3. 원격 Git 반영과 Root Application 등록

Argo CD는 작업 폴더나 로컬 커밋이 아니라 원격 Git을 읽습니다. 레지스트리 매니페스트 변경을 커밋한 뒤 반드시 원격 브랜치에 푸시합니다. `local.env`와 두 Kubernetes Secret은 Git에 올리지 않습니다. Root Application이 이미 등록되어 있다면 `kubectl apply`는 생략해도 되며, 푸시된 변경을 Argo CD가 자동으로 동기화합니다.

```bash
kubectl apply -f argocd/root.yaml
kubectl -n kube-system rollout status deployment/reflector --timeout=5m
kubectl -n registry-system rollout status statefulset/zot --timeout=5m
kubectl -n registry-system get statefulset,pod,service,pvc,ingress,networkpolicy
```

### 4. Reflector 복제 범위 제한

Reflector가 `registry-credentials`를 하네스의 워크로드 네임스페이스에만 자동 복제하도록 표시합니다.

```bash
kubectl -n registry-system annotate secret registry-credentials --overwrite \
  reflector.v1.k8s.emberstack.com/reflection-allowed="true" \
  reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces-selector="simple-k3s-harness.dev/workload=true" \
  reflector.v1.k8s.emberstack.com/reflection-auto-enabled="true" \
  reflector.v1.k8s.emberstack.com/reflection-auto-namespaces-selector="simple-k3s-harness.dev/workload=true"
```

CLI가 새로 만드는 Argo CD Application은 `managedNamespaceMetadata`를 통해 앱 네임스페이스에 `simple-k3s-harness.dev/workload=true` 라벨을 붙입니다. 기존 앱 네임스페이스에는 한 번 직접 표시합니다.

```bash
kubectl label namespace my-api \
  simple-k3s-harness.dev/workload="true" --overwrite
```

이 라벨은 레지스트리 자격 증명과 공유 DB 접속 Secret을 받을 권한을 뜻합니다. 하네스가 관리하지 않는 네임스페이스나 시스템 네임스페이스에는 붙이지 않습니다. 대상 네임스페이스에 같은 이름의 Secret을 따로 만들면 Reflector가 충돌을 감지하고 복제를 건너뛰므로 `registry-credentials`는 `registry-system`의 원본 Secret에서만 관리합니다. Reflector의 허용/자동 복제 네임스페이스 목록과 셀렉터를 모두 생략하거나 빈 값으로 두면 복제 범위가 전체 네임스페이스로 넓어질 수 있으므로 금지합니다.

CNPG가 `database-system` 네임스페이스에 만든 `shared-db-app` Secret에도 같은 제한을 적용합니다.

```bash
kubectl -n database-system annotate secret shared-db-app --overwrite \
  reflector.v1.k8s.emberstack.com/reflection-allowed="true" \
  reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces-selector="simple-k3s-harness.dev/workload=true" \
  reflector.v1.k8s.emberstack.com/reflection-auto-enabled="true" \
  reflector.v1.k8s.emberstack.com/reflection-auto-namespaces-selector="simple-k3s-harness.dev/workload=true"
```

`shared-db-app`은 공유 PostgreSQL 계정의 접속 정보입니다. 앱별 Secret을 발급하는 기능은 범위에 포함하지 않습니다.

## 자체 컨테이너 레지스트리 운영

### 이미지 push

CI에는 `REGISTRY_HOST`, `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`를 마스킹된 Secret으로 등록합니다. 로컬에서는 먼저 `source ./local.env`를 실행합니다. 같은 태그를 덮어쓸 수 있지만, 추적성과 롤백을 위해 `latest` 대신 커밋 SHA처럼 매번 새로운 태그를 사용하는 것을 권장합니다.

```bash
IMAGE_TAG="git-$(git rev-parse --short=12 HEAD)"
IMAGE="$REGISTRY_HOST/apps/my-api:$IMAGE_TAG"

printf '%s' "$REGISTRY_PASSWORD" |
docker login "$REGISTRY_HOST" --username "$REGISTRY_USERNAME" --password-stdin
docker build -t "$IMAGE" .
docker push "$IMAGE"
docker logout "$REGISTRY_HOST"
```

CI 로그에 `REGISTRY_PASSWORD`를 출력하지 않습니다.

### Secret 회전

`admin` 비밀번호를 바꿀 때는 `local.env`의 `REGISTRY_PASSWORD`를 수정하고 2단계의 두 Secret 생성 명령을 다시 실행한 뒤 zot을 명시적으로 재시작합니다. 실행 중인 zot이 Kubernetes projected Secret의 파일 교체를 즉시 감지한다고 가정하지 않습니다.

```bash
kubectl -n registry-system rollout restart statefulset/zot
kubectl -n registry-system rollout status statefulset/zot --timeout=5m
```

같은 유지보수 창에서 대상 네임스페이스의 `registry-credentials` 복제본이 갱신되었는지 확인합니다. CI Secret과 비밀번호 관리자도 함께 갱신합니다. 복제된 자격 증명을 회수할 때는 원본 Secret을 바로 삭제하지 말고 먼저 `reflection-allowed="false"`로 바꾼 뒤 대상 복제본이 사라졌는지 확인합니다.

### 백업과 복구

zot 이미지 데이터는 `local-path` StorageClass의 `zot-pvc-zot-0` PVC에 저장되며, 컨테이너에서는 `/var/lib/registry/data` 경로에 마운트됩니다. 이 볼륨은 노드 로컬 RWO 볼륨이므로 고가용성이나 노드 외부 백업을 제공하지 않습니다.

- CI push를 멈추고 진행 중인 업로드가 없는 유지보수 창에 백업합니다.
- Argo CD에서 `root-apps`와 `zot` Application의 자동 동기화를 순서대로 중지한 뒤 `statefulset/zot`의 레플리카 수를 `0`으로 내려 Pod 종료를 확인합니다. 실행 중인 PVC를 파일 단위로 복사해도 일관성이 보장된다고 가정하지 않습니다.
- 스토리지 드라이버의 스냅샷 또는 전체 PVC를 보존하는 백업 도구로 다른 물리 장치에 복사합니다. 설정은 Git에 있지만 계정 비밀번호는 비밀번호 관리자에서 별도로 복구할 수 있어야 합니다.
- 백업 후 StatefulSet의 레플리카 수를 `1`로 되돌리고 zot이 Ready인지 확인한 다음 Argo CD 자동 동기화를 재개합니다.
- 복구 연습에서는 같은 zot 버전에서 `zot verify /etc/zot/config.json` 명령으로 설정을 확인하고, 서버가 중지된 상태에서 `zot scrub /etc/zot/config.json`으로 OCI 데이터 무결성을 검사한 뒤 대표 이미지의 digest를 지정해 pull합니다. `scrub`은 손상을 고치지 않고 감지만 하며, `component=scrub status=affected` WARN이 있으면 해당 콘텐츠를 손상된 것으로 판단합니다.

감사 로그는 같은 PVC의 `/var/lib/registry/zot-audit.log`에 계속 누적되며 zot이 자동으로 회전하지 않습니다. 크기를 감시하고 정기적으로 노드 외부에 보관한 뒤, 실행 중인 파일은 `inode`를 바꾸지 않는 `copytruncate` 방식으로 회전합니다. 이 감사 로그는 성공한 변경 작업과 GC를 중심으로 기록합니다. 이미지 pull과 인증 실패는 zot의 일반 stdout 로그도 함께 확인합니다.

### 검증

```bash
source ./local.env

python3 -m unittest discover -s tests
kubectl -n registry-system rollout status statefulset/zot --timeout=5m
kubectl -n registry-system exec statefulset/zot -- \
  zot verify /etc/zot/config.json

HTTP_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://$REGISTRY_HOST/v2/")"
test "$HTTP_STATUS" = "401"

# curl이 admin 비밀번호를 대화식으로 묻습니다.
curl --fail --user "$REGISTRY_USERNAME" \
  "https://$REGISTRY_HOST/v2/_catalog"

kubectl -n my-api get secret registry-credentials \
  -o jsonpath='{.type}{"\n"}'
```

마지막 명령의 결과는 `kubernetes.io/dockerconfigjson`이어야 합니다. Secret의 `.dockerconfigjson` 데이터를 터미널이나 로그에 출력해 검증하지 않습니다. 실제 운영 전에는 `admin` 계정으로 테스트 태그를 push하고, 복제된 Secret을 사용하는 앱 네임스페이스에서 그 이미지를 pull하는 과정까지 확인합니다.

## CLI 사용법

AI 에이전트는 아래 flat 명령만 사용합니다. `app create` 같은 중첩 명령이나 `delete` 명령은 제공하지 않습니다.

```bash
python3 tools/platform.py doctor
python3 tools/platform.py schema
python3 tools/platform.py list
python3 tools/platform.py create my-api --image registry.homelab.robinjoon.xyz/apps/my-api:git-0123456789ab --db-name my_api_db
python3 tools/platform.py get my-api
python3 tools/platform.py patch my-api --file /tmp/patch.json
python3 tools/platform.py validate my-api
python3 tools/platform.py validate --all
python3 tools/platform.py render my-api
```

`--db-name`을 지정하면 공유 `shared-db` Cluster 안에 해당 논리적 database를 선언합니다. 환경변수, ConfigMap, Secret 참조, 볼륨 마운트, Service, Ingress, cert-manager TLS는 JSON 계약이 허용하는 범위에서 설정할 수 있습니다. 평문 Secret 값은 Git에 저장하지 않습니다.

자체 레지스트리 이미지를 사용할 때는 Reflector가 앱 네임스페이스에 복제한 `registry-credentials`를 이름으로만 참조합니다.

```json
{
  "workload": {
    "imagePullSecrets": [
      {"name": "registry-credentials"}
    ]
  }
}
```

CLI와 Chart는 레지스트리 Secret을 생성하거나 인증 정보를 values에 저장하지 않습니다. Reflector가 원본 `registry-credentials` Secret의 `type`과 `data`를 허용된 워크로드 네임스페이스에 복제합니다.

`platform/defaults.json`은 CLI와 Argo CD가 앱 values보다 먼저 적용하며, 앱 계약으로 덮어쓸 수 없습니다.

변경 후 검증하고 Git에 커밋하고 푸시하면 Argo CD가 클러스터에 반영합니다. CLI의 검증은 계약과 Helm 렌더링을 확인하는 단계이며, 실제 클러스터 상태나 모든 Kubernetes 운영 조건을 보장하지는 않습니다.
