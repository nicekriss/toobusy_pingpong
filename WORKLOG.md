# 작업일지

## 2026-06-24

### 오늘 한 일

- 커스텀 ComfyUI 워크플로 시스템 추가
  - `config.json` 선언만으로 새 텍스트 프롬프트형 워크플로를 봇/대시보드 모드로 등록할 수 있게 함
  - 프롬프트 노드, 이미지 입력 노드, 시드 노드, 저장 prefix, 고정 옵션 주입 지원
  - 설정 오류가 나도 봇이 죽지 않고 안내 메시지를 보내도록 처리

- 새 워크플로 등록
  - Krea2 텍스트-이미지 워크플로 등록
  - Boogu 이미지 편집 워크플로 등록
  - Boogu 편집은 대시보드에서 이미지 1장 첨부 후 `/부구편집` 모드로 사용 가능

- 대시보드 확장
  - 커스텀 워크플로가 대시보드 모드 목록에 자동 표시
  - 이미지/영상/음악 폴더 열기 버튼 추가
  - 대시보드 재시작 버튼 추가
  - 봇 시작 시 대시보드 자동 실행
  - 이미지 크게보기에서 요청 원문과 실제 생성 프롬프트 표시
  - 원본 품질 확대 보기 개선
  - 상단 영역 고정, 영상 플레이어 접기, 비디오 리스트 접힘 처리
  - 음악 플레이리스트, 볼륨 조절, 재생/정지 단축키 추가
  - 시스템 리소스, Comfy 상태/큐, 접이식 Comfy 로그 추가
  - YouTube 채널 배너와 최신 영상 썸네일 표시 추가
  - LLM 모델 선택 UI 추가
  - Director Board, Klein/FaceSwap 레퍼런스 보드 추가
  - 이미지 갤러리를 48장 단위 페이지 방식으로 변경

- 생성 옵션 추가
  - 이미지 비율과 해상도 선택
  - 편집 워크플로 해상도 선택
  - 영상 길이, fps, 폭 선택
  - 영상 길이에 맞춰 Director Board 타임라인 프레임 범위 자동 조정

- 봇 안정화
  - 하트비트 스레드 분리로 긴 생성 중에도 대시보드가 오프라인으로 보이는 문제 완화
  - Comfy 큐/로컬 큐 상태 표시 개선
  - LLM 프롬프트에 섞이는 사고 과정/카운터 문장 정리
  - "이미지 여러 장" 요청을 한 큐당 한 장씩 나눠 보내도록 처리
  - 텔레그램 사진으로 영상 생성 흐름 추가

- 배포/설치 정리
  - `requirements.txt`, `run_bot.bat`, `run_dashboard.bat` 추가
  - 설치 스크립트는 유지하고, 배포 zip 생성용 스크립트는 제거
  - README에 설치/실행/커스텀 워크플로 설명 갱신

### 검증

- `python -m py_compile pingpong.py dashboard.py setup.py`
- 대시보드 JS 파싱 테스트
- Krea2/Boogu 커스텀 워크플로 노드 주입 테스트
- 이미지 옵션 주입 테스트
- 영상 길이/fps/폭 주입 테스트
- Boogu 편집 해상도 주입 테스트
- 대시보드 재시작 및 `/api/modes`, `/api/list` 확인
- 이미지 페이지네이션 115장 기준 `48 / 48 / 19` 확인

### 발행

- `be5dbb8` Add dashboard workflow controls and edit workflow
- `2c247c0` Add dashboard generation options
- `9ea8aee` Paginate dashboard image gallery

### 다음에 보면 좋은 것

- 영상 Director Board를 실제 노드와 더 깊게 맞춘 타임라인 편집기로 확장
- 커스텀 워크플로별 옵션 스키마를 `config.json`에서 선언 가능하게 만들기
- 이미지/영상/음악 날짜별 필터와 검색
- 오래된 생성물 아카이브/정리 도구
- Comfy 실행 로그를 파일 기반으로 더 자세히 표시
## 2026-07-11 KREA2 Canny Telegram sketch workflow

- Added `.크케`: attach one Telegram image and put the short command plus a description in its caption.
- Base pass is locked to KREA2 + NK2E Canny, 8 steps.
- Final pass is a 2K HiRes Fix, 4 steps, denoise 0.35, connected directly to the clean KREA2 UNET with no NK2E/ControlNet reference.
- Added direct routing for one-image custom workflows so a captioned Telegram image runs immediately instead of opening the generic photo-action menu.
- Verified Python/JSON syntax, graph references, and all 19 node/input schemas against the live ComfyUI API.
## 2026-07-11 Dot aliases for Telegram commands

- Every built-in slash command now also accepts a leading dot for easier mobile entry (`.영상`, `.음악`, `.그림`, `.레크2`, `.취소`, `.메뉴`, `.페이스스왑`, etc.).
- Custom workflow triggers are normalized the same way, whether configured with `/` or `.`.
- Existing slash commands remain backward compatible.
## 2026-07-11 Manual gallery compare workbench

- Added a persistent bottom `직접비교` workbench to the Pingpong gallery.
- Gallery image cards can be dragged into independent LEFT/RIGHT sets; each side supports multiple images, removal, and current-image selection.
- Added per-card `L` and `R` buttons as a reliable alternative to HTML5 drag-and-drop.
- `비교 열기` reuses the full-screen wipe viewer with zoom, pan, reset, number-key selection, and independent left/right rails.
- Manual sets persist in browser localStorage and do not modify bundles or sidecars.
## 2026-07-11 External images in manual compare

- Manual compare LEFT/RIGHT zones now accept image files dragged from Explorer.
- Added clipboard screenshot paste with a visible LEFT/RIGHT paste target toggle.
- Added a multi-file picker fallback.
- External images are downscaled to a maximum 1800px edge and encoded as WebP quality 0.9 before entering the comparison session.
- Verified an image clipboard paste creates a screenshot card in the selected RIGHT set.
## 2026-07-11 Native-pixel 4K comparison

- External files and clipboard screenshots now use original Blob URLs with no resizing, recompression, or format conversion.
- Added a `1:1` button and `N` shortcut to calculate native-pixel zoom from image natural dimensions.
- Raised wipe viewer zoom limit from 12x to 32x; double-click still resets to fit.
- External Blob images intentionally persist for the current browser session only; gallery-backed selections remain localStorage-persistent.
- Verified two original 1920×1080 PNGs remained 1920×1080 and native mode moved fit zoom from 100% to 161%.
## 2026-07-11 Right comparison resolution label fix

- Fixed a CSS class collision between the right comparison label (`lb`) and the global lightbox `.lb { display:none }` rule.
- Renamed the right label modifier to `rb` in CSS and HTML.
- Verified both LEFT and RIGHT labels are visible and independently show `1920×1080` in the live manual comparison viewer.
## 2026-07-11 Gallery marquee multi-delete

- Added a `다중선택` mode to the image gallery.
- Mouse-dragging a marquee across cards selects every intersecting card; Ctrl/Shift preserves prior selections for additive passes.
- Selected cards show an amber outline/check badge and the toolbar reports both card count and actual image-file count.
- Bundle cards carry every displayed bundle member as their delete payload.
- `선택 삭제` asks for confirmation and moves all unique selected image files through the existing `.trash` delete API.
- Verified a marquee over three cards yields `3개 카드 · 3개 파일` and enables the delete button; no user files were deleted during testing.

## 2026-07-16

### 오늘 한 일

- 대시보드 비디오 플레이어 리스트 레이아웃 수정
  - 영상이 많아지면 VIDEO LIST가 무한정 길어져 옆 플레이어(.crt)까지 같이 늘어나며 레이아웃이 무너지던 문제
  - `#vrows`를 `.vlist` 안에서 absolute + overflow-y:auto로 바꿔 리스트가 항상 플레이어 높이에 맞춰 내부 스크롤되게 함
  - 영상 생성 모드(.videoMode)의 기존 그리드(132px) 레이아웃은 position:static으로 유지
