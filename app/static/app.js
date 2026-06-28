const $ = (id) => document.getElementById(id);

let currentUrl = "";
let currentTitle = "";

function fmtSize(bytes) {
  if (!bytes) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${u[i]}`;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function loadConfig() {
  const res = await fetch("/api/config");
  if (!res.ok) return;
  const c = await res.json();
  const parts = [
    `📁 保存目录: <code>${c.download_dir}</code>`,
    `🧵 线程: ${c.threads}`,
    c.proxy ? `🌐 代理: ${c.proxy}` : "🌐 代理: 未配置",
    c.aria2c ? "⚡ aria2c 多线程: 已启用" : "⚡ aria2c: 未装(用原生分片)",
    c.ffmpeg ? "🎞️ ffmpeg: 就绪" : "⚠️ ffmpeg: 缺失(HLS无法合并)",
    c.cookies_uploaded ? "🍪 cookies: 已上传" : "🍪 cookies: 未上传",
  ];
  $("cfgBar").innerHTML = parts.join(" &nbsp;·&nbsp; ");
}

async function parse() {
  const url = $("urlInput").value.trim();
  if (!url) return;
  currentUrl = url;
  $("parseError").textContent = "";
  $("resultCard").classList.add("hidden");
  $("parseBtn").disabled = true;
  $("parseBtn").textContent = "解析中…";
  try {
    const res = await fetch("/api/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "解析失败");
    renderResult(data);
  } catch (e) {
    $("parseError").textContent = e.message;
  } finally {
    $("parseBtn").disabled = false;
    $("parseBtn").textContent = "解析";
  }
}

function resetUrl() {
  $("urlInput").value = "";
  $("parseError").textContent = "";
  $("resultCard").classList.add("hidden");
  currentUrl = "";
  currentTitle = "";
  $("urlInput").focus();
}

function renderResult(data) {
  currentTitle = data.title;
  $("vTitle").textContent = data.title;
  $("vUploader").textContent = data.uploader ? `@${data.uploader}` : "";
  const thumb = $("thumb");
  if (data.thumbnail) { thumb.src = data.thumbnail; thumb.style.display = "block"; }
  else thumb.style.display = "none";

  const list = $("resList");
  list.innerHTML = "";
  data.resolutions.forEach((r) => {
    const btn = document.createElement("button");
    btn.className = "res-btn";
    const size = r.filesize ? ` · ${fmtSize(r.filesize)}` : "";
    const tbr = r.tbr ? ` · ${r.tbr}k` : "";
    btn.innerHTML = `<strong>${r.height}p</strong><span>${r.label}${tbr}${size}</span>`;
    btn.onclick = () => startDownload(r, btn);
    list.appendChild(btn);
  });
  $("resultCard").classList.remove("hidden");
}

async function startDownload(r, btn) {
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = "提交中…";
  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: currentUrl,
        format_id: r.format_id,
        title: currentTitle,
        resolution: `${r.height}p`,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      btn.innerHTML = "✅ 已加入下载";
      refreshTasks();
      document.getElementById("taskTable").scrollIntoView({ behavior: "smooth", block: "center" });
    } else {
      btn.innerHTML = "❌ 失败";
      alert("启动下载失败：" + (data.detail || res.status));
    }
  } catch (e) {
    btn.innerHTML = "❌ 失败";
    alert("启动下载失败：" + e.message);
  } finally {
    setTimeout(() => { btn.disabled = false; btn.innerHTML = original; }, 2000);
  }
}

async function refreshTasks() {
  const res = await fetch("/api/tasks");
  if (!res.ok) return;
  const tasks = await res.json();
  const body = $("taskBody");
  if (!tasks.length) {
    body.innerHTML = '<tr><td colspan="5" class="muted center">暂无任务</td></tr>';
    return;
  }
  body.innerHTML = tasks.map(renderTask).join("");
}

function renderTask(t) {
  let statusCell;
  if (t.status === "downloading") {
    const extra = [t.speed, t.eta ? `剩余 ${t.eta}` : ""].filter(Boolean).join(" · ");
    statusCell = `<div class="progress"><div class="bar" style="width:${t.progress}%"></div></div>
      <span class="small">${t.progress}% ${extra}</span>`;
  } else if (t.status === "paused") {
    statusCell = `<div class="progress"><div class="bar paused" style="width:${t.progress}%"></div></div>
      <span class="small">⏸ 已暂停 · ${t.progress}%</span>`;
  } else if (t.status === "finished") {
    statusCell = `<span class="badge ok">✅ 完成</span>`;
  } else if (t.status === "error") {
    const err = escapeHtml((t.error || "未知错误").slice(0, 200));
    statusCell = `<span class="badge err">❌ 失败</span><div class="err-msg" title="${escapeHtml(t.error || '')}">${err}</div>`;
  } else {
    statusCell = `<span class="badge">⏳ 排队中</span>`;
  }

  let actions = "";
  if (t.status === "downloading") {
    actions += `<button class="link" onclick="pauseTask('${t.id}')">暂停</button> `;
  }
  if (t.status === "paused") {
    actions += `<button class="link" onclick="resumeTask('${t.id}')">继续</button> `;
  }
  if (t.status === "finished") {
    actions += `<a class="link" href="/api/tasks/${t.id}/file">下载到本地</a> `;
  }
  if (t.status === "error") {
    actions += `<button class="link" onclick="retryTask('${t.id}')">重试</button> `;
  }
  actions += `<button class="link danger" onclick="deleteTask('${t.id}')">删除</button>`;

  return `<tr>
    <td class="title-cell" title="${escapeHtml(t.title || '')}">${escapeHtml(t.title || t.url)}</td>
    <td>${t.resolution || ""}</td>
    <td class="small">${fmtSize(t.filesize) || "—"}</td>
    <td>${statusCell}</td>
    <td>${actions}</td>
  </tr>`;
}

async function deleteTask(id) {
  await fetch(`/api/tasks/${id}`, { method: "DELETE" });
  refreshTasks();
}

async function retryTask(id) {
  await fetch(`/api/tasks/${id}/retry`, { method: "POST" });
  refreshTasks();
}

async function pauseTask(id) {
  await fetch(`/api/tasks/${id}/pause`, { method: "POST" });
  refreshTasks();
}

async function resumeTask(id) {
  await fetch(`/api/tasks/${id}/resume`, { method: "POST" });
  refreshTasks();
}

async function clearTasks() {
  if (!confirm("确定清空所有任务记录？（不会删除已下载到磁盘的文件）")) return;
  await fetch("/api/tasks/clear", { method: "POST" });
  refreshTasks();
}

async function uploadCookie() {
  const f = $("cookieFile").files[0];
  if (!f) { $("cookieHint").textContent = "请选择文件"; return; }
  const body = new FormData();
  body.append("file", f);
  const res = await fetch("/api/cookies", { method: "POST", body });
  $("cookieHint").textContent = res.ok ? "✅ 上传成功" : "❌ 上传失败";
  loadConfig();
}

async function saveToken() {
  const auth_token = $("authToken").value.trim();
  const ct0 = $("ct0Token").value.trim();
  if (!auth_token) { $("tokenHint").textContent = "请填写 auth_token"; return; }
  const res = await fetch("/api/cookies/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auth_token, ct0 }),
  });
  const data = await res.json().catch(() => ({}));
  $("tokenHint").textContent = res.ok ? "✅ 已保存" : `❌ ${data.detail || "保存失败"}`;
  if (res.ok) { $("authToken").value = ""; $("ct0Token").value = ""; loadConfig(); }
}

$("parseBtn").onclick = parse;
$("resetBtn").onclick = resetUrl;
$("urlInput").addEventListener("keydown", (e) => { if (e.key === "Enter") parse(); });
$("uploadCookieBtn").onclick = uploadCookie;
$("saveTokenBtn").onclick = saveToken;
$("clearTasksBtn").onclick = clearTasks;
$("logoutBtn").onclick = async () => { await fetch("/api/logout", { method: "POST" }); location.href = "/login"; };

loadConfig();
refreshTasks();
setInterval(refreshTasks, 1500);
