const $ = (id) => document.getElementById(id);

let hosts = [];
let selectedHost = null;
let editingHostId = null;
let launches = [];
let availableTopics = [];
let selectedTopics = new Set();
let currentBag = null;
let currentDomains = [];
let topicLoadError = "";

async function api(path, options = {}) {
  const sessionToken = sessionStorage.getItem("managerSession") || "";
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(sessionToken ? { "X-Manager-Session": sessionToken } : {}),
      ...(options.headers || {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== "/api/login") {
    sessionStorage.removeItem("managerSession");
    showLoginPage();
  }
  if (!response.ok) {
    const message = body.detail || body.message || response.statusText || "API request failed";
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return body;
}

function showToast(message, duration = 3200) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), duration);
}

function setPage(name) {
  for (const page of ["hostsPage", "controlPage", "detailsPage", "rosbagPage"]) {
    $(page).classList.toggle("hidden", page !== name);
  }
  $("navHosts").classList.toggle("active", name === "hostsPage" || name === "controlPage" || name === "detailsPage");
  $("navRosbag").classList.toggle("active", name === "rosbagPage");
}

function setStatus(element, status) {
  element.textContent = status || "UNKNOWN";
  element.className = "status-pill " + (
    status === "ONLINE" ? "online" :
    status === "OFFLINE" ? "offline" : "unknown"
  );
}

function renderHosts() {
  const root = $("hostCards");
  root.replaceChildren();
  if (!hosts.length) {
    const empty = document.createElement("div");
    empty.className = "empty-card";
    empty.textContent = "登録済みPCはありません。下のフォームから登録してください。";
    root.append(empty);
    return;
  }

  for (const host of hosts) {
    const row = document.createElement("div");
    row.className = "host-card-row";

    const openButton = document.createElement("button");
    openButton.className = "host-card";
    openButton.type = "button";

    const left = document.createElement("div");
    const name = document.createElement("div");
    name.className = "host-card-name";
    name.textContent = host.name;
    const meta = document.createElement("div");
    meta.className = "host-card-meta";
    meta.textContent = host.address + " · Domain ID " + host.domain_id;
    left.append(name, meta);

    const status = document.createElement("span");
    status.className = "status-pill " + (host.status === "ONLINE" ? "online" : "offline");
    status.textContent = host.status || "OFFLINE";
    openButton.append(left, status);
    openButton.addEventListener("click", () => openHost(host.id));

    const actions = document.createElement("div");
    actions.className = "host-card-actions";

    const editButton = document.createElement("button");
    editButton.className = "host-action-button";
    editButton.type = "button";
    editButton.textContent = "編集";
    editButton.addEventListener("click", () => beginEditHost(host));

    const deleteButton = document.createElement("button");
    deleteButton.className = "host-action-button host-delete-button";
    deleteButton.type = "button";
    deleteButton.textContent = "削除";
    deleteButton.addEventListener("click", () => deleteHost(host));

    actions.append(editButton, deleteButton);
    row.append(openButton, actions);
    root.append(row);
  }
}

function beginEditHost(host) {
  editingHostId = host.id;
  const form = $("registerForm");
  form.elements.namedItem("name").value = host.name;
  form.elements.namedItem("address").value = host.address;
  form.elements.namedItem("port").value = host.port;
  form.elements.namedItem("domain_id").value = host.domain_id;
  $("hostFormTitle").textContent = "PC情報を編集";
  $("hostSubmitButton").textContent = "変更を保存";
  $("cancelHostEdit").classList.remove("hidden");
  $("registrationResult").classList.add("hidden");
  $("registerPanel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function cancelHostEdit() {
  editingHostId = null;
  $("registerForm").reset();
  $("hostFormTitle").textContent = "PCを登録";
  $("hostSubmitButton").textContent = "PCを登録";
  $("cancelHostEdit").classList.add("hidden");
  $("registrationResult").classList.add("hidden");
}

async function deleteHost(host) {
  if (!window.confirm(
    host.name + " (" + host.address + ") の登録を削除しますか？\\n" +
    "Agent側のプログラムや起動中のlaunchは停止しません。"
  )) return;

  try {
    await api("/api/hosts/" + host.id, { method: "DELETE" });
    if (editingHostId === host.id) cancelHostEdit();
    if (selectedHost && selectedHost.id === host.id) {
      selectedHost = null;
      setPage("hostsPage");
    }
    await loadHosts();
    await loadDomains();
    showToast("PC登録を削除し、hosts.yamlを更新しました。");
  } catch (error) {
    showToast("PC登録の削除に失敗しました: " + error.message, 6000);
  }
}

async function loadHosts() {
  try {
    hosts = await api("/api/hosts");
    renderHosts();
    if (selectedHost) {
      selectedHost = hosts.find((host) => host.id === selectedHost.id) || selectedHost;
    }
  } catch (error) {
    showToast("PC一覧を取得できません: " + error.message);
  }
}

async function openHost(hostId) {
  selectedHost = hosts.find((item) => item.id === hostId) || null;
  if (!selectedHost) return;
  $("controlHostName").textContent = selectedHost.name;
  $("controlHostAddress").textContent = selectedHost.address;
  $("controlDomainId").textContent = selectedHost.domain_id;
  setPage("controlPage");
  await refreshControl();
}

async function refreshControl() {
  if (!selectedHost) return;
  $("controlHostName").textContent = selectedHost.name;
  $("controlHostAddress").textContent = selectedHost.address;
  $("controlDomainId").textContent = selectedHost.domain_id;
  let online = false;
  try {
    const status = await api("/api/hosts/" + selectedHost.id + "/status");
    online = true;
    setStatus($("controlHostStatus"), "ONLINE");
    const domain = status.system && status.system.default_domain_id;
    if (domain !== undefined) $("controlDomainId").textContent = domain;
  } catch (error) {
    setStatus($("controlHostStatus"), "OFFLINE");
  }
  try {
    const data = await api("/api/hosts/" + selectedHost.id + "/launches");
    launches = data.launches || [];
    renderLaunches();
  } catch (error) {
    launches = [];
    renderLaunches();
    $("launchMessage").textContent = "launch情報を取得できません: " + error.message;
  }
  $("launchStart").disabled = !online || !$("launchSelect").value || $("launchSelect").value === "__running__";
  $("shutdownButton").disabled = !online;
  updateLaunchButtons();
  await refreshBagStatus();
}

function renderLaunches() {
  const select = $("launchSelect");
  const previous = select.value;
  select.replaceChildren();
  select.add(new Option("launchを選択", ""));
  for (const item of launches) {
    const mark = item.status === "RUNNING" ? "●" :
      item.status === "STARTING" ? "◐" :
      item.status === "STOPPING" ? "◌" :
      item.status === "ERROR" ? "!" : "○";
    const label = mark + " " + item.display_name + " (" + (item.status || "STOPPED") + ")";
    select.add(new Option(label, item.id));
  }
  const runningCount = launches.filter((item) => ["STARTING", "RUNNING"].includes(item.status)).length;
  if (runningCount > 0) {
    select.add(new Option("■ 実行中のlaunchをすべて選択 (" + runningCount + ")", "__running__"));
  }
  if ([...select.options].some((option) => option.value === previous)) {
    select.value = previous;
  }
  $("launchCount").textContent = "実行中 " + runningCount + " / 登録 " + launches.length;
  updateLaunchButtons();
}

function updateLaunchButtons() {
  const selected = launches.find((item) => item.id === $("launchSelect").value);
  const allRunning = $("launchSelect").value === "__running__";
  const anyRunning = launches.some((item) => ["STARTING", "RUNNING"].includes(item.status));
  const online = $("controlHostStatus").textContent === "ONLINE";
  const selectedActive = selected && ["RUNNING", "STARTING", "STOPPING"].includes(selected.status);
  const selectedStoppable = selected && ["STARTING", "RUNNING"].includes(selected.status);
  $("launchStart").disabled = !online || !selected || selectedActive || allRunning;
  $("launchStop").disabled = !online || (!allRunning && !selectedStoppable) || (allRunning && !anyRunning);
}

async function startSelectedLaunch() {
  if (!selectedHost) return;
  const launchId = $("launchSelect").value;
  if (!launchId || launchId === "__running__") return;
  $("launchStart").disabled = true;
  $("launchMessage").textContent = "起動要求を送信しています...";
  try {
    const result = await api("/api/hosts/" + selectedHost.id + "/launches/start", {
      method: "POST", body: JSON.stringify({ launch_id: launchId }),
    });
    $("launchMessage").textContent = result.message || "起動要求を受け付けました。";
    await refreshControl();
  } catch (error) {
    $("launchMessage").textContent = "起動失敗: " + error.message;
  }
}

async function stopSelectedLaunch() {
  if (!selectedHost) return;
  const value = $("launchSelect").value;
  if (!value) return;
  const launchIds = value === "__running__" ? ["__running__"] : [value];
  const label = value === "__running__" ? "実行中の管理launchすべて" :
    (launches.find((item) => item.id === value)?.display_name || value);
  if (!window.confirm(label + " を停止しますか？")) return;
  $("launchStop").disabled = true;
  $("launchMessage").textContent = "停止要求を送信しています...";
  try {
    const result = await api("/api/hosts/" + selectedHost.id + "/launches/stop", {
      method: "POST", body: JSON.stringify({ launch_ids: launchIds }),
    });
    $("launchMessage").textContent = result.ok ? "停止要求が完了しました。" : "一部の停止に失敗しました。";
    await refreshControl();
  } catch (error) {
    $("launchMessage").textContent = "停止失敗: " + error.message;
  }
}

async function requestShutdown() {
  if (!selectedHost) return;
  const confirmed = window.confirm(
    selectedHost.name + " (" + selectedHost.address + ") をシャットダウンしますか？\n\n" +
    "実行中のlaunchやROSbagの状態に関わらず、シャットダウン要求を送信します。"
  );
  if (!confirmed) return;
  $("shutdownButton").disabled = true;
  try {
    await api("/api/hosts/" + selectedHost.id + "/shutdown", { method: "POST", body: "{}" });
    showToast("シャットダウン要求を送信しました。");
  } catch (error) {
    showToast("シャットダウン要求に失敗しました: " + error.message, 6000);
  } finally {
    $("shutdownButton").disabled = $("controlHostStatus").textContent !== "ONLINE";
  }
}

async function loadDomains() {
  const previous = $("bagDomain").value;
  try {
    const data = await api("/api/domains");
    currentDomains = data.domains || [];
  } catch (error) {
    currentDomains = selectedHost ? [Number(selectedHost.domain_id)] : [];
  }
  const select = $("bagDomain");
  select.replaceChildren();
  if (!currentDomains.length) {
    select.add(new Option("Domain ID未登録", ""));
    return;
  }
  for (const id of currentDomains) select.add(new Option(String(id), String(id)));
  const preferred = previous || (selectedHost ? String(selectedHost.domain_id) : "");
  select.value = currentDomains.map(String).includes(preferred) ? preferred : String(currentDomains[0]);
}

function renderSelectedTopics() {
  const root = $("selectedTopicChips");
  root.replaceChildren();
  if (!selectedTopics.size) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = "トピック未選択";
    root.append(empty);
  } else {
    for (const topic of selectedTopics) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = topic;
      root.append(chip);
    }
  }
  $("topicCount").textContent = "選択中：" + selectedTopics.size + " topics";
}

function renderTopicList() {
  const root = $("topicList");
  root.replaceChildren();
  if (topicLoadError) {
    const error = document.createElement("div");
    error.className = "muted";
    error.textContent = topicLoadError;
    root.append(error);
    return;
  }
  if (!availableTopics.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "トピックが見つかりません。Domain IDとDDS通信を確認してください。";
    root.append(empty);
    return;
  }
  const sorted = [
    ...availableTopics.filter((topic) => selectedTopics.has(topic)),
    ...availableTopics.filter((topic) => !selectedTopics.has(topic)),
  ];
  for (const topic of sorted) {
    const label = document.createElement("label");
    label.className = "topic-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedTopics.has(topic);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) selectedTopics.add(topic);
      else selectedTopics.delete(topic);
      renderSelectedTopics();
      renderTopicList();
    });
    const text = document.createElement("span");
    text.textContent = topic;
    label.append(checkbox, text);
    root.append(label);
  }
}

async function refreshTopics() {
  const domainId = $("bagDomain").value;
  if (domainId === "") return;
  $("refreshTopics").disabled = true;
  topicLoadError = "";
  $("topicList").textContent = "トピックを取得しています...";
  try {
    const data = await api("/api/rosbag/topics?domain_id=" + encodeURIComponent(domainId));
    availableTopics = data.topics || [];
    selectedTopics = new Set([...selectedTopics].filter((topic) => availableTopics.includes(topic)));
  } catch (error) {
    availableTopics = [];
    topicLoadError = "トピック取得失敗: " + error.message;
  } finally {
    $("refreshTopics").disabled = false;
    renderTopicList();
    renderSelectedTopics();
  }
}

async function refreshBagStatus() {
  try {
    currentBag = await api("/api/rosbag/status");
    const recording = Boolean(currentBag.recording);
    const state = currentBag.status || (recording ? "RECORDING" : "STOPPED");
    const storage = currentBag.storage || {};
    const statusLabels = {
      STARTING: "◐ STARTING",
      RECORDING: "● RECORDING",
      STOPPING: "◌ STOPPING",
      STOPPED: "○ STOPPED",
      ERROR: "! ERROR",
    };
    $("bagStatus").textContent = statusLabels[state] || state;
    $("bagStatus").className = "bag-status " + (
      state === "ERROR" ? "error" :
      recording ? "recording" : "stopped"
    );
    const info = currentBag.recording_info;
    $("bagPath").textContent = info && info.output_path
      ? "保存先：" + info.output_path
      : "保存先：親PC / " + (storage.root || "rosbags");

    const freeGb = Number(storage.free_gb);
    let storageText = Number.isFinite(freeGb)
      ? "親PCの空き容量：" + freeGb.toFixed(2) + " GB"
      : "親PCの空き容量：取得不可";
    storageText += "（警告 " + Number(storage.warning_free_gb || 0).toFixed(1) +
      " GB未満 / 新規記録不可 " + Number(storage.minimum_free_gb || 0).toFixed(1) + " GB未満）";
    if (storage.low_space_warning) storageText += "\n警告：保存先の空き容量が少なくなっています。";
    if (storage.cannot_start) storageText += "\n新しい記録を開始できません。空き容量を確保してください。";
    $("bagStorage").textContent = storageText;
    $("bagStorage").className = "storage-status " + (
      storage.cannot_start ? "critical" :
      storage.low_space_warning ? "warning" : ""
    );

    const noTopics = selectedTopics.size === 0 || topicLoadError;
    $("bagStart").disabled = recording || state === "STARTING" || state === "STOPPING" ||
      Boolean(storage.cannot_start) || noTopics || !$("bagDomain").value;
    $("bagStop").disabled = !recording || state !== "RECORDING";
  } catch (error) {
    $("bagStatus").textContent = "● ERROR";
    $("bagStatus").className = "bag-status error";
    $("bagPath").textContent = error.message;
    $("bagStorage").textContent = "保存先容量を取得できません。";
    $("bagStorage").className = "storage-status critical";
    $("bagStart").disabled = true;
    $("bagStop").disabled = true;
  }
  updateGlobalBagInfo();
}

function updateGlobalBagInfo() {
  const root = $("globalBagInfo");
  if (!root) return;
  root.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = currentBag && currentBag.recording ? "● RECORDING" : "● STOPPED";
  const detail = document.createElement("p");
  const info = currentBag && currentBag.recording_info;
  detail.textContent = info
    ? "Domain ID " + info.domain_id + " · " + (info.topics?.length || 0) + " topics · " + (info.output_path || "")
    : "現在、親PCで記録中のrosbagはありません。";
  root.append(title, detail);
  const storage = currentBag && currentBag.storage;
  if (storage) {
    const capacity = document.createElement("p");
    capacity.textContent = "空き容量：" + Number(storage.free_gb || 0).toFixed(2) + " GB" +
      (storage.low_space_warning ? "（警告）" : "");
    capacity.className = storage.cannot_start ? "storage-status critical" :
      storage.low_space_warning ? "storage-status warning" : "storage-status";
    root.append(capacity);
  }
  const hint = document.createElement("p");
  hint.className = "help-text";
  hint.textContent = "ROSbagの開始・停止とトピック選択は、PC操作画面のROSbag管理パネルから行えます。";
  root.append(hint);
}

async function startBag() {
  const domainId = $("bagDomain").value;
  const topics = [...selectedTopics];
  if (!domainId) return showToast("Domain IDを選択してください。");
  if (!topics.length) return showToast("記録するトピックを1つ以上選択してください。");
  if (currentBag && currentBag.recording) return showToast("すでにROSbagを記録中です。");
  if (!window.confirm("Domain ID " + domainId + " の " + topics.length + " topicsを親PCに記録しますか？")) return;
  $("bagStart").disabled = true;
  $("bagMessage").textContent = "ROSbagを起動しています...";
  try {
    const result = await api("/api/rosbag/start", {
      method: "POST", body: JSON.stringify({ domain_id: Number(domainId), topics }),
    });
    $("bagMessage").textContent = "記録を開始しました。";
    $("topicPicker").open = false;
    await refreshBagStatus();
    showToast(result.recording_info?.output_path || "ROSbag recording started");
  } catch (error) {
    $("bagMessage").textContent = "記録開始失敗: " + error.message;
  } finally {
    await refreshBagStatus();
  }
}

async function stopBag() {
  if (!currentBag || !currentBag.recording) return;
  if (!window.confirm("ROSbagの記録を停止してファイルを確定しますか？")) return;
  $("bagStop").disabled = true;
  $("bagMessage").textContent = "ROSbagを正常終了しています...";
  try {
    await api("/api/rosbag/stop", { method: "POST", body: "{}" });
    $("bagMessage").textContent = "記録を停止しました。";
    await refreshBagStatus();
  } catch (error) {
    $("bagMessage").textContent = "記録停止失敗: " + error.message;
  }
}

async function openDetails() {
  if (!selectedHost) return;
  setPage("detailsPage");
  $("detailTitle").textContent = selectedHost.name + " 詳細";
  $("basicInfo").replaceChildren();
  $("systemInfo").replaceChildren();
  $("nodeList").textContent = "読み込み中...";
  try {
    const status = await api("/api/hosts/" + selectedHost.id + "/status");
    const system = status.system || {};
    renderDefinitionList($("basicInfo"), [
      ["PC名", selectedHost.name],
      ["IPアドレス", (system.ip_addresses || [selectedHost.address]).join(", ")],
      ["Distribution", system.ros_distro || "不明"],
      ["OS", system.os || "不明"],
      ["デフォルトDomain ID", system.default_domain_id ?? selectedHost.domain_id],
    ]);
    renderDefinitionList($("systemInfo"), [
      ["CPU使用率", formatPercent(system.cpu_percent)],
      ["メモリ使用率", formatPercent(system.memory_percent)],
      ["メモリ容量", formatBytes(system.memory_total_bytes)],
      ["ディスク使用率", formatPercent(system.disk_percent)],
      ["ディスク空き", formatBytes(system.disk_free_bytes)],
      ["CPU温度", system.cpu_temperature_c == null ? "取得不可" : Number(system.cpu_temperature_c).toFixed(1) + " °C"],
    ]);
    const nodes = system.ros_nodes || [];
    const root = $("nodeList");
    root.replaceChildren();
    if (!nodes.length) {
      root.textContent = "ノードを取得できませんでした（ROS 2環境またはDomain IDを確認してください）。";
    } else {
      for (const node of nodes) {
        const item = document.createElement("div");
        item.className = "node-item";
        item.textContent = node;
        root.append(item);
      }
    }
  } catch (error) {
    $("basicInfo").textContent = "情報を取得できません: " + error.message;
    $("nodeList").textContent = "PCに接続できません。";
    $("systemInfo").replaceChildren();
  }
  const bagInfo = await api("/api/rosbag/status").catch(() => null);
  const info = bagInfo && bagInfo.recording_info;
  renderDefinitionList($("detailRosbagInfo"), [
    ["保存先", info?.output_path || "親PC / 記録停止中"],
    ["記録状態", bagInfo?.recording ? "RECORDING" : "STOPPED"],
    ["記録Domain ID", info?.domain_id ?? "-"],
  ]);
}

function renderDefinitionList(root, rows) {
  root.replaceChildren();
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = String(value ?? "");
    root.append(dt, dd);
  }
}

function formatPercent(value) {
  return value == null ? "不明" : Number(value).toFixed(1) + " %";
}
function formatBytes(value) {
  if (value == null) return "不明";
  return (Number(value) / (1024 ** 3)).toFixed(1) + " GB";
}

async function registerHost(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const payload = {
    name: form.get("name"),
    address: form.get("address"),
    port: Number(form.get("port")),
    domain_id: Number(form.get("domain_id")),
  };

  const wasEditing = editingHostId !== null;
  try {
    if (wasEditing) {
      await api("/api/hosts/" + editingHostId, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      editingHostId = null;
      formElement.reset();
      $("hostFormTitle").textContent = "PCを登録";
      $("hostSubmitButton").textContent = "PCを登録";
      $("cancelHostEdit").classList.add("hidden");
      $("registrationResult").textContent =
        "PC情報を更新しました。config/hosts.yamlも更新されています。Agentトークンは変更していません。";
      $("registrationResult").classList.remove("hidden");
      showToast("PC情報を更新しました。");
    } else {
      const result = await api("/api/hosts", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("registrationResult").textContent =
        "PCを登録しました。Agent側の設定ファイルに以下を反映してください。\n\n" +
        "agent/config/agent.yaml の agent.token に次の値を設定します。\n" +
        result.agent_token + "\n\n" +
        result.agent_config +
        "\n\nこのトークンは画面を閉じると再表示されません。";
      $("registrationResult").classList.remove("hidden");
      formElement.reset();
      showToast("PCを登録し、config/hosts.yamlを更新しました。");
    }

    await loadHosts();
    await loadDomains();
  } catch (error) {
    showToast(
      (wasEditing ? "PC情報の更新" : "PC登録") +
      "に失敗しました: " + error.message,
      6000,
    );
  }
}

async function showRosbagPage() {
  if (selectedHost) {
    setPage("controlPage");
    document.querySelector(".bag-actions")?.scrollIntoView({ behavior: "smooth", block: "center" });
  } else {
    setPage("rosbagPage");
    await refreshBagStatus();
    showToast("Domain IDは登録済みPC・launchの設定から自動収集されます。まずPCを登録してください。");
  }
}

$("navHosts").addEventListener("click", () => setPage("hostsPage"));
$("navRosbag").addEventListener("click", showRosbagPage);
$("registerForm").addEventListener("submit", registerHost);
$("cancelHostEdit").addEventListener("click", cancelHostEdit);
$("backToHosts").addEventListener("click", () => setPage("hostsPage"));
$("backToHostsFromBag").addEventListener("click", () => setPage("hostsPage"));
$("backToControl").addEventListener("click", () => setPage("controlPage"));
$("detailsButton").addEventListener("click", openDetails);
$("launchSelect").addEventListener("change", updateLaunchButtons);
$("launchStart").addEventListener("click", startSelectedLaunch);
$("launchStop").addEventListener("click", stopSelectedLaunch);
$("shutdownButton").addEventListener("click", requestShutdown);
$("bagDomain").addEventListener("change", () => {
  selectedTopics.clear();
  availableTopics = [];
  renderSelectedTopics();
  refreshTopics();
});
$("refreshTopics").addEventListener("click", refreshTopics);
$("selectAllTopics").addEventListener("click", () => {
  selectedTopics = new Set(availableTopics);
  renderSelectedTopics();
  renderTopicList();
});
$("clearAllTopics").addEventListener("click", () => {
  selectedTopics.clear();
  renderSelectedTopics();
  renderTopicList();
});
$("bagStart").addEventListener("click", startBag);
$("bagStop").addEventListener("click", stopBag);
$("topicPicker").addEventListener("toggle", (event) => {
  if (event.currentTarget.open) renderTopicList();
});

let pollTimer = null;

function showLoginPage() {
  $("appHeader").classList.add("hidden");
  for (const page of ["hostsPage", "controlPage", "detailsPage", "rosbagPage"]) {
    $(page).classList.add("hidden");
  }
  $("loginPage").classList.remove("hidden");
}

function showDashboard() {
  $("loginPage").classList.add("hidden");
  $("appHeader").classList.remove("hidden");
  setPage("hostsPage");
}

async function submitLogin(event) {
  event.preventDefault();
  $("loginMessage").textContent = "認証しています...";
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("loginPassword").value }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || "ログインに失敗しました");
    sessionStorage.setItem("managerSession", result.session_token);
    $("loginPassword").value = "";
    $("loginMessage").textContent = "";
    showDashboard();
    await initializeDashboard();
  } catch (error) {
    $("loginMessage").textContent = error.message;
  }
}

async function initializeDashboard() {
  await loadHosts();
  await loadDomains();
  await refreshTopics();
  await refreshBagStatus();
  if (pollTimer) window.clearInterval(pollTimer);
  pollTimer = window.setInterval(async () => {
    if (!sessionStorage.getItem("managerSession")) return;
    await loadHosts();
    await refreshBagStatus();
    if (selectedHost && !$("controlPage").classList.contains("hidden")) {
      await refreshControl();
    }
  }, 3000);
}

async function initialize() {
  $("loginForm").addEventListener("submit", submitLogin);
  const token = sessionStorage.getItem("managerSession");
  if (!token) {
    showLoginPage();
    return;
  }
  try {
    await api("/api/session");
    showDashboard();
    await initializeDashboard();
  } catch (error) {
    sessionStorage.removeItem("managerSession");
    showLoginPage();
  }
}
initialize();
