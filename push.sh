#!/bin/bash
# 一键推送到 GitHub 脚本
# 使用方法: ./push.sh "提交信息"

set -e  # 遇到错误立即退出

# 进入 GitHub 上传目录
cd "$(dirname "$0")"

# 检查是否有未提交的更改
if [ -z "$(git status --porcelain)" ]; then
    echo "没有需要推送的更改"
    exit 0
fi

# 添加所有更改
git add -A

# 使用参数作为提交信息，如果没有参数则使用默认信息
COMMIT_MSG="${1:-Update agent strategy}"
git commit -m "$COMMIT_MSG"

# 推送到 main 分支
git push origin main

echo "推送完成！"
echo "提交哈希: $(git rev-parse --short HEAD)"
echo "仓库: https://github.com/QYshen-0521/Agentic-Observer"
