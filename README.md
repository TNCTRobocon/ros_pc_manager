# ROS PC Manager

同一LAN内の複数Ubuntu PCをスマートフォンのブラウザから管理するWebアプリです。
親PCで管理サーバとROSbag記録を実行し、管理対象PCには小さなAgentを配置します。

## 主な機能

- IPアドレスを手動登録し、Agentのオンライン状態を表示
- PC登録・編集・削除をWeb画面から実行
- 登録情報をSQLiteに保存し、config/hosts.yamlへ自動出力（認証トークンは出力しない）
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

一度実行すれば、それ以降、シャットダウン後も、再実行は不要です。

~~~bash
cd /home/miquiteru/ros_pc_manager
./scripts/setup.sh
~~~

config/server.yamlを確認します。初期値ではROS 2 Jazzyをsourceし、rosbagをvar/rosbagsへ保存します。親PCで独自メッセージ型を記録する場合は、ros.overlaysに必要なWorkspaceを追加してください。

Manager Serverをsystemdに登録し、OS起動時の自動起動を有効にします。

一度実行すれば、それ以降、シャットダウン後も、再実行は不要です。

~~~bash
./scripts/setup_server_service.sh
~~~

このスクリプトは、存在しない場合だけconfig/server.yamlとconfig/domains.yamlをサンプルから作成し、ROS 2環境を検証してからサービス登録・自動起動ON・即時起動を行います。既存設定は上書きしません。通常ユーザーで実行してください（sudoはスクリプト内で要求されます）。

初回起動時に管理パスワードが生成され、var/admin_password.txtに保存されます。ファイル権限は0600です。パスワードを確認し、スマートフォンから次のURLを開いてログインしてください。

手動起動を使う場合は、従来どおり `./scripts/run_server.sh` を実行できます。ただし、systemdサービスと手動起動を同時に実行しないでください。

~~~text
http://<親PCのIPアドレス>:8080/
~~~

親PCのファイアウォールでは、LAN内からTCP 8080への接続を許可してください。インターネットへポート公開しないでください。

## 各管理対象PCのセットアップ

同じリポジトリを対象PCにも配置し、Python依存関係をセットアップします。

一度実行すれば、それ以降、シャットダウン後も、再実行は不要です。

~~~bash
./scripts/setup.sh
~~~

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

親PCのWeb画面で、PC名・IPアドレス・Agentポート（既定8765）・デフォルトDomain IDを入力してPCを登録します。登録時に一度だけ表示されるAgent Tokenを控えてください。

対象PCで、Agentをsystemdに登録してOS起動時の自動起動を有効にします。

一度実行すれば、それ以降、シャットダウン後も、再実行は不要です。

~~~bash
./scripts/setup_agent_service.sh
~~~

初回は `agent/config/agent.yaml` と `agent/config/launches.yaml` を作成します。Agent Tokenが未設定の場合は対話入力を求めます。TokenをManagerで発行する前に実行しても、Token未設定のままサービスを有効化することはありません。Tokenは表示せず、agent.yamlを0600で保存します。

launch設定ファイルが存在しない場合は `launches: []` の空設定を作成します。既存のagent.yaml / launches.yamlをテンプレートで上書きすることはありません（Tokenが未設定・仮値の場合はtoken項目だけ更新します）。登録済みlaunchがある場合は、ROS 2 Workspace・package・launchファイルを検証し、問題があればサービス登録前に停止します。launchが0件でもAgentは起動できます。launch設定はWeb画面のlaunch設定管理、またはYAML編集で追加してください。

手動起動を使う場合は、先に `./scripts/prepare_agent_config.sh` で設定を作成・検証し、その後 `./scripts/run_agent.sh` を実行します。`prepare_agent_config.sh` はsystemd登録や自動起動設定を行いません。systemdサービスと手動起動を同時に実行しないでください。

親PC自身も管理対象に含める場合は、親PCのWeb画面で親PC自身を登録し、親PC上でも `setup_agent_service.sh` を実行してください。Manager ServerとAgentは別サービスとして動作します。

## [管理パスワード]を表示

親PC上で実行してください。

~~~bash
cd ~/ros_pc_manager
cat var/admin_password.txt
~~~

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

シャットダウンが「sudo: a password is required」などで失敗する場合、Agentを実行しているLinuxユーザーに、上記のコマンドだけをパスワードなしで実行できる権限を付与します。**シャットダウン対象となる各PCで個別に設定してください。** 親PCの設定だけでは、別PCのシャットダウン権限は付与されません。

### 1. Agentの実行ユーザーを確認

Agentが起動しているPCで実行します。

```bash
ps -eo user,pid,args | grep '[r]os-pc-manager-agent'
```

出力例：

```text
miquiteru  1234  .../ros-pc-manager-agent --config ...
```

この例ではAgent実行ユーザーは `miquiteru` です。systemdで起動している場合も、実際のAgentプロセスのユーザーを確認してください。Agentがrootで実行されている場合、このsudoers設定は不要です（rootでのAgent実行は通常推奨しません）。

### 2. sudoersファイルを編集

対象PCで次を実行します。

```bash
sudo visudo -f /etc/sudoers.d/ros-pc-manager
```

次の1行を追加します。左側のユーザー名は、手順1で確認したAgent実行ユーザーに置き換えてください。

```text
miquiteru ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
```

例えばAgent実行ユーザーが `robo-a` なら、次のようにします。

```text
robo-a ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
```

保存して終了します。sudoersファイルを直接エディタで編集せず、必ず `visudo` を使用してください。

### 3. ファイル権限と構文を確認

```bash
sudo chmod 0440 /etc/sudoers.d/ros-pc-manager
sudo visudo -cf /etc/sudoers.d/ros-pc-manager
```

構文が正しければ、次のように表示されます。

```text
/etc/sudoers.d/ros-pc-manager: parsed OK
```

### 4. パスワードなしで実行できるか確認

Agentを実行しているユーザーで、次を実行します。

```bash
sudo -n -l /usr/bin/systemctl poweroff
```

許可されたコマンドとして `/usr/bin/systemctl poweroff` が表示されれば確認完了です。この確認コマンドは権限を照会するだけで、シャットダウンは実行しません。**動作確認のために `systemctl poweroff` 自体を手動実行しないでください。**

### 5. スマートフォンから再試行

sudoers設定はコマンド実行時に参照されるため、通常AgentやManager Serverの再起動は不要です。スマートフォンから対象PCのシャットダウンを再試行してください。

それでも失敗する場合は、Agent実行ユーザー、sudoersファイルの内容・権限、`visudo -cf` の結果を確認してください。特に、Agentを起動したユーザーとsudoersに記載したユーザーが一致している必要があります。

### sudoers設定例

このリポジトリには `deploy/sudoers/ros-pc-manager.example` も用意しています。ファイル内の `__AGENT_USER__` をAgent実行ユーザーに置き換えてから、管理者が `/etc/sudoers.d/ros-pc-manager` に配置し、必ず `chmod 0440` と `visudo -cf` で検証してください。

許可対象は `/usr/bin/systemctl poweroff` のみに限定します。Agentに任意のrootコマンド実行権限は与えません。

## systemdによる自動起動

通常は次の2つのセットアップスクリプトを使用します。

- 親PC：`./scripts/setup_server_service.sh`
- 各管理対象PC：`./scripts/setup_agent_service.sh`

どちらも、先に `./scripts/setup.sh` を実行してPython環境を構築してください。セットアップスクリプトは通常ユーザーで実行します。必要なsystemd登録時のみ内部でsudoを使用します。

スクリプトはプロジェクトの配置先・実行ユーザー・グループを使ってsystemdユニットを生成します。生成済みユニットには管理マーカーを付けます。同名の既存ユニットに管理マーカーがない場合は、ユーザー独自設定を上書きしないよう処理を中止します。

設定ファイルが既にある場合は上書きしません。Agent Tokenが未設定・仮値の場合、またはROS 2環境・launch設定の検証に失敗した場合は、自動起動を有効化しません。Agent TokenはManagerのPC登録で発行された値を使用してください。Manager側に登録されたTokenとAgent側のTokenは一致している必要があります。

状態・ログの確認：

~~~bash
systemctl status pc-manager-server
systemctl status pc-manager-agent
journalctl -u pc-manager-server -f
journalctl -u pc-manager-agent -f
~~~

自動起動の解除：

~~~bash
sudo systemctl disable --now pc-manager-server
sudo systemctl disable --now pc-manager-agent
~~~

Agentサービスは `KillMode=process` を使用します。Agentサービスの再起動時に管理launchプロセスは終了せず、Agent再起動後にPIDとプロセス開始時刻から管理状態を復元します。

`agent.yaml` の変更を反映する場合は `sudo systemctl restart pc-manager-agent` を実行してください。`server.yaml` の変更を反映する場合は、先にROSbag記録を停止してから `sudo systemctl restart pc-manager-server` を実行してください。Manager Serverの停止・再起動中はROSbag記録を継続できず、記録ファイルの正常確定も保証されません。

## セキュリティとネットワーク

- 管理UIは初回生成の管理パスワードでログインします。セッションはサーバメモリ内で12時間有効です。
- Agent APIはPCごとのBearer tokenを要求します。
- Agent tokenは親PCのSQLite DBに保存されます。DBは可能な範囲で0600に設定します。
- config/hosts.yamlはSQLiteから自動生成する参照用ファイルです。編集元として使用せず、PC登録・編集・削除後とサーバ起動時に再生成します。Agent tokenは含めません。
- 通信は同一LAN内のHTTPです。信頼できる隔離LANで使用し、ルーターで外部公開しないでください。共有・不特定のLANではTLS/VPNを追加してください。
- ROS 2 DDSのマルチキャスト通信がLAN内で届く必要があります。Wi-Fiのクライアント隔離、VLAN、ファイアウォールでDiscoveryが遮断されると親PCからtopicを発見できません。
- シャットダウン確認は行いますが、launchやrosbag記録中でも実行可能です。ROSbag記録中に対象PCを停止すると、そのPCからの記録データは止まります。親PC自体をシャットダウンするとrosbagも終了します。

## 開発・テスト

~~~bash
.venv/bin/python -m pip install -r requirements-dev.txt
bash -n scripts/setup_agent_service.sh scripts/setup_server_service.sh
.venv/bin/python -m compileall -q scripts backend agent tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests -q
node --check frontend/app.js
~~~

実機操作前に、Agentが稼働しているか、親PCからAgentのIP・ポートへ到達できるか、Domain IDのDDS Discoveryが成立するかを確認してください。
