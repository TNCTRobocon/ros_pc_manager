# ROS PC Manager

同一LAN内の複数Ubuntu PCをスマートフォンのブラウザから管理するWebアプリです。
親PCで管理サーバとROSbag記録を実行し、管理対象PCには小さなAgentを配置します。

## 主な機能

- IPアドレスを手動登録し、Agentのオンライン状態を表示
- YAMLに登録したlaunchの起動・停止
- launchの実行状態マーク（●実行中 / ○停止中 / ◐起動中 / ◌停止中）
- 管理Agentが起動したlaunchのみ一覧表示・一括停止
- 端末から直接起動したlaunchは一覧・一括停止に含めない
- 実行中launchをすべて選択して一括停止
- 親PCでDomain ID単位にrosbag2を記録
- Domain IDごとにtopic discoveryし、複数topicを選択して記録
- CPU・メモリ・ディスク・ROS 2ノード一覧などの詳細表示
- 確認後のPCシャットダウン要求

## アーキテクチャ

- backend/pc_manager_server: FastAPIサーバ、PC登録DB、Agent通信、親PCのrosbag2管理
- agent/pc_manager_agent: PC側Agent。管理対象launchの起動・停止とPC状態取得
- frontend: スマホ向けレスポンシブWeb UI
- config: 親PCのROS環境・Domain IDなどの設定例
- deploy: systemd / sudoersのテンプレート
- tests: 自動テスト

Agentが起動したlaunchはPID・プロセスグループ・起動時刻を記録します。Agent再起動後もPIDと/procの開始時刻を照合して状態を復元します。外部起動プロセスを名前検索して停止することはありません。systemdテンプレートでは`KillMode=process`を指定し、Agentサービス再起動時に管理launchまで終了しないようにします。このためAgentサービスを停止しただけではlaunchは停止せず、Agent再起動後に再び管理できます。PCのシャットダウン時はOSの終了処理に従って各プロセスも終了します。

## 親PCでのセットアップ

Ubuntu 24.04 / Python 3.12 / ROS 2 Jazzyを想定しています。

~~~bash
cd /home/miquiteru/ros_pc_manager
./scripts/setup.sh
~~~

config/server.yamlを確認します。初期値ではROS 2 Jazzyをsourceし、rosbagをvar/rosbagsへ保存します。親PCで独自メッセージ型を記録する場合は、ros.overlaysに必要なWorkspaceを追加してください。

管理サーバを起動します。

~~~bash
./scripts/run_server.sh
~~~

初回起動時に管理パスワードが生成され、var/admin_password.txtに保存されます。ファイル権限は0600です。パスワードを確認し、スマートフォンから次のURLを開いてログインしてください。

~~~text
http://<親PCのIPアドレス>:8080/
~~~

親PCのファイアウォールでは、LAN内からTCP 8080への接続を許可してください。インターネットへポート公開しないでください。

## 各管理対象PCのセットアップ

同じリポジトリを対象PCにも配置し、Python依存関係をセットアップします。

~~~bash
./scripts/setup.sh
./scripts/prepare_agent_config.sh
~~~

agent/config/agent.yamlとagent/config/launches.yamlを編集します。Agentのtokenには、親PCの画面でPC登録した際に一度だけ表示されるAgentトークンを設定してください。

launches.yamlの例：

~~~yaml
launches:
  - id: enemyrobot_bringup
    display_name: "敵ロボット全体起動"
    workspace: /home/user/enemyrobot26_ws
    package: enemyrobot_bringup
    launch_file: bringup.launch.py
    ros_domain_id: 32
    arguments: {}
~~~

- id: PC内で一意の管理ID。英数字・_・.・-のみ
- display_name: Web画面に表示する任意の名前
- workspace: ビルド済みWorkspaceの絶対パス
- package / launch_file: ros2 launchへ渡す値
- ros_domain_id: そのlaunchを起動するDomain ID
- arguments: launch引数のキー・値

起動前に、指定Workspaceのinstall/setup.bashが存在することを確認してください。launch引数はYAMLで固定し、画面から任意コマンドを送る機能は設けていません。

Agentを起動します。

~~~bash
./scripts/run_agent.sh
~~~

親PCのWeb画面で、PC名・IPアドレス・Agentポート（既定8765）・デフォルトDomain IDを入力して登録します。PC登録時に表示されるトークンを対象PCのAgent設定へコピーします。親PC自身を操作対象に含める場合も、親PC上でAgentを起動し、親PCのLAN側IPアドレスを登録してください。

## ROSbagの動作

- 記録プロセスは親PC上で1つだけ動作します。
- 同時記録は初期仕様では1 Domain IDです。
- Domain ID選択後、親PC上でros2 topic list --no-daemonを実行してtopicを取得します。
- 選択したtopicのみros2 bag record --topics ...へ渡します。
- 保存先は<rosbag_root>/domain_<ID>/YYYY-MM-DD/recording_YYYYMMDD_HHMMSSです。
- 記録停止はSIGINTで正常終了を要求し、rosbag2のメタデータ確定を待ちます。
- ディスク空き容量が設定値を下回る場合、新規記録を拒否します。古いbagの自動削除はしません。

ROSbagでカスタムメッセージを扱う場合、親PCにもそのメッセージ型をビルドしたWorkspaceのoverlayが必要です。同じDomain IDに複数PCがいる場合、同名topicはDomain全体のtopicとして記録され、発行元PCごとの分離はしません。

## シャットダウン権限

Agentは、実行ユーザーがrootでなければ次のコマンドを非対話で実行します。

~~~bash
sudo -n /usr/bin/systemctl poweroff
~~~

許可する場合は、deploy/sudoers/ros-pc-manager.exampleの__AGENT_USER__をAgent実行ユーザーに置き換え、管理者がvisudoで検証して/etc/sudoers.d/ros-pc-managerへ設置してください。許可対象はsystemctl poweroffのみに限定します。Agentに任意のrootコマンド実行権限は与えません。

## systemd

deploy/systemd/*.service.exampleはテンプレートです。__RUN_USER__、__RUN_GROUP__、__PROJECT_ROOT__を実環境に置き換えてからsystemdへ登録してください。Agentの状態ファイルとログディレクトリはAgent実行ユーザーが書き込めるように準備してください。

## セキュリティとネットワーク

- 管理UIは初回生成の管理パスワードでログインします。セッションはサーバメモリ内で12時間有効です。
- Agent APIはPCごとのBearer tokenを要求します。
- Agent tokenは親PCのSQLite DBに保存されます。DBは可能な範囲で0600に設定します。
- 通信は同一LAN内のHTTPです。信頼できる隔離LANで使用し、ルーターで外部公開しないでください。共有・不特定のLANではTLS/VPNを追加してください。
- ROS 2 DDSのマルチキャスト通信がLAN内で届く必要があります。Wi-Fiのクライアント隔離、VLAN、ファイアウォールでDiscoveryが遮断されると親PCからtopicを発見できません。
- シャットダウン確認は行いますが、launchやrosbag記録中でも実行可能です。ROSbag記録中に対象PCを停止すると、そのPCからの記録データは止まります。親PC自体をシャットダウンするとrosbagも終了します。

## 開発・テスト

~~~bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m compileall -q backend agent tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests -q
node --check frontend/app.js
~~~

実機操作前に、Agentが稼働しているか、親PCからAgentのIP・ポートへ到達できるか、Domain IDのDDS Discoveryが成立するかを確認してください。
