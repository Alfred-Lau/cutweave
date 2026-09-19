/* CutWeave 控制台前端逻辑（原生 JS，无构建依赖） */
"use strict";

const API = "/api/v1";
const $ = (s) => document.querySelector(s);
const state = {
  drafts: [],
  currentId: null,
  draft: null,
  ttsFile: null, // {file_path, file_url, duration, engine}
};

/* ---------------- 基础 ---------------- */
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  t.classList.toggle("err", isErr);
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, 3600);
}

async function api(path, opts = {}) {
  const init = { headers: {} };
  if (opts.body !== undefined) {
    init.method = opts.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  } else if (opts.method) {
    init.method = opts.method;
  }
  let resp;
  try {
    resp = await fetch(API + path, init);
  } catch (e) {
    throw new Error("网络错误：引擎不可达");
  }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* 非 JSON */ }
  if (!resp.ok) {
    throw new Error((data && (data.detail || data.message)) || `HTTP ${resp.status}`);
  }
  return data;
}

const fmtTime = (t) => `${Number(t).toFixed(1)}s`;
const baseName = (u) => (u || "").split("/").pop().slice(0, 28) || "(内嵌文字)";

/* ---------------- Tab 切换 ---------------- */
$("#main-nav").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  document.querySelectorAll("#main-nav button").forEach((b) => b.classList.toggle("active", b === btn));
  document.querySelectorAll("main > section").forEach((s) =>
    s.classList.toggle("active", s.id === "tab-" + btn.dataset.tab));
  if (btn.dataset.tab === "voices") loadVoices();
  if (btn.dataset.tab === "tts") loadVoiceOptions();
});

/* 子表单切换 */
$("#add-nav").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-sub]");
  if (!btn) return;
  document.querySelectorAll("#add-nav button").forEach((b) => b.classList.toggle("active", b === btn));
  ["video", "audio", "text"].forEach((k) => { $("#form-" + k).hidden = k !== btn.dataset.sub; });
});

/* ---------------- health ---------------- */
async function loadHealth() {
  try {
    const h = await api("/health");
    setBadge("#b-font", h.font_ready, h.font_ready ? "" : "未找到");
    setBadge("#b-volc", h.volcengine_ready);
    setBadge("#b-cosy", h.cosyvoice_ready);
  } catch (e) {
    setBadge("#b-font", false, "引擎离线");
    setBadge("#b-volc", false);
    setBadge("#b-cosy", false);
  }
}
function setBadge(sel, on, text) {
  const el = $(sel);
  el.classList.toggle("on", !!on);
  el.classList.toggle("off", !on);
  if (text !== undefined && text !== "") el.append(" " + text);
}

/* ---------------- 草稿列表 ---------------- */
async function loadDrafts() {
  const ul = $("#draft-list");
  try {
    state.drafts = await api("/drafts");
  } catch (e) {
    ul.innerHTML = `<li class="dim">加载失败：${e.message}</li>`;
    return;
  }
  if (!Array.isArray(state.drafts) || state.drafts.length === 0) {
    ul.innerHTML = '<li class="dim">还没有草稿，创建一个吧</li>';
    return;
  }
  ul.innerHTML = "";
  for (const d of state.drafts) {
    const li = document.createElement("li");
    if (d.draft_id === state.currentId) li.classList.add("active");
    const c = d.canvas || {};
    li.innerHTML = `
      <span class="d-name">${esc(d.name || d.draft_id)}</span>
      <span class="d-meta">${c.width || "?"}×${c.height || "?"} · ${fmtTime(d.duration || 0)}</span>
      <button class="danger" title="删除草稿">✕</button>`;
    li.addEventListener("click", (e) => {
      if (e.target.closest("button.danger")) return;
      selectDraft(d.draft_id);
    });
    li.querySelector("button.danger").addEventListener("click", async () => {
      if (!confirm(`确定删除草稿「${d.name || d.draft_id}」？不可恢复`)) return;
      try {
        await api("/drafts/" + d.draft_id, { method: "DELETE" });
        toast("已删除");
        if (state.currentId === d.draft_id) { state.currentId = null; state.draft = null; showEditor(false); }
        loadDrafts();
      } catch (err) { toast(err.message, true); }
    });
    ul.appendChild(li);
  }
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("#d-create").addEventListener("click", async () => {
  const [w, h] = $("#d-size").value.split("x");
  try {
    const d = await api("/drafts", {
      body: { name: $("#d-name").value.trim() || "未命名草稿", width: +w, height: +h, fps: +$("#d-fps").value },
    });
    toast(`已创建：${d.name}`);
    $("#d-name").value = "";
    await loadDrafts();
    selectDraft(d.draft_id);
  } catch (e) { toast(e.message, true); }
});

/* ---------------- 草稿编辑 ---------------- */
async function selectDraft(id) {
  state.currentId = id;
  try {
    state.draft = await api("/drafts/" + id);
  } catch (e) { toast(e.message, true); return; }
  showEditor(true);
  renderEditor();
  loadDrafts();
}

function showEditor(show) {
  $("#editor").hidden = !show;
  $("#editor-empty").hidden = show;
  if (!show) $("#render-result").hidden = true;
}

function renderEditor() {
  const d = state.draft;
  if (!d) return;
  $("#ed-name").textContent = d.name;
  $("#ed-meta").textContent = `${d.canvas.width}×${d.canvas.height} · ${d.canvas.fps}fps · ${d.materials.length} 素材`;
  $("#ed-duration").textContent = `总时长 ${fmtTime(d.duration)}`;
  renderTimeline(d);
}

function kindOf(d, seg) {
  const m = d.materials.find((x) => x.material_id === seg.material_id);
  return m ? m.kind : "video";
}
function resourceOf(kind) {
  return { video: "videos", image: "images", audio: "audios", text: "texts" }[kind] || "videos";
}
function blockLabel(d, seg) {
  const m = d.materials.find((x) => x.material_id === seg.material_id);
  if (!m) return seg.segment_id.slice(0, 8);
  if (m.kind === "text") return (m.text || "").slice(0, 18);
  return baseName(m.url);
}

function renderTimeline(d) {
  const total = Math.max(d.duration, 0.1);
  const wrap = $("#timeline");
  wrap.innerHTML = "";
  const rows = [["video", "视频轨"], ["audio", "音频轨"], ["text", "文字轨"]];
  for (const [type, label] of rows) {
    const row = document.createElement("div");
    row.className = "tl-row";
    row.innerHTML = `<div class="tl-label">${label}</div>`;
    const lane = document.createElement("div");
    lane.className = "tl-lane";
    const segs = (d.tracks || []).filter((t) => t.type === type)
      .flatMap((t) => t.segments.map((s) => ({ ...s, level: t.level })));
    if (segs.length === 0) {
      lane.innerHTML = '<span class="tl-empty">空</span>';
    }
    for (const s of segs) {
      const kind = kindOf(d, s);
      const b = document.createElement("div");
      b.className = "tl-block " + kind;
      b.style.left = (s.start / total * 100) + "%";
      b.style.width = Math.max((s.end - s.start) / total * 100, 1.2) + "%";
      b.title = `${blockLabel(d, s)}  ${fmtTime(s.start)} → ${fmtTime(s.end)}（点击删除）`;
      b.textContent = blockLabel(d, s);
      b.addEventListener("click", () => removeSegment(d, s, kind));
      lane.appendChild(b);
    }
    row.appendChild(lane);
    wrap.appendChild(row);
  }
}

async function removeSegment(d, seg, kind) {
  if (!confirm(`删除片段「${blockLabel(d, seg)}」（${fmtTime(seg.start)} → ${fmtTime(seg.end)}）？`)) return;
  try {
    await api(`/drafts/${d.draft_id}/${resourceOf(kind)}/${seg.segment_id}`, { method: "DELETE" });
    toast("已删除片段");
    selectDraft(d.draft_id);
  } catch (e) { toast(e.message, true); }
}

/* ---------------- 添加素材 ---------------- */
function num(v, fallback = null) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

$("#form-video").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    url: f.url.value.trim(),
    start: num(f.start.value, 0),
    source_in: num(f.source_in.value, 0),
    level: num(f.level.value, 0),
    x: num(f.x.value, 0),
    y: num(f.y.value, 0),
    scale: num(f.scale.value, 1),
    alpha: num(f.alpha.value, 1),
  };
  const end = num(f.end.value), dur = num(f.duration.value);
  if (end !== null) body.end = end; else if (dur !== null) body.duration = dur;
  await addResource("videos", body, f);
});

$("#form-audio").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    url: f.url.value.trim(),
    start: num(f.start.value, 0),
    source_in: num(f.source_in.value, 0),
    volume: num(f.volume.value, 1),
  };
  const end = num(f.end.value), dur = num(f.duration.value);
  if (end !== null) body.end = end; else if (dur !== null) body.duration = dur;
  await addResource("audios", body, f);
});

$("#form-text").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    text: f.text.value,
    start: num(f.start.value, 0),
    end: num(f.end.value, 5),
    font_size: num(f.font_size.value, 64),
    font_color: f.font_color.value,
    y: num(f.y.value, 0),
  };
  if (f.border_color.value) { body.border_color = f.border_color.value; body.border_width = 2; }
  await addResource("texts", body, f);
});

async function addResource(resource, body, form) {
  if (!state.currentId) { toast("请先选择草稿", true); return; }
  try {
    const r = await api(`/drafts/${state.currentId}/${resource}`, { body });
    toast(`已添加 ${r.items.length} 个片段`);
    form.reset();
    $("#probe-out").textContent = "";
    selectDraft(state.currentId);
  } catch (e) { toast(e.message, true); }
}

/* probe */
$("#btn-probe").addEventListener("click", async () => {
  const url = document.querySelector('#form-video input[name="url"]').value.trim();
  const out = $("#probe-out");
  if (!url) { toast("先填素材 URL", true); return; }
  out.textContent = "探测中…";
  try {
    const p = await api(`/media/probe?url=${encodeURIComponent(url)}`);
    out.textContent = `✓ ${p.width}×${p.height} · ${fmtTime(p.duration)}`;
    const f = $("#form-video");
    if (!num(f.end.value) && !num(f.duration.value)) {
      f.duration.value = Math.round(p.duration * 10) / 10;
    }
  } catch (e) { out.textContent = "✗ " + e.message; }
});

/* ---------------- 渲染 ---------------- */
$("#ed-render").addEventListener("click", async () => {
  if (!state.currentId) return;
  const btn = $("#ed-render");
  btn.disabled = true;
  btn.textContent = "⏳ 渲染中…（约几秒到几分钟）";
  const box = $("#render-result");
  box.hidden = false;
  box.innerHTML = '<p class="dim">正在调用 FFmpeg 合成，请稍候…</p>';
  try {
    const job = await api("/render/tasks", { body: { draft_id: state.currentId } });
    showRenderResult(job);
  } catch (e) {
    box.innerHTML = `<p class="fail">提交失败：${esc(e.message)}</p>`;
  }
  btn.disabled = false;
  btn.textContent = "🚀 一键渲染";
});

function showRenderResult(job) {
  const box = $("#render-result");
  if (job.status === "succeeded") {
    box.innerHTML = `
      <h3><span class="ok">✅ 渲染成功</span>
        <span class="dim">${job.task_id} · ${fmtTime(job.duration)} · ${job.width}×${job.height}</span></h3>
      <video controls src="${job.file_url}"></video>
      <div class="form-foot">
        <a class="ghost" href="${job.file_url}" download>⬇️ 下载成片</a>
      </div>`;
    toast("渲染完成 🎉");
  } else {
    box.innerHTML = `<h3><span class="fail">❌ 渲染失败</span></h3><pre class="hint" style="white-space:pre-wrap">${esc(job.error || "未知错误")}</pre>`;
  }
}

/* ---------------- TTS 工坊 ---------------- */
$("#tts-go").addEventListener("click", async () => {
  const text = $("#tts-text").value.trim();
  if (!text) { toast("先输入文案", true); return; }
  const engine = $("#tts-engine").value;
  const voice = $("#tts-voice").value.trim();
  const status = $("#tts-status");
  const btn = $("#tts-go");
  btn.disabled = true;
  status.textContent = "合成中…（长文案会分段合成再拼接，稍等）";
  try {
    const r = await api("/ai/tts", { body: { text, engine, voice: voice || null } });
    state.ttsFile = r;
    $("#tts-result").hidden = false;
    $("#tts-audio").src = r.file_url;
    $("#tts-meta").textContent = `引擎 ${r.engine} · 时长 ${fmtTime(r.duration)} · ${r.file_path}`;
    $("#tts-download").href = r.file_url;
    const t = state.draft ? `${state.draft.name}` : "未选择";
    $("#tts-target").textContent = t;
    $("#tts-insert").style.display = state.currentId ? "" : "none";
    status.textContent = "";
    toast("合成完成 ✅");
  } catch (e) {
    status.textContent = "";
    toast(e.message, true);
  }
  btn.disabled = false;
});

$("#ins-go").addEventListener("click", async () => {
  if (!state.ttsFile || !state.currentId) { toast("请先合成并选择草稿", true); return; }
  const start = num($("#ins-start").value, 0);
  const dur = num($("#ins-duration").value);
  const body = { url: state.ttsFile.file_path, start };
  if (dur !== null) body.duration = dur;
  try {
    await api(`/drafts/${state.currentId}/audios`, { body });
    toast("已插入当前草稿音频轨 ✅");
    selectDraft(state.currentId);
  } catch (e) { toast(e.message, true); }
});

/* ---------------- 声音库 ---------------- */
async function loadVoiceOptions() {
  try {
    const r = await api("/ai/voices");
    const dl = $("#voice-options");
    dl.innerHTML = "";
    for (const v of (r.voices || [])) {
      const o = document.createElement("option");
      o.value = v.voice_id;
      o.label = v.name || "";
      dl.appendChild(o);
    }
  } catch (e) { /* 静默 */ }
}

async function loadVoices() {
  const ul = $("#voices-list");
  try {
    const r = await api("/ai/voices");
    const voices = r.voices || [];
    if (voices.length === 0) {
      ul.innerHTML = '<li class="dim">暂无登记的克隆音色（火山复刻的 S_ 声音 ID 直接在 TTS 工坊填写即可，无需登记）</li>';
      return;
    }
    ul.innerHTML = "";
    for (const v of voices) {
      const li = document.createElement("li");
      li.innerHTML = `
        <span class="v-name">${esc(v.name || "(未命名)")}</span>
        <span class="v-id">${esc(v.voice_id)}</span>
        <button class="danger" title="删除">✕</button>`;
      li.querySelector("button.danger").addEventListener("click", async () => {
        if (!confirm(`删除音色「${v.name || v.voice_id}」？`)) return;
        try {
          await api("/ai/voices/" + encodeURIComponent(v.voice_id), { method: "DELETE" });
          toast("已删除");
          loadVoices();
        } catch (e) { toast(e.message, true); }
      });
      ul.appendChild(li);
    }
  } catch (e) {
    ul.innerHTML = `<li class="dim">加载失败：${esc(e.message)}</li>`;
  }
}

/* ---------------- init ---------------- */
loadHealth();
loadDrafts();
loadVoiceOptions();
