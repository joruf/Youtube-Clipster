#!/usr/bin/env bash
# Connect phones over ADB TCP port 5555.
#
# Network profiles (matched by this PC's subnet):
#   home   192.168.178.*
#   away   192.168.198.*  (other Wi-Fi; update phone IPs in adb-phones.env)
#
# Usage:
#   scripts/adb-phones.sh           auto-detect profile
#   scripts/adb-phones.sh away      force profile
#   scripts/adb-phones.sh all         try every known endpoint
#   ADB_PHONES="192.168.198.28:5555" scripts/adb-phones.sh
#
# Config: scripts/adb-phones.env (template: adb-phones.env.example)
# Once after USB/wireless pairing:  adb -s <device> tcpip 5555
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults — overridden by adb-phones.env
ADB_SUBNET_home="${ADB_SUBNET_home:-192.168.178.}"
ADB_PHONES_home="${ADB_PHONES_home:-192.168.178.22:5555 192.168.178.50:5555}"

ADB_SUBNET_away="${ADB_SUBNET_away:-192.168.198.}"
ADB_PHONES_away="${ADB_PHONES_away:-192.168.198.28:5555}"

if [[ -f "$HERE/adb-phones.env" ]]; then
    # shellcheck source=/dev/null
    source "$HERE/adb-phones.env"
fi

local_ipv4() {
    ip -4 route get 1.1.1.1 2>/dev/null \
        | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}' \
        || hostname -I 2>/dev/null | awk '{print $1}'
}

detect_profile() {
    local ip="${1:-}"
    [[ -z "$ip" ]] && return 1
    if [[ "$ip" == "$ADB_SUBNET_home"* ]]; then
        echo home
        return 0
    fi
    if [[ "$ip" == "$ADB_SUBNET_away"* ]]; then
        echo away
        return 0
    fi
    return 1
}

phones_for_profile() {
    case "$1" in
        home) printf '%s\n' $ADB_PHONES_home ;;
        away) printf '%s\n' $ADB_PHONES_away ;;
        all)
            printf '%s\n' $ADB_PHONES_home $ADB_PHONES_away
            ;;
        *)
            echo "Unknown profile: $1 (home | away | all)" >&2
            exit 1
            ;;
    esac
}

unique_endpoints() {
    awk '!seen[$0]++'
}

profile="${ADB_PHONES_PROFILE:-${1:-}}"
if [[ -z "$profile" ]]; then
    ip="$(local_ipv4 || true)"
    profile="$(detect_profile "$ip" || true)"
    if [[ -z "$profile" ]]; then
        echo "No subnet match for ${ip:-unknown IP} — trying all profiles." >&2
        profile=all
    else
        echo "Network profile: $profile (${ip})" >&2
    fi
else
    echo "Network profile: $profile (manual)" >&2
fi

if [[ -n "${ADB_PHONES:-}" ]]; then
    mapfile -t endpoints < <(printf '%s\n' $ADB_PHONES | unique_endpoints)
else
    mapfile -t endpoints < <(phones_for_profile "$profile" | tr ' ' '\n' | sed '/^$/d' | unique_endpoints)
fi

if [[ ${#endpoints[@]} -eq 0 ]]; then
    echo "No ADB endpoints configured." >&2
    exit 1
fi

for endpoint in "${endpoints[@]}"; do
    adb connect "$endpoint" >/dev/null || true
done

adb devices -l
