#!/bin/sh
set -eu

# Filled by 01_create_resources.py; repository defaults are empty.
ENDPOINT=''
SESSION_ID=''
ENV_API_KEY=''

: "${ENDPOINT:?请先运行 01_create_resources.py 或填写 ENDPOINT}"
: "${SESSION_ID:?请先运行 01_create_resources.py 或填写 SESSION_ID}"
: "${ENV_API_KEY:?请先运行 01_create_resources.py 或填写 ENV_API_KEY}"

# Optional first argument: resume after a persisted event sequence.
AFTER_SEQUENCE=${1:-0}
case "$AFTER_SEQUENCE" in
  *[!0-9]*|'') echo "after_sequence must be a non-negative integer" >&2; exit 2 ;;
esac

curl -N --fail-with-body --silent --show-error --connect-timeout 10 \
  "${ENDPOINT%/}/v1/sessions/${SESSION_ID}/events/stream" \
  -H "Authorization: Bearer ${ENV_API_KEY}" \
  -H "Accept: text/event-stream" \
  --get --data-urlencode "after_sequence=$AFTER_SEQUENCE" &
STREAM_PID=$!

cleanup() {
  kill "$STREAM_PID" 2>/dev/null || true
  wait "$STREAM_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
wait "$STREAM_PID"
