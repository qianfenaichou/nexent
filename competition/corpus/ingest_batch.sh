#!/usr/bin/env bash
# Batch ingest with auto session refresh (token lives 7200s; long batches
# with big PDFs can outlive it). Serial pacing keeps Ray memory sane.
set -u
cd "$(dirname "$0")"
EMAIL="t02admin@knowevo.com"
PASS="${KW_INGEST_PASSWORD:?set KW_INGEST_PASSWORD}"
INDEX="1-fdc99d691db5406e980751dbe131f201"
BASE="http://localhost:3000"

refresh() {
  TOKEN=$(/usr/bin/curl -s -i -X POST "$BASE/api/user/signin" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" \
    | grep -oP 'nexent_access_token=\K[^;]+' | head -1)
  echo "$TOKEN" > /tmp/t02_token.txt
  echo "[token] refreshed (${#TOKEN} chars)"
}

ingest_one() {  # <local_file> <asset_no>
  local file="$1" asset="$2" name path code
  name=$(/usr/bin/basename "$file")
  local up
  up=$(/usr/bin/curl -s -X POST "$BASE/api/file/upload" \
    -b "nexent_access_token=$TOKEN" -H "Authorization: Bearer $TOKEN" \
    -F "file=@$file" -F 'destination=minio' -F "index_name=$INDEX")
  path=$(echo "$up" | /usr/bin/python3 -c \
    "import json,sys; d=json.load(sys.stdin); print((d.get('uploaded_file_paths') or [''])[0])" 2>/dev/null)
  if [ -z "$path" ]; then
    if echo "$up" | grep -q expired; then refresh; fi
    echo "$asset UPLOAD_FAIL $(echo "$up" | head -c 100)"
    return 1
  fi
  code=$(/usr/bin/curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/file/process" \
    -b "nexent_access_token=$TOKEN" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d "{\"files\":[{\"path_or_url\":\"$path\",\"filename\":\"$name\"}],\"index_name\":\"$INDEX\",\"destination\":\"minio\",\"chunking_strategy\":\"basic\"}")
  echo "$asset $code $path"
  [ "$code" = "201" ]
}

refresh
for spec in "$@"; do
  file="${spec%%|*}"; asset="${spec##*|}"
  ingest_one "$file" "$asset" || true
  /bin/sleep "${PACE:-10}"
done