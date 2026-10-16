#!/bin/sh
set -eu

# Filled by 01_create_resources.py; repository defaults are empty.
ENDPOINT=''
SESSION_ID=''
ENV_API_KEY=''

: "${ENDPOINT:?请先运行 01_create_resources.py 或填写 ENDPOINT}"
: "${SESSION_ID:?请先运行 01_create_resources.py 或填写 SESSION_ID}"
: "${ENV_API_KEY:?请先运行 01_create_resources.py 或填写 ENV_API_KEY}"

curl --fail-with-body --silent --show-error --connect-timeout 10 --max-time 60 \
  "${ENDPOINT%/}/v1/sessions/${SESSION_ID}/events" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ENV_API_KEY}" \
  -d '{
    "events": [{
      "type": "user.message",
      "content": [{
        "type": "text",
        "text": "帮我查下「25财年 摩根大通的财报数据，然后分析比较下，然后做成一份分析PPT"
      }]
    }]
  }'
printf '\n'
