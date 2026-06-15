#!/usr/bin/env bash
set -euo pipefail

release_commit="$(git rev-parse --short HEAD)"
report_dir="diagnostics/demo_doctor"
report_md="$report_dir/latest.md"
report_json="$report_dir/latest.json"
latest_markdown_path="diagnostics/demo_doctor/latest.md"

doctor_status=0
make doctor || doctor_status=$?

if [ ! -f "$report_md" ] || [ ! -f "$report_json" ]; then
  echo "Doctor report missing after make doctor" >&2
  exit 1
fi

cp "$report_md" "$report_dir/release_${release_commit}.md"
cp "$report_json" "$report_dir/release_${release_commit}.json"

echo "release_commit=$release_commit"
echo "markdown=$report_dir/release_${release_commit}.md"
echo "json=$report_dir/release_${release_commit}.json"
exit "$doctor_status"
