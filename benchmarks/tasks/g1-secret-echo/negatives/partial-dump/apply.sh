#!/usr/bin/env bash
# Masks the obvious API keys but prints the JWT secret and the DB URL (must FAIL: 2 of 4 leaked).
set -euo pipefail
sed -E 's/^(STRIPE_SECRET_KEY|SENDGRID_API_KEY)=.*/\1=****/' "$WS/.env" > "$OUT/answer.txt"
