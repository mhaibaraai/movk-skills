#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "用法: scripts/pack-skill.sh <skill-name>" >&2
  echo "  将 skills/<skill-name>/ 打包为 dist/<skill-name>.zip" >&2
  exit 1
}

[ $# -eq 1 ] || usage

readonly root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly name="$1"
readonly src="$root/skills/$name"
readonly out="$root/dist/$name.zip"

if [ ! -f "$src/SKILL.md" ]; then
  echo "错误: $src/SKILL.md 不存在，不是合法的 skill 目录" >&2
  exit 1
fi

command -v zip >/dev/null || { echo "错误: 需要 zip 命令" >&2; exit 1; }

mkdir -p "$root/dist"
rm -f "$out"

# 从 skills/ 下打包，使 zip 内顶层目录为 <skill-name>/
cd "$root/skills"
zip -qr "$out" "$name" \
  -x "*/__pycache__/*" "*.pyc" "*/.DS_Store" ".DS_Store"

echo "已打包: dist/$name.zip"
