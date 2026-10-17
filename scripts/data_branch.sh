#!/usr/bin/env bash
# 把快取資料存在獨立的 data 分支（每次強制覆蓋成單一 commit，不會讓 repo 越來越大）
set -euo pipefail
REMOTE="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

pull_into() {  # $1 = 目的資料夾
  rm -rf "$1"
  if git ls-remote --exit-code --heads "$REMOTE" data >/dev/null 2>&1; then
    git clone -q --depth 1 --branch data "$REMOTE" "$1"
    rm -rf "$1/.git"
  fi
  mkdir -p "$1/cache" "$1/site_data"
}

push_dir() {  # $1 = 要推上去的資料夾
  cd "$1"
  rm -rf .git
  git init -q -b data
  git config user.name "github-actions"
  git config user.email "github-actions@users.noreply.github.com"
  git add -A
  git commit -qm "data $(date -u +%Y-%m-%dT%H:%MZ)"
  git push -q -f "$REMOTE" data
}

case "$1" in
  pull)
    pull_into databr
    mkdir -p site/data/logs
    [ -f databr/site_data/backtest.json ] && cp databr/site_data/backtest.json site/data/ || true
    du -sh databr || true
    ;;
  push)
    mkdir -p databr/site_data
    cp -r site/data/. databr/site_data/ 2>/dev/null || true
    push_dir databr
    ;;
  push-tw)
    # 回填工作只更新台股財報：先抓最新 data 分支，再只覆蓋台股相關檔案，避免蓋掉其他工作的結果
    pull_into databr_latest
    mkdir -p databr_latest/cache/tw_fin
    cp -r databr/cache/tw_fin/. databr_latest/cache/tw_fin/
    for f in tw_fin_meta.json tw_progress.json tw_universe.csv tw_stock_info.csv backtest_triggered; do
      [ -f "databr/cache/$f" ] && cp "databr/cache/$f" databr_latest/cache/ || true
    done
    push_dir databr_latest
    ;;
esac
