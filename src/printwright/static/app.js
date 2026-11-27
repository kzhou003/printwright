// printwright web UI. Plain ES modules, no build step.
import { Viewer } from "./viewer.js";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// ------------------------------------------------------------------ i18n
const I18N = {
  zh: {
    settings: "设置", create: "新建", newTitlePh: "新项目，例如“GoPro 支架”", welcomeTitle: "说清需求，定好规格，打印出来。",
    welcomeBody: "在左侧新建项目。先聊清楚需求并冻结规格，再由模型生成 CAD、自动检查，最后为你的 Bambu 打印机切片。",
    s1: "需求", s1d: "对话直到规格清楚", s2: "规格", s2d: "冻结设计契约", s3: "模型", s3d: "CAD、检查、渲染、审查", s4: "切片", s4d: "生成 Bambu Studio 3MF",
    chatPh: "它要做什么？尺寸、装在哪里、材料、颜色……", send: "发送", specStatus: "规格", freeze: "冻结规格", unfreeze: "解冻",
    specHint: "规格就是契约：CAD 按它生成，也按它检查。可以直接编辑 JSON。", save: "保存", generate: "生成模型",
    openFusion: "在 Fusion 打开", openBlender: "在 Blender 打开", reveal: "显示文件", assembled: "装配位置", printPose: "打印摆放",
    checks: "检查", review: "视觉审查", applyReview: "按审查意见修改", feedback: "下一版的修改意见",
    feedbackPh: "例如：壁厚改成 3 mm，孔左移 5 mm，耳朵更圆一些", revise: "修改", script: "CAD 脚本（可编辑）",
    rebuild: "保存并重建（不调用 AI）", sliceSettings: "切片设置", slice: "为 Bambu Studio 切片", force: "忽略阻断性检查",
    noSlice: "还没有切片。", close: "关闭", draft: "草稿", frozen: "已冻结", ready: "可以切片", notReady: "有阻断项",
    switchTo: "切换到", noIter: "还没有模型。冻结规格后点“生成模型”。", working: "进行中", done: "完成", error: "出错",
    openBambu: "在 Bambu Studio 打开", openProject: "打开工程（未切片）", printTime: "打印时间", weight: "耗材", plates: "盘",
    tryExample: "试用", examplePh: "从示例开始（无需 AI）",
    confirmMode: "切换模式会把规格改回草稿，需要重新确认并冻结。继续？", modeLabel: "模式",
  },
};
let lang = localStorage.getItem("pw-lang") || (navigator.language.startsWith("zh") ? "zh" : "en");
const t = (k, fallback) => (lang === "zh" && I18N.zh[k]) || fallback || k;
function applyI18n() {
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  $$("[data-i18n]").forEach((el) => { el.dataset.en ??= el.textContent; el.textContent = lang === "zh" ? (I18N.zh[el.dataset.i18n] || el.dataset.en) : el.dataset.en; });
  $$("[data-i18n-ph]").forEach((el) => { el.dataset.enph ??= el.placeholder; el.placeholder = lang === "zh" ? (I18N.zh[el.dataset.i18nPh] || el.dataset.enph) : el.dataset.enph; });
  $("#langBtn").textContent = lang === "zh" ? "EN" : "中文";
}

// ------------------------------------------------------------------ api
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-Printwright": "1" } };
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch(`/api${path}`, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
  return data;
}

// ------------------------------------------------------------------ state
const S = { health: null, projects: [], project: null, pid: null, iter: null, iterData: null, viewer: null, tab: "req", job: null };

async function boot() {
  applyI18n();
  S.health = await api("/health");
  renderStatus();
  $("#newMode").innerHTML = S.health.modes.map((m) => `<option value="${m.name}">${esc(lang === "zh" ? m.title_zh : m.title)}</option>`).join("");
  await loadProjects();
  const ex = await api("/examples");
  $("#exSel").innerHTML = `<option value="">${esc(t("examplePh", "Start from an example (no AI)"))}</option>` +
    ex.map((e) => `<option value="${esc(e.name)}">${esc(e.title)} · ${esc(modeTitle(e.mode))}</option>`).join("");
  const [pid, tab] = location.hash.slice(1).split("/");
  if (tab) S.tab = tab;
  if (pid) openProject(pid);
}

function renderStatus() {
  const d = S.health.doctor;
  const pv = d.providers[d.provider] || {};
  const item = (on, label) => `<span class="${on ? "on" : "off"}">${esc(label)}</span>`;
  $("#status").innerHTML = [
    item(pv.available, `AI: ${d.provider}`),
    item(!!d.bambu_studio.path, `Bambu Studio ${d.bambu_studio.version || ""}`),
    item(!!d.blender, "Blender"),
    item(!!d.fusion, "Fusion"),
  ].join("");
}

async function loadProjects() {
  S.projects = await api("/projects");
  $("#plist").innerHTML = S.projects.map((p) => `
    <div class="pitem ${p.id === S.pid ? "on" : ""}" data-id="${p.id}">
      <b>${esc(p.title)}</b><small>${esc(modeTitle(p.mode))} · ${esc(t(p.status, p.status))} · v${p.iterations}</small>
    </div>`).join("");
  $$(".pitem").forEach((el) => el.onclick = () => openProject(el.dataset.id));
}

const modeInfo = (m) => S.health.modes.find((x) => x.name === m) || S.health.modes[0];
const modeTitle = (m) => { const i = modeInfo(m); return lang === "zh" ? i.title_zh : i.title; };

async function openProject(pid) {
  S.pid = pid; location.hash = `${pid}/${S.tab}`;
  S.project = await api(`/projects/${pid}`);
  $("#empty").hidden = true; $("#proj").hidden = false;
  await loadProjects();
  renderProject();
  const last = S.project.iterations.length;
  S.iter = last || null;
  if (S.tab === "model" || S.tab === "slice") await loadIteration();
  const running = (S.project.jobs || [])[0];
  if (running) watchJob(running);
}

async function refreshProject() { S.project = await api(`/projects/${S.pid}`); renderProject(); }

function renderProject() {
  const p = S.project, spec = p.spec;
  $("#ptitle").textContent = spec.title || p.title;
  $("#pmeta").textContent = `${p.id} · CAD: ${p.backend} · ${spec.print.printer} ${spec.print.nozzle} mm`;
  $("#modeSeg").innerHTML = S.health.modes.map((m) => `<button data-mode="${m.name}" class="${m.name === spec.mode ? "on" : ""}" role="radio">${esc(lang === "zh" ? m.title_zh : m.title)}</button>`).join("");
  $$("#modeSeg button").forEach((b) => b.onclick = () => changeMode(b.dataset.mode));
  $("#modeDesc").textContent = lang === "zh" ? modeInfo(spec.mode).summary_zh : modeInfo(spec.mode).summary;
  const rec = spec.mode_recommendation;
  const banner = $("#recBanner");
  banner.hidden = !(rec && rec.mode !== spec.mode);
  if (!banner.hidden) {
    banner.innerHTML = `<span>💡 ${esc(rec.reason)}</span><button class="small">${esc(t("switchTo", "Switch to"))} ${esc(modeTitle(rec.mode))}</button>`;
    $("button", banner).onclick = () => changeMode(rec.mode);
  }
  // chat
  $("#msgs").innerHTML = p.chat.map((m) => `<div class="msg ${m.role}">${esc(m.content)}${m.cost_usd ? `<span class="cost">$${m.cost_usd.toFixed(3)}</span>` : ""}</div>`).join("");
  $("#msgs").lastElementChild?.scrollIntoView({ block: "end" });
  // spec side
  const pill = $("#specPill");
  pill.textContent = t(spec.status, spec.status);
  pill.className = `pill ${spec.status === "frozen" ? "ok" : "warn"}`;
  $("#lint").className = "lint";
  $("#lint").innerHTML = p.lint.map((i) => `<div class="${i.severity}">${i.severity === "error" ? "✕" : i.severity === "warning" ? "!" : "i"} ${esc(i.message)}</div>`).join("");
  $("#freezeBtn").hidden = spec.status === "frozen";
  $("#unfreezeBtn").hidden = spec.status !== "frozen";
  $("#freezeBtn").disabled = !p.can_freeze;
  $("#specMd").innerHTML = specHtml(spec);
  if (document.activeElement !== $("#specJson")) $("#specJson").value = JSON.stringify(spec, null, 2);
  // tabs
  $$("#tabs button").forEach((b) => {
    b.classList.toggle("on", b.dataset.tab === S.tab);
    b.classList.toggle("done", (b.dataset.tab === "spec" && spec.status === "frozen") || (b.dataset.tab === "model" && p.iterations.some((i) => i.ready_to_slice)) || (b.dataset.tab === "slice" && p.slices.some((s) => s.ok)));
  });
  $$(".tab").forEach((el) => el.hidden = el.id !== `tab-${S.tab}`);
  $("#genBtn").textContent = p.iterations.length ? t("generate", "Generate new version") : t("generate", "Generate model");
  renderSliceForm();
  renderSlices();
}

function specHtml(s) {
  const rows = (arr, cols) => `<table>${arr.map((r) => `<tr>${cols.map((c) => `<td>${esc(c(r))}</td>`).join("")}</tr>`).join("")}</table>`;
  const list = (title, arr) => arr?.length ? `<h2>${esc(title)}</h2><ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  let h = `<h1>${esc(s.title)}</h1><p>${esc(s.summary)}</p>`;
  if (s.use_case) h += `<p><b>Use:</b> ${esc(s.use_case)}</p>`;
  h += `<h2>Parts</h2>` + rows(s.parts, [(p) => p.name, (p) => p.filament, (p) => p.color, (p) => `×${p.quantity}`]);
  if (s.dimensions.length) h += `<h2>Dimensions</h2>` + rows(s.dimensions, [(d) => d.name, (d) => `${d.value} ${d.unit}${d.tolerance != null ? ` ±${d.tolerance}` : ""}`, (d) => d.critical ? "critical" : "", (d) => d.note]);
  h += list("Features", s.features);
  if (s.hardware.length) h += `<h2>Hardware</h2>` + rows(s.hardware, [(x) => `${x.quantity}× ${x.name}`, (x) => x.standard, (x) => Object.entries(x.key_dimensions).map(([k, v]) => `${k}=${v}`).join(", ")]);
  if (s.fits.length) h += `<h2>Fits</h2>` + rows(s.fits, [(f) => f.name, (f) => f.kind, (f) => f.parts.join(" / "), (f) => f.clearance != null ? `${f.clearance} mm/side` : "?"]);
  h += list("Constraints", s.constraints) + list("Assumptions", s.assumptions) + list("Open questions", s.open_questions);
  if (s.acceptance.length) h += `<h2>Acceptance</h2><ul>${s.acceptance.map((c) => `<li><code>${esc(c.kind)}</code> ${esc(c.description)}</li>`).join("")}</ul>`;
  return h;
}

async function changeMode(mode) {
  if (mode === S.project.spec.mode) return;
  if (S.project.spec.status === "frozen" && !confirm(t("confirmMode", "Switching mode puts the spec back to draft. Continue?"))) return;
  await api(`/projects/${S.pid}/mode`, { method: "POST", body: { mode } });
  await refreshProject(); loadProjects();
}

// ------------------------------------------------------------------ jobs
async function runJob(title, path, body) {
  try {
    const job = await api(path, { method: "POST", body });
    watchJob(job, title);
  } catch (e) { alert(e.message); }
}

function watchJob(job, title) {
  S.job = job.id;
  const box = $("#joblog");
  box.hidden = false;
  $("#jobTitle").textContent = title || job.kind;
  $$("button.primary").forEach((b) => b.disabled = true);
  const poll = async () => {
    const j = await api(`/jobs/${job.id}`);
    $("#jobLines").textContent = j.log.join("\n") + (j.error ? `\n\n${j.error}` : "");
    $("#jobLines").scrollTop = 1e9;
    const st = $("#jobState");
    st.textContent = t(j.status === "running" || j.status === "queued" ? "working" : j.status, j.status);
    st.className = `pill ${j.status === "done" ? "ok" : j.status === "error" ? "bad" : "info"}`;
    if (j.status === "queued" || j.status === "running") return setTimeout(poll, 900);
    $$("button.primary").forEach((b) => b.disabled = false);
    await refreshProject(); await loadProjects();
    if (j.kind === "generate" || j.kind === "rebuild") { S.iter = S.project.iterations.length; showTab("model"); }
    if (j.kind === "slice") showTab("slice");
    if (j.status === "done") setTimeout(() => { if (S.job === job.id) box.hidden = true; }, 4000);
  };
  poll();
}

// ------------------------------------------------------------------ model tab
async function loadIteration() {
  const its = S.project.iterations;
  $("#iterSel").innerHTML = its.map((i) => `<option value="${i.n}">v${String(i.n).padStart(3, "0")} ${i.ok ? (i.ready_to_slice ? "✓" : "!") : "✕"}</option>`).join("");
  if (!its.length) {
    $("#checks").innerHTML = `<span class="meta">${esc(t("noIter", "No model yet. Freeze the spec, then Generate."))}</span>`;
    $("#renders").innerHTML = ""; $("#reviewCard").hidden = true; $("#code").value = ""; return;
  }
  S.iter = S.iter || its.length;
  $("#iterSel").value = S.iter;
  const d = S.iterData = await api(`/projects/${S.pid}/iterations/${S.iter}`);
  const info = d.info;
  $("#iterMeta").textContent = `${info.backend || ""} · ${info.source || ""}${info.cost_usd ? ` · $${info.cost_usd}` : ""}${info.provider ? ` · ${info.provider}` : ""}`;
  $("#code").value = d.code;
  const rp = $("#readyPill");
  if (!info.ok) {
    rp.textContent = t("error", "failed"); rp.className = "pill bad";
    $("#checks").innerHTML = `<pre class="err">${esc(info.error || "")}</pre>`;
  } else {
    rp.textContent = info.ready_to_slice ? t("ready", "ready to slice") : t("notReady", "blocked");
    rp.className = `pill ${info.ready_to_slice ? "ok" : "bad"}`;
    const icon = { pass: "✓", warn: "!", fail: "✕", skip: "–" };
    const cls = { pass: "ok", warn: "warn", fail: "bad", skip: "" };
    const fmt = (v) => v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    $("#checks").innerHTML = `<table class="checks-t">${(d.report?.checks || []).sort((a, b) => "fwps".indexOf(a.status[0]) - "fwps".indexOf(b.status[0])).map((c) => `
      <tr><td><span class="pill ${cls[c.status]}">${icon[c.status]}${c.blocking ? "■" : ""}</span></td>
      <td><b>${esc(c.title)}</b>${c.part ? ` <span class="m">${esc(c.part)}</span>` : ""}<div class="m">${esc(c.message)}${c.measured != null ? ` measured ${esc(fmt(c.measured))}` : ""}${c.expected != null ? ` · expected ${esc(fmt(c.expected))}` : ""}</div></td></tr>`).join("")}</table>`;
  }
  const rv = d.review;
  $("#reviewCard").hidden = !rv;
  if (rv) {
    $("#verdict").textContent = rv.verdict; $("#verdict").className = `pill ${rv.verdict === "pass" ? "ok" : "warn"}`;
    $("#review").innerHTML = `<p>${esc(rv.summary)}</p>` + rv.issues.map((i) => `<div class="issue ${esc(i.severity)}"><b>${esc(i.title)}</b> — ${esc(i.detail)}${i.suggestion ? `<div class="meta">→ ${esc(i.suggestion)}</div>` : ""}</div>`).join("");
  }
  const bust = `?t=${info.created || Date.now()}`;
  $("#renders").innerHTML = d.renders.filter((r) => !r.endsWith("sheet.png") && !r.endsWith("plate_1.png")).map((r) => `<img src="/api/projects/${S.pid}/files/${r}${bust}" alt="${esc(r.split("/").pop())}">`).join("");
  $$("#renders img").forEach((img) => img.onclick = () => lightbox(img.src));
  await showModel();
}

async function showModel() {
  const d = S.iterData;
  if (!d?.manifest) return;
  if (!S.viewer) S.viewer = new Viewer($("#viewer"));
  const bed = S.health?.printer?.size || [256, 256];
  S.viewer.setBed(bed);
  const pose = $("input[name=pose]:checked").value;
  const colors = Object.fromEntries(S.project.spec.parts.map((p) => [p.name, p.color]));
  const parts = d.manifest.parts.map((p) => ({ name: p.name, color: colors[p.name], url: `/api/projects/${S.pid}/files/iterations/v${String(S.iter).padStart(3, "0")}/build/${pose === "print" ? p.stl : p.assembly_stl}?t=${d.info.created}` }));
  const size = await S.viewer.show(parts, { onBed: pose === "print" });
  $("#dims").textContent = `${size.map((v) => v.toFixed(1)).join(" × ")} mm`;
}

function lightbox(src) {
  const el = document.createElement("div");
  el.className = "lightbox"; el.innerHTML = `<img src="${src}">`;
  el.onclick = () => el.remove(); document.body.appendChild(el);
}

// ------------------------------------------------------------------ slice tab
function renderSliceForm() {
  const s = S.project.spec, pp = s.print, md = modeInfo(s.mode).slice;
  const opt = (vals, cur) => vals.map((v) => `<option ${String(v) === String(cur) ? "selected" : ""}>${esc(v)}</option>`).join("");
  $("#sliceForm").innerHTML = `
    <label>Printer</label><div>${esc(pp.printer)} · ${pp.nozzle} mm</div>
    <label>Process</label><div>${esc(pp.process || `${md.layer_height.toFixed(2)} mm ${md.quality} (${modeTitle(s.mode)})`)}</div>
    <label>Plate</label><select data-k="bed_type">${opt(["Textured PEI Plate", "Cool Plate", "Engineering Plate", "High Temp Plate", "Supertack Plate"], pp.bed_type)}</select>
    <label>Supports</label><select data-k="supports">${opt(["none", "auto", "tree"], pp.supports || md.supports)}</select>
    <label>Infill %</label><input data-k="infill_percent" type="number" min="0" max="100" value="${pp.infill_percent ?? md.infill_percent}">
    <label>Walls</label><input data-k="wall_loops" type="number" min="1" max="12" value="${pp.wall_loops ?? md.wall_loops}">
    <label>Brim</label><select data-k="brim">${opt(["auto", "none", "outer"], pp.brim || md.brim)}</select>
    <label>Layout</label><select data-k="layout">${opt(["separate", "assembled"], pp.layout)}</select>
    <div class="full meta">${s.parts.map((p) => `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${esc(p.color)};margin-right:4px"></span>${esc(p.name)}: ${esc(p.filament)}`).join(" · ")}</div>`;
  $$("#sliceForm [data-k]").forEach((el) => el.onchange = saveSliceForm);
}

async function saveSliceForm() {
  const spec = structuredClone(S.project.spec);
  $$("#sliceForm [data-k]").forEach((el) => {
    const k = el.dataset.k; let v = el.value;
    if (el.type === "number") v = v === "" ? null : Number(v);
    spec.print[k] = v;
  });
  const wasFrozen = spec.status === "frozen";
  await api(`/projects/${S.pid}/spec`, { method: "PUT", body: spec });
  if (wasFrozen) await api(`/projects/${S.pid}/freeze`, { method: "POST", body: { frozen: true, force: true } });
  await refreshProject();
}

function renderSlices() {
  const sl = [...S.project.slices].reverse();
  if (!sl.length) return;
  const s = sl[0], base = `slices/s${String(s.n).padStart(3, "0")}`;
  if (!s.ok) { $("#sliceOut").innerHTML = `<b>s${s.n}</b><pre class="err">${esc(s.error || "failed")}</pre>`; return; }
  const pi = (s.plates_info || [])[0] || {};
  const mins = Math.round((pi.print_seconds || 0) / 60);
  $("#sliceOut").innerHTML = `
    <div class="card-h"><b>s${String(s.n).padStart(3, "0")} · v${String(s.iteration).padStart(3, "0")}</b>${s.forced ? `<span class="pill bad">forced</span>` : `<span class="pill ok">OK</span>`}</div>
    <div class="kpis"><div class="kpi"><b>${Math.floor(mins / 60)}h ${mins % 60}m</b><span>${esc(t("printTime", "print time"))}</span></div>
      <div class="kpi"><b>${(pi.weight_g || 0).toFixed(1)} g</b><span>${esc(t("weight", "filament"))}</span></div>
      <div class="kpi"><b>${(pi.filaments || []).length}</b><span>filaments</span></div></div>
    <div class="meta">${(pi.filaments || []).map((f) => `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${esc(f.color)};margin-right:4px"></span>${esc(f.type)} ${f.grams.toFixed(1)} g`).join(" · ")}</div>
    ${(s.warnings || []).map((w) => `<div class="err">${esc(w)}</div>`).join("")}
    <img class="thumb" src="/api/projects/${S.pid}/files/${base}/plate_1.png?t=${s.created}" onerror="this.remove()">
    <div class="row gap wrap">
      <button class="primary" data-file="${base}/${esc(s.files.sliced)}" data-app="bambu">${esc(t("openBambu", "Open in Bambu Studio"))}</button>
      ${s.files.project ? `<button class="ghost" data-file="${base}/${esc(s.files.project)}" data-app="bambu">${esc(t("openProject", "Open project (unsliced)"))}</button>` : ""}
      <button class="ghost" data-file="${base}/${esc(s.files.sliced)}" data-app="reveal">${esc(t("reveal", "Show files"))}</button>
    </div>`;
  $$("#sliceOut [data-app]").forEach((b) => b.onclick = () => openApp(b.dataset.app, b.dataset.file));
}

async function openApp(app, path) {
  try { await api(`/projects/${S.pid}/open`, { method: "POST", body: { app, path } }); }
  catch (e) { alert(e.message); }
}

// ------------------------------------------------------------------ wiring
function showTab(tab) {
  S.tab = tab;
  if (S.pid) history.replaceState(null, "", `#${S.pid}/${tab}`);
  $$("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $$(".tab").forEach((el) => el.hidden = el.id !== `tab-${tab}`);
  if (tab === "model" || tab === "slice") loadIteration();
}

$$("#tabs button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
$("#langBtn").onclick = () => { lang = lang === "zh" ? "en" : "zh"; localStorage.setItem("pw-lang", lang); applyI18n(); if (S.project) renderProject(); loadProjects(); };
$("#newForm").onsubmit = async (e) => {
  e.preventDefault();
  const p = await api("/projects", { method: "POST", body: { title: $("#newTitle").value, mode: $("#newMode").value } });
  $("#newTitle").value = ""; S.tab = "req"; openProject(p.id);
};
$("#exBtn").onclick = async () => {
  const name = $("#exSel").value; if (!name) return;
  const job = await api("/examples", { method: "POST", body: { name } });
  $("#exBtn").disabled = true;
  const poll = async () => {
    const j = await api(`/jobs/${job.id}`);
    if (j.status === "queued" || j.status === "running") return setTimeout(poll, 800);
    $("#exBtn").disabled = false;
    if (j.status === "error") return alert(j.error);
    S.tab = "model"; await openProject(j.result.project);
  };
  poll();
};
$("#chatForm").onsubmit = (e) => {
  e.preventDefault();
  const m = $("#chatIn").value.trim(); if (!m) return;
  S.project.chat.push({ role: "user", content: m }); renderProject();
  $("#chatIn").value = "";
  runJob(t("s1", "Requirements"), `/projects/${S.pid}/chat`, { message: m });
};
$("#chatIn").onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#chatForm").requestSubmit(); };
$("#freezeBtn").onclick = async () => { try { await api(`/projects/${S.pid}/freeze`, { method: "POST", body: { frozen: true } }); await refreshProject(); loadProjects(); } catch (e) { alert(e.message); } };
$("#unfreezeBtn").onclick = async () => { await api(`/projects/${S.pid}/freeze`, { method: "POST", body: { frozen: false } }); await refreshProject(); loadProjects(); };
$("#saveSpec").onclick = async () => {
  $("#specErr").textContent = "";
  try { await api(`/projects/${S.pid}/spec`, { method: "PUT", body: JSON.parse($("#specJson").value) }); await refreshProject(); }
  catch (e) { $("#specErr").textContent = e.message; }
};
$("#genBtn").onclick = () => runJob(t("s3", "Model"), `/projects/${S.pid}/generate`, {});
$("#reviseBtn").onclick = () => { const f = $("#feedback").value.trim(); runJob(t("revise", "Revise"), `/projects/${S.pid}/generate`, { feedback: f || null }); $("#feedback").value = ""; };
$("#applyReview").onclick = () => {
  const rv = S.iterData?.review; if (!rv) return;
  const fb = "Address these review findings:\n" + rv.issues.map((i) => `- ${i.title}: ${i.detail} ${i.suggestion || ""}`).join("\n");
  runJob(t("revise", "Revise"), `/projects/${S.pid}/generate`, { feedback: fb });
};
$("#rebuildBtn").onclick = () => runJob(t("rebuild", "Rebuild"), `/projects/${S.pid}/iterations/${S.iter}/rebuild`, { code: $("#code").value });
$("#iterSel").onchange = (e) => { S.iter = Number(e.target.value); loadIteration(); };
$$("input[name=pose]").forEach((r) => r.onchange = showModel);
$("#sliceBtn").onclick = () => runJob(t("s4", "Slice"), `/projects/${S.pid}/slice`, { iteration: S.iter, force: $("#forceSlice").checked });
$$("[data-open]").forEach((b) => b.onclick = () => {
  const d = S.iterData; if (!d) return;
  const base = `iterations/v${String(S.iter).padStart(3, "0")}`;
  const app = b.dataset.open;
  let path = base;
  if (app === "fusion") path = d.manifest?.parts?.some((p) => p.step) ? `${base}/build/assembly.step` : `${base}/build/${d.manifest.parts[0].stl}`;
  if (app === "blender") path = d.blend ? `${base}/renders/scene.blend` : `${base}/build/${d.manifest.parts[0].stl}`;
  if (app === "reveal") path = `${base}/build/manifest.json`;
  openApp(app, path);
});
$("#settingsBtn").onclick = async () => {
  const s = await api("/settings");
  const fields = [
    ["provider", ["claude-code", "openai", "anthropic"]], ["claude_code_model", null], ["claude_code_effort", ["", "low", "medium", "high", "xhigh", "max"]],
    ["claude_code_max_budget_usd", null], ["openai_model", null], ["openai_reasoning_effort", ["", "low", "medium", "high"]],
    ["anthropic_model", null], ["anthropic_effort", ["low", "medium", "high", "xhigh", "max"]],
    ["cad_backend", ["cadquery", "organic", "openscad"]], ["max_repair_attempts", null], ["vision_review", ["true", "false"]],
    ["renderer", ["auto", "blender", "matplotlib"]], ["bambu_studio_path", null], ["blender_path", null],
  ];
  $("#settingsFields").innerHTML = fields.map(([k, opts]) => `<label>${k}</label>${opts
    ? `<select data-s="${k}">${opts.map((o) => `<option ${String(s[k] ?? "") === o ? "selected" : ""}>${o}</option>`).join("")}</select>`
    : `<input data-s="${k}" value="${esc(s[k] ?? "")}">`}`).join("");
  $("#doctor").textContent = JSON.stringify(S.health.doctor, null, 2);
  $("#settingsDlg").showModal();
};
$("#saveSettings").onclick = async (e) => {
  e.preventDefault();
  const body = {};
  $$("[data-s]").forEach((el) => {
    let v = el.value;
    if (v === "") v = null; else if (v === "true" || v === "false") v = v === "true"; else if (/^\d+(\.\d+)?$/.test(v) && el.dataset.s !== "claude_code_model") v = Number(v);
    body[el.dataset.s] = v;
  });
  try { await api("/settings", { method: "PUT", body }); S.health = await api("/health"); renderStatus(); $("#settingsDlg").close(); }
  catch (err) { alert(err.message); }
};

boot().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<pre class="err">${esc(e.stack || e)}</pre>`); });
