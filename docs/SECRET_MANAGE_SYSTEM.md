# Secret Manage System: 외부 API와 배포·저장 계약

상태: 구현·배포 완료된 SMS의 외부 계약. 운영 기준일: 2026-09-11.

이 문서는 하네스가 시크릿 관리 앱(Secret Manage System, SMS)을 배포하고 공통 Action이 호출하는 데 필요한 **외부 계약**과 운영자의 직접 관리 경로를 정한다. C4의 시스템 경계를 참고해 SMS를 하나의 서비스로 다루며 내부 컴포넌트까지 펼치지 않는다. 시스템 간 관계는 [전체 설계](../SYSTEM_DESIGN.md), 호출 방법은 [공통 GitHub Action](GITHUB_ACTION.md)을 참고한다.

CI 조회 API·배포 입력·운영자 직접 관리 경로가 유지된다면 SMS의 테이블, 인증 라이브러리, UI 내부 구현을 바꿔도 이 문서를 수정할 필요가 없어야 한다. 내부 설계와 구현 시험은 SMS 구현 저장소가 소유한다.

## 1. 책임과 검토 조건

SMS는 CI 자격증명을 보관하고, GitHub Actions의 실행 신원을 확인한 뒤 요청한 앱 이름의 값을 반환한다. 운영자에게는 값을 등록·교체·삭제하는 관리 API와 템플릿엔진 기반의 간단한 웹 UI를 제공한다. 운영자가 SMS에 직접 접속하며 하네스와 공통 Action은 관리 API를 호출하지 않는다. UI와 관리 API의 상세 설계는 SMS 구현 저장소가 소유한다.

**앱 간 격리는 최소화한다.** CI 조회 진입이 허용된 모든 레포는 등록된 모든 앱 이름을 조회할 수 있다. 앱 이름은 조회 키이며 레포·namespace·권한 경계가 아니다. 앱별 ACL, CI용 쓰기 API, GitHub job의 DB 쓰기, Kubernetes 앱 실행용 Secret 등록·조회·갱신은 제공하지 않는다. 운영자 관리 권한은 CI 조회 권한과 구분한다.

| ID | 이 문서의 검토 조건 |
| --- | --- |
| S1 | SMS 내부 구현을 바꿔도 CI 조회 API·배포 입력·운영자 직접 관리 경로가 같으면 이 문서의 계약은 유지된다. |
| S2 | 앱 이름별 JSON 구조와 오류가 모호하지 않고 모든 허용 레포에 같은 조회 권한을 적용한다. |
| S3 | CI OIDC 조회와 운영자 관리 인증을 구분하고 비밀을 로그·오류에 남기지 않는다. |
| S4 | 하네스가 제공할 DB·접속 설정과 SMS가 소유할 저장·관리 책임을 구분한다. |
| S5 | 공유 계정과 평문 저장의 신뢰 경계를 유지하고 앱 실행용 Secret 관리나 소비 앱의 CI 구현을 포함하지 않는다. |

## 2. 데이터와 조회 API v1

논리 데이터는 앱 이름을 키로, Secret 키와 문자열 값을 담은 객체를 값으로 사용한다. 공통 인프라도 `zot`, `harness`라는 이름을 쓴다.

```json
{
  "zot": {
    "REGISTRY_USERNAME": "<secret value>",
    "REGISTRY_PASSWORD": "<secret value>"
  },
  "harness": {
    "HARNESS_ACTIONS_TOKEN": "<secret value>"
  }
}
```

레지스트리 로그인과 하네스 호출이 필요한 CI는 `zot`과 `harness`를 각각 조회한다. 동일 값을 소비 앱 이름 아래에 복제할 필요는 없다. 앱 자체의 CI 값은 해당 앱 이름으로 추가할 수 있다. Kubernetes의 동명 앱이나 Secret을 탐색하지 않는다.

SMS는 이미 발급된 CI 자격증명을 보관한다. DB 값을 바꿔도 zot 계정이나 GitHub 토큰 자체가 발급·교체되지는 않는다. 운영자는 실제 발급 시스템의 값과 SMS의 저장 값을 함께 맞추며, 기존 레지스트리 인증·이미지 pull용 Kubernetes Secret 관리도 유지한다.

```http
GET /v1/ci/secrets/zot
Authorization: Bearer <GitHub OIDC JWT>
```

경로는 `GET /v1/ci/secrets/{app}`다. 본문·쿼리 매개변수는 받지 않는다. 성공 응답은 HTTP 200, `Content-Type: application/json`, `Cache-Control: no-store`이며, 요청한 앱 이름 하나만 최상위 키로 유지한다.

```json
{
  "zot": {
    "REGISTRY_USERNAME": "<secret value>",
    "REGISTRY_PASSWORD": "<secret value>"
  }
}
```

CI 조회 API에는 전체 앱 목록·전체 값 조회나 키별 필터가 없다. 신원과 진입 정책을 확인한 뒤 앱 객체 전체를 반환한다. 정적 자격증명에 별도 만료 시간을 붙이지 않는다. OIDC 토큰이 만료돼도 이미 받은 비밀번호·토큰이 자동으로 무효화되는 것은 아니다.

### 공통 데이터 검증 규약

아래 규칙은 SMS가 제공하는 CI 데이터와 공통 Action의 응답 처리에 함께 적용한다. SMS는 운영자가 관리하는 값도 이 규약을 충족하도록 보장한다.

- 앱 이름: 1~63자, 소문자 영문·숫자·하이픈만 허용하며 처음과 끝은 영문 또는 숫자다.
- Secret 키: `[A-Za-z_][A-Za-z0-9_]*`. `PATH`, `BASH_ENV`, `ENV`, `NODE_OPTIONS`와 `GITHUB_`, `RUNNER_`, `ACTIONS_`, `INPUT_`으로 시작하는 키는 대소문자 구분 없이 거부한다. 이는 환경변수 전달의 실행 정확성을 위한 제한이며 앱별 권한 정책이 아니다.
- 값: 비어 있지 않은 UTF-8 문자열. 여러 줄·따옴표·특수문자를 보존하고 null·숫자·객체·배열·NUL·임의 바이너리는 거부한다.
- 앱 객체는 Secret 키를 하나 이상 가진다. JSON의 중복 키를 허용하지 않는다. 앱 하나의 최종 UTF-8 JSON 응답은 64KiB 이하다. 객체의 키 순서는 계약에 포함하지 않는다.
- 잘못된 객체를 일부만 반환하지 않는다. 앱별 필수 키 목록은 소비 CI가 검사하며 SMS에 별도 선언하지 않는다.

### 오류

```json
{"error":{"code":"POLICY_DENIED","requestId":"<opaque request id>"}}
```

| HTTP | code | 의미 |
| --- | --- | --- |
| 400 | `INVALID_REQUEST` | 앱 이름 오류 또는 지원하지 않는 본문·쿼리 |
| 401 | `INVALID_IDENTITY` | JWT 누락, 잘못된 서명·발급자·수신 대상·시간·필수 클레임 |
| 403 | `POLICY_DENIED` | 신원은 유효하지만 허용 레포·브랜치·이벤트·워크플로 조건과 불일치 |
| 404 | `APP_NOT_FOUND` | 진입 인증을 통과했으나 앱 이름이 등록되지 않음 |
| 429 | `RATE_LIMITED` | 요청 제한 초과. `Retry-After`를 초 단위로 제공 |
| 503 | `SECRET_NOT_READY` | 저장된 CI 값을 읽을 수 없거나 데이터가 규약에 맞지 않아 제공할 수 없음 |
| 503 | `IDENTITY_PROVIDER_UNAVAILABLE` | 검증에 필요한 서명 키를 안전하게 확보할 수 없음 |

인증을 통과하기 전에는 앱 존재 여부를 알리지 않는다. 오류 응답도 `no-store`를 적용하며 입력 값·JWT·Secret·DB 접속 정보를 포함하지 않는다. 애플리케이션과 연동 시스템의 로그에도 토큰과 Secret을 남기지 않는다. 상태 코드·request ID·실행 레포 ID 같은 비밀이 아닌 진단 정보만 남긴다.

## 3. CI 조회용 OIDC 진입 인증

SMS는 GitHub가 발급한 OIDC JWT의 서명, 발급자, audience, 유효시간과 필수 실행 신원을 검증한다. 고정 발급자는 `https://token.actions.githubusercontent.com`, audience는 `urn:homelab:ci-secrets:v1`이다. 이 audience는 공통 Action의 토큰 요청과 일치해야 한다. JWT에는 비어 있지 않은 `sub`와 시간 클레임 `exp`·`nbf`·`iat`가 있어야 하며, 만료됐거나 아직 유효하지 않거나 미래에 발급된 토큰은 거부한다. 유효하지 않거나 필수 신원 정보가 없는 토큰은 401로 거부한다.

실행 신원에는 `repository_id`, `repository_owner_id`, `ref`, `event_name`, `workflow_ref`가 필요하다. 아래 진입 정책에 등록된 레포 항목 하나에서 두 ID가 모두 일치하고, ref·event·workflow가 각각 해당 허용 목록의 값 중 하나와 일치해야 한다. 정책은 하네스가 관리하는 비밀이 아닌 배포 설정이고, 검증과 정책 적용은 SMS의 책임이다. 필요한 발급자 정보를 확보할 수 없어 신원을 검증하지 못하면 503 `IDENTITY_PROVIDER_UNAVAILABLE`로 실패하며 인증을 생략하지 않는다.

노션 블로그의 진입 정책 예시는 다음과 같다. ID와 브랜치는 2026-09-01 조사 기준이며 실제 도입 시 저장소 상태와 대조한다.

```yaml
repositories:
  - repositoryId: "1284508552"
    repositoryOwnerId: "45223837"
    repository: robinjoon/Notion-Blog # 사람이 읽는 식별 정보
    allowedRefs: [refs/heads/master]
    allowedEvents: [push, workflow_dispatch]
    allowedWorkflows:
      - robinjoon/Notion-Blog/.github/workflows/ci.yml@refs/heads/master
```

`master`는 위 레포의 정책 값이며 모든 앱에 강제하는 이름이 아니다. 정책에 없는 레포와 `pull_request`, `pull_request_target`은 거부한다. 허용 여부는 명시된 ID와 실행 클레임으로 판단한다. 일반 JavaScript Action 호출에는 재사용 워크플로용 `job_workflow_ref`를 요구하지 않으며, job 이름을 OIDC가 증명하는 신원으로 취급하지 않는다. [GitHub OIDC 클레임](https://docs.github.com/en/actions/reference/security/oidc)

허용된 레포 하나가 침해되면 SMS의 모든 CI 값에 접근할 수 있다는 운영 전제를 받아들인다. 이 OIDC 신원은 CI 조회 권한만 부여하며 운영자 관리 권한으로 인정하지 않는다. OIDC는 네트워크 연결이나 실행 코드의 안전성을 보장하지 않는다.

## 4. 배포 입력과 저장 책임

### 하네스가 제공할 구성

**v1은 기존 `database-system/shared-db` PostgreSQL Cluster를 사용한다.** 하네스는 공통 Chart에 `database.name: secret_manage_system`을 선언하고 기존 공유 계정 `defaultuser`를 소유자와 접속 계정으로 제공한다. 별도 DB 인스턴스·계정·SMS 전용 PVC를 추가하지 않는다.

SMS의 DB 접속 입력은 다음과 같다. 이는 하네스가 실제로 주입할 실행 설정이며 DB 내부 스키마와는 별개다.

| 환경변수 | 하네스가 제공할 값 또는 참조 |
| --- | --- |
| `DB_HOST` | Chart가 주입하는 `shared-db-rw.database-system.svc.cluster.local` |
| `DB_PORT` | SMS namespace의 `shared-db-app` Secret에서 `port` 참조 |
| `SPRING_DATASOURCE_URL` | `jdbc:postgresql://$(DB_HOST):$(DB_PORT)/secret_manage_system` |
| `SPRING_DATASOURCE_USERNAME` | 같은 Secret에서 `username` 참조 |
| `SPRING_DATASOURCE_PASSWORD` | 같은 Secret에서 `password` 참조 |

환경변수 확장을 위해 `DB_PORT`는 접속 URL보다 먼저 선언한다. 기존 Secret의 `dbname`·URI를 그대로 쓰거나 전체를 `envFrom`으로 가져오지 않는다. Secret 복제와 공통 DB 접속 규칙은 [하네스 운영 문서](../README.md)를 따른다. 실제 자격증명은 Git이나 이미지에 넣지 않는다.

CI 조회 API는 `https://secrets.homelab.robinjoon.xyz`에 배포되어 있으며 공통 Action도 이 주소를 사용한다. 하네스는 HTTPS 경로와 호출 job에서의 도달 가능성을 준비한다. 운영자 UI·관리 API도 HTTPS로 접근하며, SMS가 정한 운영자 인증에 필요한 배포 설정만 하네스에 전달한다. 네트워크 접근 수단은 호출자 인증과 별도로 구성한다.

### SMS가 소유할 저장·관리 책임

SMS는 앱 이름별 CI 객체를 PostgreSQL에 영속적으로 보관하고 공개 API 규약에 맞게 반환한다. 논리 DB 생성과 접속 정보 공급은 하네스가 맡고, 내부 테이블·스키마 변경·저장 형식은 SMS 구현 저장소가 소유한다.

SMS 애플리케이션이 관리 API와 서버에서 HTML을 렌더링하는 템플릿 기반 UI를 함께 제공한다. 운영자는 브라우저로 SMS에 직접 접속해 CI 값을 등록·교체·삭제한다. 하네스에 관리 화면이나 입력 중계 기능을 추가하지 않는다. 관리 API의 경로·메서드·요청 및 응답, 화면 구성, 템플릿엔진 선택은 SMS 구현 저장소에서 정의한다.

관리 UI와 API는 운영자 인증을 요구한다. 인증 방식과 운영자 등록 방법은 SMS에서 정하며, GitHub Actions OIDC 토큰만으로는 관리 기능에 접근할 수 없다. 브라우저의 쿠키·세션 인증을 사용하는 쓰기 요청은 CSRF로 인한 변경을 막아야 한다. 이 문서는 인증 라이브러리나 세션 구현을 지정하지 않는다. [Spring의 브라우저 요청 CSRF 보호](https://docs.spring.io/spring-security/reference/servlet/exploits/csrf.html)

입력한 값은 공통 데이터 규약을 충족해야 하며 잘못된 입력은 저장하지 않는다. 비밀을 포함하는 관리 화면·API 응답에는 `no-store`를 적용하고 입력 값·인증 정보는 URL·로그·오류에 남기지 않는다. 값을 관리하는 동안에도 CI 조회 API는 규약을 만족하는 완전한 앱 객체를 반환하거나 오류로 실패해야 하며 일부 값만 반영된 응답을 보내지 않는다. SMS에는 Kubernetes Secret을 조작할 권한이 필요하지 않다.

### 평문 저장과 공유 계정의 경계

CI 자격증명은 **앱 수준에서 암호화하지 않고 평문으로 보관**한다. 별도의 마스터 키·unseal은 도입하지 않는다. 공유 `defaultuser` 자격증명을 가진 DB 사용 앱, PostgreSQL 관리자, 홈서버 관리자와 SMS 프로세스는 값을 직접 읽고 변경·삭제할 수 있다. 이는 현재의 앱 간 공동 신뢰 원칙에서 수용하는 범위다. CI OIDC와 운영자 인증은 각각 HTTP 진입을 보호하며 이 DB 접근을 제한하지 않는다.

## 5. 외부 계약 검증

아래는 SMS의 외부 계약 검증 기준이다. 2026-09-10 배포 점검에서는 더미 객체 등록·조회·교체·삭제, 입력 검증, CSRF, 비인증 요청 거부와 상태 확인을 검증했다. 2026-09-11 [노션 블로그 CI](https://github.com/robinjoon/Notion-Blog/actions/runs/34603784590)에서는 실제 OIDC로 `zot`·`harness` 조회에 성공했다. 모든 장애·실행 출처 조합을 운영 환경에서 재현한 것은 아니다. 내부 테이블·라이브러리·관리 도구의 구조를 검사하지 않고 호출자가 관찰하는 결과를 확인한다.

| 조건 | 관찰할 결과 |
| --- | --- |
| 허용 레포 두 곳에서 `zot`·`harness` 각각 조회 | 모두 200, 요청한 앱 객체 하나만 반환; 호출 레포명과 앱 이름이 달라도 성공 |
| 변조 JWT·잘못된 audience·만료·필수 신원 정보 누락 | 401, 앱 존재 여부와 토큰·값 노출 없음 |
| 유효 JWT지만 미허용 레포·브랜치·이벤트·워크플로 실행 | 403; 허용 여부는 배포된 진입 정책과 일치 |
| 인증 후 미등록 앱 / 잘못된 앱 이름 | 각각 404 `APP_NOT_FOUND` / 400 `INVALID_REQUEST` |
| 저장소 장애 또는 규약에 맞지 않는 저장 데이터 | 503 `SECRET_NOT_READY`; 일부 값이나 내부 접속 정보 노출 없음 |
| 필요한 발급자 정보를 확보할 수 없어 신원을 검증할 수 없음 | 503 `IDENTITY_PROVIDER_UNAVAILABLE`; 검증을 생략한 성공 없음 |
| 운영자가 웹 UI에서 유효한 더미 값을 등록·교체·삭제 | 하네스의 관리 API 호출 없이 완료되고, 이후 CI 조회에 변경 내용이 반영됨 |
| 운영자가 데이터 규약에 맞지 않는 값을 입력 | 입력 거부, 기존 값 유지 |
| 운영자 인증 없이 또는 CI OIDC 토큰만으로 관리 UI·API 접근 | 관리 기능을 사용할 수 없고 저장 값이 변경되지 않음 |
| 브라우저의 인증을 악용한 CSRF 쓰기 요청 | 요청 거부, 저장 값이 변경되지 않음 |
| 운영자가 값을 교체하는 동안 조회 | 변경 전·후의 완전한 객체 또는 명시적 오류; 일부 값만 섞인 응답 없음 |
| 요청 제한 초과 | 429 `RATE_LIMITED`, 초 단위 `Retry-After` 제공 |

CI 응답에는 공통 데이터 제약과 `no-store`를 적용하고, 조회·관리 어느 경로에서도 성공·실패와 무관하게 비밀이 로그에 노출되지 않아야 한다. 관리 UI·API의 상세 시험은 SMS 구현 저장소에서 정의한다. 이후 [공통 Action의 통합 시험](GITHUB_ACTION.md)에서 같은 job의 환경변수 전달과 기존 릴리스 연동을 확인한다. 앱 실행용 Secret은 변경하지 않는다.
