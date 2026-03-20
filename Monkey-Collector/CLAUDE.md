# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Monkey-Collector는 GUI World Modeling 학습 데이터를 자동 수집하는 파이프라인이다. Android AVD에서 **Smart Explorer**(XML 기반 지능형 액션 선택, MobileForge 포팅)로 UI 이벤트를 생성하고, AccessibilityService 기반 Android 앱이 UI 변화를 감지하여 스크린샷+XML을 TCP로 Python 서버에 전송한다. 수집된 raw data는 annotation pipeline을 통해 grounding/OCR/state_diff/element_qa/world_modeling 학습 데이터(JSONL)로 변환된다.

이 저장소는 더 큰 프로젝트(Qwen3-VL-8B 기반 GUI Foundation Model)의 Phase 1 Stage 1 데이터 수집 부분이다. PRD.md에 전체 학습 파이프라인 설계가 기술되어 있다.

## Commands

```bash
# 설치
pip install -e .                    # 기본 (수집만)
pip install -e ".[annotation]"      # LLM 캡셔닝 포함

# AVD 셋업
./scripts/setup_avd.sh

# Android 앱 빌드
cd app/ && ./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk

# 데이터 수집
monkey-collect run --app com.android.calculator2 --events 100
monkey-collect batch --apps-config configs/collection/apps.yaml
monkey-collect annotate --session <session_id>
monkey-collect pipeline --apps-config configs/collection/apps.yaml

# 배치 수집 + annotation 한번에
./scripts/collect.sh
```

## Architecture

두 개의 독립적인 코드베이스로 구성된다:

### Python Server (`collection/`)

```
Smart Explorer ──ADB input──▶ AVD (지능형 액션 실행)
TCP Server   ◀──TCP──────── Android App (스크린샷+XML 수신)
Annotation                  (수집 후 오프라인 변환)
```

- **cli.py**: `monkey-collect` CLI 엔트리포인트. argparse 기반 4개 서브커맨드 (run/batch/annotate/pipeline)
- **orchestrator.py**: `CollectionOrchestrator` — Smart Explorer 루프로 세션 관리. XML 파싱 → 액션 선택 → ADB 실행 → TCP 수신 대기 → 반복
- **server.py**: `CollectionServer` — 단일 클라이언트 TCP 서버. `wait_for_xml()` 동기 대기 메서드로 Smart Explorer와 동기화. 4종 메시지 타입 처리 (S/X/E/F)
- **explorer/**: `SmartExplorer`(XML 기반 지능형 액션 선택, MobileForge 포팅), `action_space`(Action 데이터클래스)
- **adb/**: `AdbClient`(subprocess 래퍼, tap/swipe/input_text/long_press/dump_xml 포함), `MonkeyRunner`(레거시)
- **storage/writer.py**: `DataWriter` — 세션 디렉토리 구조(screenshots/, xml/, events.jsonl, metadata.json) 관리
- **fallback/monitor.py**: `FallbackMonitor` — 앱 이탈 감지 추적, 연속 이탈 시 force restart 판단
- **annotation/**: 6개 annotation 모듈
  - `xml_parser.py`: uiautomator XML → `UIElement` dataclass 리스트 (모든 annotation의 입력)
  - `xml_encoder.py`: uiautomator XML → HTML-style XML (MobileGPT-V2 parseXML.py 포팅). `encode_to_html_xml()`이 LLM용 최종 출력
  - `grounding.py`, `ocr_extractor.py`, `element_qa.py`: UIElement → Format A (conversations 형식) QA 생성
  - `state_diff.py`: 연속 XML 쌍 비교 → 변화 감지 QA
  - `world_modeling.py`: before/after XML + action → ShareGPT Format B (gui-model_stage1.jsonl 호환)
  - `llm_annotator.py`: 스크린샷 → OpenAI API 캡셔닝 (선택적)
- **format/converter.py**: `FormatConverter` — 전체 annotation 오케스트레이션. 세션별 raw data 순회하며 각 annotation 모듈 호출 후 JSONL 출력

### Android App (`app/`)

Kotlin 기반 AccessibilityService 앱. Gradle 빌드 (app/ 내 nested gradle project).

- **CollectorService.kt**: AccessibilityService 핵심. 이벤트 수신 → 300ms debounce → ScreenStabilizer 안정화 대기 → 시각적 변화 확인 → 스크린샷+XML 캡처 → TCP 전송. 앱 이탈 감지 시 back/force launch 처리. 포그라운드 서비스로 MediaProjection 지원
- **ScreenStabilizer.kt**: MediaProjection 기반 화면 안정화. 100px 저해상도로 프레임 캡처 → BitmapComparator로 연속 비교 → 3회 연속 2% 이내 → 안정 판정. computer-use-preview-for-mobile의 `performWaitForStableScreen()` 포팅
- **BitmapComparator.kt**: 두 Bitmap의 픽셀 차이 비율 계산 (0.0=동일 ~ 1.0=완전 다름)
- **MediaProjectionHelper.kt**: Activity→Service 간 MediaProjection 권한 데이터 전달 싱글톤
- **TcpClient.kt**: TCP 프로토콜 구현 (S/X/E/F 메시지)
- **XmlDumper.kt**: AccessibilityNodeInfo → uiautomator XML 변환
- **ScreenCapture.kt**: API 30+ 스크린샷 캡처 (고해상도, 안정화 후 최종 캡처용)
- **MainActivity.kt**: 서버 IP/포트/타겟 패키지 설정 + MediaProjection 권한 요청 (ActivityResultLauncher)

### Data Flow

1. SmartExplorer → XML 파싱 → 지능형 액션 선택 → `adb input tap/swipe/text` 실행
2. CollectorService → UI 변화 감지 → 화면 안정화 → 스크린샷+XML을 TCP로 전송
3. CollectionServer → DataWriter로 세션 디렉토리에 저장 + SmartExplorer에 XML 전달
4. SmartExplorer → 수신 XML로 다음 액션 결정 (step-by-step 루프)
5. FormatConverter → 6종 annotation JSONL 생성

## Key Design Decisions

- **좌표 정규화**: 모든 bbox는 `[0, 1000]` 범위로 정규화 (`grounding.normalize_bounds()`)
- **두 가지 출력 포맷**: Format A (conversations 형식, grounding/OCR/state_diff/element_qa/caption용), Format B (ShareGPT messages 형식, world_modeling용)
- **xml_encoder의 이중 출력**: `parse_to_html_xml()`은 bounds 포함, `encode_to_html_xml()`은 bounds/important/class 제거된 LLM용 버전
- **flagged step 필터링**: 앱 이탈(external_app) 발생 step은 annotation에서 자동 제외
- **step 카운터**: DataWriter에서 XML 저장 시(`save_xml()`) step_count 증가 — 스크린샷과 XML은 같은 step 번호로 매칭
- **Smart Explorer 동기화**: `server.wait_for_xml(timeout)` → 액션 실행 후 TCP로 XML 수신 대기 → timeout 시 ADB `uiautomator dump` fallback

## Configuration

- `configs/collection/default.yaml`: Smart Explorer 액션 가중치, 서버 설정, annotation 활성화/비활성화, 출력 경로
- `configs/collection/apps.yaml`: 수집 대상 앱 목록 (package, source, max_events)

## Dependencies

- Python >= 3.10, pyyaml, Pillow, loguru (core), openai (optional annotation)
- Android SDK (adb, avdmanager, emulator), Java 17+ (앱 빌드)
