/* 离心机转子配平规划器 —— 原生前端逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  slots: [],            // {index, angle, enabled}
  tubes: [],            // {id, mass, locked, slot}
  result: null,         // 最新计算结果
  record: null,         // 含 id/name/request
  replay: { steps: [], pos: 0, timer: null },
  nextTubeId: 1,
};

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._h);
  toast._h = setTimeout(() => (t.className = "toast"), 3200);
}
function fmt(x, d = 2) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  if (Math.abs(x) >= 1000) return x.toFixed(0);
  return Number(x).toFixed(d);
}
function angleNorm(a) { return ((a % 360) + 360) % 360; }

// ---------------------------------------------------------------------------
// 输入:槽位
// ---------------------------------------------------------------------------
function genSlots() {
  const n = Math.max(2, Math.min(128, parseInt($("slotCount").value) || 12));
  $("slotCount").value = n;
  state.slots = Array.from({ length: n }, (_, i) => ({
    index: i, angle: +(360 * i / n).toFixed(4), enabled: true,
  }));
  renderSlotEditor();
  drawRotor(null);
}

function renderSlotEditor() {
  const box = $("slotEditor");
  box.innerHTML = "";
  const table = document.createElement("table");
  table.innerHTML = "<thead><tr><th>槽</th><th>角度°</th><th>启用</th></tr></thead>";
  const tb = document.createElement("tbody");
  state.slots.forEach((s) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${s.index}</td>
      <td><input type="number" step="any" value="${s.angle}" data-role="angle" data-idx="${s.index}" style="width:90px"></td>
      <td><input type="checkbox" ${s.enabled ? "checked" : ""} data-role="en" data-idx="${s.index}"></td>`;
    tb.appendChild(tr);
  });
  table.appendChild(tb);
  box.appendChild(table);
  table.addEventListener("change", (e) => {
    const idx = +e.target.dataset.idx;
    const sl = state.slots[idx];
    if (e.target.dataset.role === "angle") sl.angle = parseFloat(e.target.value) || 0;
    if (e.target.dataset.role === "en") sl.enabled = e.target.checked;
    drawRotor(state.result);
  });
}

// ---------------------------------------------------------------------------
// 输入:试管
// ---------------------------------------------------------------------------
function addTube(t) {
  const id = t?.id ?? state.nextTubeId++;
  state.nextTubeId = Math.max(state.nextTubeId, id + 1);
  state.tubes.push({
    id,
    mass: t?.mass ?? 50,
    locked: t?.locked ?? false,
    slot: t?.slot ?? null,
  });
  renderTubes();
}

function renderTubes() {
  const tb = $("tubeTable").querySelector("tbody");
  tb.innerHTML = "";
  state.tubes.forEach((t, i) => {
    const tr = document.createElement("tr");
    tr.className = "tube-row";
    const opts = ['<option value="">—</option>']
      .concat(state.slots.map((s) => `<option value="${s.index}" ${t.slot === s.index ? "selected" : ""}>${s.index}</option>`))
      .join("");
    tr.innerHTML = `
      <td><input type="number" data-f="id" value="${t.id}" style="width:52px"></td>
      <td><input type="number" step="any" data-f="mass" value="${t.mass}"></td>
      <td style="text-align:center"><input type="checkbox" data-f="locked" ${t.locked ? "checked" : ""}></td>
      <td><select data-f="slot" ${t.locked ? "" : "disabled"}>${opts}</select></td>
      <td><button class="iconbtn" data-act="del">删</button></td>`;
    tr.addEventListener("input", (e) => {
      const f = e.target.dataset.f;
      if (!f) return;
      if (f === "id") t.id = parseInt(e.target.value) || t.id;
      if (f === "mass") t.mass = parseFloat(e.target.value) || 0;
      if (f === "locked") {
        t.locked = e.target.checked;
        renderTubes();
      }
      if (f === "slot") t.slot = e.target.value === "" ? null : parseInt(e.target.value);
    });
    tr.querySelector('[data-act="del"]').onclick = () => {
      state.tubes.splice(i, 1);
      renderTubes();
    };
    tb.appendChild(tr);
  });
  $("tubeCount").textContent = `共 ${state.tubes.length} 支`;
}

// ---------------------------------------------------------------------------
// SVG 转子绘制
// ---------------------------------------------------------------------------
const SVG_NS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function polar(cx, cy, r, angleDeg) {
  // 0° 在正上方, 顺时针
  const a = (angleDeg - 90) * Math.PI / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

function drawRotor(result, replayPlacements = null, replayVectors = null) {
  const svg = $("rotor");
  svg.innerHTML = `
    <defs>
      <marker id="arrow1" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
        <path d="M0,0 L6,3 L0,6 Z" fill="#4f9dff"/>
      </marker>
      <marker id="arrow2" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
        <path d="M0,0 L6,3 L0,6 Z" fill="#36d399"/>
      </marker>
    </defs>
    <circle class="hub" cx="210" cy="210" r="30"/>
    <text x="210" y="206" text-anchor="middle" fill="#94a3c0" font-size="9">R=${$("radius").value}mm</text>
    <text x="210" y="220" text-anchor="middle" fill="#94a3c0" font-size="9">${$("rpm").value}rpm</text>`;

  if (!state.slots.length) return;
  const R = 158;
  const tubeBySlot = new Map();
  const movedSlots = new Set();
  let placements;
  if (replayPlacements) {
    placements = replayPlacements;
  } else if (result) {
    placements = result.placements;
    result.placements.forEach((p) => {
      if (p.moved) movedSlots.add(p.slot);
    });
  }
  if (placements) placements.forEach((p) => { if (p.slot !== null) tubeBySlot.set(p.slot, p); });

  // 对径配对辅助线
  if (result && !replayPlacements) {
    result.pairs.forEach((pr) => {
      const a = state.slots[pr.slot_a], b = state.slots[pr.slot_b];
      if (!a || !b) return;
      const [x1, y1] = polar(210, 210, R, a.angle);
      const [x2, y2] = polar(210, 210, R, b.angle);
      svg.appendChild(el("line", {
        x1, y1, x2, y2,
        stroke: pr.opposite_deviation_deg <= 0.5 ? "#36d39955" : "#fbbd2344",
        "stroke-width": 1, "stroke-dasharray": "4 4",
      }));
    });
  }

  state.slots.forEach((s) => {
    const [x, y] = polar(210, 210, R, s.angle);
    const g = el("g", { class: "slot-node" + (s.enabled ? "" : " disabled"),
      transform: `translate(${x.toFixed(2)},${y.toFixed(2)})` });
    const occ = tubeBySlot.get(s.index);
    if (occ) {
      if (occ.locked) g.classList.add("locked");
      if (movedSlots.has(s.index)) g.classList.add("moved");
    }
    g.appendChild(el("circle", { class: "ring", r: occ ? 20 : 15 }));
    if (occ) {
      const t1 = el("text", { class: "tube-label", y: -2 });
      t1.textContent = "T" + occ.tube_id;
      g.appendChild(t1);
      const t2 = el("text", { y: 9, "font-size": 9, fill: "#cdd8ee", "text-anchor": "middle" });
      t2.textContent = occ.mass + "g";
      g.appendChild(t2);
      const t3 = el("text", { y: 32, "font-size": 9, fill: "#94a3c0", "text-anchor": "middle" });
      t3.textContent = "#" + s.index;
      g.appendChild(t3);
    } else {
      const t1 = el("text", { y: 3, "font-size": 10 });
      t1.textContent = s.index;
      g.appendChild(t1);
    }
    g.addEventListener("click", () => {
      // 仅点击空槽时切换启用以避免误触; 禁用槽点击重新启用
      if (!occ) {
        s.enabled = !s.enabled;
        renderSlotEditor();
        drawRotor(result, replayPlacements);
      }
    });
    g.addEventListener("mouseenter", () => g.classList.add("hover"));
    g.addEventListener("mouseleave", () => g.classList.remove("hover"));
    svg.appendChild(g);
  });

  // 不平衡向量(回放时显示该步瞬时向量)
  const vectors = replayVectors || (replayPlacements ? null : result?.metrics) || null;
  if (vectors) {
    const scale1 = Math.min(130 / Math.max(vectors.u1_mag_g_mm, 1), 0.13);
    const scale2 = Math.min(130 / Math.max(vectors.u2_mag_g_mm, 1), 0.13);
    drawVector(svg, vectors.u1_angle_deg, vectors.u1_mag_g_mm * scale1, "vec vec1", "url(#arrow1)", 210, 210);
    drawVector(svg, vectors.u2_angle_deg, vectors.u2_mag_g_mm * scale2, "vec vec2", "url(#arrow2)", 210, 210);
  }
}

function drawVector(svg, angleDeg, len, cls, marker, cx, cy) {
  if (len < 2) return;
  const [x, y] = polar(cx, cy, len, angleDeg);
  svg.appendChild(el("line", { x1: cx, y1: cy, x2: x, y2: y, class: cls, "marker-end": marker }));
}

// ---------------------------------------------------------------------------
// 结果渲染
// ---------------------------------------------------------------------------
function renderMetrics(m) {
  $("mU1").innerHTML = `${fmt(m.u1_mag_g_mm)} <span class="u">g·mm @${fmt(m.u1_angle_deg, 1)}°</span>`;
  $("mU2").innerHTML = `${fmt(m.u2_mag_g_mm)} <span class="u">g·mm @${fmt(m.u2_angle_deg, 1)}°</span>`;
  $("mForce").innerHTML = `${fmt(m.centrifugal_force_n, 3)} <span class="u">N</span>`;
  $("mMoment").innerHTML = `${fmt(m.resultant_moment_n_m, 4)} <span class="u">N·m</span>`;
  $("mEcc").innerHTML = `${fmt(m.equivalent_eccentricity_um, 2)} <span class="u">μm</span>
    <span class="grade g${m.grade}">${m.grade}</span>`;
  $("mGrade").innerHTML = `<span class="grade g${m.grade}" style="font-size:15px">${m.grade}</span>
    <span class="u">${{ A: "优", B: "良", C: "合格", D: "偏高" }[m.grade]}</span>`;
}

function renderResult(record) {
  state.record = record;
  state.result = record.result;
  const r = state.result, m = r.metrics;
  renderMetrics(m);
  $("solveInfo").textContent = `求解耗时 ${r.solve_time_ms ?? "–"} ms · ${r.total_tubes} 支试管 · ${r.steps.length} 个步骤`;
  const sum = $("summary");
  sum.style.display = "block";
  sum.innerHTML = r.summary;
  $("btnExport").disabled = false;
  $("btnCopy").disabled = false;

  drawRotor(r);
  renderPairs(r);
  renderPlacements(r);
  initReplay(r);
}

function renderPairs(r) {
  const box = $("pairList");
  box.innerHTML = "";
  if (!r.pairs.length && !r.singletons.length) {
    box.innerHTML = '<p class="muted">暂无摆放。</p>';
    return;
  }
  r.pairs.forEach((p) => {
    const d = document.createElement("div");
    d.className = "reason";
    d.innerHTML = `<div class="rhead">对径配对 · 槽 ${p.slot_a} ↔ 槽 ${p.slot_b}
      ${p.locked_a || p.locked_b ? '<span class="pill lock">含锁定</span>' : ""}</div>${p.reason}`;
    box.appendChild(d);
  });
  r.singletons.forEach((s) => {
    const d = document.createElement("div");
    d.className = "reason single";
    d.innerHTML = `<div class="rhead">无对径搭档 · 槽 ${s.slot}
      ${s.locked ? '<span class="pill lock">锁定</span>' : ""}</div>${s.reason}`;
    box.appendChild(d);
  });
}

function renderPlacements(r) {
  const tb = $("placementTable").querySelector("tbody");
  tb.innerHTML = "";
  r.placements.forEach((p) => {
    const tr = document.createElement("tr");
    const tags = [
      p.locked ? '<span class="pill lock">锁定</span>' : "",
      p.moved ? '<span class="pill move">已移动</span>' : "",
    ].join(" ");
    tr.innerHTML = `
      <td>${p.tube_id}</td><td>${p.mass}</td>
      <td>${p.slot ?? "–"}</td>
      <td>${p.angle !== null && p.angle !== undefined ? fmt(p.angle, 1) + "°" : "–"}</td>
      <td>${p.previous_slot ?? "–"}</td><td>${tags}</td>`;
    tb.appendChild(tr);
  });
}

// ---------------------------------------------------------------------------
// 回放
// ---------------------------------------------------------------------------
function initReplay(r) {
  stopPlay();
  state.replay.steps = r.steps;
  state.replay.pos = r.steps.length - 1;
  const range = $("rpRange");
  range.min = 0;
  range.max = Math.max(0, r.steps.length - 1);
  range.value = range.max;
  showReplayStep();
}

function showReplayStep() {
  const { steps, pos } = state.replay;
  if (!steps.length) return;
  const step = steps[pos];
  $("rpPos").textContent = `${pos + 1}/${steps.length}`;
  $("rpRange").value = pos;
  const kindMap = { init: "初始", pair: "配对", single: "补偿", refine: "微调优化" };
  $("stepReason").innerHTML =
    `<span class="badge">${kindMap[step.kind] || step.kind} · 步骤 ${pos + 1}</span> ${step.reason}
     <div class="small" style="margin-top:4px">此步后: |U₁|=${fmt(step.vectors.u1_mag)} g·mm,
      |U₂|=${fmt(step.vectors.u2_mag)} g·mm</div>`;
  drawRotor(state.result, step.placements, {
    u1_mag_g_mm: step.vectors.u1_mag, u1_angle_deg: step.vectors.u1_angle,
    u2_mag_g_mm: step.vectors.u2_mag, u2_angle_deg: step.vectors.u2_angle,
  });
}

function stopPlay() {
  if (state.replay.timer) { clearInterval(state.replay.timer); state.replay.timer = null; $("rpPlay").textContent = "▶ 播放"; }
}
$("rpFirst").onclick = () => { stopPlay(); state.replay.pos = 0; showReplayStep(); };
$("rpPrev").onclick = () => { stopPlay(); state.replay.pos = Math.max(0, state.replay.pos - 1); showReplayStep(); };
$("rpNext").onclick = () => { stopPlay(); state.replay.pos = Math.min(state.replay.steps.length - 1, state.replay.pos + 1); showReplayStep(); };
$("rpLast").onclick = () => { stopPlay(); state.replay.pos = state.replay.steps.length - 1; showReplayStep(); };
$("rpRange").oninput = (e) => { stopPlay(); state.replay.pos = +e.target.value; showReplayStep(); };
$("rpPlay").onclick = () => {
  if (state.replay.timer) { stopPlay(); return; }
  $("rpPlay").textContent = "⏸ 暂停";
  state.replay.timer = setInterval(() => {
    if (state.replay.pos >= state.replay.steps.length - 1) { stopPlay(); state.replay.pos = 0; showReplayStep(); return; }
    state.replay.pos++; showReplayStep();
  }, 900);
};

// ---------------------------------------------------------------------------
// 调用后端
// ---------------------------------------------------------------------------
function buildRequest() {
  const req = {
    name: $("planName").value || "未命名方案",
    slot_count: state.slots.length,
    slots: state.slots,
    tubes: state.tubes.map((t) => ({
      id: t.id, mass: t.mass, locked: t.locked,
      slot: t.locked ? t.slot : null,
    })),
    radius_mm: parseFloat($("radius").value),
    rpm: parseFloat($("rpm").value),
    w1: parseFloat($("w1").value),
    w2: parseFloat($("w2").value),
    move_weight: parseFloat($("moveWeight").value),
    restarts: parseInt($("restarts").value),
    chain_steps: parseInt($("chainSteps").value),
  };
  if ($("incremental").checked && state.result) {
    req.previous = state.result.placements
      .filter((p) => p.slot !== null && p.slot !== undefined)
      .map((p) => ({ tube_id: p.tube_id, slot: p.slot }));
    req.parent_id = state.record?.id ?? null;
  }
  return req;
}

async function compute() {
  if (!state.tubes.length) { toast("请先添加试管。", true); return; }
  // 前端预检:锁定试管必须有槽
  for (const t of state.tubes) {
    if (t.locked && (t.slot === null || t.slot === undefined || isNaN(t.slot))) {
      toast(`锁定试管 ${t.id} 必须指定固定槽位。`, true); return;
    }
    if (!(t.mass > 0)) { toast(`试管 ${t.id} 质量必须为正数。`, true); return; }
  }
  const enabled = state.slots.filter((s) => s.enabled).length;
  if (state.tubes.length > enabled) { toast(`试管数(${state.tubes.length})超过可用槽位数(${enabled})。`, true); return; }

  $("btnPlan").disabled = true;
  $("btnPlan").textContent = "计算中…";
  try {
    const resp = await fetch("/api/plan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequest()),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "计算失败");
    renderResult(data);
    if ($("incremental").checked) $("incremental").checked = false;
    toast(data.id ? `方案已保存 (#${data.id})。` : "计算完成。");
  } catch (e) {
    toast(e.message, true);
  } finally {
    $("btnPlan").disabled = false;
    $("btnPlan").textContent = "计算配平方案";
  }
}
$("btnPlan").onclick = compute;

// ---------------------------------------------------------------------------
// 导出
// ---------------------------------------------------------------------------
$("btnExport").onclick = () => {
  if (!state.record) return;
  const blob = new Blob([JSON.stringify(state.record, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `balance-plan-${state.record.id ?? "draft"}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
};
$("btnCopy").onclick = async () => {
  if (!state.record) return;
  try { await navigator.clipboard.writeText(JSON.stringify(state.record, null, 2)); toast("JSON 已复制到剪贴板。"); }
  catch { toast("复制失败,请使用导出。", true); }
};

// ---------------------------------------------------------------------------
// 历史
// ---------------------------------------------------------------------------
const historySel = new Set();

function syncCompareButton() {
  const btn = $("btnCompare");
  btn.disabled = historySel.size !== 2;
  $("compareHint").textContent = historySel.size === 0
    ? "勾选两条方案后可并排比较摆放明细与不平衡量。"
    : `已选 ${historySel.size} 条(需恰好 2 条)。`;
}

$("btnHistory").onclick = async () => {
  historySel.clear();
  syncCompareButton();
  $("historyDialog").classList.add("open");
  await renderHistory();
};

async function renderHistory() {
  const resp = await fetch("/api/plans?limit=100");
  const data = await resp.json();
  const box = $("historyList");
  box.innerHTML = "";
  if (!data.items.length) { box.innerHTML = '<p class="muted">暂无保存的方案。</p>'; return; }
  data.items.forEach((it) => {
    const d = document.createElement("div");
    d.className = "history-item" + (historySel.has(it.id) ? " selected" : "");
    const checked = historySel.has(it.id) ? "checked" : "";
    d.innerHTML = `
      <div class="rowline">
        <input type="checkbox" title="加入比较" ${checked}>
        <div>
          <div>#${it.id} ${it.name} ${it.parent_id ? `<span class="small">(派生自 #${it.parent_id})</span>` : ""}</div>
          <div class="small">${new Date(it.created_at).toLocaleString()} ·
            U₁=${fmt(it.u1_mag_g_mm)} g·mm · U₂=${fmt(it.u2_mag_g_mm)} · 评级 ${it.grade} · F=${fmt(it.centrifugal_force_n, 2)}N</div>
        </div>
      </div>
      <div class="rowline">
        <button class="iconbtn" data-a="load">载入</button>
        <button class="iconbtn" data-a="export">导出</button>
        <button class="iconbtn" data-a="del">删除</button>
      </div>`;
    const cb = d.querySelector('input[type="checkbox"]');
    cb.onclick = (e) => e.stopPropagation();
    cb.onchange = () => {
      if (cb.checked) {
        if (historySel.size >= 2) { cb.checked = false; toast("最多选择两条方案进行比较。", true); return; }
        historySel.add(it.id);
      } else {
        historySel.delete(it.id);
      }
      d.classList.toggle("selected", cb.checked);
      syncCompareButton();
    };
    d.querySelector('[data-a="load"]').onclick = () => loadPlan(it.id);
    d.querySelector('[data-a="export"]').onclick = () => { window.location = `/api/plan/${it.id}/export`; };
    d.querySelector('[data-a="del"]').onclick = async () => {
      if (!confirm(`删除方案 #${it.id}?`)) return;
      await fetch(`/api/plan/${it.id}`, { method: "DELETE" });
      historySel.delete(it.id);
      syncCompareButton();
      renderHistory();
    };
    box.appendChild(d);
  });
}
$("btnCloseHistory").onclick = () => $("historyDialog").classList.remove("open");

// ---------------------------------------------------------------------------
// 方案比较
// ---------------------------------------------------------------------------
function cmpFmt(v, nd) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (typeof v === "string") return v;
  const n = Number(v);
  if (Math.abs(n) >= 1000) return n.toFixed(0);
  return n.toFixed(nd ?? 2);
}

function renderDiffTable(title, rows) {
  if (!rows || !rows.length) return "";
  const body = rows.map((r) => {
    const bTxt = r.change
      ? `${cmpFmt(r.value_b, r.ndigits)}${r.unit ? " " + r.unit : ""} <span class="small">(${r.change})</span>`
      : `${cmpFmt(r.value_b, r.ndigits)}${r.unit ? " " + r.unit : ""}`;
    return `<tr><td>${r.label}</td>
      <td style="text-align:right">${cmpFmt(r.value_a, r.ndigits)}${r.unit ? " " + r.unit : ""}</td>
      <td style="text-align:right">${bTxt}</td></tr>`;
  }).join("");
  return `<div class="diff-group-title">${title}</div>
    <table class="cmp-table"><thead><tr><th>参数</th><th style="text-align:right">方案 A</th><th style="text-align:right">方案 B</th></tr></thead>
    <tbody>${body}</tbody></table>`;
}

function renderCompare(data) {
  const A = data.plan_a, B = data.plan_b, body = $("compareBody");
  const head = `<div class="cmp-grid">
      <div class="cmp-col"><h3>A · #${A.id} ${A.name}</h3>
        <div class="small">${new Date(A.created_at).toLocaleString()} · 评级 <b>${A.grade}</b></div></div>
      <div class="cmp-col"><h3 class="b">B · #${B.id} ${B.name}</h3>
        <div class="small">${new Date(B.created_at).toLocaleString()} · 评级 <b>${B.grade}</b></div></div>
    </div>`;

  const cond = data.same_conditions
    ? '<div class="cmp-ok">两方案的转子几何与求解输入一致, 指标差值可视为<b>同条件</b>下的优化对比。</div>'
    : `<div class="cmp-warn">⚠ 两方案的转子或求解输入不同(<b>${
        data.differing_groups.map((g) => ({ rotor: "转子参数", solver: "求解输入", tubes: "试管清单" }[g])).join("、")
      }</b>), 下列指标差值并非同条件优化结果, 请勿直接据差值评判优劣; 参数差异见下。</div>`;

  const metricRows = data.metrics.map((m) => {
    let dTxt = '<span class="delta-flat">–</span>';
    if (m.delta_b_minus_a !== null && m.delta_b_minus_a !== undefined) {
      const d = m.delta_b_minus_a;
      if (Math.abs(d) < 1e-9) {
        dTxt = '<span class="delta-flat">0</span>';
      } else {
        const cls = d > 0 ? "delta-up" : "delta-down";
        const sign = d > 0 ? "+" : "−";
        dTxt = `<span class="${cls}">${sign}${cmpFmt(Math.abs(d), m.ndigits)}${m.unit ? " " + m.unit : ""}</span>`;
      }
    }
    return `<tr><td>${m.label}</td>
      <td style="text-align:right">${cmpFmt(m.value_a, m.ndigits)}${m.unit ? " " + m.unit : ""}</td>
      <td style="text-align:right">${cmpFmt(m.value_b, m.ndigits)}${m.unit ? " " + m.unit : ""}</td>
      <td style="text-align:right">${dTxt}</td></tr>`;
  }).join("");
  const metrics = `<h2 style="font-size:13px;margin:12px 0 6px">一阶 / 二阶不平衡量与力学指标(B − A)</h2>
    <table class="cmp-table">
      <thead><tr><th>指标</th><th style="text-align:right">方案 A</th><th style="text-align:right">方案 B</th><th style="text-align:right">差值</th></tr></thead>
      <tbody>${metricRows}</tbody></table>
    <div class="small" style="margin-top:4px">方向类差值为按角度环绕归一化到 ±180° 的变化量; 其余指标差值为 B 减 A。</div>`;

  const inputDiff = data.same_conditions ? "" : (
    `<h2 style="font-size:13px;margin:12px 0 4px">参数差异</h2>` +
    renderDiffTable("转子几何(槽位 / 半径)", data.input_diff.rotor) +
    renderDiffTable("求解输入(转速 / 权重 / 搜索参数)", data.input_diff.solver) +
    renderDiffTable("试管清单(增删 / 质量 / 锁定)", data.input_diff.tubes)
  );

  const stMap = {
    added: ['新增', 'st-added'], removed: ['移除', 'st-removed'],
    moved: ['换槽', 'st-moved'], unchanged: ['未变', 'st-unchanged'],
  };
  const c = data.placement_counts;
  const plRows = data.placements.map((r) => {
    const [stTxt, stCls] = stMap[r.status];
    const lock = (la, lb) => (la || lb ? ` <span class="pill lock">锁</span>` : "");
    const slot = (s, ang) => s === null || s === undefined
      ? '<span class="small">未放置</span>'
      : `${s} <span class="small">(${cmpFmt(ang, 1)}°)</span>`;
    return `<tr>
      <td>${r.tube_id}${lock(r.locked_a, r.locked_b)}</td>
      <td>${cmpFmt(r.mass_a ?? r.mass_b, 1)}</td>
      <td>${slot(r.slot_a, r.angle_a)}</td>
      <td>${slot(r.slot_b, r.angle_b)}</td>
      <td><span class="status-tag ${stCls}">${stTxt}</span>${r.status === "moved" ? ` <span class="small">${r.slot_a}→${r.slot_b}</span>` : ""}</td>
    </tr>`;
  }).join("");
  const placements = `<h2 style="font-size:13px;margin:12px 0 6px">摆放明细(按试管编号合并)
      <span class="small">新增 ${c.added} · 移除 ${c.removed} · 换槽 ${c.moved} · 未变 ${c.unchanged}</span></h2>
    <div class="legend" style="justify-content:flex-start">
      <span><i style="background:var(--acc2)"></i>新增(B 有 A 无)</span>
      <span><i style="background:var(--bad)"></i>移除(A 有 B 无)</span>
      <span><i style="background:var(--warn)"></i>换槽(编号相同槽位不同)</span>
      <span><i style="background:var(--mut)"></i>未变</span>
    </div>
    <table class="cmp-table">
      <thead><tr><th>试管</th><th>质量(g)</th><th>A 槽位</th><th>B 槽位</th><th>状态</th></tr></thead>
      <tbody>${plRows}</tbody></table>`;

  body.innerHTML = head + cond + metrics + inputDiff + placements;
}

$("btnCompare").onclick = async () => {
  if (historySel.size !== 2) return;
  const [a, b] = [...historySel];
  try {
    const resp = await fetch(`/api/plans/compare?a=${a}&b=${b}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "比较失败");
    renderCompare(data);
    $("compareDialog").classList.add("open");
  } catch (e) {
    toast(e.message, true);
  }
};
$("btnCloseCompare").onclick = () => $("compareDialog").classList.remove("open");

async function loadPlan(id) {
  const resp = await fetch(`/api/plan/${id}`);
  if (!resp.ok) { toast("载入失败。", true); return; }
  const data = await resp.json();
  const q = data.request;
  $("planName").value = data.name;
  state.slots = q.slots.map((s) => ({ ...s }));
  state.tubes = q.tubes.map((t) => ({ ...t }));
  state.nextTubeId = Math.max(0, ...state.tubes.map((t) => t.id)) + 1;
  $("slotCount").value = q.slot_count;
  $("radius").value = q.radius_mm; $("rpm").value = q.rpm;
  $("w1").value = q.w1; $("w2").value = q.w2;
  $("moveWeight").value = q.move_weight;
  $("restarts").value = q.restarts; $("chainSteps").value = q.chain_steps;
  renderSlotEditor(); renderTubes();
  renderResult({ id: data.id, name: data.name, request: q, result: data.result });
  $("historyDialog").classList.remove("open");
  toast(`已载入方案 #${id}。`);
}

// ---------------------------------------------------------------------------
// 标签页 & 初始化
// ---------------------------------------------------------------------------
document.querySelectorAll(".tabs button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    ["replay", "pairs", "placements"].forEach((t) => {
      $("tab-" + t).style.display = t === b.dataset.tab ? "" : "none";
    });
  };
});

$("btnGenSlots").onclick = genSlots;
$("slotCount").onchange = genSlots;
$("btnAddTube").onclick = () => addTube();

function loadSample() {
  $("slotCount").value = 12;
  $("radius").value = 100; $("rpm").value = 4000;
  $("planName").value = "示例: 6 支非等质量试管";
  genSlots();
  state.tubes = [];
  state.nextTubeId = 1;
  [[50], [50], [51], [49], [30], [30]].forEach(([mass]) => addTube({ mass }));
  renderTubes();
  drawRotor(null);
}
$("btnSample").onclick = loadSample;

// 初始
genSlots();
loadSample();
