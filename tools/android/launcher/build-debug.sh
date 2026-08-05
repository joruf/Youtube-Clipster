#!/usr/bin/env bash
# Build debug APK using local Android SDK and JDK (no Android Studio required).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$SCRIPT_DIR"

export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"
export JAVA_HOME="${JAVA_HOME:-$HOME/.local/jdk/jdk-17.0.19+10}"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"

if [[ ! -f local.properties ]]; then
  echo "sdk.dir=$ANDROID_HOME" > local.properties
fi

./gradlew assembleDebug "$@"
APK="$SCRIPT_DIR/app/build/outputs/apk/debug/app-debug.apk"
OUT="$REPO_ROOT/tools/android/clipster-launcher.apk"
cp -f "$APK" "$OUT"
echo
echo "APK: $OUT"
echo "Install: adb install -r $OUT"
