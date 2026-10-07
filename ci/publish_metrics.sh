#!/usr/bin/env bash
# Publishes build-metrics.json to the "ci-metrics" branch as runs/<run_id>.json.
# The exporter reads these files over raw.githubusercontent.com, so build-quality
# metrics reach Prometheus/Grafana without any credentials on the monitoring side.
set -euo pipefail

METRICS_FILE="${1:-build-metrics.json}"
BRANCH="${METRICS_BRANCH:-ci-metrics}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
: "${GITHUB_RUN_ID:?}" "${GITHUB_REPOSITORY:?}"

WORKDIR="$(mktemp -d)"
REMOTE="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

git config --global user.name "github-actions[bot]"
git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"

if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
  git clone --quiet --depth 1 --branch "$BRANCH" "$REMOTE" "$WORKDIR"
else
  git init --quiet "$WORKDIR"
  git -C "$WORKDIR" checkout --quiet --orphan "$BRANCH"
  git -C "$WORKDIR" remote add origin "$REMOTE"
  printf '# CI build metrics\n\nOne JSON file per pipeline run (runs/<run_id>.json), written by the CI/CD workflow\nand scraped by the CI/CD metrics exporter for Grafana.\n' > "$WORKDIR/README.md"
fi

mkdir -p "$WORKDIR/runs"
cp "$METRICS_FILE" "$WORKDIR/runs/${GITHUB_RUN_ID}.json"
cd "$WORKDIR"
git add -A
git commit --quiet -m "metrics: run #${GITHUB_RUN_NUMBER:-?} (${GITHUB_SHA:0:7})"

for attempt in 1 2 3 4 5; do
  if git push --quiet origin "HEAD:${BRANCH}"; then
    echo "Published runs/${GITHUB_RUN_ID}.json to ${BRANCH}"
    exit 0
  fi
  echo "Push rejected (attempt ${attempt}); rebasing on latest ${BRANCH}"
  sleep $((attempt * 2))
  git pull --quiet --rebase origin "$BRANCH"
done
echo "Could not publish metrics after 5 attempts" >&2
exit 1
