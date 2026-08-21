# Simple-K3S-Herness (AI-Driven GitOps System)

이 프로젝트는 K3s 홈랩 환경에서 **AI 에이전트(또는 개발자)가 K8s의 복잡성(YAML, CRD 등)에 휘둘리지 않고 안전하게 워크로드를 관리**할 수 있도록 고안된 **통제된 GitOps 파이프라인**입니다.

## 🌟 핵심 철학 (Core Philosophy)
1. **No Raw YAML**: 개별 앱 배포 시 날것의 Kubernetes YAML 매니페스트를 작성하지 않습니다.
2. **JSON Contract (SSOT)**: 오직 추상화된 JSON 모델(`values.json`)만 조작하며, 모든 복잡성은 시스템이 제공하는 단일 Base Helm Chart가 처리합니다.
3. **App-of-Apps 아키텍처**: ArgoCD를 기반으로 인프라(DB, Reflector)와 개별 앱들을 완벽히 선언적으로 관리합니다.
4. **동적 DB 자동 프로비저닝**: CloudNativePG(CNPG)를 활용하여, 앱 배포 시 이름만 지정하면 무중단으로 해당 앱 전용 DB 인스턴스가 프로비저닝됩니다.

---

## 📂 저장소 구조
```text
.
├── argocd/               # ArgoCD App-of-Apps 설정 (Root, Project, 인프라 앱)
├── chart/                # 유일한 Base Helm Chart (스키마 검증 및 템플릿 포함)
├── infrastructure/       # 공통 인프라 매니페스트 (CNPG Cluster 뼈대 등)
├── platform/             # 플랫폼 전역 기본 설정 (Traefik, ClusterIssuer 등)
├── skills/               # AI 에이전트를 위한 작업 지침 (SKILL.md)
├── tools/                # ★ 유일한 워크로드 관리 인터페이스 (platform.py)
└── workloads/            # 생성된 개별 앱들의 JSON Contract 파일들
```

---

## 🚀 초기 구축 가이드 (Bootstrapping)

이 저장소를 K3s 클러스터에 처음 연동할 때 **반드시 수행해야 하는 1회성 초기화 작업**입니다.

### 1. ArgoCD Root Application 등록
클러스터에 ArgoCD가 설치되어 있다면, 이 저장소를 바라보도록 최상위 앱을 등록합니다.
```bash
kubectl apply -f argocd/root.yaml
```
*(이후 ArgoCD가 CNPG 오퍼레이터, Reflector, Shared DB 인프라 등을 자동으로 띄우기 시작합니다.)*

### 2. 🚨 (중요) DB Secret 복제 트리거 설정 (1회 수동)
보안을 위해 DB 비밀번호를 Git에 하드코딩하지 않았으므로, CNPG가 클러스터 내부에서 자동으로 생성한 마스터 계정 Secret(`shared-db-app`)을 Reflector가 인식할 수 있도록 **수동으로 어노테이션을 부여**해야 합니다.

모든 인프라 파드가 정상적으로 뜨고 `shared-db` 클러스터가 활성화된 후, 터미널에서 **딱 한 번** 아래 명령어를 실행하세요.

```bash
# CNPG가 생성한 Secret에 Reflector 전파 허용 어노테이션 부여
kubectl annotate secret shared-db-app -n database-system \
  reflector.v1.k8s.emberstack.com/reflection-allowed="true" \
  reflector.v1.k8s.emberstack.com/reflection-auto-enabled="true"
```
> **Tip:** 이 작업을 수행하면, 이후 배포되는 모든 앱 네임스페이스로 DB 비밀번호 Secret이 자동 복제되어 앱들이 정상적으로 DB에 연결할 수 있게 됩니다.

---

## 🛠️ 앱 배포 및 관리 방법 (Usage)

이 저장소에 새로운 워크로드를 추가하거나 수정할 때는 **절대로 YAML 파일을 직접 만들지 말고** 제공된 CLI 도구를 사용합니다. (AI 에이전트도 동일한 규칙을 따릅니다.)

### 1. 새 워크로드 생성
```bash
python3 tools/platform.py create my-api \
  --image ghcr.io/my-org/my-api:v1.0.0 \
  --db-name my_api_db
```
* `--db-name` 지정 시, 해당 앱을 위한 전용 DB(CNPG Database CR)가 자동으로 생성됩니다.

### 2. 환경변수 및 설정 변경 (JSON Patch)
앱의 설정을 바꿀 때는 JSON 병합 패치(Merge Patch) 방식을 사용합니다.
```bash
# /tmp/patch.json 에 변경할 내용 작성 후
python3 tools/platform.py patch my-api --file /tmp/patch.json
```

### 3. 유효성 검증
```bash
python3 tools/platform.py validate my-api
```
* 스키마(`values.schema.json`) 어긋남, 포트 충돌, 오타 등을 `helm lint`를 통해 1차적으로 완벽히 잡아냅니다.

변경이 완료되면 Git에 커밋(Commit) 및 푸시(Push)하십시오. ArgoCD가 자동으로 변경 사항을 클러스터에 반영합니다.
