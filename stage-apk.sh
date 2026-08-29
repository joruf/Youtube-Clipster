#!/usr/bin/env bash
#
# Build the Clipster launcher APK and stage it for download.
#
# Run:  ./stage-apk.sh
set -euo pipefail

# Gradle needs a JDK (javac), not only a JRE.
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
  echo "JDK not found. Set JAVA_HOME or install OpenJDK 17+." >&2
  exit 1
fi
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$HERE/tools/android/launcher"
TARGET="$HOME/clipster-apk"
APK="$LAUNCHER/app/build/outputs/apk/debug/app-debug.apk"
OUT="$HERE/tools/android/clipster-launcher.apk"
AAPT="$HOME/Android/Sdk/build-tools/35.0.0/aapt2"

if [[ ! -f "$LAUNCHER/local.properties" ]] || ! grep -q '^sdk.dir=' "$LAUNCHER/local.properties" 2>/dev/null; then
  printf 'sdk.dir=%s\n' "$ANDROID_HOME" > "$LAUNCHER/local.properties"
fi

cd "$LAUNCHER"
if [[ "${SKIP_GRADLE:-}" != 1 ]]; then
    if [[ "${BUILD_VERBOSE:-}" == 1 ]]; then
        echo "  → Gradle assembleDebug (may take a few minutes)…" >&2
        ./gradlew assembleDebug --console=plain
    else
        ./gradlew assembleDebug -q
    fi
fi

if [[ ! -f "$APK" ]]; then
    echo "APK not found: $APK (run Gradle first)" >&2
    exit 1
fi

cp -f "$APK" "$OUT"

if [[ "${BUILD_VERBOSE:-}" == 1 ]]; then
    echo "  → Reading APK metadata…" >&2
fi
BADGING="$("$AAPT" dump badging "$APK")"
VERSION="$(printf '%s' "$BADGING" | sed -n "s/.*versionName='\([^']*\)'.*/\1/p")"
CODE="$(printf '%s' "$BADGING" | sed -n "s/.*versionCode='\([^']*\)'.*/\1/p")"

NAME="clipster-$VERSION-b$CODE.apk"
STAMP="$(date '+%Y-%m-%d %H:%M')"
SIZE="$(du -h "$APK" | cut -f1)"
SHA="$(sha256sum "$APK" | cut -c1-16)"

mkdir -p "$TARGET"
find "$TARGET" -maxdepth 1 -name 'clipster-*.apk' -delete
cp "$APK" "$TARGET/$NAME"
cp "$APK" "$TARGET/clipster.apk"

if [[ "${BUILD_VERBOSE:-}" == 1 ]]; then
    echo "  → Writing download page (index.html)…" >&2
fi
cat > "$TARGET/index.html" <<HTML
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YouTube Clipster $VERSION</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 2.5rem 1.25rem;
         background: #1a1a2e; color: #fff; }
  .card { max-width: 26rem; margin: 0 auto; background: #fff; color: #1a1a2e;
          border-radius: 1rem; padding: 1.75rem; }
  h1 { margin: 0 0 .25rem; font-size: 1.4rem; }
  p.sub { margin: 0 0 1.5rem; color: #444; font-size: .95rem; }
  a.btn { display: block; text-align: center; background: #1a1a2e; color: #fff;
          text-decoration: none; padding: .9rem 1rem; border-radius: .6rem; font-weight: 600; }
  dl { display: grid; grid-template-columns: auto 1fr; gap: .4rem 1rem; margin-top: 1.5rem; font-size: .9rem; }
  dt { color: #444; } dd { margin: 0; }
</style>
</head>
<body>
<div class="card">
  <h1>YouTube Clipster</h1>
  <p class="sub">Launcher APK (Termux setup helper)</p>
  <a class="btn" href="clipster.apk?v=$CODE">Download clipster.apk</a>
  <dl>
    <dt>Version</dt><dd>$VERSION (build $CODE)</dd>
    <dt>Built</dt><dd>$STAMP</dd>
    <dt>Size</dt><dd>$SIZE</dd>
    <dt>SHA-256</dt><dd><code>$SHA…</code></dd>
  </dl>
</div>
</body>
</html>
HTML

if [ ! -f "$TARGET/serve.py" ]; then
    cp "$HOME/fundus-apk/serve.py" "$TARGET/serve.py" 2>/dev/null \
        || cp "$HOME/wildpick-apk/serve.py" "$TARGET/serve.py" 2>/dev/null || true
    chmod +x "$TARGET/serve.py" 2>/dev/null || true
fi

echo
echo "Staged in $TARGET:"
echo "  clipster.apk   ($SIZE, $VERSION build $CODE)"
echo "  $NAME"
echo "  tools/android/clipster-launcher.apk"
echo
echo "Start server:  python3 $TARGET/serve.py"
echo "Install:       adb install -r $TARGET/clipster.apk"
