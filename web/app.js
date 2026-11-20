/* Process Mining Advisor dashboard: plain JS, no build step. */
const $ = (s) => document.querySelector(s);
const api = async (path, opts = {}) => {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || r.statusText));
  return body;
};
const fmt = (x, d = 0) => (x === null || x === undefined ? "–" : Number(x).toLocaleString("sr-RS", { maximumFractionDigits: d, minimumFractionDigits: d }));
const rsd = (x) => (x === null || x === undefined ? "–" : fmt(x) + " RSD");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const toast = (msg) => { const t = $("#toast"); t.textContent = msg; t.style.display = "block"; setTimeout(() => (t.style.display = "none"), 3500); };

let state = { status: null, levers: [], estimates: [], cy: null };
const LABELS = {
  manual_hours_per_year: "Ručni rad (h)", cycle_days_mean: "Prosečno trajanje (dani)", cycle_days_median: "Medijana trajanja (dani)",
  discount_missed_rsd_per_year: "Propušteni popusti (RSD)", paid_twice_per_year: "Dupla plaćanja (broj)",
  approval_wait_h_mean_logistika: "Čekanje na odobrenje, Logistika (h)", maverick_cases_per_year: "Nabavke bez PO (broj)",
};

async function loadAll() {
  state.status = await api("/api/status");
  const s = state.status;
  $("#runLabel").textContent = `${s.label} · ${s.source === "simulated" ? "simulirani log" : "učitan log"}`;
  $("#llmBadge").textContent = s.llm_provider ? `AI: ${s.llm_model}` : "AI: šablon (nema ključa)";
  state.levers = await api("/api/levers");
  const [ov, est, fnd] = await Promise.all([api("/api/overview"), api("/api/estimates"), api("/api/findings")]);
  state.estimates = est;
  renderKpis(ov, fnd, est);
  renderFindings(fnd);
  renderLevers(est, []);
  renderWhatIfChecks(fnd);
  renderConformance(ov);
  await Promise.all([renderMap(), renderCandidates(), renderVariants(), renderAdvisor(), renderAudit()]);
  if (s.simulated) api("/api/compare").then((c) => renderLevers(est, c)).catch(() => {});
  else $("#compareNote").textContent = "(šta-ako simulacija nije dostupna za učitan log)";
}

function renderKpis(ov, fnd, est) {
  const k = ov.kpis;
  const missed = est.find((e) => e.lever === "daily_payment_run");
  const tot = est.filter((e) => e.applicable && e.net_rsd_per_year > 0 && fnd.some((f) => f.levers.includes(e.lever)))
    .reduce((a, e) => a + e.net_rsd_per_year, 0);
  const items = [
    [fmt(k.cases), `slučajeva · ${k.period}`],
    [fmt(k.cycle_days_median, 1) + " d", "medijana trajanja (p90 " + fmt(k.cycle_days_p90, 1) + " d)"],
    [fmt(k.manual_hours_per_year) + " h", `ručni rad godišnje (${fmt(k.manual_fte, 1)} FTE)`],
    [fmt(ov.conformance.fitting_cases_pct, 1) + "%", "slučajeva po propisanom procesu"],
    [fmt(k.variants), "varijanti procesa"],
    [String(fnd.length), "nalaza"],
    [rsd(tot), "procena neto uštede / god. (zbir, vidi Šta ako)"],
  ];
  $("#kpis").innerHTML = items.map(([v, l]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  void missed;
}

/* ------------------------------------------------------------------ process map */
async function renderMap() {
  const minShare = Number($("#minShare").value) / 100;
  $("#minShareVal").textContent = $("#minShare").value;
  const g = await api(`/api/dfg?min_share=${minShare}`);
  const mode = document.querySelector("input[name=edgeLabel]:checked").value;
  const nodes = new Map(g.nodes.map((n) => [n.id, n]));
  const used = new Set();
  g.edges.forEach((e) => { used.add(e.source); used.add(e.target); });
  const maxC = Math.max(...g.edges.map((e) => e.count));
  const els = [];
  for (const id of used) {
    const n = nodes.get(id) || { id, events: 0 };
    const pseudo = id.startsWith("▶") || id.startsWith("■");
    const manual = (n.manual_share_pct || 0) >= 50;
    els.push({ data: { id, label: pseudo ? id : `${id}\n${fmt(n.events)}`, kind: pseudo ? "pseudo" : manual ? "manual" : "system", info: n } });
  }
  g.edges.forEach((e, i) => {
    const label = mode === "count" ? fmt(e.count) : e.wait_h_median >= 24 ? fmt(e.wait_h_median / 24, 1) + " d" : fmt(e.wait_h_median, 1) + " h";
    els.push({ data: { id: "e" + i, source: e.source, target: e.target, label: e.source.startsWith("▶") || e.target.startsWith("■") ? fmt(e.count) : label,
      w: 1 + 7 * Math.sqrt(e.count / maxC), slow: e.wait_h_median > 24 && !e.target.startsWith("Receive") ? 1 : 0, info: e } });
  });
  if (state.cy) state.cy.destroy();
  state.cy = cytoscape({
    container: $("#cy"), elements: els, wheelSensitivity: 0.2,
    style: [
      { selector: "node", style: { label: "data(label)", "text-wrap": "wrap", "text-valign": "center", "font-size": 12, "font-weight": 600, color: "#fff",
        "background-color": "#4b8bbe", shape: "round-rectangle", width: 210, height: 44, "text-max-width": 200 } },
      { selector: "node[kind='manual']", style: { "background-color": "#e8833a" } },
      { selector: "node[kind='pseudo']", style: { "background-color": "#1d2433", width: 70, height: 26, shape: "ellipse" } },
      { selector: "edge", style: { width: "data(w)", "line-color": "#9aa4b2", "target-arrow-color": "#9aa4b2", "target-arrow-shape": "triangle",
        "curve-style": "bezier", label: "data(label)", "font-size": 11, "text-background-color": "#fff", "text-background-opacity": 0.9,
        "text-background-padding": 2, color: "#475467" } },
      { selector: "edge[slow = 1]", style: { "line-color": "#d43f3a", "target-arrow-color": "#d43f3a", color: "#b42318" } },
      { selector: ":selected", style: { "border-width": 3, "border-color": "#1d2433" } },
    ],
    layout: { name: "dagre", rankDir: "TB", nodeSep: 25, rankSep: 38, edgeSep: 8 },
  });
  state.cy.on("tap", "node", (ev) => {
    const n = ev.target.data("info");
    if (!n.service_min_median && n.service_min_median !== 0) return;
    $("#nodeInfo").innerHTML = `<h3>${esc(n.id)}</h3><table>
      <tr><td>Događaja</td><td class="num">${fmt(n.events)}</td></tr>
      <tr><td>Rad (medijana)</td><td class="num">${fmt(n.service_min_median, 1)} min</td></tr>
      <tr><td>Čekanje pre (medijana)</td><td class="num">${fmt(n.wait_h_median, 1)} h</td></tr>
      <tr><td>Ručno</td><td class="num">${fmt(n.manual_share_pct, 1)}%</td></tr></table>`;
  });
  state.cy.on("tap", "edge", (ev) => {
    const e = ev.target.data("info");
    $("#nodeInfo").innerHTML = `<h3>${esc(e.source)} → ${esc(e.target)}</h3><table>
      <tr><td>Prelaza</td><td class="num">${fmt(e.count)}</td></tr>
      <tr><td>Čekanje, medijana</td><td class="num">${fmt(e.wait_h_median, 1)} h</td></tr>
      <tr><td>Čekanje, prosek</td><td class="num">${fmt(e.wait_h_mean, 1)} h</td></tr></table>`;
  });
}

async function renderCandidates() {
  const a = await api("/api/activities");
  $("#candidates").innerHTML = `<table><tr><th>Aktivnost</th><th class="num">Puta/god.</th><th class="num">Min (med.)</th><th class="num">Sati/god.</th><th class="num">RSD/god.</th></tr>` +
    a.candidates.map((c) => `<tr><td>${esc(c.activity)}</td><td class="num">${fmt(c.manual_events_per_year)}</td><td class="num">${fmt(c.service_min_median, 1)}</td>
      <td class="num">${fmt(c.hours_per_year)}</td><td class="num">${fmt(c.cost_rsd_per_year)}</td></tr>`).join("") + "</table>";
}

function renderConformance(ov) {
  const c = ov.conformance;
  $("#conformance").innerHTML = `<p><b>${fmt(c.fitting_cases_pct, 1)}%</b> slučajeva prolazi propisani tok (token replay fitness ${fmt(c.log_fitness, 3)}).
    Model otkriven Inductive Miner-om: fitness ${fmt(ov.model.log_fitness, 3)}, precision ${fmt(ov.model.precision, 3)}.</p>
    <table><tr><th>Odstupanje</th><th class="num">Slučajeva</th></tr>` +
    c.top_deviations.map((d) => `<tr><td>${d.order_only ? "drugi redosled istih koraka" :
      (d.extra.length ? "višak: " + d.extra.map(esc).join(", ") : "") + (d.missing.length ? (d.extra.length ? "<br>" : "") + "nedostaje: " + d.missing.map(esc).join(", ") : "")}</td>
      <td class="num">${fmt(d.cases)} (${fmt(d.share_pct, 1)}%)</td></tr>`).join("") + "</table>";
}

/* ------------------------------------------------------------------ findings & levers */
function renderFindings(list) {
  if (!list.length) { $("#findingsList").innerHTML = `<div class="card">Detektori nisu našli probleme iznad pragova.</div>`; return; }
  const title = (id) => (state.levers.find((l) => l.id === id) || {}).title || id;
  $("#findingsList").innerHTML = list.map((f) => `<div class="card finding ${f.severity}">
    <div class="sev">${f.severity === "high" ? "visok prioritet" : "srednji prioritet"}</div>
    <h3>${esc(f.title)}</h3><p>${esc(f.summary)}</p>
    <div class="ev">${Object.entries(f.evidence).map(([k, v]) => `<code>${esc(k)}</code> ${esc(Array.isArray(v) ? v.join(", ") : v)}`).join(" · ")}</div>
    <p class="small">Mera: <b>${f.levers.map((l) => esc(title(l))).join(", ")}</b></p></div>`).join("");
}

function renderLevers(est, cmp) {
  const byC = Object.fromEntries((cmp || []).map((c) => [c.lever, c]));
  const rows = est.filter((e) => e.applicable).sort((a, b) => b.net_rsd_per_year - a.net_rsd_per_year);
  $("#leverTable").innerHTML = `<tr><th>Mera</th><th>Vrsta</th><th class="num">Sati/god.</th><th class="num">Popusti/god.</th><th class="num">Neto RSD/god.</th>
    <th class="num">Uvođenje</th><th class="num">Povrat</th><th class="num">Simulacija</th><th>Pretpostavke</th></tr>` +
    rows.map((e) => {
      const c = byC[e.lever];
      const simTxt = !c ? "–" : c.metric === "approval_wait_h_mean_logistika"
        ? `čekanje ${fmt(c.estimated, 1)} h (procena) / ${fmt(c.simulated, 1)} h (sim.)`
        : `${fmt(c.simulated)} ${c.metric === "manual_hours_per_year" ? "h" : "RSD"}${c.error_pct !== null ? ` (greška ${fmt(c.error_pct, 1)}%)` : ""}`;
      const effects = e.effects && e.effects.approval_wait_h_mean_after !== undefined
        ? `<br><span class="small muted">čekanje ${fmt(e.effects.approval_wait_h_mean_now, 1)} → ${fmt(e.effects.approval_wait_h_mean_after, 1)} h</span>` : "";
      return `<tr><td><b>${esc(e.title)}</b>${effects}</td><td><span class="pill ${e.confidence}">${e.confidence === "measured" ? "izmereno" : "pretpostavka"}</span><span class="pill">${esc(e.kind)}</span></td>
        <td class="num">${fmt(e.hours_per_year)}</td><td class="num">${fmt(e.cash_rsd_per_year)}</td><td class="num"><b>${fmt(e.net_rsd_per_year)}</b></td>
        <td class="num">${fmt(e.one_off_rsd)}</td><td class="num">${e.payback_months ? fmt(e.payback_months, 1) + " mes." : "–"}</td>
        <td class="num small">${simTxt}</td><td class="small muted">${e.assumptions.slice(0, 2).map(esc).join("<br>")}</td></tr>`;
    }).join("");
}

function renderWhatIfChecks(fnd) {
  const suggested = new Set(fnd.flatMap((f) => f.levers));
  $("#leverChecks").innerHTML = state.levers.map((l) => `<label><input type="checkbox" value="${l.id}" ${suggested.has(l.id) ? "checked" : ""}>
    <span><b>${esc(l.title)}</b><br><span class="small muted">${esc(l.description)}</span></span></label>`).join("");
  $("#btnWhatIf").disabled = !state.status.simulated;
  $("#whatifNote").textContent = state.status.simulated ? "Isti kvartal, isti slučajevi, nova pravila. Redovi i rasporedi se preračunavaju."
    : "Za učitan log potreban je simulacioni model kalibrisan na tom logu.";
}

async function runWhatIf() {
  const levers = [...document.querySelectorAll("#leverChecks input:checked")].map((i) => i.value);
  if (!levers.length) return toast("Izaberi bar jednu meru.");
  $("#btnWhatIf").disabled = true;
  $("#whatifResult").textContent = "Simuliram…";
  try {
    const r = await api("/api/whatif", { method: "POST", body: JSON.stringify({ levers }) });
    const b = r.simulation.before, a = r.simulation.after;
    const lowerIsBetter = true;
    $("#whatifResult").innerHTML = `<table><tr><th>Pokazatelj</th><th class="num">Pre</th><th class="num">Posle</th><th class="num">Razlika</th></tr>` +
      Object.keys(LABELS).filter((k) => b[k] !== null && b[k] !== undefined).map((k) => {
        const d = a[k] - b[k];
        const cls = d === 0 ? "" : (d < 0) === lowerIsBetter ? "better" : "worse";
        return `<tr><td>${LABELS[k]}</td><td class="num">${fmt(b[k], 1)}</td><td class="num">${fmt(a[k], 1)}</td><td class="num ${cls}">${d > 0 ? "+" : ""}${fmt(d, 1)}</td></tr>`;
      }).join("") + `</table><p class="small muted">Zbir pojedinačnih procena iz loga: ${fmt(r.package.total.hours_per_year)} h, neto ${rsd(r.package.total.net_rsd_per_year)} godišnje,
      uvođenje ${rsd(r.package.total.one_off_rsd)}, povrat ${r.package.total.payback_months ? fmt(r.package.total.payback_months, 1) + " meseci" : "–"}. ${esc(r.package.note)}.</p>`;
  } catch (e) { $("#whatifResult").textContent = e.message; }
  $("#btnWhatIf").disabled = false;
}

/* ------------------------------------------------------------------ advisor */
async function renderAdvisor() {
  const rep = await api("/api/report");
  if (rep && rep.text) {
    let html = marked.parse(rep.text);
    const g = rep.grounding || {};
    for (const u of g.ungrounded || []) html = html.split(esc(u)).join(`<mark title="broj nije nađen u rezultatima alata">${esc(u)}</mark>`);
    $("#report").classList.remove("muted");
    $("#report").innerHTML = html;
    $("#grounding").innerHTML = rep.source === "template" ? `<div class="ground ok">Šablonski izveštaj (bez AI): svi brojevi dolaze direktno iz analize.</div>`
      : g.numbers === undefined ? `<div class="ground warn">Provera brojeva nije urađena (savetnik nije završio).</div>`
      : g.ungrounded && g.ungrounded.length
        ? `<div class="ground warn">${g.grounded} od ${g.numbers} brojeva nađeno u rezultatima alata. Proveri označene: ${g.ungrounded.map(esc).join(", ")}</div>`
        : `<div class="ground ok">${g.grounded} od ${g.numbers} brojeva nađeno u rezultatima alata (${esc(rep.model)}).</div>`;
  }
  const recs = await api("/api/recommendations");
  $("#recs").innerHTML = recs.length ? recs.map((r) => `<div class="rec">
      <div class="row"><span class="t">P${r.priority} · ${esc(r.title)}</span><span class="status ${r.status}">${r.status}</span></div>
      <div class="meta">neto ${rsd(r.estimate.net_rsd_per_year)} / god. · ${fmt(r.estimate.hours_per_year)} h · povrat ${r.estimate.payback_months ? fmt(r.estimate.payback_months, 1) + " mes." : "–"} · predložio: ${esc(r.created_by)}</div>
      <div class="small">${esc(r.rationale)}</div>
      ${r.status === "PENDING" ? `<div class="btns"><button data-acc="${r.id}">Prihvati</button><button class="ghost" data-rej="${r.id}">Odbij</button></div>`
        : `<div class="meta">${r.status === "ACCEPTED" ? "prihvatio" : "odbio"}: ${esc(r.decided_by)} ${r.note ? "· " + esc(r.note) : ""}</div>`}
    </div>`).join("") : `<p class="muted">Nema preporuka. Pokreni savetnika.</p>`;
}

async function decide(id, accept) {
  const user = $("#userName").value.trim();
  if (!user) return toast("Upiši svoje ime: odluku donosi imenovana osoba.");
  try { localStorage.setItem("pma-user", user); } catch (e) {}
  const note = accept ? "" : prompt("Razlog (opciono):") || "";
  try {
    await api(`/api/recommendations/${id}/decision`, { method: "POST", headers: { "X-User": user }, body: JSON.stringify({ accept, note }) });
    await Promise.all([renderAdvisor(), renderAudit()]);
  } catch (e) { toast(e.message); }
}

async function runAdvisor() {
  $("#btnAdvise").disabled = true;
  $("#btnAdvise").textContent = "Savetnik radi…";
  try {
    const user = $("#userName").value.trim() || "analiticar";
    const r = await api("/api/advise", { method: "POST", headers: { "X-User": user }, body: JSON.stringify({}) });
    if (r.error) toast("Greška: " + r.error);
    await Promise.all([renderAdvisor(), renderAudit()]);
  } catch (e) { toast(e.message); }
  $("#btnAdvise").disabled = false;
  $("#btnAdvise").textContent = "Pokreni savetnika";
}

/* ------------------------------------------------------------------ variants & audit */
async function renderVariants() {
  const v = await api("/api/variants?top=12");
  $("#variantsList").innerHTML = `<table><tr><th>#</th><th>Tok</th><th class="num">Slučajeva</th><th class="num">Kumulativno</th><th class="num">Trajanje (med.)</th></tr>` +
    v.map((x, i) => `<tr><td>${i + 1}</td><td class="chips">${x.variant.map((a) => `<span>${esc(a)}</span>`).join("→")}</td>
      <td class="num">${fmt(x.cases)} (${fmt(x.share_pct, 1)}%)</td><td class="num">${fmt(x.cumulative_pct, 1)}%</td><td class="num">${fmt(x.cycle_days_median, 1)} d</td></tr>`).join("") + "</table>";
}

async function renderAudit() {
  const a = await api("/api/audit");
  $("#auditVerify").innerHTML = a.verify.ok ? `<div class="ground ok">Lanac heševa je ispravan (${a.verify.entries} zapisa).</div>`
    : `<div class="ground warn">Lanac je prekinut kod zapisa #${a.verify.broken_at}.</div>`;
  $("#auditTable").innerHTML = `<tr><th>Vreme</th><th>Ko</th><th>Akcija</th><th>Podaci</th><th>Heš</th></tr>` +
    a.entries.map((e) => `<tr><td class="small">${esc(e.ts)}</td><td>${esc(e.actor)}</td><td>${esc(e.action)}</td>
      <td class="small muted">${esc(JSON.stringify(e.payload))}</td><td class="small muted">${esc(e.hash.slice(0, 12))}…</td></tr>`).join("");
}

/* ------------------------------------------------------------------ wiring */
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "map" && state.cy) state.cy.resize();
  try { localStorage.setItem("pma-tab", b.dataset.tab); } catch (e) {}
}));
$("#minShare").addEventListener("change", renderMap);
$("#minShare").addEventListener("input", () => ($("#minShareVal").textContent = $("#minShare").value));
document.querySelectorAll("input[name=edgeLabel]").forEach((r) => r.addEventListener("change", renderMap));
$("#btnWhatIf").addEventListener("click", runWhatIf);
$("#btnAdvise").addEventListener("click", runAdvisor);
$("#recs").addEventListener("click", (ev) => {
  const a = ev.target.dataset.acc, r = ev.target.dataset.rej;
  if (a) decide(a, true);
  if (r) decide(r, false);
});
$("#btnScenario").addEventListener("click", () => {
  $("#issueChecks").innerHTML = state.status.issues.map((i) => `<label><input type="checkbox" value="${i.id}" ${(state.status.planted || []).includes(i.id) ? "checked" : ""}> ${esc(i.label)}</label>`).join("");
  $("#scenarioDialog").showModal();
});
$("#scenarioDialog").addEventListener("close", async () => {
  if ($("#scenarioDialog").returnValue !== "ok") return;
  const issues = [...document.querySelectorAll("#issueChecks input:checked")].map((i) => i.value);
  await api("/api/runs/demo", { method: "POST", body: JSON.stringify({ seed: Number($("#seedInput").value), issues }) });
  toast("Nova firma simulirana.");
  loadAll();
});
$("#fileInput").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  const r = await fetch("/api/runs/upload", { method: "POST", body: fd });
  const body = await r.json();
  if (!r.ok) return toast(body.detail || "Greška pri učitavanju");
  toast(`Učitano: ${body.label}`);
  loadAll();
});
try {
  $("#userName").value = localStorage.getItem("pma-user") || "";
  const t = localStorage.getItem("pma-tab");
  if (t) document.querySelector(`#tabs button[data-tab=${t}]`)?.click();
} catch (e) {}
loadAll().catch((e) => toast(e.message));
