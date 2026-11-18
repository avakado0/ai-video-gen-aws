#!/usr/bin/env bash
# Build the folder Terraform zips for the API Lambda: build/api/
#
#   api/            the app (lambdas/api), imported as `api.handler` by Lambda
#   <dependencies>  FastAPI, Mangum and friends from requirements-api.txt
#
# Run it before `terraform plan` whenever lambdas/api/ or requirements-api.txt changed.
# Dependencies are fetched as Linux wheels for the Lambda runtime (python3.12, x86_64), so
# the result does not depend on the machine this runs on. boto3 is not bundled: the runtime has it.
set -euo pipefail

cd "$(dirname "$0")/.."
out=build/api

rm -rf "$out"
mkdir -p "$out"

python3 -m pip install \
  --quiet --disable-pip-version-check \
  --target "$out" \
  --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: \
  --no-compile \
  -r requirements-api.txt

cp -r lambdas/api "$out/api"

# Keep the zip identical for identical input: no bytecode, no timestamps, no console scripts.
find "$out" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$out/bin"  # console scripts the Lambda never runs
find "$out" -exec touch -h -d '2020-01-01 00:00:00 UTC' {} +

echo "built $out ($(du -sh "$out" | cut -f1) unzipped; Lambda allows 250 MB unzipped, 50 MB zipped)"
