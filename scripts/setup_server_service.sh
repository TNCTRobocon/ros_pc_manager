#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="pc-manager-server.service"
UNIT_PATH="/etc/systemd/system/$SERVICE"
MARKER="# Managed by ROS PC Manager setup scripts. Do not edit manually."

fail() {
  echo "エラー: $*" >&2
  exit 1
}

if [[ "$EUID" -eq 0 ]]; then
  fail "sudoを付けず、通常ユーザーで実行してください。必要な管理者権限はスクリプト内でsudoを使用します。"
fi
[[ -x "$ROOT/.venv/bin/python" ]] || fail "./scripts/setup.sh を先に実行してください（Python環境がありません）。"
[[ -x "$ROOT/.venv/bin/ros-pc-manager-server" ]] || fail "Serverコマンドがありません。./scripts/setup.sh を再実行してください。"
command -v systemctl >/dev/null 2>&1 || fail "systemdが利用できません。"
command -v systemd-analyze >/dev/null 2>&1 || fail "systemd-analyzeが利用できません。"
command -v sudo >/dev/null 2>&1 || fail "sudoが利用できません。"

fragment_path="$(systemctl show -p FragmentPath --value "$SERVICE" 2>/dev/null || true)"
if [[ -n "$fragment_path" && "$fragment_path" != "$UNIT_PATH" && ! -e "$UNIT_PATH" ]]; then
  fail "$SERVICE は別の場所で既に定義されています: $fragment_path"
fi
echo "=== Server設定の作成・検証 ==="
"$ROOT/.venv/bin/python" "$ROOT/scripts/service_setup.py" prepare-server

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
tmp_unit="$tmp_dir/$SERVICE"
"$ROOT/.venv/bin/python" "$ROOT/scripts/service_setup.py" render-server --output "$tmp_unit"

systemd-analyze verify "$tmp_unit" || fail "生成したsystemd設定の検証に失敗しました。"
sudo -v
if sudo test -L "$UNIT_PATH"; then
  fail "$UNIT_PATH はシンボリックリンクです。自動上書きしません。"
fi
if sudo test -e "$UNIT_PATH"; then
  if ! sudo grep -Fqx "$MARKER" "$UNIT_PATH"; then
    fail "$UNIT_PATH はこのセットアップスクリプトが管理していない既存ファイルです。内容を確認してから手動で統合してください。"
  fi
fi
changed=1
if sudo test -f "$UNIT_PATH" && sudo cmp -s "$tmp_unit" "$UNIT_PATH"; then
  changed=0
fi
if [[ "$changed" -eq 1 ]]; then
  sudo install -o root -g root -m 0644 "$tmp_unit" "$UNIT_PATH"
  echo "systemdユニットを更新しました。"
else
  echo "systemdユニットに変更はありません。"
fi

sudo systemctl daemon-reload
if systemctl is-active --quiet "$SERVICE"; then
  if [[ "$changed" -eq 1 ]]; then
    if ! sudo systemctl restart "$SERVICE"; then
      sudo journalctl -u "$SERVICE" -n 60 --no-pager >&2 || true
      fail "Manager Serverの再起動に失敗しました。上記ログを確認してください。"
    fi
  else
    echo "Manager Serverは既に起動しています。再起動は行いません。"
  fi
else
  if ! sudo systemctl start "$SERVICE"; then
    sudo journalctl -u "$SERVICE" -n 60 --no-pager >&2 || true
    fail "Manager Serverの起動に失敗しました。上記ログを確認してください。"
  fi
fi

if ! systemctl is-active --quiet "$SERVICE"; then
  sudo journalctl -u "$SERVICE" -n 60 --no-pager >&2 || true
  fail "Manager Serverの起動に失敗しました。上記ログを確認してください。"
fi

# Enable boot autostart only after the service is confirmed active.
sudo systemctl enable "$SERVICE"

echo
echo "Manager Serverの設定が完了しました。"
echo "自動起動: $(systemctl is-enabled "$SERVICE")"
echo "状態: $(systemctl is-active "$SERVICE")"
echo "ログ確認: journalctl -u $SERVICE -f"
echo "管理パスワード: $ROOT/var/admin_password.txt"
