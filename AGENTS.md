# 에이전트 시작 지침

이 파일은 이 저장소에서 시작하는 에이전트의 공통 진입점이다. 이전 대화나 개인 메모리에 의존하지 말고 아래 시스템 요약과 작업에 해당하는 문서를 먼저 읽는다. 사용자 요청과 작업 범위는 그대로 존중하며, 이 문서 자체가 커밋·push·배포·계정 변경을 승인하지는 않는다.

## 전체 시스템

- `robinjoon-homelab`은 단일 운영자의 개인용 앱과 공통 홈랩 인프라를 관리하는 GitHub Organization이다. 노션 블로그는 여러 동등한 앱 중 하나이며 구조의 중심이 아니다. 직접 개발한 앱과 직접 구축한 오픈소스 서비스를 함께 운영한다.
- 이 저장소 `Simple-K3S-Herness`는 **배포 하네스**다. 앱 소스와 비밀 값의 저장소가 아니다. 워크로드 계약, 공통 Helm Chart, 공통 인프라 선언, 구성 CLI, CI 릴리스 workflow와 공통 Secret 조회 Action을 소유한다.
- SMS 구현 코드는 별도 비공개 저장소 [`robinjoon-homelab/Secret-Manager-System`](https://github.com/robinjoon-homelab/Secret-Manager-System)에 있다. 일반 앱의 소스·빌드 CI도 해당 앱 저장소가 소유한다. SMS의 클래스·DB 스키마·관리 UI 내부 구현을 이 하네스에 복제하지 않는다.
- **제1원칙은 앱 간 격리 최소화**다. namespace와 논리 DB는 운영상의 구분이며, 공유 PostgreSQL 계정과 레지스트리 계정을 사용한다. 외부 접근 인증과 비밀의 Git·로그 노출 방지는 유지한다.

주 배포 흐름은 `앱 소스 → 앱 CI → zot 이미지 발행 → 하네스 릴리스 요청 → 하네스 Git → Argo CD → k3s 앱 실행`이다. 운영자·에이전트는 구성 CLI로 배포 계약을 변경한다. 앱 CI는 릴리스 workflow로 기존 이미지 태그만 바꾼다. Argo CD가 읽는 것은 원격 Git이며 로컬 파일 수정이나 커밋만으로는 배포되지 않는다.

k3s의 공통 서비스는 Argo CD(GitOps 동기화), zot(이미지), 공유 PostgreSQL/CNPG(DB), Kubernetes Secrets(실행 설정), cert-manager(TLS)이며 Traefik은 기본 앱 접속을 담당한다. SMS도 하네스로 배포하는 서비스다. 다이어그램에서는 이 서비스들을 내부 컨트롤러까지 나누지 않는다. DNS·외부 네트워크는 별도 계층이다.

CI와 앱 실행용 비밀은 다음처럼 구분한다.

- 공통 Action은 **호출 앱의 GitHub-hosted runner job 안에서** 실행된다. GitHub OIDC로 호출 레포의 실행 신원을 증명하고 `https://secrets.homelab.robinjoon.xyz`의 SMS에서 CI 자격증명을 조회해 같은 job의 후속 step에 환경변수로 전달한다. 별도 러너 인프라나 컨테이너 서버가 아니다.
- SMS는 CI 값과 OIDC 허용 정책을 기존 공유 PostgreSQL에 저장한다. 운영자가 SMS UI·관리 API에서 직접 관리하며 하네스 배포 values에 정책을 넣지 않는다. 허용된 실행은 필요한 앱 이름(`zot`, `harness` 등)을 조회한다. 앱 이름은 권한 경계가 아니다. Organization 소속만으로 모든 실행이 허용되는 것도 아니다.
- 배포된 앱은 기존 Kubernetes Secrets를 계속 사용한다. SMS가 Kubernetes Secrets를 생성·갱신하거나 CI 응답을 앱 Pod에 자동 전달하는 경로는 **없다**. SMS 자체 CI는 SMS 장애 중에도 배포할 수 있도록 GitHub Secrets를 유지한다.

## 작업별 문서

전체 관계의 대표 그림은 [단일 draw.io 관계도와 설명](docs/diagrams/README.md)이다. 이미지 도구가 없어도 위 요약과 해당 문서의 텍스트로 관계를 파악할 수 있다. 다음 상세 문서는 관련 작업에만 읽으며, 매 세션 모든 API 필드와 운영 명령을 로딩할 필요는 없다.

| 작업 | 먼저 읽을 문서 |
| --- | --- |
| 전체 관계·책임 경계 확인 | [전체 설계](SYSTEM_DESIGN.md) |
| 앱 추가·배포 구성 수정 | [워크로드 스킬](skills/homelab-k3s-workloads/SKILL.md), [워크로드 계약](docs/WORKLOAD_PLATFORM.md) |
| 하네스 CLI·Chart·공통 인프라 자체 개발 | [워크로드 계약](docs/WORKLOAD_PLATFORM.md), [운영 README](README.md)의 해당 절 |
| SMS와의 연결·허용 정책 확인 | [SMS 외부 계약](docs/SECRET_MANAGE_SYSTEM.md); 내부 변경은 SMS 구현 저장소에서 수행 |
| 공통 Action 수정·소비 앱 CI 연결 | [공통 Action](docs/GITHUB_ACTION.md) |

## 작업 경계와 확인

- 작업을 시작할 때 Git 상태를 확인하고 기존 사용자 변경을 보존한다. 문서의 완료 기록은 과거 확인 결과이며 현재 클러스터 건강 상태를 보증하지 않는다. 현재 상태 확인이 필요할 때만 해당 원격 Git·CI·클러스터를 읽기 전용으로 점검한다.
- **일반 앱 배포 구성 작업**은 `tools/platform.py`와 워크로드 스킬의 범위에서 처리한다. values·Argo Application을 직접 편집하거나 `kubectl apply`로 GitOps를 우회하지 않는다. 하네스 자체 기능 개발은 이 일반 앱 작업과 구분하고, 요청된 범위의 CLI·Chart·계약을 함께 수정·검증한다.
- 비밀 값은 Git·문서·로그·이미지에 기록하지 않는다. 온보딩을 위해 `local.env`, Secret의 `data`, SMS 응답이나 DB 내용을 열람·덤프하지 않는다. 값 대신 참조 이름·공개 설정·상태로 먼저 확인한다. 앱 간 공동 신뢰가 인증 생략이나 임의 권한 확대를 뜻하지 않는다.
- 앱 구성 변경은 CLI `validate`·`render`, 하네스 코드 변경은 관련 기존 테스트, Action 변경은 문서에 정의된 Node 24 테스트·번들 재생성을 확인한다. 문서만 바꾸었다면 링크·현재 선언과의 일치·diff를 점검한다. 로컬 검증과 실제 CI·Argo CD·앱 준비 상태 검증을 구분해 보고한다.
- 구조가 바뀌면 이 요약·전체 관계도와 해당 계약 문서의 일치를 유지한다. 비밀 값이나 일시적인 이미지 태그·토큰 ID를 이 시작 지침에 복제하지 않는다.
