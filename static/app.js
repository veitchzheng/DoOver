/* DoOver 错题本 · 前端单页应用 */
"use strict";

/* ================= 基础工具 ================= */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const SUBJ = {
  math:    { label: "数学", color: "#FF8A3D" },
  chinese: { label: "语文", color: "#4C8DFF" },
  english: { label: "英语", color: "#22B573" },
  other:   { label: "其他", color: "#9B6BFF" },
};
const REASON = {
  concept: "概念不会", careless: "审题马虎", calc: "计算失误", clueless: "完全没思路",
};

const state = {
  meta: null, terms: [], llm: null,
  today: null, tIdx: 0, parentJudge: false,
  items: [], iPage: 1, iTotal: 0, iMore: false,
  filters: { subject: "all", status: "reviewing", term: null, kp_id: null, q: "" },
  add: { mode: "photo", photo: null, counter: 0, subject: "math", reason: "careless",
         wSubject: "chinese", wReason: "concept", kpSel: {} },
  stats: null,
  me: null, gloryAll: false,
};

/* ================= API 封装 ================= */
async function api(path, opts = {}) {
  opts.headers = opts.headers || {};
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  if (res.status === 401) { showLogin(); throw new Error("未登录"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "请求失败");
  return data;
}

/* ================= 提示与弹层 ================= */
let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2200);
}
function openModal(html) {
  $("#modalBody").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
function photoModal(photoId) {
  openModal(`<div class="photo-view" data-act="closeModal" title="点击任意处关闭">
    <img src="/photos/${photoId}" alt="题目照片"></div>`);
}
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
function confirmModal(text, onOk) {
  openModal(`<h3>确认操作</h3><p class="confirm-text">${text}</p>
    <div class="m-acts"><button class="btn btn-ghost" onclick="closeModal()">再想想</button>
    <button class="btn btn-brand" id="cfOk">确定</button></div>`);
  $("#cfOk").onclick = () => { closeModal(); onOk(); };
}

/* ================= 小怪兽 SVG ================= */
function monsterSVG(color, mood = "happy", size = 90) {
  const eyes = mood === "sleepy"
    ? `<path d="M31 46 q7 6 14 0" stroke="#2A2320" stroke-width="3.5" fill="none" stroke-linecap="round"/>
       <path d="M55 46 q7 6 14 0" stroke="#2A2320" stroke-width="3.5" fill="none" stroke-linecap="round"/>
       <text x="72" y="30" font-size="13" font-weight="900" fill="${color}">z</text>
       <text x="84" y="16" font-size="9" font-weight="900" fill="${color}">z</text>`
    : `<circle cx="38" cy="45" r="9" fill="#fff"/><circle cx="63" cy="45" r="9" fill="#fff"/>
       <circle cx="38" cy="46" r="4.2" fill="#2A2320"/><circle cx="63" cy="46" r="4.2" fill="#2A2320"/>`;
  const mouth = mood === "cheer"
    ? `<path d="M38 60 Q51 74 64 60 Q51 68 38 60Z" fill="#2A2320"/>
       <path d="M10 34 l-6 -8 M90 34 l6 -8" stroke="${color}" stroke-width="5" stroke-linecap="round"/>`
    : `<path d="M40 61 Q50 69 60 61" stroke="#2A2320" stroke-width="3.5" fill="none" stroke-linecap="round"/>`;
  return `<svg viewBox="0 0 100 100" width="${size}" height="${size}">
    <path d="M27 28 L17 8 L39 20 Z" fill="${color}"/><path d="M73 28 L83 8 L61 20 Z" fill="${color}"/>
    <path d="M50 16 C75 16 88 36 88 58 C88 79 72 90 50 90 C28 90 12 79 12 58 C12 36 25 16 50 16 Z" fill="${color}"/>
    <ellipse cx="50" cy="72" rx="21" ry="14" fill="rgba(255,255,255,.28)"/>
    ${eyes}${mouth}</svg>`;
}
function medalSVG(color, locked) {
  const c = locked ? "#C9BCA9" : color;
  return `<svg viewBox="0 0 60 60" width="52" height="52">
    <circle cx="30" cy="30" r="25" fill="${locked ? "#F1E9DB" : "#FFF6DF"}" stroke="${c}" stroke-width="4"/>
    <path d="M30 14 l4.7 9.8 10.6 1.4 -7.8 7.4 2 10.5 -9.5 -5.2 -9.5 5.2 2 -10.5 -7.8 -7.4 10.6 -1.4 Z"
      fill="${c}"/></svg>`;
}
function flyStar(x, y, n = 1) {
  for (let i = 0; i < n; i++) {
    const s = document.createElement("div");
    s.className = "star-fly";
    s.textContent = "⭐";
    s.style.left = (x - 8 + (Math.random() * 44 - 22)) + "px";
    s.style.top = (y - 10) + "px";
    s.style.animationDelay = (i * 0.12) + "s";
    document.body.appendChild(s);
    setTimeout(() => s.remove(), 1300);
  }
}

/* ================= 启动与路由 ================= */
async function boot() {
  try {
    state.meta = await api("/api/meta");
    state.role = state.meta.role;                 // 家长/孩子分端：孩子只看复习界面
    api("/api/llm/status").then(s => { state.llm = s; }).catch(() => {});
    hideLogin();
    if (!location.hash) location.hash = "#/today";
    route();
  } catch (e) { /* 已弹出登录 */ }
}
function showLogin() { $("#loginMask").classList.remove("hidden"); }
function hideLogin() { $("#loginMask").classList.add("hidden"); }

const PAGES = { today: renderToday, items: renderItems, add: renderAdd, stats: renderStats, me: renderMe };
const CHILD_ALLOWED = ["today", "me"];          // 孩子端仅今日复习 + 我的
function route() {
  const tab = (location.hash.replace("#/", "") || "today").split("?")[0];
  if (state.role === "child" && !CHILD_ALLOWED.includes(tab)) { location.hash = "#/today"; return; }
  if (tab !== "today") stopAiPoll();
  // 分端可见性：孩子隐藏 错题本/加题/统计
  $$(".tab").forEach(t => {
    const parentOnly = ["items", "add", "stats"].includes(t.dataset.tab);
    t.style.display = (state.role === "child" && parentOnly) ? "none" : "";
  });
  $$(".tab").forEach(t => t.classList.toggle("on", t.dataset.tab === tab));
  Object.entries(PAGES).forEach(([k, el]) =>
    $(`#page-${k}`).classList.toggle("hidden", k !== tab));
  (PAGES[tab] || renderToday)();
}
window.addEventListener("hashchange", route);
$$(".tab").forEach(t => t.addEventListener("click", () => { location.hash = "#/" + t.dataset.tab; }));

async function tryLogin() {
  const code = $("#loginCode").value.trim();
  if (!code) return;
  try {
    await api("/api/login", { method: "POST", body: { code } });
    $("#loginErr").textContent = "";
    $("#loginCode").value = "";
    boot();
  } catch (e) {
    $("#loginErr").textContent = e.message;
  }
}
$("#loginBtn").addEventListener("click", tryLogin);
$("#loginCode").addEventListener("keydown", e => { if (e.key === "Enter") tryLogin(); });
$("#loginMonster").innerHTML = monsterSVG("#FF6B5B", "happy", 110);

function refreshTopRight() {
  const t = state.meta?.settings?.current_term || "";
  $("#topRight").textContent = t ? `${t} · v${state.meta.version}` : "";
}

/* ================= 今日页 ================= */
async function renderToday() {
  refreshTopRight();
  const box = $("#page-today");
  let data;
  try { data = await api("/api/today"); } catch (e) { return; }
  state.today = data;
  state.tIdx = 0;
  state.parentJudge = false;

  const n = data.items.length;
  const subj = it => SUBJ[it.subject]?.color || "#888";
  const head = `
    <div class="today-hero">
      <div class="mascot">${monsterSVG(n ? "#FF6B5B" : "#B8AA9A", n ? "happy" : "sleepy")}</div>
      <div><h2>${n ? `今天要消灭 <span class="num">${n}</span> 只小怪兽` : "今天没有任务"}</h2>
        <div class="sub">${n ? "纸上演算，想好了再翻面对答案" : "去玩吧，该玩就玩"}</div></div>
    </div>
    <div class="today-meta">
      <span class="pill">🔥 连续复习 <b>${data.streak}</b> 天</span>
      <span class="pill" id="pillStars"><span class="star-ico">★</span> 今日 +${data.stars_today}</span>
      ${data.total_due > n ? `<span class="pill" style="color:#9A6B00">还有 ${data.total_due - n} 只在排队</span>` : ""}
      ${data.pending_verify ? `<span class="pill" style="color:#D94A3B">待复核 ${data.pending_verify} 题</span>` : ""}
    </div>`;
  const banner = state.today?.challenges?.length
    ? `<div class="variant-banner" data-act="challengeList">⚔️ 还有 ${state.today.challenges.length} 只变式怪等你挑战！</div>` : "";
  if (n) {
    box.innerHTML = banner + head + `<div class="progress">
      <div class="bar"><i id="tBar" style="width:0%"></i></div>
      <div class="txt" id="tProg">${state.tIdx} / ${n}</div>
    </div>
    <div id="todayStage"></div>`;
    renderTodayCard();
    return;
  }
  box.innerHTML = banner + head + `
    <div class="card empty-state">
      <div class="mascot">${monsterSVG("#B8AA9A", "sleepy", 120)}</div>
      <h3>今天的小怪兽都消灭啦</h3>
      <p>到期了才会出现在这里，不用提前刷题</p>
      ${state.today?.challenges?.length ? `<p style="margin-top:10px"><button class="btn btn-brand btn-sm" data-act="challengeList">⚔️ 去挑战变式怪</button></p>` : ""}
    </div>`;
}

function itemFront(it) {
  const subj = SUBJ[it.subject];
  const head = `
    <div class="qhead">
      <span class="subj-badge sj-${it.subject}">${subj.label}</span>
      ${it.kp_name ? `<span class="kp-badge">${esc(it.kp_name)}</span>` : ""}
      ${it.stubborn ? `<span class="stubborn-badge">顽固怪</span>` : ""}
      ${it.stage ? `<span class="stage-dots">${[0,1,2,3,4].map(i =>
        `<i class="${i < it.stage ? "on" : ""}"></i>`).join("")}</span>` : ""}
    </div>`;
  if (it.item_type === "word") {
    return head + `
      <div class="qbody">
        <div class="qword-prompt">${esc(it.prompt || "")}</div>
        <div class="qword-sub">${it.subject === "english" ? "看中文提示，在纸上写出英文单词"
          : it.subject === "chinese" ? "看拼音提示，在纸上写出汉字" : "看提示，写一写"}</div>
      </div>
      ${hintBoxHTML()}
      <div class="qfooter">${hintBtnHTML()}<button class="btn btn-brand btn-xl" data-act="flip">我想好了，翻面对答案</button></div>`;
  }
  const pid = it.photo_ids?.[0];
  return head + `
    <div class="qbody"><img class="qphoto" src="/photos/${pid}" alt="题目照片"></div>
    ${hintBoxHTML()}
    <div class="qfooter">${hintBtnHTML()}<button class="btn btn-brand btn-xl" data-act="flip">我想好了，翻面对答案</button></div>`;
}

/* ----- 渐进式提示（翻面前的思考引导） ----- */
function hintsAvailable() {
  return state.meta?.settings?.ai_hint_enabled !== false && state.llm?.configured;
}
function hintBtnHTML() {
  return hintsAvailable() ? `<button class="btn btn-ghost hint-btn" data-act="hint">💡 卡住了？给点提示</button>` : "";
}
function hintBoxHTML() {
  return hintsAvailable() ? `<div id="hintBox" class="ai-box hidden"></div>` : "";
}

let hintState = { idx: 0, list: null, loading: false };

async function showHint(btnEl) {
  const it = state.today.items[state.tIdx];
  if (!it || hintState.loading) return;
  if (!hintState.list) {
    let cached = null;
    try { cached = JSON.parse(it.ai_hints || "null"); } catch (e) { /* 忽略坏缓存 */ }
    if (!Array.isArray(cached) || !cached.length) {
      // 没有缓存提示：现场生成（首次点击才付费）
      hintState.loading = true;
      const old = btnEl ? btnEl.textContent : "";
      if (btnEl) { btnEl.textContent = "🤖 想一下，提示马上来…"; btnEl.disabled = true; }
      try {
        const r = await api(`/api/items/${it.id}/hints`, { method: "POST" });
        cached = r.hints;
        it.ai_hints = JSON.stringify(r.hints);
      } catch (e) {
        toast(e.message);
        if (btnEl) { btnEl.textContent = old; btnEl.disabled = false; }
        hintState.loading = false;
        return;
      }
      hintState.loading = false;
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = old; }
    }
    hintState.list = cached;
  }
  if (!hintState.list || !hintState.list.length) { toast("这道题暂无提示"); return; }
  hintState.idx = Math.min(hintState.idx + 1, hintState.list.length);
  const box = $("#hintBox");
  if (box) {
    box.classList.remove("hidden");
    box.innerHTML = hintState.list.slice(0, hintState.idx).map((h, n) =>
      `<div class="ai-row"><span class="ai-k">💡 提示${n + 1}</span><span>${esc(h)}</span></div>`).join("")
      + `<div class="ai-src">用了提示，判定时记得如实选「提示后才对」哦</div>`;
  }
  if (hintState.idx >= hintState.list.length && btnEl) btnEl.classList.add("hidden");
  else if (btnEl) btnEl.textContent = "💡 再看一条提示";
}

function itemBack(it) {
  const subj = SUBJ[it.subject];
  const head = `
    <div class="qhead">
      <span class="subj-badge sj-${it.subject}">${subj.label}</span>
      ${it.reason_label ? `<span class="reason-badge">错因：${esc(it.reason_label)}</span>` : ""}
      ${it.wrong_count ? `<span class="kp-badge">已错 ${it.wrong_count} 次</span>` : ""}
    </div>`;
  let body = "";
  if (it.item_type === "word") {
    body = `<div class="qbody">
      <div class="word-check">
        <span class="w wrong">${esc(it.wrong_text || "")}</span>
        <span class="arrow">→</span>
        <span class="w right">${esc(it.correct_text || "")}</span>
      </div>
      ${it.solution ? `<p style="text-align:center;color:var(--ink2);font-size:13px">${esc(it.solution)}</p>` : ""}
      ${aiBlockHTML(it)}
    </div>`;
  } else {
    const pid = it.photo_ids?.[0];
    body = `<div class="qbody">
      ${pid ? `<img class="qphoto" src="/photos/${pid}" alt="题目照片">` : ""}
      ${it.wrong_answer ? `<p style="margin-top:10px;font-size:14px">✏️ 当时错在：<b>${esc(it.wrong_answer)}</b></p>` : ""}
      ${it.solution ? `<p style="margin-top:8px;font-size:14px">✅ 正确做法：<b>${esc(it.solution)}</b></p>` : ""}
      ${(!it.solution && !it.wrong_answer) ? `<div class="parent-need">这题没有录入答案，请家长拿原卷对照判定</div>` : ""}
      ${aiBlockHTML(it)}
    </div>`;
  }
  const pv = state.meta.settings.verify_enabled;
  return head + body + `
    <div class="qfooter">
      <div class="judge-row">
        <button class="btn btn-pass" data-act="judge" data-r="pass">我自己做对了</button>
        <button class="btn btn-hint" data-act="judge" data-r="hint">提示后才对</button>
        <button class="btn btn-fail" data-act="judge" data-r="fail">又做错了</button>
      </div>
      <div class="judge-note">
        ${pv ? `<label class="toggle"><input type="checkbox" id="pjTog" ${state.parentJudge ? "checked" : ""}>
          <span class="tk"></span>家长在旁边，由家长判定</label>` : "<span></span>"}
        <span style="font-size:12px;color:var(--ink2)">对一题得 1 颗星，消灭得 5 颗</span>
      </div>
    </div>`;
}

function renderTodayCard() {
  const it = state.today.items[state.tIdx];
  const n = state.today.items.length;
  if (!it) { renderTodayDone(); return; }
  $("#tBar").style.width = Math.round(state.tIdx * 100 / n) + "%";
  $("#tProg").textContent = `${state.tIdx} / ${n}`;
  $("#todayStage").innerHTML = `
    <div class="qcard" id="qcard">
      <div class="qface front" id="faceF">${itemFront(it)}</div>
      <div class="qface back hidden" id="faceB">${itemBack(it)}</div>
    </div>`;
  $("#pjTog")?.addEventListener("change", e => { state.parentJudge = e.target.checked; });
  hintState = { idx: 0, list: null, loading: false };
  try {
    const cached = JSON.parse(it.ai_hints || "null");
    if (Array.isArray(cached)) hintState.list = cached;
  } catch (e) { /* 忽略坏缓存 */ }
  watchAiPending(it);
}

function renderTodayDone() {
  stopAiPoll();
  const n = state.today.items.length;
  $("#tBar").style.width = "100%";
  $("#tProg").textContent = `${n} / ${n}`;
  const chs = state.today.challenges || [];
  const challengeRow = chs.length
    ? `<p style="margin-top:14px"><b>⚔️ 变式挑战</b><br>
        <span style="font-size:12px;color:var(--ink2)">消灭的题可以再战同类怪，答对 +2 颗星</span></p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:8px">
        ${chs.map(c => `<button class="btn btn-ghost btn-sm" data-variant="${c.item_id}">
          ⚔️ ${SUBJ[c.subject]?.label}${c.kp_name ? " · " + esc(c.kp_name) : ""}</button>`).join("")}</div>` : "";
  $("#todayStage").innerHTML = `
    <div class="card empty-state">
      <div class="mascot">${monsterSVG("#22B573", "cheer", 130)}</div>
      <h3>今天的小怪兽都消灭啦！</h3>
      <p>今日共得 ${state.today.stars_today} 颗星，明天有新的到期题再来</p>
      ${challengeRow}
      <p style="margin-top:14px"><a class="btn btn-ghost" href="#/me">去我的页面看星星</a></p>
    </div>`;
  $$("#todayStage [data-variant]").forEach(b =>
    b.addEventListener("click", () => openVariantChallenge(+b.dataset.variant)));
}

function challengeListModal() {
  const chs = state.today.challenges || [];
  if (!chs.length) return;
  openModal(`<h3>⚔️ 变式挑战</h3>
    <p class="confirm-text">打败过的怪兽可以再战同类怪，答对 +2 颗星，做错也不影响复习计划</p>
    ${chs.map(c => `<div class="ch-row">
      <span>${SUBJ[c.subject]?.label}${c.kp_name ? " · " + esc(c.kp_name) : ""}</span>
      <button class="btn btn-brand btn-sm" data-cgo="${c.item_id}">挑战</button>
    </div>`).join("")}
    <div class="m-acts"><button class="btn btn-ghost" data-act="closeModal">关闭</button></div>`);
  $$("#modalBody [data-cgo]").forEach(b =>
    b.addEventListener("click", () => openVariantChallenge(+b.dataset.cgo)));
}

async function judge(result, btnEl) {
  const it = state.today.items[state.tIdx];
  if (!it) return;
  const judged_by = state.parentJudge ? "parent" : "child";
  try {
    const res = await api(`/api/items/${it.id}/review`, { method: "POST", body: { result, judged_by } });
    let stars = (res.stars || []).reduce((a, s) => a + s[1], 0);
    if (result === "pass") {
      flyStar(btnEl.getBoundingClientRect().left, btnEl.getBoundingClientRect().top);
      if (res.item.status === "mastered") {
        toast(it.stubborn || it.wrong_count >= 3
          ? `顽固怪逆袭消灭！+${stars} 颗星 🏆` : `消灭了它！+${stars} 颗星 🎉`);
      } else {
        toast(`做对了，+1 颗星 ⭐ 进入第 ${res.item.stage} 档`);
      }
    } else if (result === "hint") {
      toast("提示后才对，明天再考一次这题");
    } else {
      toast("没关系，错题回炉，明天再战一次");
    }
    if (res.day_bonus) setTimeout(() => { flyStar(innerWidth / 2, innerHeight / 2, 5); toast("今日全对奖励 +2 颗星！"); }, 600);
    state.today.stars_today += stars + (res.day_bonus ? 2 : 0);
    const ps = $("#pillStars");
    if (ps) ps.innerHTML = `<span class="star-ico">★</span> 今日 +${state.today.stars_today}`;
    const justMastered = res.item.status === "mastered" && it.status !== "mastered";
    Object.assign(it, res.item);   // 同步最新状态供完成页使用
    state.tIdx++;
    if (state.tIdx >= state.today.items.length) renderTodayDone();
    else renderTodayCard();
    if (justMastered && state.llm?.configured) openMasteredBonus(it, stars);
  } catch (e) { toast(e.message); }
}

/* ----- 变式题（消灭彩蛋）：再战同类怪 ----- */
function openMasteredBonus(it, stars) {
  openModal(`<h3>🎉 消灭了这只小怪兽！</h3>
    <div style="text-align:center;margin:6px 0">${monsterSVG("#22B573", "cheer", 100)}</div>
    <p class="confirm-text" style="text-align:center">这只怪兽被彻底打败啦，+${stars} 颗星入账！<br>
      要不要<b>再战一只同类怪兽</b>，试试举一反三？<br>
      <span style="font-size:12px;color:var(--ink2)">答对得 2 颗星；做错也不影响复习计划</span></p>
    <div class="m-acts">
      <button class="btn btn-ghost" data-act="closeModal">继续复习</button>
      <button class="btn btn-brand" id="variantGo">⚔️ 再战同类怪</button>
    </div>`);
  $("#variantGo").onclick = () => openVariantChallenge(it.id);
}

async function openVariantChallenge(itemId) {
  let data;
  try { data = await api(`/api/items/${itemId}/variant`); }
  catch (e) { toast(e.message); return; }
  if (!data.exists) {
    closeModal();
    toast("⚔️ 变式怪还在生成中，稍后再来挑战");
    return;
  }
  if (data.locked) {
    closeModal();
    toast(data.result ? "这只变式怪你已经挑战过啦" : "这道变式题等家长审阅后开放挑战");
    return;
  }
  const v = data.variant;
  openModal(`<h3>⚔️ 变式挑战 · 举一反三</h3>
    <div class="ai-box"><div class="ai-row"><span class="ai-k">题目</span><span>${esc(v.question)}</span></div></div>
    <div class="m-acts" id="vcStage1">
      <button class="btn btn-ghost" data-act="closeModal">先不挑战了</button>
      <button class="btn btn-brand" id="vcReveal">我想好了，看答案</button>
    </div>
    <div id="vcAnswer" class="hidden">
      <div class="ai-box" style="margin-top:6px">
        <div class="ai-row"><span class="ai-k">答案</span><span>${esc(v.answer)}</span></div>
      </div>
      <p class="confirm-text" style="text-align:center">对照答案，如实判定</p>
      <div class="m-acts">
        <button class="btn btn-pass" id="vcPass">我做对了 +2★</button>
        <button class="btn btn-fail" id="vcFail">没做对</button>
      </div>
    </div>`);
  $("#vcReveal").onclick = () => {
    $("#vcStage1").classList.add("hidden");
    $("#vcAnswer").classList.remove("hidden");
  };
  const doJudge = async result => {
    try {
      const r = await api(`/api/variants/${v.id}/judge`, { method: "POST", body: { result } });
      closeModal();
      if (state.today?.challenges) {   // 挑战完毕：即时更新待挑战列表与横幅
        state.today.challenges = state.today.challenges.filter(c => c.item_id !== itemId);
        const b = document.querySelector(".variant-banner");
        if (b) {
          if (state.today.challenges.length)
            b.textContent = `⚔️ 还有 ${state.today.challenges.length} 只变式怪等你挑战！`;
          else b.remove();
        }
      }
      if (result === "pass") {
        flyStar(innerWidth / 2, innerHeight / 3, 2);
        if (state.today) {
          state.today.stars_today += r.stars;
          const ps = $("#pillStars");
          if (ps) ps.innerHTML = `<span class="star-ico">★</span> 今日 +${state.today.stars_today}`;
        }
        toast("变式挑战成功！+2 颗星 ⚔️");
      } else {
        toast("没关系，这只怪兽回去再练练，不影响复习计划");
      }
    } catch (e) { toast(e.message); }
  };
  $("#vcPass").onclick = () => doJudge("pass");
  $("#vcFail").onclick = () => doJudge("fail");
}

/* ----- AI 错题分析：展示与轮询 ----- */
function aiBlockHTML(it, opts = {}) {
  if (it.ai_status === "done" && (it.ai_points || it.ai_pitfalls)) {
    return `<div class="ai-box">
      <div class="ai-row"><span class="ai-k">🎯 考点</span><span>${esc(it.ai_points || "—")}</span></div>
      ${it.ai_pitfalls ? `<div class="ai-row"><span class="ai-k">⚠️ 易错点</span><span>${esc(it.ai_pitfalls)}</span></div>` : ""}
      ${it.ai_model ? `<div class="ai-src">🤖 ${esc(it.ai_model)} 分析</div>` : ""}
    </div>`;
  }
  if (it.ai_status === "pending")
    return `<div class="ai-box ai-pending">🤖 AI 正在分析这道题的考点和易错点…</div>`;
  if (opts.showFail && it.ai_status === "failed")
    return `<div class="ai-box ai-fail">🤖 上次分析没成功：${esc(it.ai_error || "未知原因")}</div>`;
  return "";
}

let aiPollTimer = null;
function stopAiPoll() {
  if (aiPollTimer) { clearInterval(aiPollTimer); aiPollTimer = null; }
}
function watchAiPending(it) {
  stopAiPoll();
  if (it.ai_status !== "pending") return;
  const id = it.id;
  aiPollTimer = setInterval(async () => {
    try {
      Object.assign(it, await api(`/api/items/${id}`));
    } catch (e) { stopAiPoll(); return; }
    if (it.ai_status !== "pending") {
      stopAiPoll();
      const fb = $("#faceB");
      if (fb && !fb.classList.contains("hidden")) fb.innerHTML = itemBack(it);
    }
  }, 4000);
}

/* 全局事件代理（动态内容） */
document.addEventListener("click", e => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const act = el.dataset.act;
  if (act === "flip") {  // 原地换面：隐藏题目面，淡入答案面
    $("#faceF").classList.add("hidden");
    $("#faceB").classList.remove("hidden");
    return;
  }
  if (act === "judge") { judge(el.dataset.r, el); return; }
  if (act === "hint") { showHint(el); return; }
  if (act === "challengeList") { challengeListModal(); return; }
  if (act === "closeModal") { closeModal(); return; }
});

/* ================= 错题本页 ================= */
async function renderItems() {
  refreshTopRight();
  const box = $("#page-items");
  const f = state.filters;
  if (!state.terms.length) {
    try { state.terms = (await api("/api/stats")).terms; } catch (e) {}
  }
  if (!f.term) f.term = state.meta.settings.current_term;
  const kpOpts = kpsFor(f.subject === "all" ? "" : f.subject);
  box.innerHTML = `
    <div class="filter-bar">
      <div class="chip-row" id="fSubj">
        ${["all", "math", "chinese", "english", "other"].map(s =>
          `<button class="chip c-${s} ${f.subject === s ? "on" : ""}" data-s="${s}">
           ${s === "all" ? "全部" : SUBJ[s].label}</button>`).join("")}
      </div>
      <div class="chip-row" id="fStatus">
        ${[["reviewing", "复习中"], ["stubborn", "顽固题"], ["paused", "已暂停"],
           ["mastered", "已消灭"], ["all", "全部"]].map(([k, l]) =>
          `<button class="chip ${f.status === k ? "on" : ""}" data-st="${k}">${l}</button>`).join("")}
      </div>
      <div class="search-row">
        <input id="fQ" placeholder="搜索题目 / 错字 / 做法…" value="${esc(f.q)}">
        <button class="btn btn-brand btn-sm" data-act="export" data-tpl="practice" title="周五打印，周末纸质重练">重练卷</button>
        <button class="btn btn-ghost btn-sm" data-act="export" data-tpl="collection" title="归档留存 / 给老师看">收藏本</button>
      </div>
      <div class="search-row">
        <select class="input" id="fTerm" style="flex:1">
          ${state.terms.map(t => `<option value="${esc(t)}" ${f.term === t ? "selected" : ""}>${esc(t)}</option>`).join("")}
          <option value="all" ${f.term === "all" ? "selected" : ""}>全部学期</option>
        </select>
        <select class="input" id="fKp" style="flex:1.4">
          <option value="">知识点：全部</option>
          ${kpOpts.map(k => `<option value="${k.id}" ${f.kp_id == k.id ? "selected" : ""}>${esc(k.name)}</option>`).join("")}
        </select>
      </div>
    </div>
    <div id="itemsList"></div>`;

  $("#fSubj").addEventListener("click", e => {
    const c = e.target.closest(".chip"); if (!c) return;
    f.subject = c.dataset.s; renderItems();
  });
  $("#fStatus").addEventListener("click", e => {
    const c = e.target.closest(".chip"); if (!c) return;
    f.status = c.dataset.st; renderItems();
  });
  $("#fQ").addEventListener("change", e => { f.q = e.target.value.trim(); loadItems(true); });
  $("#fTerm").addEventListener("change", e => { f.term = e.target.value; loadItems(true); });
  $("#fKp").addEventListener("change", e => { f.kp_id = e.target.value || null; loadItems(true); });
  loadItems(true);
}

function kpsFor(subject) {
  const kps = state.meta.kps;
  return subject ? kps.filter(k => k.subject === subject) : kps;
}

function itemsQuery() {
  const f = state.filters;
  const p = new URLSearchParams();
  if (f.subject !== "all") p.set("subject", f.subject);
  if (f.status === "stubborn") { p.set("status", "reviewing"); p.set("stubborn", 1); }
  else if (f.status !== "all") p.set("status", f.status);
  if (f.term) p.set("term", f.term);
  if (f.kp_id) p.set("kp_id", f.kp_id);
  if (f.q) p.set("q", f.q);
  return p;
}

async function loadItems(reset) {
  const f = state.filters;
  if (reset) { state.iPage = 1; state.items = []; state.iMore = true; }
  if (!state.iMore) return;
  const p = itemsQuery();
  p.set("page", state.iPage);
  try {
    const res = await api("/api/items?" + p.toString());
    state.items.push(...res.items);
    state.iTotal = res.total;
    state.iMore = state.items.length < res.total;
    state.iPage++;
  } catch (e) { toast(e.message); return; }
  const list = $("#itemsList");
  list.innerHTML = `
    <p style="font-size:13px;color:var(--ink2);margin:2px 0 10px">共 ${state.iTotal} 道题</p>
    ${state.items.map(itemRowHTML).join("") || `<div class="card empty-state">
      <h3>没有符合条件的错题</h3><p>换一组筛选条件试试</p></div>`}
    ${state.iMore ? `<button class="btn btn-ghost btn-xl" data-act="more">加载更多</button>` : ""}`;
  list.querySelector("[data-act=more]")?.addEventListener("click", () => loadItems(false));
}

function itemRowHTML(it) {
  const thumb = it.item_type === "photo" && it.photo_ids?.length
    ? `<img src="/photos/${it.photo_ids[0]}?size=thumb" alt="">`
    : `<span class="glyph">${esc((it.correct_text || it.prompt || "?").slice(0, 2))}</span>`;
  const ops = [];
  if (it.status === "reviewing") ops.push(`<button data-act="pause" data-id="${it.id}">暂停</button>`);
  if (it.status === "paused") ops.push(`<button data-act="resume" data-id="${it.id}">恢复</button>`);
  if (it.status === "mastered") ops.push(`<button data-act="retest" data-id="${it.id}">再考</button>`);
  ops.push(`<button data-act="edit" data-id="${it.id}">编辑</button>`);
  ops.push(`<button class="danger" data-act="del" data-id="${it.id}">删除</button>`);
  const stTxt = it.status === "mastered"
    ? `已消灭 · ${String(it.mastered_at || "").slice(0, 10)}`
    : it.status === "paused" ? "已暂停" : `下次 ${it.due_date || "-"}`;
  const preview = it.item_type === "word"
    ? `${esc(it.prompt || "")}：错写「${esc(it.wrong_text || "")}」 → 正确「${esc(it.correct_text || "")}」`
    : (it.wrong_answer ? `当时错在：${esc(it.wrong_answer)}` : (it.solution ? esc(it.solution) : "照片题，点开看原图"));
  return `<div class="item-card sj-${it.subject}" data-act="view" data-id="${it.id}">
    <div class="thumb">${thumb}</div>
    <div class="mid">
      <div class="l1">
        <span class="subj-badge sj-${it.subject}">${SUBJ[it.subject].label}</span>
        ${it.kp_name ? `<span class="kp-badge">${esc(it.kp_name)}</span>` : ""}
        ${it.reason_label ? `<span class="reason-badge">${esc(it.reason_label)}</span>` : ""}
        ${it.stubborn ? `<span class="stubborn-badge">顽固 ×${it.wrong_count}</span>` : ""}
      </div>
      <div class="l2">${preview}</div>
      <div class="l3">
        <span class="stage-dots">${[0,1,2,3,4].map(i =>
          `<i class="${i < it.stage ? "on" : ""}"></i>`).join("")}</span>
        <span>${it.stage}/5</span><span>${stTxt}</span>
      </div>
    </div>
    <div class="ops">${ops.join("")}</div>
  </div>`;
}

$("#page-items").addEventListener("click", async e => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const id = el.dataset.id;
  const act = el.dataset.act;
  if (e.target.closest(".ops") && ["pause", "resume", "retest", "del"].includes(act)) {
    e.stopPropagation();
    try {
      if (act === "del") {
        confirmModal("删除后这道错题和它的复习记录都会消失，确定吗？", async () => {
          await api(`/api/items/${id}`, { method: "DELETE" });
          toast("已删除"); renderItems();
        });
      } else {
        await api(`/api/items/${id}`, { method: "PATCH", body: { action: act } });
        toast({ pause: "已暂停，不再排期", resume: "已恢复复习", retest: "已排入明日再考" }[act]);
        renderItems();
      }
    } catch (err) { toast(err.message); }
    return;
  }
  if (act === "edit" || (act === "view" && el.classList.contains("item-card"))) {
    editItemModal(state.items.find(i => i.id == id));
  }
});

async function editItemModal(it) {
  if (!it) return;
  const kps = kpsFor(it.subject);
  // 变式题区（家长）：已消灭的题可查看/预审/重新生成变式题
  let varSection = "";
  let vd = null;
  if (state.role !== "child") {
    try { vd = await api(`/api/items/${it.id}/variant`); } catch (e) { vd = null; }
    if (vd && vd.exists) {
      const v = vd.variant;
      const stateBadge = v.status === "approved"
        ? '<span class="kp-badge">已放行</span>' : '<span class="reason-badge">待预审</span>';
      const usedBadge = v.used ? `<span class="kp-badge">已挑战${v.result === "pass" ? "✓" : "✗"}</span>` : "";
      varSection = `<div class="ai-box" style="gap:8px">
        <div class="ai-row"><span class="ai-k">⚔️ 变式题</span><span>${stateBadge}${usedBadge}</span></div>
        <div class="pv-lines"><div class="pv-q">${esc(v.question)}</div>
          <div>答案：${esc(v.answer)}</div></div>
        ${!v.used ? `<div style="display:flex;gap:6px;flex-wrap:wrap">
          ${v.status === "pending" ? '<button class="btn btn-brand btn-sm" id="vApprove">✓ 放行给孩子</button>' : ""}
          <button class="btn btn-ghost btn-sm" id="vRegen">↻ 重新生成</button>
          <button class="btn btn-ghost btn-sm" id="vReject">✗ 驳回</button></div>`
          : '<div class="ai-src">已挑战过，不可重玩</div>'}
      </div>`;
    } else if (it.status === "mastered" && state.llm?.configured) {
      varSection = `<div class="ai-box ai-none">⚔️ 变式题：尚未生成
        <button class="btn btn-ghost btn-sm" id="vGen" style="margin-left:8px">立即生成</button></div>`;
    }
  }
  // 原题预览：照片题显示原图，错字词显示三件套对照，编辑时心里有底
  const preview = it.item_type === "photo"
    ? (it.photo_ids?.length
        ? `<div class="edit-preview">
             <img src="/photos/${it.photo_ids[0]}" alt="原题照片" onclick="photoModal(${it.photo_ids[0]})">
             <div class="edit-preview-tip">点击图片放大查看</div>
           </div>`
        : `<div class="edit-preview empty">这张照片题没有图片（可能被误删过文件）</div>`)
    : `<div class="edit-preview word">
         <div class="wp">${esc(it.prompt || "")}</div>
         <div class="wline"><span class="w wrong">${esc(it.wrong_text || "")}</span>
           <span class="warrow">→</span>
           <span class="w right">${esc(it.correct_text || "")}</span></div>
       </div>`;
  // AI 归因建议：与当前错因/知识点不一致时显示，一键采用（AI 只提议，家长拍板）
  const chips = [];
  if (it.ai_reason_suggest && it.reason !== it.ai_reason_suggest)
    chips.push(`<span class="sug-chip">错因「${REASON[it.ai_reason_suggest] || it.ai_reason_suggest}」<b data-sug="reason" data-v="${it.ai_reason_suggest}">采用</b></span>`);
  if (it.ai_kp_suggest && it.ai_kp_suggest !== it.kp_name)
    chips.push(`<span class="sug-chip">知识点「${esc(it.ai_kp_suggest)}」<b data-sug="kp" data-v="${esc(it.ai_kp_suggest)}">采用</b></span>`);
  const sug = chips.length
    ? `<div class="ai-box ai-sug"><span class="ai-k">🤖 AI 建议</span>${chips.join("")}</div>` : "";
  // AI 分析区：已完成展示结果；失败/未分析且已配置模型时给操作按钮
  const ai = it.ai_status === "done"
    ? `<div class="ai-box">
        <div class="ai-row"><span class="ai-k">🎯 考点</span><span>${esc(it.ai_points || "—")}</span></div>
        ${it.ai_pitfalls ? `<div class="ai-row"><span class="ai-k">⚠️ 易错点</span><span>${esc(it.ai_pitfalls)}</span></div>` : ""}
        ${it.ai_model ? `<div class="ai-src">🤖 ${esc(it.ai_model)} 分析</div>` : ""}
      </div>`
    : it.ai_status === "pending"
      ? `<div class="ai-box ai-pending">🤖 AI 正在分析这道题，完成后重新打开可看结果</div>`
      : it.ai_status === "failed"
        ? `<div class="ai-box ai-fail">🤖 上次分析没成功：${esc(it.ai_error || "未知原因")}</div>`
        : `<div class="ai-box ai-none">这道题还没有 AI 分析</div>`;
  const aiBtn = it.ai_status !== "pending" && state.llm?.configured
    ? `<button class="btn btn-ghost btn-sm" id="eAiBtn" style="margin-top:8px">${it.ai_status === "done" || it.ai_status === "failed" ? "重新分析" : "🤖 AI 分析这题"}</button>`
    : "";
  openModal(`<h3>编辑错题</h3>${preview}${sug}${ai}${aiBtn}${varSection}
    <div class="field-label">科目</div>
    <select class="input" id="eSubj">${Object.entries(SUBJ).map(([k, v]) =>
      `<option value="${k}" ${it.subject === k ? "selected" : ""}>${v.label}</option>`).join("")}</select>
    <div class="field-label">知识点</div>
    <select class="input" id="eKp">
      <option value="">（不选）</option>
      ${kps.map(k => `<option value="${k.id}" ${it.kp_id == k.id ? "selected" : ""}>${esc(k.name)}</option>`).join("")}
    </select>
    <div class="field-label">错因</div>
    <select class="input" id="eReason">
      <option value="">（不选）</option>
      ${Object.entries(REASON).map(([k, v]) =>
        `<option value="${k}" ${it.reason === k ? "selected" : ""}>${v}</option>`).join("")}
    </select>
    ${it.item_type === "word" ? `
      <div class="field-label">提示项</div><input class="input" id="ePrompt" value="${esc(it.prompt)}">
      <div class="form-grid">
        <div><div class="field-label">孩子错写</div><input class="input" id="eWrong" value="${esc(it.wrong_text)}"></div>
        <div><div class="field-label">正确写法</div><input class="input" id="eCorrect" value="${esc(it.correct_text)}"></div>
      </div>` : `
      <div class="field-label">当时错在</div><input class="input" id="eWrongAnswer" value="${esc(it.wrong_answer)}">
      <div class="field-label">正确做法（导出收藏本时会印出来）</div><textarea class="input" id="eSolution">${esc(it.solution)}</textarea>`}
    <div class="m-acts">
      <button class="btn btn-ghost" data-act="closeModal">取消</button>
      <button class="btn btn-brand" id="eSave">保存</button>
    </div>`);
  const vReview = async (action, tip) => {
    try {
      await api(`/api/variants/${vd.variant.id}/review`, { method: "POST", body: { action } });
      toast(tip); editItemModal(it);
    } catch (e) { toast(e.message); }
  };
  $("#vApprove")?.addEventListener("click", () => vReview("approve", "已放行，孩子可以挑战了 ⚔️"));
  $("#vRegen")?.addEventListener("click", () => vReview("regenerate", "已重新生成，稍后刷新查看"));
  $("#vReject")?.addEventListener("click", () => vReview("reject", "已驳回该变式题"));
  $("#vGen")?.addEventListener("click", async () => {
    try {
      await api(`/api/items/${it.id}/variant/generate`, { method: "POST" });
      toast("⚔️ 变式怪生成中，稍后重新打开查看");
      setTimeout(() => editItemModal(it), 4000);
    } catch (e) { toast(e.message); }
  });
  $$("#modalBody [data-sug]").forEach(b => b.addEventListener("click", async () => {
    const patch = b.dataset.sug === "reason" ? { reason: b.dataset.v } : { kp_name: b.dataset.v };
    try {
      Object.assign(it, await api(`/api/items/${it.id}`, { method: "PATCH", body: patch }));
      toast("已采用 AI 建议 ✅");
      editItemModal(it);   // 重开弹窗刷新建议与分析区
    } catch (e) { toast(e.message); }
  }));
  $("#eAiBtn")?.addEventListener("click", async () => {
    try {
      await api(`/api/items/${it.id}/analyze`, { method: "POST" });
      toast("AI 分析已开始，稍后打开可见 🤖");
      closeModal();
    } catch (e) { toast(e.message); }
  });
  $("#eSave").onclick = async () => {
    const body = { subject: $("#eSubj").value, kp_id: +$("#eKp").value || null,
                   reason: $("#eReason").value || null };
    if (it.item_type === "word") {
      body.prompt = $("#ePrompt").value; body.wrong_text = $("#eWrong").value;
      body.correct_text = $("#eCorrect").value;
    } else {
      body.wrong_answer = $("#eWrongAnswer").value; body.solution = $("#eSolution").value;
    }
    try {
      await api(`/api/items/${it.id}`, { method: "PATCH", body });
      toast("已保存"); closeModal(); renderItems();
    } catch (e) { toast(e.message); }
  };
}

/* ================= 导出 PDF ================= */
function openExport(preselect) {
  const f = state.filters;
  const scope = [
    f.subject !== "all" ? `科目：${SUBJ[f.subject].label}` : "科目：全部",
    `学期：${f.term === "all" ? "全部学期" : f.term}`,
    f.status !== "all" ? (f.status === "stubborn" ? "仅顽固题" : `状态：${
      { reviewing: "复习中", paused: "已暂停", mastered: "已消灭" }[f.status]}`) : "状态：全部",
    f.kp_id ? "知识点：按当前选择" : "",
  ].filter(Boolean).join(" · ");
  openModal(`<h3>导出 PDF</h3>
    <div class="mode-cards" style="margin-bottom:12px">
      <div class="mode-card" data-tpl="collection" id="tplC"><div class="m">📘</div>收藏本<br><span style="font-size:11px;font-weight:600">题+错因+正解，归档翻阅</span></div>
      <div class="mode-card" data-tpl="practice" id="tplP"><div class="m">📝</div>重练卷<br><span style="font-size:11px;font-weight:600">题+答题留白，周五打印周末重练</span></div>
    </div>
    <p class="confirm-text" style="background:var(--accent-light);border-radius:12px;padding:10px 12px;font-size:13px">
      当前范围：${scope}<br>共 ${state.iTotal} 道题（${state.iTotal === state.items.length ? "全部导出" : "按范围导出，与列表筛选一致"}）</p>
    <div class="field-label">排版</div>
    <div class="form-grid">
      <select class="input" id="exGroup">
        <option value="kp">按知识点分组</option><option value="time">按时间排序</option>
      </select>
      <select class="input" id="exOrder">
        <option value="stubborn">顽固题靠前</option><option value="shuffle">整卷乱序</option>
        <option value="none">按到期顺序</option>
      </select>
    </div>
    ${state.llm?.configured ? `
    <div class="ex-var" id="exVarRow">
      <label class="ex-var-l"><input type="checkbox" id="exVar" checked>
      卷末附「举一反三」AI 变式题（每个知识点 1 道同类新题，检验纠错效果）</label>
    </div>` : ""}
    <div class="m-acts">
      <button class="btn btn-ghost" data-act="closeModal">取消</button>
      <button class="btn btn-brand" id="exGo">生成 PDF</button>
    </div>`);
  let tpl = preselect === "practice" ? "practice" : "collection";
  const pick = t => {
    tpl = t;
    $("#tplC").classList.toggle("on", t === "collection");
    $("#tplP").classList.toggle("on", t === "practice");
    const row = $("#exVarRow");
    if (row) row.style.display = t === "practice" ? "" : "none";  // 变式题只属重练卷
  };
  pick(tpl);  // 入口按钮直达时预选对应模板
  $("#tplC").onclick = () => pick("collection");
  $("#tplP").onclick = () => pick("practice");
  $("#exGo").onclick = async () => {
    const f = state.filters;
    const varRow = $("#exVarRow");
    const wantVar = varRow && varRow.style.display !== "none" && $("#exVar").checked;
    const body = { template: tpl, group_by: $("#exGroup").value,
                   shuffle: $("#exOrder").value === "shuffle",
                   stubborn_top: $("#exOrder").value === "stubborn" };
    if (wantVar) body.variants = true;
    if (f.term) body.term = f.term;
    if (f.subject !== "all") body.subject = f.subject;
    if (f.status === "stubborn") body.stubborn = 1;
    else if (f.status !== "all") body.status = f.status;
    if (f.kp_id) body.kp_id = +f.kp_id;
    if (state.iTotal === state.items.length && state.items.length)
      body.item_ids = state.items.map(i => i.id);
    const go = $("#exGo");
    const oldTxt = go.textContent;
    go.disabled = true;
    go.textContent = wantVar ? "AI 出变式题中…" : "生成中…";
    try {
      const res = await api("/api/export/pdf", { method: "POST", body });
      closeModal();
      toast(wantVar && res.variants === 0
        ? "PDF 已生成，但变式题没能生成（可稍后重试）"
        : res.variants ? `PDF 已生成，附 ${res.variants} 道变式题` : "PDF 已生成，开始下载");
      const a = document.createElement("a");
      a.href = res.download_url; a.download = res.filename;
      document.body.appendChild(a); a.click(); a.remove();
    } catch (e) {
      toast(e.message);
      go.disabled = false;
      go.textContent = oldTxt;
    }
  };
}
document.addEventListener("click", e => {
  const btn = e.target.closest("[data-act=export]");
  if (btn) openExport(btn.dataset.tpl);
});

/* ================= 加题页 ================= */
function renderAdd() {
  refreshTopRight();
  const box = $("#page-add");
  const a = state.add;
  if (a.counter) box.dataset.wc = a.counter;
  box.innerHTML = `
    <div class="mode-cards">
      <div class="mode-card ${a.mode === "photo" ? "on" : ""}" data-act="mode" data-m="photo">
        <div class="m">📸</div>拍照题</div>
      <div class="mode-card ${a.mode === "word" ? "on" : ""}" data-act="mode" data-m="word">
        <div class="m">✏️</div>错字词</div>
    </div>
    <div id="addBody"></div>`;
  renderAddBody();
}

function renderAddBody() {
  const a = state.add;
  const body = $("#addBody");
  if (a.mode === "photo") body.innerHTML = addPhotoHTML();
  else body.innerHTML = addWordHTML();
  // 选"自定义知识点"时展开输入框
  ["#pKp", "#wKp"].forEach(selId => {
    $(selId)?.addEventListener("change", e => {
      const wrapEl = $(selId + "CustomWrap");
      if (wrapEl) wrapEl.classList.toggle("hidden", e.target.value !== "custom");
    });
  });
}

function kpSelectHTML(id, subject, sel) {
  const kps = kpsFor(subject);
  return `<select class="input" id="${id}">
    ${kps.map(k => `<option value="${k.id}" ${sel == k.id ? "selected" : ""}>${esc(k.name)}</option>`).join("")}
    <option value="custom">＋ 自定义知识点…</option>
  </select>`;
}
function reasonBtnsHTML(cur, prefix) {
  return `<div class="big-btn-row">
    ${Object.entries(REASON).map(([k, v]) =>
      `<button type="button" class="bb ${cur === k ? "on" : ""}" data-reason="${k}"
        style="--c:var(--${{concept: "fail", careless: "hint", calc: "chinese", clueless: "other"}[k]})"
        data-prefix="${prefix}">${v}</button>`).join("")}
  </div>`;
}

function addPhotoHTML() {
  const a = state.add;
  const photoPart = a.photo
    ? `<div class="crop-tips">照片已就绪，长边自动压缩到 1200px 上传</div>
       <img src="${a.photo.preview}" style="width:100%;border-radius:16px;border:1px solid var(--line)">
       <div class="crop-acts" style="margin-top:10px">
         <button class="btn btn-ghost" data-act="rephoto">重拍 / 重选</button>
       </div>`
    : `<div class="photo-pick" data-act="pickPhoto">
         <span class="big">📷</span>点击拍照或从相册选择<br>
         <span style="font-size:12px">拍卷子时尽量只框住这一道题</span>
       </div>`;
  return `
    ${photoPart}
    <div class="field-label">科目</div>
    <div class="big-btn-row">
      ${Object.entries(SUBJ).map(([k, v]) =>
        `<button type="button" class="bb subj-b ${a.subject === k ? "on" : ""}" data-subj="${k}"
          style="--c:${v.color}">${v.label}</button>`).join("")}
    </div>
    <div class="field-label">知识点</div>${kpSelectHTML("pKp", a.subject, a.kpSel[a.subject])}
    <div id="pKpCustomWrap" class="hidden"><div class="field-label">自定义知识点名称</div>
      <input class="input" id="pKpCustom" placeholder="如：植树问题"></div>
    <div class="field-label">错在哪里？（选一个）</div>${reasonBtnsHTML(a.reason, "p")}
    <div class="field-label">当时错在（选填）</div>
    <input class="input" id="pWrongAnswer" placeholder="如：最后一步把 96 写成 47">
    <div class="field-label">正确做法（选填，复习翻面和收藏本会显示）</div>
    <textarea class="input" id="pSolution" placeholder="一句话讲清正确思路"></textarea>
    <div style="margin-top:16px"><button class="btn btn-brand btn-xl" data-act="submitPhoto">加入明天的复习</button></div>`;
}

function addWordHTML() {
  const a = state.add;
  const wSubjects = ["chinese", "english", "other"];
  return `
    <div class="add-ok-tip" id="wTip" style="display:${a.counter ? "" : "none"}">
      已连录 ${a.counter} 个，再录一个？表单已清空</div>
    <div class="field-label">科目</div>
    <div class="big-btn-row" style="grid-template-columns:repeat(3,1fr)">
      ${wSubjects.map(k => `<button type="button" class="bb ${a.wSubject === k ? "on" : ""}"
        data-wsubj="${k}" style="--c:${SUBJ[k].color}">${SUBJ[k].label}</button>`).join("")}
    </div>
    <div class="field-label" id="wPromptLabel">${
      a.wSubject === "english" ? "中文提示（如：星期三）" : "拼音 / 提示（如：zhì 治理）"}</div>
    <input class="input" id="wPrompt" placeholder="${a.wSubject === "english" ? "星期三" : "zhì（治理）"}">
    <div class="form-grid">
      <div><div class="field-label">孩子错写</div><input class="input" id="wWrong" placeholder="Wensday"></div>
      <div><div class="field-label">正确写法</div><input class="input" id="wCorrect" placeholder="Wednesday"></div>
    </div>
    <div class="field-label">知识点</div>${kpSelectHTML("wKp", a.wSubject, a.kpSel[a.wSubject])}
    <div id="wKpCustomWrap" class="hidden"><div class="field-label">自定义知识点名称</div>
      <input class="input" id="wKpCustom" placeholder="如：星期类单词"></div>
    <div class="field-label">错在哪里？（选一个）</div>${reasonBtnsHTML(a.wReason, "w")}
    <div style="margin-top:16px"><button class="btn btn-brand btn-xl" data-act="submitWord">加入明天的复习</button></div>`;
}

function updateKpOptions(selEl, subject, selected) {
  const kps = kpsFor(subject);
  selEl.innerHTML = kps.map(k =>
    `<option value="${k.id}" ${selected == k.id ? "selected" : ""}>${esc(k.name)}</option>`).join("")
    + `<option value="custom">＋ 自定义知识点…</option>`;
}

function markOn(btn) {
  btn.parentElement.querySelectorAll(".bb").forEach(x => x.classList.remove("on"));
  btn.classList.add("on");
}

$("#page-add").addEventListener("click", e => {
  const el = e.target.closest("[data-act]");
  const a = state.add;
  if (!el) {
    const bb = e.target.closest(".bb");
    if (bb) {
      if (bb.dataset.reason) {
        if (bb.dataset.prefix === "p") a.reason = bb.dataset.reason; else a.wReason = bb.dataset.reason;
        markOn(bb);  // 只切换高亮，不重绘表单（避免清掉已填内容）
      } else if (bb.dataset.subj) {
        a.subject = bb.dataset.subj;
        markOn(bb);
        updateKpOptions($("#pKp"), a.subject, a.kpSel[a.subject]);
      } else if (bb.dataset.wsubj) {
        a.wSubject = bb.dataset.wsubj;
        markOn(bb);
        updateKpOptions($("#wKp"), a.wSubject, a.kpSel[a.wSubject]);
        const lb = $("#wPromptLabel"), ip = $("#wPrompt");
        if (lb && ip) {
          const en = a.wSubject === "english";
          lb.textContent = en ? "中文提示（如：星期三）" : "拼音 / 提示（如：zhì 治理）";
          ip.placeholder = en ? "星期三" : "zhì（治理）";
        }
      }
    }
    return;
  }
  const act = el.dataset.act;
  if (act === "mode") { a.mode = el.dataset.m; renderAdd(); return; }
  if (act === "pickPhoto") { $("#photoFile").click(); return; }
  if (act === "rephoto") { a.photo = null; renderAddBody(); return; }
  if (act === "submitPhoto") submitPhotoItem();
  if (act === "submitWord") submitWordItem();
});

/* ----- 拍照 / 裁剪 / 压缩 ----- */
const photoInput = document.createElement("input");
photoInput.type = "file"; photoInput.accept = "image/*";
// 不设 capture：设了会在安卓上强制拉起相机、无法选相册；不设则弹「拍照/相册」菜单
photoInput.id = "photoFile";
photoInput.style.display = "none";
document.body.appendChild(photoInput);

photoInput.addEventListener("change", () => {
  const f = photoInput.files[0];
  if (!f) return;
  const rd = new FileReader();
  rd.onload = () => openCrop(rd.result);
  rd.readAsDataURL(f);
  photoInput.value = "";
});

function openCrop(dataUrl) {
  const img = new Image();
  img.onload = () => {
    openModal(`<h3>框住这道错题</h3>
      <div class="crop-wrap" id="cropWrap"><img id="cropImg" src="${dataUrl}">
        <div class="crop-box" id="cropBox"></div></div>
      <p class="crop-tips">默认保留整张照片；想只框住这道题时，按住拖动虚线框重新选区</p>
      <div class="m-acts">
        <button class="btn btn-ghost" id="cropRetake">重选照片</button>
        <button class="btn btn-brand" id="cropOk">用这张</button>
      </div>`);
    const wrap = $("#cropWrap"), box = $("#cropBox"), cimg = $("#cropImg");
    let sel = null, drag = null;
    function setBox(x, y, w, h) {
      const W = cimg.clientWidth, H = cimg.clientHeight;
      x = Math.max(0, Math.min(x, W)); y = Math.max(0, Math.min(y, H));
      w = Math.max(24, Math.min(w, W - x)); h = Math.max(24, Math.min(h, H - y));
      sel = { x, y, w, h };
      box.style.left = x + "px"; box.style.top = y + "px";
      box.style.width = w + "px"; box.style.height = h + "px";
    }
    function pos(e) {
      const r = wrap.getBoundingClientRect();
      const t = e.touches ? e.touches[0] : e;
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    }
    function down(e) {
      const p = pos(e);
      if (sel && p.x > sel.x && p.x < sel.x + sel.w && p.y > sel.y && p.y < sel.y + sel.h) {
        drag = { mode: "move", dx: p.x - sel.x, dy: p.y - sel.y };
      } else {
        drag = { mode: "new", sx: p.x, sy: p.y };
        setBox(p.x, p.y, 1, 1);
      }
    }
    function move(e) {
      if (!drag) return;
      e.preventDefault();
      const p = pos(e);
      if (drag.mode === "new") setBox(Math.min(drag.sx, p.x), Math.min(drag.sy, p.y),
        Math.abs(p.x - drag.sx), Math.abs(p.y - drag.sy));
      else setBox(p.x - drag.dx, p.y - drag.dy, sel.w, sel.h);
    }
    function up() { drag = null; }
    wrap.addEventListener("mousedown", down); wrap.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    wrap.addEventListener("touchstart", down, { passive: true });
    wrap.addEventListener("touchmove", move, { passive: false });
    wrap.addEventListener("touchend", up);
    setTimeout(() => {  // 默认选区 = 整张照片（直接点"用这张"不裁任何内容）
      setBox(0, 0, cimg.clientWidth, cimg.clientHeight);
    }, 60);
    $("#cropRetake").onclick = () => { closeModal(); photoInput.click(); };
    $("#cropOk").onclick = () => {
      const scale = img.naturalWidth / cimg.clientWidth;
      const sx = sel.x * scale, sy = sel.y * scale;
      const sw = sel.w * scale, sh = sel.h * scale;
      const k = Math.min(1, 1200 / Math.max(sw, sh));
      const cv = document.createElement("canvas");
      cv.width = Math.round(sw * k); cv.height = Math.round(sh * k);
      cv.getContext("2d").drawImage(img, sx, sy, sw, sh, 0, 0, cv.width, cv.height);
      cv.toBlob(b => {
        state.add.photo = { blob: b, preview: cv.toDataURL("image/jpeg", 0.7) };
        closeModal(); renderAddBody();
      }, "image/jpeg", 0.82);
    };
  };
  img.src = dataUrl;
}

async function submitPhotoItem() {
  const a = state.add;
  if (!a.photo) { toast("先拍一张题目照片"); return; }
  const payload = { subject: a.subject, reason: a.reason,
                    wrong_answer: $("#pWrongAnswer").value.trim(),
                    solution: $("#pSolution").value.trim() };
  const kpVal = $("#pKp").value;
  if (kpVal === "custom") {
    const name = $("#pKpCustom").value.trim();
    if (name) payload.kp_name = name;
  } else payload.kp_id = +kpVal;
  try {
    const fd = new FormData();
    fd.append("payload", JSON.stringify(payload));
    fd.append("photo", a.photo.blob, "photo.jpg");
    await api("/api/items", { method: "POST", body: fd });
    a.photo = null;
    if (payload.kp_id) a.kpSel[a.subject] = payload.kp_id;
    state.meta = await api("/api/meta");   // 刷新自定义知识点
    toast(state.llm?.configured ? "已加入明天的复习 ⭐ AI 正在分析考点…" : "已加入明天的复习 ⭐");
    renderAddBody();
  } catch (e) { toast(e.message); }
}

async function submitWordItem() {
  const a = state.add;
  const prompt = $("#wPrompt").value.trim();
  const wrong = $("#wWrong").value.trim();
  const correct = $("#wCorrect").value.trim();
  if (!prompt || !wrong || !correct) { toast("提示、错写、正确写法都要填"); return; }
  const payload = { subject: a.wSubject, reason: a.wReason,
                    prompt, wrong_text: wrong, correct_text: correct };
  const kpVal = $("#wKp").value;
  if (kpVal === "custom") {
    const name = $("#wKpCustom").value.trim();
    if (name) payload.kp_name = name;
  } else payload.kp_id = +kpVal;
  try {
    await api("/api/items", { method: "POST", body: payload });
    a.counter++;
    if (payload.kp_id) a.kpSel[a.wSubject] = payload.kp_id;
    state.meta = await api("/api/meta");
    renderAddBody();
    $("#wTip").style.display = "";
    $("#wPrompt").focus();
    toast(state.llm?.configured ? "已加入明天的复习 ⭐ AI 正在分析考点…" : "已加入明天的复习 ⭐");
  } catch (e) { toast(e.message); }
}

/* ================= 统计页 ================= */
async function renderStats() {
  refreshTopRight();
  const box = $("#page-stats");
  const cur = state.meta.settings.current_term;
  if (!state.stats || state.stats.term !== cur) await loadStats(cur);
  const s = state.stats;
  const maxC = Math.max(...s.top5.map(t => t.c), 1);
  const reasonColors = ["#FF8A3D", "#4C8DFF", "#22B573", "#9B6BFF", "#B8AA9A"];
  const totalReasons = s.reasons.reduce((a, r) => a + r.c, 0) || 1;
  const maxSubj = Math.max(...s.subjects.map(x => x.c), 1);
  const backup = s.backup.latest;
  const ageTxt = backup ? `${backup.age_hours < 48 ? "🟢" : "🟡"} 最近备份 ${fmtAge(backup.age_hours)}` : "🟡 还没有备份";

  box.innerHTML = `
    <div class="chip-row">${s.terms.map(t =>
      `<button class="chip ${s.term === t ? "on" : ""}" data-term="${esc(t)}">${esc(t)}</button>`).join("")}</div>
    <div class="stat-grid">
      <div class="stat-c c1"><div class="v">${s.total}</div><div class="l">收录错题</div></div>
      <div class="stat-c c2"><div class="v">${s.mastered}</div><div class="l">已消灭</div></div>
      <div class="stat-c c3"><div class="v">${s.stubborn}</div><div class="l">顽固题</div></div>
      <div class="stat-c c4"><div class="v">${s.rate}%</div><div class="l">消灭率</div></div>
    </div>
    <div class="card"><div class="card-title"><span class="ico">📉</span>薄弱知识点 TOP5 —— 周末补什么</div>
      ${s.top5.map((t, i) => `
        <div class="bar-row">
          <span class="bl">${esc(t.name)}</span>
          <span class="bt"><i style="width:${Math.round(t.c * 100 / maxC)}%;background:${reasonColors[i % 5]}"></i></span>
          <span class="bv">${t.c} 题${t.stubborn ? ` <span style="color:var(--fail)">顽固${t.stubborn}</span>` : ""}</span>
        </div>`).join("") || "<p style='font-size:13px;color:var(--ink2)'>本学期还没有错题</p>"}
    </div>
    <div class="card"><div class="card-title"><span class="ico">🧠</span>错因占比 —— 补习惯还是补知识</div>
      <div class="stack-bar">${s.reasons.map((r, i) =>
        `<i style="width:${r.c * 100 / totalReasons}%;background:${reasonColors[i % 5]}"></i>`).join("")}</div>
      <div class="legend">${s.reasons.map((r, i) =>
        `<span><span class="sw" style="background:${reasonColors[i % 5]}"></span>${
          REASON[r.key] || r.key} ${r.c}</span>`).join("")}</div>
      <p style="font-size:12px;color:var(--ink2);margin-top:10px">审题马虎多 → 抓圈关键词习惯；概念不会多 → 回归讲解</p>
    </div>
    <div class="card"><div class="card-title"><span class="ico">📚</span>科目分布（深色为已消灭）</div>
      ${s.subjects.map(x => `
        <div class="bar-row">
          <span class="bl">${SUBJ[x.subject]?.label || x.subject}</span>
          <span class="bt" style="background:${SUBJ[x.subject]?.color}22">
            <i style="width:${x.c * 100 / maxSubj}%;background:${SUBJ[x.subject]?.color}"></i></span>
          <span class="bv">${x.c} / 消${x.mastered}</span>
        </div>`).join("") || ""}
    </div>
    <div class="card"><div class="card-title"><span class="ico">👀</span>待复核（孩子判的"做对了"）
      ${s.pending.length ? `<span class="stubborn-badge">${s.pending.length}</span>` : ""}</div>
      ${s.pending.map(p => {
        const pvBody = p.item_type === "word"
          ? `提示「${esc(p.prompt)}」错写「${esc(p.wrong_text)}」→ 应为「${esc(p.correct_text)}」`
          : `${p.photo_ids?.length
              ? `<img class="pv-thumb" src="/photos/${p.photo_ids[0]}?size=thumb"
                   onclick="photoModal(${p.photo_ids[0]})">`
              : ""}
             <div class="pv-lines">
               ${p.question_text
                 ? `<div class="pv-q">${esc(p.question_text)}</div>`
                 : `<div class="pv-q pv-dim">${p.photo_ids?.length ? "点缩略图看原题" : "照片题，请对照原卷提问"}</div>`}
               ${p.wrong_answer ? `<div>✏️ 当时错在：${esc(p.wrong_answer)}</div>` : ""}
               ${p.solution ? `<div>✅ 正确做法：${esc(p.solution)}</div>` : ""}
             </div>`;
        return `
        <div class="pending-card">
          <div class="p-head"><span class="subj-badge sj-${p.subject}">${SUBJ[p.subject]?.label}</span>
            ${p.kp_name ? `<span class="kp-badge">${esc(p.kp_name)}</span>` : ""}
            <span class="kp-badge">${p.item_type === "word" ? "错字词" : "照片题"}</span></div>
          <div class="p-body ${p.item_type !== "word" && p.photo_ids?.length ? "pv-photo" : ""}">${pvBody}</div>
          <div class="p-acts">
            <button class="btn btn-pass btn-sm" data-pv="${p.log_id}" data-ok="1">确实做对了</button>
            <button class="btn btn-fail btn-sm" data-pv="${p.log_id}" data-ok="0">其实不对</button>
          </div>
        </div>`; }).join("") || "<p style='font-size:13px;color:var(--ink2)'>没有待复核的判定，孩子自评很靠谱</p>"}
    </div>
    <div class="card"><div class="card-title"><span class="ico">🛟</span>数据备份</div>
      <p style="font-size:14px;margin-bottom:12px">${ageTxt}
        <span style="color:var(--ink2);font-size:12px">（每日 3:30 自动备份，保留最近 ${s.backup.keep} 份）</span></p>
      <div style="display:flex;gap:10px">
        <button class="btn btn-brand btn-sm" data-act="backupNow">立即备份</button>
        <button class="btn btn-ghost btn-sm" data-act="dlBackup">下载最新备份</button>
      </div>
    </div>`;

  box.querySelectorAll("[data-term]").forEach(b =>
    b.addEventListener("click", () => loadStats(b.dataset.term).then(renderStats)));
  box.querySelectorAll("[data-pv]").forEach(b =>
    b.addEventListener("click", async () => {
      try {
        await api(`/api/review/${b.dataset.pv}/verify`, { method: "POST", body: { approve: b.dataset.ok === "1" } });
        toast(b.dataset.ok === "1" ? "已确认通过" : "已驳回，该题回炉重练");
        loadStats(state.stats.term).then(renderStats);
      } catch (e) { toast(e.message); }
    }));
  box.querySelector("[data-act=backupNow]").addEventListener("click", async () => {
    try { await api("/api/backup", { method: "POST" }); toast("备份完成 ✅"); loadStats(state.stats.term).then(renderStats); }
    catch (e) { toast(e.message); }
  });
  box.querySelector("[data-act=dlBackup]").addEventListener("click", () => {
    window.open("/api/backup/latest/download", "_blank");
  });
}

async function loadStats(term) {
  try { state.stats = await api("/api/stats?term=" + encodeURIComponent(term || "")); }
  catch (e) { toast(e.message); }
}
function fmtAge(h) {
  if (h < 1) return "刚刚";
  if (h < 24) return `${Math.round(h)} 小时前`;
  return `${Math.round(h / 24)} 天前`;
}

/* ================= 我的页 ================= */
async function renderMe() {
  refreshTopRight();
  let me, health;
  try { me = await api("/api/me"); health = await api("/api/health"); }
  catch (e) { return; }
  state.me = me;
  const st = state.meta.settings;
  const gloryList = state.gloryAll ? me.glory : me.glory.slice(0, 10);
  const RD_TAG = { pending: "🕒 等待家长确认", approved: "✅ 已兑换", rejected: "❎ 未通过" };
  const rdRow = r => `
      <div class="redeem-row">
        <span class="rd-note">${esc(r.note)}</span>
        <span class="rd-stars">${r.stars} 星</span>
        <span class="rd-tag rd-${r.status}">${RD_TAG[r.status]}</span>
        <span class="rd-date">${String(r.status === "pending" ? r.created_at : (r.judged_at || r.created_at)).slice(0, 10)}</span>
        ${r.status === "pending"
          ? (state.role === "child"
              ? `<button class="btn btn-ghost btn-sm" data-act="rdCancel" data-id="${r.id}">撤回</button>`
              : `<span class="rd-acts"><button class="btn btn-brand btn-sm" data-act="rdApprove" data-id="${r.id}">批准</button>
                 <button class="btn btn-ghost btn-sm" data-act="rdReject" data-id="${r.id}">驳回</button></span>`)
          : ""}
      </div>`;
  const rdPendingN = me.redeems.filter(r => r.status === "pending").length;
  const box = $("#page-me");
  box.innerHTML = `
    <div class="me-hero">
      <div><div class="stars">★ ${me.stars}</div><div class="sl">可用星星</div></div>
      <div class="side">累计赚得 ${me.stars_earned}<br>今日 +${me.stars_today} · 🔥 连续 ${me.streak} 天</div>
    </div>
    <div class="card"><div class="card-title"><span class="ico">🏅</span>勋章墙</div>
      <div class="badge-grid">${me.badges.map(b => `
        <div class="badge-c ${b.unlocked ? "" : "locked"}">
          <div class="bic">${medalSVG(["#FF8A3D", "#FFC53D", "#22B573", "#4C8DFF", "#E5484D", "#9B6BFF"][b.key.length % 6], !b.unlocked)}</div>
          <div class="bn">${b.name}</div><div class="bd">${b.unlocked ? b.achieved_at : b.desc}</div>
        </div>`).join("")}</div>
    </div>
    <div class="card"><div class="card-title"><span class="ico">🏆</span>光荣榜（已消灭 ${me.glory.length}）</div>
      ${gloryList.map(g => `
        <div class="glory-row">
          <span class="g-star">★</span>
          <span>${SUBJ[g.subject]?.label} · ${esc(g.kp_name || "")} ${g.item_type === "word" ? "「" + esc(g.correct_text) + "」" : ""}</span>
          <span class="g-date">${String(g.mastered_at || "").slice(0, 10)}</span>
        </div>`).join("") || "<p style='font-size:13px;color:var(--ink2)'>消灭第一只小怪兽后，这里就是战利品陈列柜</p>"}
      ${me.glory.length > 10 ? `<button class="btn btn-ghost btn-sm" style="margin-top:10px" data-act="gloryMore">
        ${state.gloryAll ? "收起" : `查看全部 ${me.glory.length}`}</button>` : ""}
    </div>
    ${state.role === "child"
      ? `<div class="card"><div class="card-title"><span class="ico">🎁</span>星星兑换</div>
          <p style="font-size:14px;line-height:1.7">${esc(me.reward_rule)}</p>
          <div class="redeem-apply">
            <input class="input" id="rdNote" maxlength="100" placeholder="想兑换什么？如：周末选一部电影">
            <input class="input" id="rdStars" type="number" min="1" max="999" inputmode="numeric" placeholder="星数">
            <button class="btn btn-brand btn-sm" data-act="rdApply">申请兑换</button>
          </div>
          <div class="redeem-list">${me.redeems.map(rdRow).join("")
            || "<p style='font-size:13px;color:var(--ink2)'>攒够星星就可以申请兑换上面的奖励啦</p>"}</div>
        </div>`
      : `<div class="card"><div class="card-title"><span class="ico">🎁</span>星星兑换规则</div>
          <textarea class="input" id="rewardRule">${esc(me.reward_rule)}</textarea>
        <div style="margin-top:10px;text-align:right">
          <button class="btn btn-brand btn-sm" data-act="saveReward">保存规则</button></div></div>
      <div class="card"><div class="card-title"><span class="ico">⏳</span>兑换审批${rdPendingN ? `（待处理 ${rdPendingN}）` : ""}</div>
          <p style="font-size:13px;color:var(--ink2);line-height:1.6">孩子攒够星后可提交兑换申请；批准即从可用余额扣星，驳回不扣。当前可用 ★ ${me.stars}，累计赚得 ${me.stars_earned}</p>
          <div class="redeem-list">${me.redeems.map(rdRow).join("")
            || "<p style='font-size:13px;color:var(--ink2)'>还没有兑换申请</p>"}</div>
        </div>`}
    ${state.role === "child" ? `<div class="card" style="text-align:center">
      <div style="font-size:13px;color:var(--ink2);margin-bottom:10px">这是你的专属界面，继续加油消灭小怪兽！</div>
      <button class="btn btn-ghost btn-sm" data-act="logout">退出登录</button>
    </div>` : `<div class="card"><div class="card-title"><span class="ico">⚙️</span>设置（家长）</div>
      <div class="set-row"><div class="sr-l">每日复习上限<div class="d">到期题多时自动排队</div></div>
        <div class="stepper">
          <button data-act="limit" data-d="-1">−</button>
          <b id="limitV" style="width:26px;text-align:center">${st.daily_limit}</b>
          <button data-act="limit" data-d="1">＋</button>
        </div></div>
      <div class="set-row"><div class="sr-l">防放水抽查<div class="d">孩子判的"做对了"需家长复核</div></div>
        <label class="toggle"><input type="checkbox" id="verifyTog" ${st.verify_enabled ? "checked" : ""}><span class="tk"></span></label>
      </div>
      <div class="set-row"><div class="sr-l">当前学期<div class="d">期末开启新学期，光荣榜保留</div></div>
        <button class="btn btn-ghost btn-sm" data-act="newTerm">开启新学期</button></div>
      <div class="set-row"><div class="sr-l">修改访问码（家长）<div class="d">家长设备登录用，拥有全部权限</div></div>
        <button class="btn btn-ghost btn-sm" data-act="chgCode">修改</button></div>
      <div class="set-row"><div class="sr-l">孩子访问码<div class="d">孩子设备用它登录，只能复习，不能录题/删除/改设置</div></div>
        <button class="btn btn-ghost btn-sm" data-act="chgChildCode">修改</button></div>
      <div class="set-row"><div class="sr-l">AI 错题分析<div class="d">${state.llm?.configured
        ? `${esc(state.llm.label)} · ${esc(state.llm.model)} · 录入后自动分析考点与易错点`
        : "未开启：编辑 data/llm.json 填入 api_key 后即可"}</div></div>
        <span style="font-size:22px">${state.llm?.configured ? "🤖" : "💤"}</span></div>
      <div class="set-row"><div class="sr-l">AI 复习提示<div class="d">翻面前 AI 给两档思考提示，不直接说答案</div></div>
        <label class="toggle"><input type="checkbox" id="hintTog" ${st.ai_hint_enabled !== false ? "checked" : ""}><span class="tk"></span></label></div>
      <div class="set-row"><div class="sr-l">变式题家长预审<div class="d">消灭后 AI 出同类变式题，先给你过目再放行；关闭则直接向孩子开放</div></div>
        <label class="toggle"><input type="checkbox" id="variantTog" ${st.variant_gate !== false ? "checked" : ""}><span class="tk"></span></label></div>
      <div class="set-row"><div class="sr-l">看图模型（照片 OCR）<div class="d">${state.llm?.vision?.configured
        ? `${esc(state.llm.vision.label)} · ${esc(state.llm.vision.model)} · 照片题自动识别题干原文`
        : "未配置：照片题暂无题干文字，分析靠错在/做法推断；llm.json 配 vision_active 即可开启"}</div></div>
        <span style="font-size:22px">${state.llm?.vision?.configured ? "👁️" : "💤"}</span></div>
      <div class="set-row"><div class="sr-l">数据备份<div class="d">${health.backup.latest
        ? `最近备份 ${fmtAge(health.backup.latest.age_hours)} · ${health.disk_free_mb}MB 空闲` : health.disk_free_mb + "MB 空闲"}</div></div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-brand btn-sm" data-act="backupNow">备份</button>
          <button class="btn btn-ghost btn-sm" data-act="dlBackup">下载</button></div></div>
      <div class="set-row"><div class="sr-l">退出登录</div>
        <button class="btn btn-ghost btn-sm" data-act="logout">退出</button></div>
    </div>`}`;

  box.querySelector("[data-act=gloryMore]")?.addEventListener("click", () => {
    state.gloryAll = !state.gloryAll; renderMe();
  });
  box.querySelector("[data-act=logout]")?.addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST" }); } catch (e) {}
    state.role = null;
    showLogin();
  });
  box.querySelector("[data-act=rdApply]")?.addEventListener("click", async () => {
    const note = $("#rdNote").value.trim();
    const stars = Number($("#rdStars").value);
    if (!note) return toast("先写清楚想兑换什么");
    if (!Number.isInteger(stars) || stars < 1 || stars > 999) return toast("星数填 1~999 的整数");
    try { await api("/api/redeem", { method: "POST", body: { note, stars } });
      toast("申请已发出，等家长确认 💌"); renderMe(); }
    catch (e) { toast(e.message); }
  });
  box.querySelector("[data-act=rdCancel]")?.addEventListener("click", async e => {
    try { await api(`/api/redeem/${e.currentTarget.dataset.id}`, { method: "DELETE" });
      toast("已撤回申请"); renderMe(); }
    catch (err) { toast(err.message); }
  });
  if (state.role === "child") return;   // ↓↓ 以下均为家长专属控件的绑定
  box.querySelectorAll("[data-act=rdApprove]").forEach(b => b.addEventListener("click", async () => {
    try { const r = await api(`/api/redeem/${b.dataset.id}/approve`, { method: "POST" });
      toast(`已批准兑换，可用 ${r.balance} 颗星`); renderMe(); }
    catch (e) { toast(e.message); }
  }));
  box.querySelectorAll("[data-act=rdReject]").forEach(b => b.addEventListener("click", async () => {
    try { await api(`/api/redeem/${b.dataset.id}/reject`, { method: "POST" });
      toast("已驳回，星星未扣"); renderMe(); }
    catch (e) { toast(e.message); }
  }));
  box.querySelector("[data-act=saveReward]")?.addEventListener("click", async () => {
    try { await api("/api/settings", { method: "PUT", body: { reward_rule: $("#rewardRule").value } });
      state.meta = await api("/api/meta"); toast("兑换规则已保存"); }
    catch (e) { toast(e.message); }
  });
  box.querySelectorAll("[data-act=limit]").forEach(b => b.addEventListener("click", async () => {
    const v = Math.max(3, Math.min(15, state.meta.settings.daily_limit + (+b.dataset.d)));
    try {
      state.meta.settings = (await api("/api/settings", { method: "PUT", body: { daily_limit: v } }));
      $("#limitV").textContent = v;
    } catch (e) { toast(e.message); }
  }));
  box.querySelector("#verifyTog").addEventListener("change", async e => {
    try { await api("/api/settings", { method: "PUT", body: { verify_enabled: e.target.checked } });
      state.meta = await api("/api/meta"); toast(e.target.checked ? "已开启防放水抽查" : "已关闭抽查"); }
    catch (err) { toast(err.message); }
  });
  box.querySelector("#hintTog").addEventListener("change", async e => {
    try { await api("/api/settings", { method: "PUT", body: { ai_hint_enabled: e.target.checked } });
      state.meta = await api("/api/meta");
      toast(e.target.checked ? "AI 复习提示已开启" : "AI 复习提示已关闭"); }
    catch (err) { toast(err.message); }
  });
  box.querySelector("#variantTog").addEventListener("change", async e => {
    try { await api("/api/settings", { method: "PUT", body: { variant_gate: e.target.checked } });
      state.meta = await api("/api/meta");
      toast(e.target.checked ? "变式题需家长预审后放行" : "变式题生成后直接向孩子开放"); }
    catch (err) { toast(err.message); }
  });
  box.querySelector("[data-act=newTerm]").addEventListener("click", () => {
    openModal(`<h3>开启新学期</h3>
      <p class="confirm-text">开启后当前学期「${esc(st.current_term)}」的错题将归档，
      光荣榜保留，错题本从零开始。</p>
      <div class="field-label">新学期名称</div>
      <input class="input" id="ntInput" placeholder="如：三年级下" value="">
      <div class="m-acts"><button class="btn btn-ghost" data-act="closeModal">取消</button>
      <button class="btn btn-brand" id="ntGo">开启</button></div>`);
    $("#ntGo").onclick = async () => {
      const t = $("#ntInput").value.trim();
      if (!t) { toast("给新学期起个名字"); return; }
      try {
        await api("/api/settings", { method: "PUT", body: { current_term: t } });
        state.meta = await api("/api/meta");
        state.terms = []; state.stats = null;
        closeModal(); toast(`新学期「${t}」开始啦 🎉`); renderMe();
      } catch (e) { toast(e.message); }
    };
  });
  box.querySelector("[data-act=chgCode]")?.addEventListener("click", () => {
    openModal(`<h3>修改家长访问码</h3>
      <div class="field-label">旧访问码</div><input class="input" id="ocOld" type="password">
      <div class="field-label">新访问码（6~12 位数字或字母）</div><input class="input" id="ocNew" type="password">
      <div class="m-acts"><button class="btn btn-ghost" data-act="closeModal">取消</button>
      <button class="btn btn-brand" id="ocGo">保存</button></div>`);
    $("#ocGo").onclick = async () => {
      try {
        await api("/api/settings", { method: "PUT", body: {
          old_access_code: $("#ocOld").value.trim(), new_access_code: $("#ocNew").value.trim() } });
        closeModal(); toast("家长访问码已修改，新设备用新码登录");
      } catch (e) { toast(e.message); }
    };
  });
  box.querySelector("[data-act=chgChildCode]")?.addEventListener("click", () => {
    openModal(`<h3>修改孩子访问码</h3>
      <p class="confirm-text">孩子设备用它登录，只能看到复习界面。修改后，孩子设备下次登录需用新码（当前已登录的设备不受影响）。</p>
      <div class="field-label">新的孩子访问码（6~12 位数字或字母）</div><input class="input" id="ccNew" type="password">
      <div class="m-acts"><button class="btn btn-ghost" data-act="closeModal">取消</button>
      <button class="btn btn-brand" id="ccGo">保存</button></div>`);
    $("#ccGo").onclick = async () => {
      try {
        await api("/api/settings", { method: "PUT", body: { new_child_code: $("#ccNew").value.trim() } });
        closeModal(); toast("孩子访问码已修改");
      } catch (e) { toast(e.message); }
    };
  });
  box.querySelector("[data-act=backupNow]")?.addEventListener("click", async () => {
    try { await api("/api/backup", { method: "POST" }); toast("备份完成 ✅"); renderMe(); }
    catch (e) { toast(e.message); }
  });
  box.querySelector("[data-act=dlBackup]")?.addEventListener("click", () =>
    window.open("/api/backup/latest/download", "_blank"));
}

/* ================= 启动 ================= */
boot();
