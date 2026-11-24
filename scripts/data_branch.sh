#!/usr/bin/env bash
# 把快取資料存在獨立的 data 分支（每次強制覆蓋成單一 commit，不會讓 repo 越來越大）
set -euo pipefail
REMOTE="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

pull_into() {  # $1 = 目的資料夾
  # 11/24：原本 ls-remote 一失敗（網路暫時出錯）就當成「沒有 data 分支」，拿空資料夾繼續，
  # 之後 push 會把整個快取蓋成空的。現在分清楚：真的沒有分支（代碼 2）才從空的開始，其他錯誤重試 3 次後停止。
  rm -rf "$1"
  local rc=1 i
  for i in 1 2 3; do
    rc=0; git ls-remote --exit-code --heads "$REMOTE" data >/dev/null 2>&1 || rc=$?
    [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ] && break
    echo "data 分支查詢失敗（代碼 $rc），第 $i 次，30 秒後重試" >&2; sleep 30
  done
  if [ "$rc" -eq 0 ]; then
    for i in 1 2 3; do
      git clone -q --depth 1 --branch data "$REMOTE" "$1" && break
      echo "data 分支下載失敗，第 $i 次" >&2; rm -rf "$1"; sleep 30
      [ "$i" -eq 3 ] && { echo "無法下載 data 分支，停止（避免用空快取覆蓋）" >&2; exit 1; }
    done
    rm -rf "$1/.git"
  elif [ "$rc" -ne 2 ]; then
    echo "無法確認 data 分支（代碼 $rc），停止（避免用空快取覆蓋）" >&2; exit 1
  fi
  mkdir -p "$1/cache" "$1/site_data"
}

guard() {  # $1 = 要推上去的資料夾：原本有完整快取，這次卻少了股價檔 → 不推
  if [ ! -f "$1/cache/prices_monthly.csv.gz" ] && git ls-remote --exit-code --heads "$REMOTE" data >/dev/null 2>&1; then
    echo "要推的快取裡沒有 prices_monthly.csv.gz，疑似快取不完整，取消推送" >&2; exit 1
  fi
}

push_dir() {  # $1 = 要推上去的資料夾
  guard "$1"
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
    rm -rf databr/site_data/kline     # K 線網站檔每天由快取重新產生，不用再存一份
    push_dir databr
    ;;
  push-tw)
    # 回填工作只更新台股財報：先抓最新 data 分支，再只覆蓋台股相關檔案，避免蓋掉其他工作的結果
    pull_into databr_latest
    mkdir -p databr_latest/cache/tw_fin
    cp -r databr/cache/tw_fin/. databr_latest/cache/tw_fin/
    for f in tw_fin_meta.json tw_progress.json tw_universe.csv tw_stock_info.csv backtest_triggered \
             tw_month_rev.csv.gz tw_month_rev_meta.json tw_month_rev_progress.json \
             tw_delisted.csv tw_delisted_prices.csv.gz tw_delisted_daily.csv.gz; do
      [ -f "databr/cache/$f" ] && cp "databr/cache/$f" databr_latest/cache/ || true
    done
    push_dir databr_latest
    ;;
  push-us-delisted)
    # 美股下市資料只更新 cache/us_delisted，避免蓋掉其他工作的結果
    pull_into databr_latest
    mkdir -p databr_latest/cache/us_delisted
    cp -r databr/cache/us_delisted/. databr_latest/cache/us_delisted/ 2>/dev/null || true
    push_dir databr_latest
    ;;
  push-chips-hist)
    # 籌碼歷史回補只更新 cache/chips_hist，避免蓋掉其他工作的結果
    pull_into databr_latest
    mkdir -p databr_latest/cache/chips_hist
    cp -r databr/cache/chips_hist/. databr_latest/cache/chips_hist/ 2>/dev/null || true
    push_dir databr_latest
    ;;
  push-derivs)
    # 衍生性商品回補只更新 derivs 資料夾和網站的 derivs.json，避免蓋掉其他工作的結果
    pull_into databr_latest
    mkdir -p databr_latest/cache/derivs databr_latest/site_data
    cp -r databr/cache/derivs/. databr_latest/cache/derivs/ 2>/dev/null || true
    [ -f site/data/derivs.json ] && cp site/data/derivs.json databr_latest/site_data/ || true
    push_dir databr_latest
    ;;
esac
