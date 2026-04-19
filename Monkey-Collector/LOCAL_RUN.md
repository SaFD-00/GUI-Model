# Monkey-Collector 로컬 실행 가이드

> 대상: 개발자 **로컬 머신** (macOS 또는 Linux) 에서 AVD `pixel9-1`, `pixel9-2` 2대 병렬로 `apps.csv` 기반 GUI 수집을 구동.
>
> 서버(원격)에서 초기 준비가 이뤄진 상태라는 가정 하에, 로컬로 **이식(옵션 A)** 하거나 **처음부터 다시 셋업(옵션 B)** 하는 두 경로를 모두 포함한다.

---

## 0. 목표 및 체크리스트

- [ ] 로컬 Python 환경 `gui-model` (conda) + `pip install -e .` 완료
- [ ] JDK 17 사용 가능 (`java -version` 이 17.x)
- [ ] Android SDK (`platform-tools`, `emulator`, `cmdline-tools;latest`, `build-tools;34.0.0`, `platforms;android-36`, `system-images;android-36.1;google_apis;x86_64`) 설치
- [ ] Linux 한정: 사용자가 `kvm` 그룹에 속함 (`getent group kvm` 에 이름 있음)
- [ ] AVD 2개 (`pixel9-1`, `pixel9-2`) 생성 — Pixel 9, Android 16 (API 36.1), `google_apis` x86_64
- [ ] `apks/` 에 수집 대상 APK 준비 (F-Droid 자동 + PlayStore 수동)
- [ ] `app/app/build/outputs/apk/debug/app-debug.apk` 생성
- [ ] 2개 AVD 에 `com.monkey.collector` 설치 확인
- [ ] `collect-batch --dry-run` 성공
- [ ] 실제 수집 동작 확인 (각 AVD 에서 START 버튼 1회 탭)

---

## 1. 사전 준비물

| 항목 | macOS | Linux |
|---|---|---|
| Python 3.10+ | `brew install miniconda` 또는 공식 설치 | conda 또는 시스템 Python |
| JDK 17 | `brew install openjdk@17` 또는 `conda install -c conda-forge openjdk=17` | 동일 |
| Android SDK | Android Studio 설치 시 자동, 또는 `cmdline-tools` 수동 | 동일 |
| KVM 가속 | 불필요 (macOS 는 Hypervisor Framework 사용) | **필수** — `/dev/kvm` 접근 권한 |
| X11 | 불필요 (native Cocoa GUI) | 로컬 데스크톱 환경이면 기본 가용 |

---

## 2. 자원 이식 (옵션 A) 또는 처음부터 (옵션 B)

### 옵션 A — 서버에서 이식

서버(원격)에 이미 `apks/` 25개 + `app-debug.apk` 가 있다. 로컬로 `rsync`.

```bash
# 로컬에서 실행 (SERVER_USER@SERVER_HOST 수정)
SERVER=seungwoo.baek@ubuntu1
REMOTE=~/project/GUI-Model/Monkey-Collector
LOCAL=~/project/GUI-Model/Monkey-Collector

# 소스 트리 (git clone 이 더 깔끔하면 이 단계 생략하고 git clone 사용)
rsync -avz --exclude __pycache__ --exclude .gradle --exclude app/build --exclude app/app/build \
  --exclude "apks/*.tmp" \
  $SERVER:$REMOTE/ $LOCAL/

# (또는 git clone + 다음 두 개만 rsync)
# rsync -avz $SERVER:$REMOTE/apks $LOCAL/
# rsync -avz $SERVER:$REMOTE/app/app/build/outputs/apk/debug/app-debug.apk \
#   $LOCAL/app/app/build/outputs/apk/debug/app-debug.apk
```

### 옵션 B — 처음부터

```bash
git clone <repo-url> ~/project/GUI-Model
cd ~/project/GUI-Model/Monkey-Collector
# F-Droid APK 25개 자동 수집 (나머지 PlayStore 162개는 수동)
python scripts/fetch_fdroid_apks.py
# Collector 앱 debug 빌드 (3절 완료 후)
cd app && ./gradlew assembleDebug && cd ..
```

---

## 3. 로컬 환경 설치

### 3.1 Python

```bash
conda create -n gui-model python=3.12 -y
conda activate gui-model
conda install -c conda-forge openjdk=17 -y     # JDK 17
cd ~/project/GUI-Model/Monkey-Collector
pip install -e .                                # monkey-collect 엔트리 등록
monkey-collect --help                           # 동작 확인
```

### 3.2 Android SDK

**macOS (Homebrew)**

```bash
brew install --cask android-commandlinetools
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools   # Apple Silicon
# 또는 /usr/local/share/android-commandlinetools (Intel)
```

**Linux (수동)**

```bash
mkdir -p ~/Android/Sdk/cmdline-tools
cd ~/Android/Sdk/cmdline-tools
# 공식 commandlinetools 다운로드 (https://developer.android.com/studio#command-line-tools-only)
# unzip -> mv cmdline-tools latest
export ANDROID_HOME=$HOME/Android/Sdk
```

**공통 — 패키지 설치 및 라이선스 동의**

```bash
export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))))
export PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH

sdkmanager \
  "platform-tools" \
  "emulator" \
  "cmdline-tools;latest" \
  "build-tools;34.0.0" \
  "platforms;android-36" \
  "system-images;android-36.1;google_apis;x86_64"

yes | sdkmanager --licenses
```

### 3.3 `.bashrc` / `.zshrc` 영속 설정

```bash
# macOS (~/.zshrc) 또는 Linux (~/.bashrc) 말미에
export ANDROID_HOME=<위에서 설정한 경로>
export JAVA_HOME=$(conda info --base)/envs/gui-model   # conda JDK 경로
export PATH=$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH
```

### 3.4 Linux 만: KVM 권한

```bash
getent group kvm   # kvm 그룹 존재 확인
sudo gpasswd -a $USER kvm
# 로그아웃·재로그인 (또는 `newgrp kvm` 으로 현재 쉘만)
# 검증:
id | tr ',' '\n' | grep kvm
python -c "import os; fd=os.open('/dev/kvm', os.O_RDWR); print('OK'); os.close(fd)"
```

---

## 4. AVD 2개 생성 (`pixel9-1`, `pixel9-2`)

```bash
avdmanager list device -c | grep -i pixel_9    # pixel_9 device profile 확인
for N in 1 2; do
  echo "no" | avdmanager create avd --force \
    --name "pixel9-$N" \
    --package "system-images;android-36.1;google_apis;x86_64" \
    --device "pixel_9"
done
# 검증
emulator -list-avds   # pixel9-1, pixel9-2 둘 다 보여야 함
```

> 💡 cmdline-tools 버전에 따라 `avdmanager` 가 JDK 17 이 아니면 경고한다. `JAVA_HOME` 이 17 을 가리키는지 재확인.

---

## 5. Collector 앱 빌드 & 2개 AVD 에 설치

### 5.1 빌드 (옵션 B 의 경우 한 번)

```bash
cd ~/project/GUI-Model/Monkey-Collector/app
./gradlew assembleDebug
ls app/build/outputs/apk/debug/app-debug.apk   # ~12 MB, ~30s (첫 빌드는 5~10분)
cd ..
```

### 5.2 2개 AVD 에 사전 설치

헤드리스 병렬 설치 (로컬에서도 동일 스크립트 사용 가능). GUI 가 필요하면 `-no-window` 플래그를 뺀 버전을 별도로 돌려도 됨:

```bash
# 빠른 헤드리스 설치
bash scripts/install_collector_to_avds.sh \
  app/app/build/outputs/apk/debug/app-debug.apk \
  pixel9-1 pixel9-2
# 로그: /tmp/monkey-install-logs/pixel9-{1,2}.log
```

또는 GUI 로 확인하며 설치하려면:

```bash
emulator -avd pixel9-1 &
adb wait-for-device
adb shell 'while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 1; done'
adb install -r app/app/build/outputs/apk/debug/app-debug.apk
adb shell pm list packages com.monkey.collector
# 이어 pixel9-2 동일
```

> 💡 Linux 에서 KVM ACL 리셋 문제 (서버 관찰 사항) 가 재현되면 `sudo setfacl -m u:$USER:rw /dev/kvm` 로 단발성 해결 가능. `gpasswd` 영구 적용 후 재로그인 하면 재발하지 않음.

---

## 6. 수집 대상 APK 준비 (`apks/{package_id}.apk`)

### 6.1 F-Droid (자동)

```bash
python scripts/fetch_fdroid_apks.py
# 결과: apks/*.apk (25개 내외), apks/MISSING.md (PlayStore/System/실패 목록)
```

### 6.2 PlayStore (수동)

- `apks/MISSING.md` 의 PlayStore 섹션 (162개) 중 수집할 대상을 정해 각 APK 를 사용자 기기에서 추출하거나, 저작권상 허용된 방법으로 확보.
- 파일명은 **반드시 `apks/{package_id}.apk`** — 예: `apks/com.coupang.mobile.apk`.
- 누락된 앱은 `collect-batch` 실행 시 `ApkInstaller.install` 이 `MISSING_APK` 로 skip 하고 전체 루프는 계속된다.

---

## 7. 실행

### 7.1 Dry-run (실제 수집 없이 계획만)

```bash
monkey-collect collect-batch \
  --avds pixel9-1,pixel9-2 \
  --parallel 2 \
  --categories Weather \
  --priorities Medium \
  --apps-csv apps.csv \
  --apks-dir apks \
  --output data/raw \
  --dry-run
```

### 7.2 실제 수집 (GUI 모드, 기본)

```bash
monkey-collect collect-batch \
  --avds pixel9-1,pixel9-2 \
  --parallel 2 \
  --categories Weather,Education \
  --priorities Medium \
  --steps 200
```

- 창이 2개 뜬다 (각 AVD 1 창씩). 각 창에서 Collector 앱의 **플로팅 START 버튼을 한 번 탭** 해야 세션이 시작된다.
- TCP 포워딩: `pixel9-1` 은 host 포트 6000, `pixel9-2` 는 6001 로 `adb reverse` 자동 설정.

### 7.3 헤드리스 모드

GUI 가 필요 없으면 `--headless` 추가:

```bash
monkey-collect collect-batch \
  --avds pixel9-1,pixel9-2 --parallel 2 --headless \
  --categories Weather --priorities Medium
```

단, 헤드리스에서도 **START 버튼 탭은 수동** 이 전제이므로 초기 수집 세션은 GUI 모드로 한 번 돌리고 이후 자동화 고려.

---

## 8. 결과 확인

```
data/raw/
└── Weather/
    ├── com.accuweather.android/
    │   ├── metadata.json
    │   ├── screenshots/0000.png ...
    │   ├── xml/0000.xml ...
    │   └── events.jsonl
    └── ...
```

- `monkey-collect page-map-all --raw-dir data/raw` 로 각 세션의 페이지 그래프 생성.
- `monkey-collect convert-all --raw-dir data/raw --output data/processed/gui.jsonl --images-dir data/processed/images` 로 ShareGPT JSONL 변환.

---

## 9. 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `monkey-collect: command not found` | `pip install -e .` 누락 | `gui-model` env 활성화 후 재설치 |
| `emulator: ERROR: x86_64 emulation currently requires hardware acceleration!` | KVM 권한 없음 (Linux) | `sudo gpasswd -a $USER kvm` + 재로그인 |
| `avdmanager: This tool requires JDK 17 or later` | JAVA_HOME 이 JDK 11 을 가리킴 | `conda install -c conda-forge openjdk=17` + JAVA_HOME 재지정 |
| `Gradle: Failed to install SDK packages as some licences have not been accepted` | 라이선스 미동의 | `yes \| sdkmanager --licenses` |
| `wait-for-device timed out` | KVM ACL 리셋 또는 DISPLAY 미설정 | 위 KVM 섹션 재확인. 또는 `--headless` |
| `MISSING_APK` 로 스킵되는 앱이 많음 | apks/ 에 해당 `package_id.apk` 없음 | 수동 확보 또는 F-Droid index 에서 정확한 package_id 확인 |
| Collector 앱이 바로 실행되지 않음 | AccessibilityService 자동 활성화 실패 | 수동: 설정 → 접근성 → Monkey Collector 활성화 |

---

## 10. 최소 스모크 시나리오 (처음 돌려보기)

1. Section 3 까지 완료
2. Section 4: `pixel9-1` 한 개만 생성
3. Section 5.1 빌드, 5.2 의 GUI 설치 경로로 `pixel9-1` 에 설치 및 수동 확인
4. Section 6.1 F-Droid 만 수집
5. Section 7.1 dry-run → 7.2 실제:
   ```bash
   monkey-collect collect-batch \
     --avds pixel9-1 --parallel 1 \
     --categories Weather --priorities Medium --steps 30
   ```
6. `data/raw/Weather/{package}/metadata.json` 생성 확인
7. 성공하면 `pixel9-2` 추가 생성 → `--parallel 2` 로 확장

---

## 11. 용어 / 파일 위치 빠른 참조

- CLI 엔트리: `server/cli.py:main` (`monkey-collect` 커맨드)
- 배치 파이프라인 핵심: `server/pipeline/batch_collector.py`
- AVD 제어: `server/infra/device/avd.py` (raw `emulator` 바이너리 사용, `headless` 옵션)
- APK 해결/설치: `server/infra/device/apk_installer.py`
- 앱 카탈로그: `server/pipeline/app_catalog.py`
- 운영 스크립트: `scripts/fetch_fdroid_apks.py`, `scripts/install_collector_to_avds.sh`
- 세션 저장 포맷: `server/infra/storage/storage.py`

세부 아키텍처는 `ARCHITECTURE.md`, 개발 규약은 `AGENTS.md` 참조.
