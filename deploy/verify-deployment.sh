#!/bin/sh
set -eu

base=${1:-http://127.0.0.1:8000}
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

health=$(curl -sS -o "$tmp" -w '%{http_code}' "$base/api/health")
test "$health" = 200
api=$(curl -sS -o "$tmp" -w '%{http_code}' "$base/api/user/state")
test "$api" = 401
page=$(curl -sS -o /dev/null -w '%{http_code}' "$base/")
test "$page" = 303
login=$(curl -sS -o /dev/null -w '%{http_code}' "$base/login")
test "$login" = 200
echo "deployment check passed: health=200 api=401 page=303 login=200"
