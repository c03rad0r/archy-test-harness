#!/usr/bin/env bash
# Manual P3 acceptance: stream a URL for longer than the 900 s deadline and
# record the observed duration and bytes.
#
# This is deliberately a wall-clock operator test, not a pytest lane. It is
# the only way to prove a film longer than 15 minutes streams to completion.
#
# Usage: scripts/media-soak.sh --url URL [--node NAME] [--minutes 16]
#                              [--header 'Authorization: ...'] [--expect-bytes N]
set -euo pipefail

URL=""
NODE=${NODE:-unknown}
MINUTES=16
EXPECT_BYTES=""
OUT=""
HEADERS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL=${2:?}; shift 2 ;;
    --node) NODE=${2:?}; shift 2 ;;
    --minutes) MINUTES=${2:?}; shift 2 ;;
    --expect-bytes) EXPECT_BYTES=${2:?}; shift 2 ;;
    --header) HEADERS+=(-H "$2"); shift 2 ;;
    --out) OUT=${2:?}; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$URL" ] || { echo "FAIL: --url is required" >&2; exit 2; }
command -v curl >/dev/null || { echo "FAIL: curl not installed" >&2; exit 1; }

TOTAL=$((MINUTES * 60))
MAX=$((TOTAL + 120))
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=${OUT:-results/media-soak-$STAMP.json}
mkdir -p "$(dirname "$OUT")"

echo "streaming $URL for up to $((MAX / 60)) min; target >= $TOTAL s"
START=$(date +%s)
set +e
RESULT=$(curl -fsS --max-time "$MAX" -o /dev/null \
  -w '%{http_code} %{size_download} %{time_total}' \
  "${HEADERS[@]}" "$URL" 2>"$OUT.stderr")
RC=$?
set -e
END=$(date +%s)
WALL=$((END - START))

read -r HTTP BYTES SECONDS_TOTAL <<<"$RESULT"
HTTP=${HTTP:-000}
BYTES=${BYTES:-0}
SECONDS_TOTAL=${SECONDS_TOTAL:-0}

PASS=1
REASONS=()
[ "$RC" -eq 0 ] || { PASS=0; REASONS+=("curl exit $RC"); }
[ "$(printf '%.0f' "$SECONDS_TOTAL")" -ge "$TOTAL" ] || { PASS=0; REASONS+=("duration ${SECONDS_TOTAL}s < ${TOTAL}s"); }
[ "$BYTES" -gt 0 ] || { PASS=0; REASONS+=("zero bytes"); }
if [ -n "$EXPECT_BYTES" ] && [ "$BYTES" -ne "$EXPECT_BYTES" ]; then
  PASS=0
  REASONS+=("bytes $BYTES != expected $EXPECT_BYTES")
fi

{
  printf '{\n'
  printf '  "node": "%s",\n' "$NODE"
  printf '  "url": "%s",\n' "$URL"
  printf '  "started": "%s",\n' "$STAMP"
  printf '  "wall_seconds": %d,\n' "$WALL"
  printf '  "curl_time_total": %s,\n' "$SECONDS_TOTAL"
  printf '  "http_code": "%s",\n' "$HTTP"
  printf '  "bytes": %s,\n' "$BYTES"
  printf '  "curl_exit": %d,\n' "$RC"
  printf '  "result": "%s",\n' "$([ "$PASS" = 1 ] && echo PASS || echo FAIL)"
  printf '  "reasons": ['
  for i in "${!REASONS[@]}"; do
    [ "$i" -gt 0 ] && printf ', '
    printf '"%s"' "${REASONS[$i]}"
  done
  printf ']\n'
  printf '}\n'
} > "$OUT"

cat "$OUT"
if [ "$PASS" = 1 ]; then
  echo "RESULT: PASS (> $TOTAL s, $BYTES bytes)"
else
  echo "RESULT: FAIL (${REASONS[*]})" >&2
  exit 1
fi
