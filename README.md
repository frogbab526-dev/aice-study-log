# aice-study-log

Notion 데이터베이스 "AICE학습일지" 를 매일 GitHub repo로 동기화합니다.

- 매일 23:30 KST (cron `30 14 * * *` UTC) 에 GitHub Actions가 실행
- 각 Notion DB row → `logs/YYYY-MM-DD.md` 파일 1개
- 내용이 바뀐 파일만 commit (변경 없으면 빈 commit 안 만듦)
- 수동 실행 및 dry-run 지원

## 구조

```
.
├── .github/workflows/notion-sync.yml   # 스케줄 + 수동 트리거
├── scripts/notion_to_md.py             # Notion API -> Markdown 변환
├── logs/                               # 생성된 일지 (.md)
├── requirements.txt
└── README.md
```

---

## 셋업 (한 번만)

### 1. Notion Integration 생성 & 토큰 발급
1. https://www.notion.so/profile/integrations 접속
2. **+ New integration** 클릭
3. 이름 입력 (예: `aice-study-log-sync`), Type은 **Internal** 선택
4. 생성 후 **Internal Integration Token** (`secret_...` 형태) 복사 → 안전한 곳에 저장

### 2. Notion 페이지에 Integration 연결 (← 가장 자주 빠뜨리는 단계)
1. Notion에서 **AICE학습일지** 데이터베이스 페이지 열기
2. 우측 상단 `···` (More) → **Connections** → 방금 만든 integration 검색 후 **Confirm**
3. 이 단계를 빼먹으면 API가 `object_not_found` 로 즉시 실패합니다 (2초 실패의 흔한 원인)

### 3. Notion Database ID 추출
- 데이터베이스 페이지 URL은 보통 이런 형태입니다:
  `https://www.notion.so/<workspace>/<DB_NAME>-<DATABASE_ID>?v=...`
- `DATABASE_ID` 는 URL 마지막 `?` 직전의 **32자리 hex** (대시 없이 또는 8-4-4-4-12 형태)
- 예: `1a2b3c4d5e6f7g8h9i0j1k2l3m4n5o6p` → 그대로 사용

### 4. GitHub repo 생성
```bash
cd /Users/maruchi/aice-study-log
git init
git add .
git commit -m "chore: initial scaffold"
git branch -M main
# 아래는 GitHub에서 frogbab526-dev/aice-study-log repo를 먼저 생성한 뒤 실행
git remote add origin https://github.com/frogbab526-dev/aice-study-log.git
git push -u origin main
```
> GitHub 웹에서 repo를 만들 때는 README/.gitignore/license **추가하지 마세요** (이미 로컬에 있어서 충돌남).

### 5. GitHub Secrets 등록
GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Name | Value |
|---|---|
| `NOTION_TOKEN` | 1단계에서 받은 `secret_xxx...` |
| `NOTION_DATABASE_ID` | 3단계에서 추출한 32자리 hex |

### 6. 첫 실행 (수동 트리거로 검증)
1. GitHub repo → **Actions** 탭 → **Notion sync** 워크플로우 선택
2. **Run workflow** → `Dry run`을 `true`로 두고 실행 → 로그에서 `would-create` 출력 확인
3. 다시 **Run workflow** → `Dry run`을 `false`로 두고 실행 → `logs/` 폴더에 .md 파일 commit 확인

---

## 로컬에서 직접 실행

```bash
cd /Users/maruchi/aice-study-log
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export NOTION_TOKEN="secret_xxx..."
export NOTION_DATABASE_ID="32-char-hex"

# 미리보기만
python scripts/notion_to_md.py --dry-run

# 실제 파일 생성
python scripts/notion_to_md.py
```

---

## 권장 Notion DB 스키마

스크립트는 스키마에 유연하지만, 아래 컬럼이 있으면 가장 깔끔합니다.

| 컬럼명 | 타입 | 용도 |
|---|---|---|
| `Title` (기본) | Title | 페이지 제목 → md 본문 첫 `#` 헤더 |
| `Date` | Date | **파일명** 결정 (`logs/2026-05-17.md`) |
| `Tags` | Multi-select | frontmatter `Tags: [...]` |
| `Status` | Select / Status | frontmatter `Status: "..."` |

- `Date` 컬럼이 없으면 페이지의 `created_time` 으로 대체합니다.
- 컬럼 이름은 한글이어도 동작합니다 (`날짜`, `태그` 등).

---

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| 2초 만에 실패, log에 `object_not_found` | Notion 페이지 Connections에 integration이 추가되지 않음 (2단계) |
| `401 Unauthorized` | 토큰 오타, 또는 `secret_` 접두사 누락 |
| `400 invalid_database_id` | DB ID에 `-` 가 섞여있거나 페이지 ID를 잘못 복사. 32자리 hex만 사용 |
| `Permission denied to push` in Actions | repo Settings → Actions → General → "Workflow permissions" 를 **Read and write** 로 변경 |
| 같은 파일이 매일 새로 commit됨 | 페이지 본문이 실제로 변경됐거나 frontmatter의 `last_edited_time` 이 갱신된 경우. 정상 동작 |
| 한글이 깨짐 | 거의 발생하지 않지만, 로컬 실행 시 `PYTHONIOENCODING=utf-8` 환경변수 설정 |
