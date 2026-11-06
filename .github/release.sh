#!/usr/bin/env bash
set -euo pipefail
cd release
sha256sum --check "astrbot_plugin_bond-${TAG_INPUT}.zip.sha256"
cd ..
# 自动标签在所有检查及归档校验通过后创建；同一作业继续发布，不依赖标签事件。
if [[ "$AUTOMATIC" == "true" ]]; then
  git -c user.name='github-actions[bot]' \
    -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
    -c tag.gpgsign=false tag -a "$TAG_INPUT" -F CHANGELOG.md
fi
python -c 'import sys; sys.path.insert(0, "scripts"); from build_package import validate_tag; from check_project import check_project; validate_tag(sys.argv[1], check_project())' "$TAG_INPUT"
if [[ "$AUTOMATIC" == "true" ]]; then
  TAG_OBJECT=$(git rev-parse "refs/tags/$TAG_INPUT")
  git -c credential.helper= -c 'credential.helper=!gh auth git-credential' \
    push origin "refs/tags/$TAG_INPUT"
fi
ACTUAL_TAG=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG_INPUT" --jq '.object.sha')
test "$ACTUAL_TAG" = "$TAG_OBJECT"
PRE_ARGS=()
case "$TAG_INPUT" in *-*) PRE_ARGS+=(--prerelease) ;; esac
# 附件上传全部完成后才公开；失败只可能留下标签或未公开草稿，重跑不覆盖它们。
gh release create "$TAG_INPUT" \
  "release/astrbot_plugin_bond-${TAG_INPUT}.zip" \
  "release/astrbot_plugin_bond-${TAG_INPUT}.zip.sha256" \
  --repo "$GITHUB_REPOSITORY" --verify-tag --draft \
  --title "$TAG_INPUT" --notes-file release/notes.md "${PRE_ARGS[@]}"
gh release edit "$TAG_INPUT" --repo "$GITHUB_REPOSITORY" --draft=false
