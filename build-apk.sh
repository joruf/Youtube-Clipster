#!/usr/bin/env bash
#
# Build YouTube Clipster debug APK: bump version, compile, stage to ~/clipster-apk/
# (via stage-apk.sh). Optionally install on connected phones via adb.
#
# Run:  ./build-apk.sh
#       ./build-apk.sh --deploy
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRADLE_PROPS="$HERE/tools/android/launcher/gradle.properties"
STAGE="$HERE/stage-apk.sh"
ADB_PHONES="$HERE/scripts/adb-phones.sh"
APK="$HERE/tools/android/launcher/app/build/outputs/apk/debug/app-debug.apk"
PACKAGE=de.loresoft.youtubeclipster
APP_SLUG=clipster
VERSION_PREFIX=CLIPSTER_
GRADLE_TASK=assembleDebug
GRADLE_CD=2
RUN_TESTS=0
SYNC_PY=1

DEPLOY=""
CURRENT_STEP=0
TOTAL_STEPS=0
PROGRESS_PID=""

have_zenity() { command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; }
use_terminal_progress() { [[ -t 2 ]]; }
pause() { [[ -t 0 ]] && read -r -p "Press Enter to close…"; }
notify() {
  local title="$1" body="$2"
  if have_zenity; then zenity --info --title="$title" --width=460 --text="$body"
  else echo; echo "$title"; echo "$body"; pause; fi
}
error_msg() {
  local msg="$1"
  if have_zenity; then zenity --error --text="$msg"; else echo "$msg" >&2; pause; fi
}

progress_start() {
  TOTAL_STEPS=3
  [[ "$RUN_TESTS" == 1 ]] && TOTAL_STEPS=$((TOTAL_STEPS + 1))
  [[ "$DEPLOY" == yes ]] && TOTAL_STEPS=$((TOTAL_STEPS + 2))
  CURRENT_STEP=0
  if have_zenity && ! use_terminal_progress; then
    local pipe; pipe="$(mktemp -u)"; mkfifo "$pipe"
    zenity --progress --title="Build YouTube Clipster APK" --width=480 --auto-close --no-cancel \
      --text="Starting…" <"$pipe" &
    PROGRESS_PID=$!; exec 3>"$pipe"; echo "0" >&3; echo "# Starting…" >&3
  fi
}
progress_update() {
  local msg="$1"
  if [[ -n "$PROGRESS_PID" ]] && [[ -e /proc/$PROGRESS_PID ]]; then
    local pct=0; [[ "$TOTAL_STEPS" -gt 0 ]] && pct=$((CURRENT_STEP * 100 / TOTAL_STEPS))
    echo "$pct" >&3; echo "# $msg" >&3
  fi
}
progress_stop() {
  if [[ -n "$PROGRESS_PID" ]] && [[ -e /proc/$PROGRESS_PID ]]; then
    echo "100" >&3; echo "# Done" >&3; exec 3>&-; wait "$PROGRESS_PID" 2>/dev/null || true; PROGRESS_PID=""
  fi
}
step() {
  local msg="$1"; CURRENT_STEP=$((CURRENT_STEP + 1)); progress_update "$msg"
  if use_terminal_progress || [[ "${BUILD_VERBOSE:-}" == 1 ]]; then
    echo "" >&2
    echo "════════════════════════════════════════════════════════════" >&2
    printf " [%d/%d] %s\n" "$CURRENT_STEP" "$TOTAL_STEPS" "$msg" >&2
    echo "════════════════════════════════════════════════════════════" >&2
  fi
}
substep() { echo "  → $1" >&2; progress_update "$1"; }

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -d|--deploy) DEPLOY=yes; shift ;;
      --no-deploy) DEPLOY=no; shift ;;
      -h|--help) echo "Usage: $0 [--deploy | --no-deploy]"; exit 0 ;;
      *) echo "Unknown option: $1 (try --help)" >&2; exit 1 ;;
    esac
  done
}

if [[ -z "${JAVA_HOME:-}" ]] || [[ ! -x "${JAVA_HOME}/bin/javac" ]]; then
  for candidate in \
    /usr/lib/jvm/java-21-openjdk-amd64 /usr/lib/jvm/java-17-openjdk-amd64 \
    /usr/lib/jvm/java-21-openjdk /usr/lib/jvm/java-17-openjdk \
    "$HOME/jdk/current" "$HOME/.local/jdk/jdk-17.0.19+10"; do
    if [[ -x "$candidate/bin/javac" ]]; then export JAVA_HOME="$candidate"; break; fi
  done
fi
if [[ ! -x "${JAVA_HOME:-/nonexistent}/bin/javac" ]]; then
  error_msg "JDK not found. Set JAVA_HOME or install OpenJDK 17+."; exit 1
fi

read_versions() {
  VERSION_NAME="$(grep "^${VERSION_PREFIX}VERSION_NAME=" "$GRADLE_PROPS" | cut -d= -f2- | tr -d '[:space:]')"
  VERSION_CODE="$(grep "^${VERSION_PREFIX}VERSION_CODE=" "$GRADLE_PROPS" | cut -d= -f2- | tr -d '[:space:]')"
}
bump_patch() {
  local name="$1"
  if [[ "$name" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    printf '%s.%s.%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "$((BASH_REMATCH[3] + 1))"
  else printf '%s\n' "$name"; fi
}
write_versions() {
  local name="$1" code="$2"
  sed -i "s/^${VERSION_PREFIX}VERSION_NAME=.*/${VERSION_PREFIX}VERSION_NAME=$name/" "$GRADLE_PROPS"
  sed -i "s/^${VERSION_PREFIX}VERSION_CODE=.*/${VERSION_PREFIX}VERSION_CODE=$code/" "$GRADLE_PROPS"
  if [[ "$SYNC_PY" == 1 ]]; then
    sed -i "s/^APP_VERSION = .*/APP_VERSION = \"$name\"/" "$HERE/clipster/__init__.py"
    sed -i "s/^APP_BUILD = .*/APP_BUILD = $code/" "$HERE/clipster/__init__.py"
  fi
}

choose_versions() {
  read_versions
  local proposed_name proposed_code choice custom_name custom_code build_new
  proposed_name="$(bump_patch "$VERSION_NAME")"
  proposed_code="$((VERSION_CODE + 1))"
  build_new="Build new version ($proposed_name)"
  if have_zenity; then
    choice="$(zenity --list --title="Build YouTube Clipster APK" --width=460 --height=280 \
      --text="Current: $VERSION_NAME (build $VERSION_CODE)\nNew:     $proposed_name (build $proposed_code)" \
      --column="Action" "$build_new" "Build without version bump" "Enter custom version…" "Cancel" 2>/dev/null || true)"
    case "${choice:-Cancel}" in
      "$build_new") NEW_NAME="$proposed_name"; NEW_CODE="$proposed_code" ;;
      "Build without version bump") NEW_NAME="$VERSION_NAME"; NEW_CODE="$VERSION_CODE" ;;
      "Enter custom version…")
        custom_name="$(zenity --entry --title="Version name" --text="${VERSION_PREFIX}VERSION_NAME:" --entry-text="$proposed_name" 2>/dev/null || true)"
        [[ -z "$custom_name" ]] && exit 0
        custom_code="$(zenity --entry --title="Build number" --text="${VERSION_PREFIX}VERSION_CODE:" --entry-text="$proposed_code" 2>/dev/null || true)"
        [[ -z "$custom_code" ]] && exit 0
        [[ "$custom_code" =~ ^[0-9]+$ ]] || { zenity --error --text="Build number must be an integer."; exit 1; }
        NEW_NAME="$custom_name"; NEW_CODE="$custom_code" ;;
      *) exit 0 ;;
    esac
  else
    echo "Build YouTube Clipster APK"
    echo "Current: $VERSION_NAME (build $VERSION_CODE)"
    echo "Suggested: $proposed_name (build $proposed_code)"
    echo; echo "  1) Build new version (suggested)"; echo "  2) Build without version bump"; echo "  3) Cancel"
    read -r -p "Choice [1]: " choice
    case "${choice:-1}" in
      1) NEW_NAME="$proposed_name"; NEW_CODE="$proposed_code" ;;
      2) NEW_NAME="$VERSION_NAME"; NEW_CODE="$VERSION_CODE" ;;
      *) exit 0 ;;
    esac
  fi
}

ask_deploy() {
  [[ -n "$DEPLOY" ]] && return
  if have_zenity; then
    if zenity --question --title="Build YouTube Clipster APK" --text="Install on connected phones after the build?"; then DEPLOY=yes; else DEPLOY=no; fi
  else
    read -r -p "Install on phones after build? [y/N]: " answer
    case "${answer:-n}" in y|Y|j|J) DEPLOY=yes ;; *) DEPLOY=no ;; esac
  fi
}

run_gradle() {
  local task="$1"
  if [[ "$GRADLE_CD" == 1 ]]; then (cd "$HERE/android" && ./gradlew "$task" --console=plain)
  elif [[ "$GRADLE_CD" == 2 ]]; then (cd "$HERE/tools/android/launcher" && ./gradlew "$task" --console=plain)
  else (cd "$HERE" && ./gradlew "$task" --console=plain); fi
}

connected_devices() { adb devices | awk '$2 == "device" && $1 !~ /^emulator-/ { print $1 }'; }

deploy_to_phones() {
  local target="$HOME/${APP_SLUG}-apk"
  local staged="$target/${APP_SLUG}-$NEW_NAME-b$NEW_CODE.apk"
  local download_name="${APP_SLUG}-$NEW_NAME-b$NEW_CODE.apk"
  local serial result_lines=() ok=0 fail=0 n=0 total
  command -v adb >/dev/null 2>&1 || { error_msg "adb not found."; return 1; }
  [[ -x "$ADB_PHONES" ]] || { error_msg "scripts/adb-phones.sh missing."; return 1; }
  [[ -f "$APK" ]] || { error_msg "APK missing: $APK"; return 1; }
  step "Connect phones via ADB"; substep "scripts/adb-phones.sh"; "$ADB_PHONES" >&2
  mapfile -t devices < <(connected_devices)
  [[ ${#devices[@]} -gt 0 ]] || { error_msg "No phone connected."; return 1; }
  total="${#devices[@]}"; step "Install on $total phone(s)"
  for serial in "${devices[@]}"; do
    n=$((n + 1)); substep "[$n/$total] $serial — install -r"
    if adb -s "$serial" install -r "$APK"; then
      [[ -f "$staged" ]] && adb -s "$serial" push "$staged" "/sdcard/Download/$download_name" >/dev/null
      version_line="$(adb -s "$serial" shell dumpsys package "$PACKAGE" | grep -E 'versionName=|versionCode=' | head -2 | tr -d '\r' | paste -sd ' ' -)"
      result_lines+=("✓ $serial: $version_line"); ok=$((ok + 1))
    else result_lines+=("✗ $serial: install failed"); fail=$((fail + 1)); fi
  done
  substep "Installed: $ok, failed: $fail"
  local summary="Installed: $ok, failed: $fail"
  [[ ${#result_lines[@]} -gt 0 ]] && summary+=$(printf "\n\n%s" "$(printf "%s\n" "${result_lines[@]}")")
  echo "$summary"; [[ "$fail" -eq 0 ]]
}

run_build() {
  export BUILD_VERBOSE=1
  progress_start
  step "Write version to gradle.properties"
  if [[ "$NEW_NAME" != "$VERSION_NAME" || "$NEW_CODE" != "$VERSION_CODE" ]]; then
    substep "$VERSION_NAME (build $VERSION_CODE) → $NEW_NAME (build $NEW_CODE)"
    write_versions "$NEW_NAME" "$NEW_CODE"
  else substep "Unchanged: $NEW_NAME (build $NEW_CODE)"; fi
  if [[ "$RUN_TESTS" == 1 ]]; then
    step "Run unit tests"; substep ":app:testDebugUnitTest"; run_gradle ":app:testDebugUnitTest"
  fi
  step "Compile APK (Gradle)"; substep "This may take a few minutes…"; run_gradle "$GRADLE_TASK"
  step "Stage APK to ~/${APP_SLUG}-apk"
  BUILD_VERBOSE=1 SKIP_GRADLE=1 SKIP_TESTS=1 "$STAGE"
  local deploy_summary=""
  [[ "$DEPLOY" == yes ]] && deploy_summary="$(deploy_to_phones || true)"
  progress_stop
  local target="$HOME/${APP_SLUG}-apk"
  local body="Version $NEW_NAME (build $NEW_CODE)\n\nStaged at:\n$target/${APP_SLUG}.apk\n$target/${APP_SLUG}-$NEW_NAME-b$NEW_CODE.apk\n\nServe: python3 $target/serve.py"
  [[ -n "$deploy_summary" ]] && body+=$'\n\n'"$deploy_summary"
  if use_terminal_progress; then
    echo "" >&2; echo "════════════════════════════════════════════════════════════" >&2
    echo " Done — $NEW_NAME (build $NEW_CODE)" >&2
    echo "════════════════════════════════════════════════════════════" >&2
  fi
  notify "YouTube Clipster APK ready" "$body"
}

main() {
  parse_args "$@"
  [[ -f "$GRADLE_PROPS" ]] || { echo "Missing: $GRADLE_PROPS" >&2; exit 1; }
  [[ -x "$STAGE" ]] || { echo "stage-apk.sh missing: $STAGE" >&2; exit 1; }
  choose_versions; ask_deploy; run_build
}
main "$@"
