#!/bin/bash
# 一键推送到 GitHub 脚本
# 使用方法: ./push.sh "提交信息"

set -euo pipefail

# 进入 GitHub 上传目录
cd "$(dirname "$0")"

# 只从 main 推送，避免把其他分支意外写入比赛主干
if [ "$(git branch --show-current)" != "main" ]; then
    echo "请先切换到 main 分支"
    exit 1
fi

# 添加所有更改
if [ -n "$(git status --porcelain)" ]; then
    git add -A
    COMMIT_MSG="${1:-Update native V4 agent}"
    git commit -m "$COMMIT_MSG"
fi

# 推送到 main 分支
git push origin HEAD:main

echo "推送完成！"
echo "提交哈希: $(git rev-parse --short HEAD)"
echo "仓库: https://github.com/QYshen-0521/Agentic-Observer"
echo "比赛提交: python submit_competition.py（固定刚推送的 commit）"
