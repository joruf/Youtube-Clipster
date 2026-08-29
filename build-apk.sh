#!/usr/bin/env bash
#
# Build YouTube Clipster debug APK: bump gradle.properties, compile, stage to ~/clipster-apk/
# (via stage-apk.sh). Optionally install on connected phones and/or copy the APK to
# /sdcard/Download/ on each phone.
#
# Run:  ./build-apk.sh
#       ./build-apk.sh --deploy          # install + copy to Download/
#       ./build-apk.sh --download-only   # copy to Download/ only
# Double-click: YouTube-Clipster-APK-bauen.desktop (terminal + zenity dialogs).
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRADLE_PROPS="$HERE/tools/android/launcher/gradle.properties"
STAGE="$HERE/stage-apk.sh"
ADB_PHONES="$HERE/scripts/adb-phones.sh"
APK="$HERE/tools/android/launcher/app/build/outputs/apk/debug/app-debug.apk"
PACKAGE=de.loresoft.youtubeclipster
APP_SLUG=clipster
APP_TITLE="YouTube Clipster"
VERSION_PREFIX=CLIPSTER_
GRADLE_TASK=assembleDebug
GRADLE_CD=2
RUN_TESTS=0
SYNC_PY=1

# Phone step after build: none | install | download | both
PHONE_ACTION=""

case "$GRADLE_CD" in
  1) GRADLE_ROOT="$HERE/android" ;;
  2) GRADLE_ROOT="$HERE/tools/android/launcher" ;;
  *) GRADLE_ROOT="$HERE" ;;
esac
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"

ensure_android_sdk() {
  local props="$GRADLE_ROOT/local.properties"
  if [[ ! -f "$props" ]] || ! grep -q '^sdk.dir=' "$props" 2>/dev/null; then
    mkdir -p "$GRADLE_ROOT"
    printf 'sdk.dir=%s\n' "$ANDROID_HOME" > "$props"
  fi
}

CURRENT_STEP=0
TOTAL_STEPS=0
PROGRESS_PID=""

have_zenity() {
  command -v zenity >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]
}

use_terminal_progress() {
  [[ -t 2 ]]
}

pause() {
  if [[ -t 0 ]]; then
    read -r -p "Press Enter to close…" || true
  fi
}

notify() {
  local title="$1"
  local body="$2"
  if have_zenity; then
    zenity --info --title="$title" --width=460 --text="$body"
  else
    echo
    echo "$title"
    echo "$body"
    pause
  fi
}

error_msg() {
  local msg="$1"
  if have_zenity; then
    zenity --error --text="$msg"
  else
    echo "$msg" >&2
    pause
  fi
}

progress_start() {
  TOTAL_STEPS=3
  [[ "$RUN_TESTS" == 1 ]] && TOTAL_STEPS=$((TOTAL_STEPS + 1))
  [[ -n "$PHONE_ACTION" && "$PHONE_ACTION" != none ]] && TOTAL_STEPS=$((TOTAL_STEPS + 2))
  CURRENT_STEP=0

  if have_zenity && ! use_terminal_progress; then
    local pipe
    pipe="$(mktemp -u)"
    mkfifo "$pipe"
    zenity --progress --title="Build $APP_TITLE APK" --width=480 --auto-close --no-cancel \
      --text="Starting…" <"$pipe" &
    PROGRESS_PID=$!
    exec 3>"$pipe"
    echo "0" >&3 || true
    echo "# Starting…" >&3 || true
  fi
}

progress_update() {
  local msg="$1"
  if [[ -n "$PROGRESS_PID" ]] && [[ -e /proc/$PROGRESS_PID ]]; then
    local pct=0
    if [[ "$TOTAL_STEPS" -gt 0 ]]; then
      pct=$((CURRENT_STEP * 100 / TOTAL_STEPS))
    fi
    echo "$pct" >&3 || true
    echo "# $msg" >&3 || true
  fi
}

progress_stop() {
  if [[ -n "$PROGRESS_PID" ]] && [[ -e /proc/$PROGRESS_PID ]]; then
    echo "100" >&3 || true
    echo "# Done" >&3 || true
    exec 3>&-
    wait "$PROGRESS_PID" 2>/dev/null || true
    PROGRESS_PID=""
  fi
}

step() {
  local msg="$1"
  CURRENT_STEP=$((CURRENT_STEP + 1))
  progress_update "$msg"
  if use_terminal_progress || [[ "${BUILD_VERBOSE:-}" == 1 ]]; then
    echo "" >&2
    echo "════════════════════════════════════════════════════════════" >&2
    printf " [%d/%d] %s\n" "$CURRENT_STEP" "$TOTAL_STEPS" "$msg" >&2
    echo "════════════════════════════════════════════════════════════" >&2
  fi
}

substep() {
  echo "  → $1" >&2
  progress_update "$1"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -d | --deploy) PHONE_ACTION=both; shift ;;
      --install) PHONE_ACTION=install; shift ;;
      --download | --download-only) PHONE_ACTION=download; shift ;;
      --no-deploy | --no-phone) PHONE_ACTION=none; shift ;;
      -h | --help)
        cat <<EOF
Usage: $0 [options]

  --deploy          Install on phones and save a copy to Download/
  --install         Install on phones only
  --download-only   Save APK copy to Download/ on phones (no install)
  --no-phone        Build and stage only (default when you decline the dialog)

Non-interactive:
  BUILD_APK_BUMP=no|yes|keep
  BUILD_APK_PHONE=both|install|download|none
EOF
        exit 0
        ;;
      *)
        echo "Unknown option: $1 (try --help)" >&2
        exit 1
        ;;
    esac
  done
}

if [[ -z "${JAVA_HOME:-}" ]] || [[ ! -x "${JAVA_HOME}/bin/javac" ]]; then
  for candidate in \
    /usr/lib/jvm/java-21-openjdk-amd64 \
    /usr/lib/jvm/java-21-openjdk \
    /usr/lib/jvm/java-17-openjdk-amd64 \
    /usr/lib/jvm/java-17-openjdk \
    "$HOME/jdk/current" \
    "$HOME/.local/jdk/jdk-17.0.19+10"; do
    if [[ -x "$candidate/bin/javac" ]]; then
      export JAVA_HOME="$candidate"
      break
    fi
  done
fi
if [[ ! -x "${JAVA_HOME:-/nonexistent}/bin/javac" ]]; then
  error_msg "JDK not found. Set JAVA_HOME or install OpenJDK 17+."
  exit 1
fi

read_versions() {
  VERSION_NAME="$(grep "^${VERSION_PREFIX}VERSION_NAME=" "$GRADLE_PROPS" | cut -d= -f2- | tr -d '[:space:]')"
  VERSION_CODE="$(grep "^${VERSION_PREFIX}VERSION_CODE=" "$GRADLE_PROPS" | cut -d= -f2- | tr -d '[:space:]')"
}

bump_patch() {
  local name="$1"
  if [[ "$name" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    printf '%s.%s.%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "$((BASH_REMATCH[3] + 1))"
  else
    printf '%s\n' "$name"
  fi
}

write_versions() {
  local name="$1"
  local code="$2"
  sed -i "s/^${VERSION_PREFIX}VERSION_NAME=.*/${VERSION_PREFIX}VERSION_NAME=$name/" "$GRADLE_PROPS"
  sed -i "s/^${VERSION_PREFIX}VERSION_CODE=.*/${VERSION_PREFIX}VERSION_CODE=$code/" "$GRADLE_PROPS"
  if [[ "$SYNC_PY" == 1 ]]; then
    sed -i "s/^APP_VERSION = .*/APP_VERSION = \\"$name\\"/" "$HERE/clipster/__init__.py"
    sed -i "s/^APP_BUILD = .*/APP_BUILD = $code/" "$HERE/clipster/__init__.py"
  fi
}

choose_versions() {
  read_versions
  local proposed_name proposed_code choice custom_name custom_code build_new

  if [[ -n "${BUILD_APK_BUMP:-}" ]]; then
    proposed_name="$(bump_patch "$VERSION_NAME")"
    proposed_code="$((VERSION_CODE + 1))"
    case "${BUILD_APK_BUMP,,}" in
      yes | 1 | bump | new)
        NEW_NAME="$proposed_name"
        NEW_CODE="$proposed_code"
        ;;
      no | 0 | keep | same)
        NEW_NAME="$VERSION_NAME"
        NEW_CODE="$VERSION_CODE"
        ;;
      *) exit 0 ;;
    esac
    return
  fi

  proposed_name="$(bump_patch "$VERSION_NAME")"
  proposed_code="$((VERSION_CODE + 1))"
  build_new="Build new version ($proposed_name)"

  if have_zenity; then
    choice="$(zenity --list --title="Build $APP_TITLE APK" --width=460 --height=280 \
      --text="Current: $VERSION_NAME (build $VERSION_CODE)\nNew:     $proposed_name (build $proposed_code)" \
      --column="Action" \
      "$build_new" \
      "Build without version bump" \
      "Enter custom version…" \
      "Cancel" 2>/dev/null || true)"

    case "${choice:-Cancel}" in
      "$build_new")
        NEW_NAME="$proposed_name"
        NEW_CODE="$proposed_code"
        ;;
      "Build without version bump")
        NEW_NAME="$VERSION_NAME"
        NEW_CODE="$VERSION_CODE"
        ;;
      "Enter custom version…")
        custom_name="$(zenity --entry --title="Version name" \
          --text="${VERSION_PREFIX}VERSION_NAME (e.g. 1.0.0):" \
          --entry-text="$proposed_name" 2>/dev/null || true)"
        [[ -z "$custom_name" ]] && exit 0
        custom_code="$(zenity --entry --title="Build number" \
          --text="${VERSION_PREFIX}VERSION_CODE (must increase, current $VERSION_CODE):" \
          --entry-text="$proposed_code" 2>/dev/null || true)"
        [[ -z "$custom_code" ]] && exit 0
        if ! [[ "$custom_code" =~ ^[0-9]+$ ]]; then
          zenity --error --text="Build number must be a whole integer."
          exit 1
        fi
        NEW_NAME="$custom_name"
        NEW_CODE="$custom_code"
        ;;
      *) exit 0 ;;
    esac
  else
    echo "Build $APP_TITLE APK"
    echo "Current: $VERSION_NAME (build $VERSION_CODE)"
    echo "Suggested: $proposed_name (build $proposed_code)"
    echo
    echo "  1) Build new version (suggested)"
    echo "  2) Build without version bump"
    echo "  3) Cancel"
    read -r -p "Choice [1]: " choice
    case "${choice:-1}" in
      1) NEW_NAME="$proposed_name"; NEW_CODE="$proposed_code" ;;
      2) NEW_NAME="$VERSION_NAME"; NEW_CODE="$VERSION_CODE" ;;
      *) exit 0 ;;
    esac
  fi
}

ask_phone_action() {
  if [[ -n "${BUILD_APK_PHONE:-}" ]]; then
    PHONE_ACTION="${BUILD_APK_PHONE,,}"
    return
  fi
  [[ -n "$PHONE_ACTION" ]] && return

  if have_zenity; then
    choice="$(zenity --list --title="Build $APP_TITLE APK" --width=520 --height=300 \
      --text="What should happen on connected phones after the build?" \
      --column="Action" \
      "Install and save to Download/" \
      "Install only" \
      "Save to Download/ only" \
      "Skip phone step" 2>/dev/null || true)"
    case "${choice:-Skip phone step}" in
      "Install and save to Download/") PHONE_ACTION=both ;;
      "Install only") PHONE_ACTION=install ;;
      "Save to Download/ only") PHONE_ACTION=download ;;
      *) PHONE_ACTION=none ;;
    esac
  else
    echo
    echo "Phone step after build:"
    echo "  1) Install and save to Download/ (default)"
    echo "  2) Install only"
    echo "  3) Save to Download/ only"
    echo "  4) Skip"
    read -r -p "Choice [1]: " choice
    case "${choice:-1}" in
      1) PHONE_ACTION=both ;;
      2) PHONE_ACTION=install ;;
      3) PHONE_ACTION=download ;;
      *) PHONE_ACTION=none ;;
    esac
  fi
}

run_gradle() {
  local task="$1"
  export ANDROID_HOME JAVA_HOME
  if [[ "$GRADLE_CD" == 1 ]]; then
    (cd "$HERE/android" && ./gradlew "$task" --console=plain)
  elif [[ "$GRADLE_CD" == 2 ]]; then
    (cd "$HERE/tools/android/launcher" && ./gradlew "$task" --console=plain)
  else
    (cd "$HERE" && ./gradlew "$task" --console=plain)
  fi
}

connected_devices() {
  adb devices | awk '$2 == "device" && $1 !~ /^emulator-/ { print $1 }'
}

download_apk_name() {
  printf '%s_%s (%s).apk\n' "$APP_SLUG" "$NEW_NAME" "$NEW_CODE"
}

apk_for_phone() {
  local target="$HOME/${APP_SLUG}-apk"
  local staged="$target/$(download_apk_name)"
  if [[ -f "$staged" ]]; then
    printf '%s\n' "$staged"
  elif [[ -f "$APK" ]]; then
    printf '%s\n' "$APK"
  else
    return 1
  fi
}

copy_apk_to_download() {
  local serial="$1"
  local src remote="/sdcard/Download/$(download_apk_name)"
  src="$(apk_for_phone)" || return 1
  adb -s "$serial" push "$src" "$remote" >/dev/null
}

deploy_to_phones() {
  local serial result_lines=() ok=0 fail=0 n=0 total
  local do_install=0 do_download=0

  case "$PHONE_ACTION" in
    both) do_install=1; do_download=1 ;;
    install) do_install=1 ;;
    download) do_download=1 ;;
    *) return 0 ;;
  esac

  if ! command -v adb >/dev/null 2>&1; then
    error_msg "adb not found. Install Android platform-tools."
    return 1
  fi
  if [[ ! -x "$ADB_PHONES" ]]; then
    error_msg "scripts/adb-phones.sh is missing or not executable."
    return 1
  fi
  if ! apk_for_phone >/dev/null; then
    error_msg "APK missing: $APK"
    return 1
  fi

  step "Connect phones via ADB"
  substep "scripts/adb-phones.sh"
  "$ADB_PHONES" >&2

  mapfile -t devices < <(connected_devices)
  if [[ ${#devices[@]} -eq 0 ]]; then
    error_msg "No phone connected. Check USB or scripts/adb-phones.sh."
    return 1
  fi

  total="${#devices[@]}"
  if [[ "$do_install" == 1 && "$do_download" == 1 ]]; then
    step "Install and copy to Download/ on $total phone(s)"
  elif [[ "$do_install" == 1 ]]; then
    step "Install on $total phone(s)"
  else
    step "Copy APK to Download/ on $total phone(s)"
  fi

  for serial in "${devices[@]}"; do
    n=$((n + 1))
    local line="" install_ok=1 download_ok=1

    if [[ "$do_install" == 1 ]]; then
      substep "[$n/$total] $serial — install -r"
      if adb -s "$serial" install -r "$APK"; then
        install_ok=0
      fi
    fi

    if [[ "$do_download" == 1 ]]; then
      substep "[$n/$total] $serial — copy to Download/$(download_apk_name)"
      if copy_apk_to_download "$serial"; then
        download_ok=0
      fi
    fi

    if [[ "$do_install" == 1 && "$install_ok" == 0 ]]; then
      line="✓ $serial: installed"
      if [[ "$do_download" == 1 ]]; then
        if [[ "$download_ok" == 0 ]]; then
          line+=", Download/$(download_apk_name)"
        else
          line+=", Download copy failed"
        fi
      fi
      version_line="$(adb -s "$serial" shell dumpsys package "$PACKAGE" \
        | grep -E 'versionName=|versionCode=' \
        | head -2 | tr -d '\r' | paste -sd ' ' -)"
      [[ -n "$version_line" ]] && line+=" ($version_line)"
      result_lines+=("$line")
      ok=$((ok + 1))
    elif [[ "$do_install" == 0 && "$do_download" == 1 && "$download_ok" == 0 ]]; then
      result_lines+=("✓ $serial: Download/$(download_apk_name)")
      ok=$((ok + 1))
    else
      if [[ "$do_install" == 1 && "$install_ok" != 0 ]]; then
        result_lines+=("✗ $serial: install failed")
      elif [[ "$do_download" == 1 && "$download_ok" != 0 ]]; then
        result_lines+=("✗ $serial: Download copy failed")
      else
        result_lines+=("✗ $serial: phone step failed")
      fi
      fail=$((fail + 1))
    fi
  done

  substep "Phones OK: $ok, failed: $fail"
  local summary="Phones OK: $ok, failed: $fail"
  if [[ ${#result_lines[@]} -gt 0 ]]; then
    summary+=$'\n\n'"$(printf '%s\n' "${result_lines[@]}")"
  fi
  echo "$summary"
  [[ "$fail" -eq 0 ]]
}

run_build() {
  export BUILD_VERBOSE=1
  progress_start

  step "Write version to gradle.properties"
  if [[ "$NEW_NAME" != "$VERSION_NAME" || "$NEW_CODE" != "$VERSION_CODE" ]]; then
    substep "$VERSION_NAME (build $VERSION_CODE) → $NEW_NAME (build $NEW_CODE)"
    write_versions "$NEW_NAME" "$NEW_CODE"
  else
    substep "Unchanged: $NEW_NAME (build $NEW_CODE)"
  fi

  if [[ "$RUN_TESTS" == 1 ]]; then
    step "Run unit tests"
    substep ":app:testDebugUnitTest"
    run_gradle ":app:testDebugUnitTest"
  fi

  step "Compile APK (Gradle)"
  substep "This may take a few minutes…"
  run_gradle "$GRADLE_TASK"

  step "Stage APK to ~/${APP_SLUG}-apk"
  BUILD_VERBOSE=1 SKIP_GRADLE=1 SKIP_TESTS=1 "$STAGE"

  local deploy_summary=""
  if [[ -n "$PHONE_ACTION" && "$PHONE_ACTION" != none ]]; then
    deploy_summary="$(deploy_to_phones || true)"
  fi

  progress_stop

  local target="$HOME/${APP_SLUG}-apk"
  local body=$'Version '"$NEW_NAME"$' (build '"$NEW_CODE"$')\n\nStaged at:\n'"$target/${APP_SLUG}.apk"$'\n'"$target/$(download_apk_name)"$'\n\nServe: python3 '"$target/serve.py"
  if [[ -n "$deploy_summary" ]]; then
    body+=$'\n\n'"$deploy_summary"
  fi

  if use_terminal_progress; then
    echo "" >&2
    echo "════════════════════════════════════════════════════════════" >&2
    echo " Done — $NEW_NAME (build $NEW_CODE)" >&2
    echo "════════════════════════════════════════════════════════════" >&2
  fi

  notify "YouTube Clipster APK ready" "$body"
}

main() {
  parse_args "$@"
  if [[ ! -f "$GRADLE_PROPS" ]]; then
    echo "gradle.properties missing: $GRADLE_PROPS" >&2
    exit 1
  fi
  if [[ ! -x "$STAGE" ]]; then
    echo "stage-apk.sh missing or not executable: $STAGE" >&2
    exit 1
  fi
  ensure_android_sdk
  choose_versions
  ask_phone_action
  run_build
}

main "$@"
