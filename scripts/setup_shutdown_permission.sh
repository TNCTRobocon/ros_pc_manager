#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUDOERS_FILE="/etc/sudoers.d/ros-pc-manager"
MARKER="# Managed by ROS PC Manager setup scripts. Do not edit manually."

fail() {
  echo "エラー: $*" >&2
  exit 1
}

if [[ "$EUID" -eq 0 ]]; then
  fail "sudoを付けず、通常ユーザーで実行してください。"
fi
[[ -x "$ROOT/.venv/bin/python" ]] || fail "./scripts/setup.sh を先に実行してください。"
command -v sudo >/dev/null 2>&1 || fail "sudoが見つかりません。"
command -v visudo >/dev/null 2>&1 || fail "visudoが見つかりません。"

AGENT_USER="$(id -un)"
RULE="${AGENT_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
tmp_file="$tmp_dir/ros-pc-manager"
"$ROOT/.venv/bin/python" "$ROOT/scripts/service_setup.py" \
  render-shutdown-sudoers --user "$AGENT_USER" --output "$tmp_file"
chmod 0440 "$tmp_file"

# Ask for the administrator password interactively when sudo credentials
# are not already cached. No password is stored by this script.
sudo -v
# visudo checks ownership as well as syntax, so validate a root-owned staging file.
sudo chown root:root "$tmp_file"
sudo visudo -cf "$tmp_file" >/dev/null || fail "生成したsudoers設定の構文検証に失敗しました。"

is_exact_rule_file() {
  sudo awk -v expected="$RULE" '
    {
      line = $0
      sub(/^[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
      if (line == "" || line ~ /^#/) {
        next
      }
      if (line == expected) {
        count++
      } else {
        unexpected = 1
      }
    }
    END {
      exit !(count == 1 && !unexpected)
    }
  ' "$SUDOERS_FILE"
}

legacy_manual_file=0
if sudo test -L "$SUDOERS_FILE"; then
  fail "$SUDOERS_FILE はシンボリックリンクです。安全のため変更しません。"
elif sudo test -e "$SUDOERS_FILE"; then
  if sudo grep -Fqx "$MARKER" "$SUDOERS_FILE"; then
    if ! is_exact_rule_file; then
      fail "$SUDOERS_FILE に想定外の設定があります。既存内容を保護するため自動変更しません。"
    fi
  elif is_exact_rule_file; then
    # Adopt the exact legacy one-rule file created by the previous README
    # instructions without replacing its contents.
    sudo visudo -cf "$SUDOERS_FILE" >/dev/null || fail "既存sudoers設定の構文が不正です。"
    sudo chown root:root "$SUDOERS_FILE"
    sudo chmod 0440 "$SUDOERS_FILE"
    legacy_manual_file=1
    echo "既存のシャットダウン許可設定をそのまま利用します。"
  else
    fail "$SUDOERS_FILE はこのスクリプトが管理していない既存ファイルです。上書きしません。内容を確認してください。"
  fi
fi

if [[ "$legacy_manual_file" -eq 0 ]]; then
  if sudo test -f "$SUDOERS_FILE" && sudo cmp -s "$tmp_file" "$SUDOERS_FILE"; then
    echo "シャットダウン権限に変更はありません。"
  else
    sudo install -o root -g root -m 0440 "$tmp_file" "$SUDOERS_FILE"
    echo "シャットダウン権限を設定しました。"
  fi
fi

sudo visudo -c >/dev/null || fail "sudoers全体の構文検証に失敗しました。"
# Query the complete privilege list. A command-specific 'sudo -l COMMAND'
# query can format/filter entries differently across sudo versions.
permission_list="$(sudo -n -l 2>&1)" || {
  echo "$permission_list" >&2
  fail "sudo権限一覧を取得できませんでした。"
}
if ! grep -Eq '^[[:space:]]*\(root\)[[:space:]]+NOPASSWD:[[:space:]]+/usr/bin/systemctl[[:space:]]+poweroff[[:space:]]*$' <<<"$permission_list"; then
  echo "$permission_list" >&2
  fail "パスワードなしのシャットダウン権限を確認できませんでした。"
fi

echo "確認完了: $AGENT_USER は /usr/bin/systemctl poweroff だけをパスワードなしで実行できます。"
