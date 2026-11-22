# ROS PC Manager

スマートフォンのブラウザから、同一LAN内にある複数のROS 2 PCを直接管理するWebアプリです。

各PCでAgentを起動し、スマートフォンのブラウザにAgent一覧とTokenを保存します。Agentはそれぞれ自分のPCだけを管理します。

(home/(user)/ros_pc_managerに)<https://github.com/TNCTRobocon/ros_pc_manager.git>からクローンできます。

## PCを登録

'''text
cd ~/ros_pc_manager
./scripts/show_agent_pairing.sh --address (hostname -I の結果)
'''
表示されたQRコードをスマホのカメラアプリから読み取り、「PCを登録」を選択すれば登録完了です。

## アーキテクチャ

```text
スマートフォン / PWA
  ├─ Agent Aへ直接接続
  ├─ Agent Bへ直接接続
  ├─ Agent Cへ直接接続
  └─ Agent一覧・Token・表示設定をIndexedDBに保存

Agent A ── mDNS ── Agent B
   │                  │
   └────── Agent C ───┘
```

- `agent/pc_manager_agent`: 各PCで動作するAgent API、ROS 2 Launch管理、ROSbag管理、mDNS発見。
- `frontend`: スマートフォン用Web GUIとPWAの静的ファイル。
- `scripts`: Agent設定作成、Token確認、systemd自動起動。
- `deploy/systemd`: Agentのsystemdユニット例。
- `tests`: 自動テスト。

スマートフォンは選択したAgentに直接API要求を送り、別のPCを経由しません。Agent同士はmDNSで存在情報だけを交換します。Agent間でTokenやROS 2操作権限を共有することはありません。

## 主な機能

- 複数PCのONLINE / OFFLINE / SHUTTING_DOWN / ERROR表示
- AgentのmDNS自動発見とIPアドレス手動登録
- AgentごとのBearer Token認証
- PC情報・Agent Tokenのスマートフォン側保存
- PC基本情報・CPU・メモリ・ディスクなどのシステム情報表示
- Agentが管理するLaunchの起動・停止・状態表示
- Launch設定の追加・編集・削除・検証
- 選択したAgent上でのROSbag記録・停止
- Domain ID単位のTopic Discoveryと複数Topic選択
- PCのシャットダウン
- PWAのホーム画面追加・静的画面のオフラインキャッシュ

LaunchManagerはAgentが起動したプロセスだけを管理します。PIDと`/proc`のプロセス開始時刻を照合し、無関係なプロセスを名前検索で停止することはありません。Agentサービスを再起動しても、管理対象Launchの状態を復元します。

## ブラウザと接続方式

### 推奨：Chrome / Edge + GitHub Pages版PWA

PWAは静的ファイルだけで構成され、GitHub PagesからHTTPS配信します。PWA配信元はPC一覧・ROS 2データ・Tokenを保存しません。

HTTPSのWebページからLAN内HTTP Agentへ接続するには、対応ブラウザのLocal Network Access許可が必要です。初回登録時にAgentへ接続するとブラウザから許可を求められる場合があります。許可を拒否した場合は、ブラウザ設定でこのサイトのローカルネットワークアクセスを確認してください。

GitHub PagesのPWA URL：

```text
https://tnctrobocon.github.io/ros_pc_manager/
```

GitHubリポジトリで一度だけ、Settings → Pages → Build and deployment → Sourceを「GitHub Actions」に設定してください。以後、`main`ブランチにPWA関連ファイルをpushすると、Actionsが静的サイトをデプロイします。

### 互換経路：Agentが配信するWeb GUI

Safariなど、HTTPSページからLAN内HTTP Agentへの接続が制限されるブラウザでは、対象LAN内の任意のAgentのWeb GUIを直接開きます。この互換経路はAgentごとにブラウザの保存領域（Origin）が異なります。複数PCの一覧を共通化するには、GitHub Pages版PWAを使用してください。

```text
http://<AgentのIPアドレス>:8765/
```

各Agentが同じWeb GUIを配信するため、特定のPCを常時起動しておく必要はありません。このHTTP画面はPWAインストールやService Workerによるオフライン起動の対象ではありません。

## 1. 各PCへのインストール

Ubuntu 24.04 / Python 3.12 / ROS 2 Jazzyを想定しています。管理したい各PCで同じリポジトリを配置し、次を実行してください。

```bash
cd /home/<user>/ros_pc_manager
./scripts/setup.sh
```

Python依存関係をインストールします。mDNS用の`zeroconf`もこのときに導入します。

## 2. Agent設定と自動起動

systemd登録・OS起動時の自動起動・Agent起動をまとめて行います。

```bash
./scripts/setup_agent_service.sh
```

初回実行では、次の設定を作成・確認します。

- `agent/config/agent.yaml`: PC名、Agent ID、Token、ROS 2環境、APIポートなど。
- `agent/config/launches.yaml`: Launch管理設定。存在しなければ空の`launches: []`を作成。

Agent IDはPCごとに生成され、設定ファイルに保存されます。Agent Tokenが未設定またはサンプル値の場合は、暗号学的乱数で自動生成します。既に有効なTokenが設定されている場合は維持します。Tokenは`agent.yaml`に保存され、ファイル権限は0600です。

このスクリプトはsystemdへの登録、自動起動ON、Agentの起動まで実行します。通常ユーザーで実行してください（必要な操作ではスクリプト内からsudoを要求します）。

### 手動起動で試す場合

systemdへ登録せず前面起動する場合：

```bash
./scripts/prepare_agent_config.sh
./scripts/run_agent.sh
```

`run_agent.sh`はAgent設定がなければ作成・検証してから起動します。systemd起動と手動起動を同時に行わないでください。

## 3. QRコード / Agent Tokenの準備

Agentのsystemdサービスが起動している状態で、対象PC上で次を実行します。

```bash
cd /home/<user>/ros_pc_manager
./scripts/show_agent_pairing.sh
```

LAN IPv4アドレスが複数ある場合は一覧から選ぶか、次のように指定します。

```bash
./scripts/show_agent_pairing.sh --address 192.168.1.20
```

ターミナルに5分間だけ有効なQRコードが表示されます。スマートフォンで読み取るとGitHub Pages版PWAが開き、Agentの接続先が登録フォームに入力されます。QRに含まれるのは一度だけ使用できる一時コードであり、長期Agent Tokenは含まれません。登録時にAgentが一時コードを検証し、成功した場合だけスマートフォンへAgent Tokenを渡します。

QRコードを使わず手動登録する場合は、次のコマンドでTokenを確認できます。

```bash
./scripts/show_agent_token.sh
```

TokenはAgentへの接続権限を持つ秘密情報です。必要な端末以外に貼り付けたり、共有したりしないでください。既存Agentの有効なTokenは自動的に変更しません。

QR登録を使う前に、GitHubリポジトリの Settings → Pages → Build and deployment → Source を「GitHub Actions」に設定し、PWAを一度デプロイしてください。QRから開くPWAのURLは `https://tnctrobocon.github.io/ros_pc_manager/` です。QR発行コマンドは対象Agentが起動しているときだけ使用できます。Agent再起動や5分経過後はコードが失効するため、再発行してください。

## 4. スマートフォンからPCを登録

1. Chrome / EdgeでGitHub Pages版PWAを開きます。
2. 初回AgentはQRコードを読み取るか、表示名・LAN IPv4アドレス・Agentポート（既定8765）・Domain ID・Agent Tokenを手入力して登録します。
3. 登録要求はスマートフォンから対象Agentへ直接送られ、Agent IDとToken認証が一致したAgentだけをIndexedDBに保存します。
4. 1台以上登録した後は「Agentを検索」を押すと、登録済みAgentがmDNSで発見した未登録Agentを一覧表示します。
5. 発見されたPCを登録フォームへ移し、QRコードまたはそのPCのAgent Tokenで登録します。

ブラウザはWebページからmDNSマルチキャストを直接受信できないため、**初回の1台だけはQRコードまたはIPアドレスの手動指定が必要です**。登録後は、起動中のどのAgentからでもほかのAgentを発見できます。特定Agentを「親PC」や常時起動PCとして固定しません。

## 5. Launch設定

各PCのAgentが、自分のPCにあるWorkspace・Package・Launchファイルを検証します。GUIまたは`agent/config/launches.yaml`で設定できます。

例：

```yaml
launches:
  - id: robot_bringup
    display_name: "Robot bringup"
    workspace: /home/user/ros2_ws
    package: robot_bringup
    launch_file: bringup.launch.py
    ros_domain_id: 32
    arguments: {}
```

- `id`: PC内で一意の管理ID。英数字・`_`・`.`・`-`を使用。
- `display_name`: GUIに表示する名前。
- `workspace`: ビルド済みWorkspaceの絶対パス。
- `package` / `launch_file`: `ros2 launch`へ渡すPackageとLaunchファイル。
- `ros_domain_id`: Launchを実行するROS_DOMAIN_ID。
- `arguments`: Launch引数の固定値。

指定Workspaceには`install/setup.bash`が必要です。Launch引数は設定時に固定され、GUIから任意のシェルコマンドを実行する機能はありません。

## 6. ROSbag

ROSbagはGUIで選択したAgentのPC上で動作します。

- Topic Discoveryは選択したAgent上で、指定Domain IDを使用して実行します。
- 選択したTopicだけをrosbag2に渡します。
- 保存先の既定値は`~/.local/state/ros-pc-manager/rosbags/domain_<ID>/YYYY-MM-DD/`です。
- 設定された空き容量下限を下回る場合、新しい記録を拒否します。
- 古いbagの自動削除は行いません。
- カスタムメッセージ型を記録する場合、記録するAgentにもその型を含むROS 2 overlayが必要です。

`agent.yaml`の`ros.overlays`にWorkspaceを追加してください。

```yaml
ros:
  overlays:
    - /home/user/custom_interfaces_ws
```

同じDomain IDに複数PCが存在する場合、同名TopicはDomain全体のTopicとして記録されます。発行元PCごとの分離は行いません。

## 7. Agentの状態・ログ

```bash
systemctl status pc-manager-agent
journalctl -u pc-manager-agent -f
```

設定変更を反映する場合：

```bash
sudo systemctl restart pc-manager-agent
```

自動起動を解除してAgentを停止する場合：

```bash
sudo systemctl disable --now pc-manager-agent
```

Agent APIはTCP 8765（既定）で待ち受けます。ファイアウォールでは信頼するLAN内だけに許可し、インターネットへポート公開しないでください。

## 8. mDNS / ネットワーク条件

- Agentの自動発見にはmDNS / DNS-SDを使用します。
- UDP 5353のマルチキャスト通信が同一LAN内で届く必要があります。
- Wi-Fiのクライアント隔離、VLAN、ルーター、ファイアウォールがマルチキャストを遮断すると自動発見できません。
- mDNSで発見できない場合は、IPアドレスを手動登録できます。
- 現在の実装は同一LANを対象にします。異なるVLAN・拠点間の自動発見やVPN越しの発見は対象外です。

## 9. シャットダウン権限

Agentは通常ユーザーで動かし、必要な場合に限り`sudo -n /usr/bin/systemctl poweroff`を呼び出します。root権限全体をAgentに渡さないでください。

対象PCで次を実行します。

```bash
sudo visudo -f /etc/sudoers.d/ros-pc-manager
```

次の1行を追加し、ユーザー名をAgent実行ユーザーに置き換えます。

```text
<AGENT_USER> ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
```

権限と構文を確認します。

```bash
sudo chmod 0440 /etc/sudoers.d/ros-pc-manager
sudo visudo -cf /etc/sudoers.d/ros-pc-manager
sudo -n -l /usr/bin/systemctl poweroff
```

許可するのはシャットダウンコマンドだけです。設定はシャットダウン対象となる各PCで個別に行ってください。

## 10. 保存データとセキュリティ

- Agent一覧、Agent Token、表示情報はスマートフォンのIndexedDBに保存します。
- Agent側はAgent Tokenを`agent/config/agent.yaml`に保存します。Tokenはファイル権限0600で保護してください。
- Agent TokenはmDNS広告に含めません。
- Agentの操作APIはBearer Token認証を必須とし、CORSはPWAの配信元とLAN内HTTP AgentのOriginに限定します。
- Agentの`/health`だけは、ネットワーク監視のため認証なしで応答します。システム情報・Launch・ROSbag・シャットダウンAPIにはTokenが必要です。
- PWAのService WorkerはHTML・CSS・JavaScriptなどの静的ファイルだけをキャッシュします。Agent API・Token・ROS 2状態はキャッシュしません。
- ブラウザのサイトデータを消去すると、保存したAgent一覧とTokenも消えます。その場合はTokenを再入力して登録してください。
- 通信はLAN内HTTPです。信頼できる隔離LANで使用し、外部公開しないでください。

## 旧Manager Serverからの移行

旧構成のManager Server・SQLite・hosts.yamlは、新しいGUIから利用しません。旧データは自動インポートしません。各Agentをスマートフォンに再登録してください。Agent側のTokenは`show_agent_token.sh`で確認でき、既存の有効なTokenは保持されます。

旧Manager Serverサービスを使っていたPCでは、Agentのsystemdサービスを有効化する`./scripts/setup_agent_service.sh`が、Agentの起動を確認した後に旧Serverを停止・自動起動解除します。旧Serverを手動で停止する場合は次を実行します。

```bash
sudo systemctl disable --now pc-manager-server.service
```

旧Server関連コードと設定は、移行期間中のロールバック用にリポジトリ内へ残しています。通常運用では`pc-manager-server`を起動しません。

## 開発・テスト

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
bash -n scripts/setup.sh scripts/run_agent.sh scripts/prepare_agent_config.sh scripts/setup_agent_service.sh scripts/show_agent_token.sh scripts/show_agent_pairing.sh
.venv/bin/python -m compileall -q scripts agent tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests -q
node --check frontend/app.js
```
