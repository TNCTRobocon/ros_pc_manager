#!/usr/bin/env bash
set -Eeuo pipefail

SUDOERS_FILE="/etc/sudoers.d/ros-pc-manager"
MARKER="# Managed by ROS PC Manager setup scripts. Do not edit manually."

fail() {
  echo "エラー: $*" >&2
  exit 1
}

if [[ "$EUID" -eq 0 ]]; then
  fail "sudoを付けず、通常ユーザーで実行してください。"
fi
command -v sudo >/dev/null 2>&1 || fail "sudoが見つかりません。"
command -v visudo >/dev/null 2>&1 || fail "visudoが見つかりません。"

sudo -v

if ! sudo test -e "$SUDOERS_FILE"; then
  echo "シャットダウン権限の設定ファイルはありません。"
  exit 0
fi
if sudo test -L "$SUDOERS_FILE"; then
  fail "$SUDOERS_FILE はシンボリックリンクです。削除しません。"
fi
if ! sudo grep -Fqx "$MARKER" "$SUDOERS_FILE"; then
  fail "$SUDOERS_FILE はこのスクリプトが作成したファイルではありません。安全のため削除しません。"
fi

sudo rm -- "$SUDOERS_FILE"
sudo visudo -c >/dev/null || fail "sudoers全体の構文検証に失敗しました。"
echo "ROS PC Managerが追加したシャットダウン権限を解除しました。"
