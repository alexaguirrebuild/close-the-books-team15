"use strict";
const $ = (s, el = document) => el.querySelector(s);
const ESC_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "\u0027": "&#39;"};
const esc = s => String(s ?? "").replace(/[&<>"\u0027]/g, c => ESC_MAP[c]);
const fmtCache = {};
function money(c, ccy = "EUR", sign = false) {
  if (c == null || c === "") return "";
  const k = ccy + sign;
  const f = fmtCache[k] || (fmtCache[k] = new Intl.NumberFormat("es-ES", {style: "currency", currency: ccy || "EUR",
    minimumFractionDigits: 2, signDisplay: sign ? "exceptZero" : "auto"}));
  return f.format(c / 100);
}
const compact = new Intl.NumberFormat("es-ES", {notation: "compact", maximumFractionDigits: 1});
const eurC = c => compact.format(c / 100) + " €";
const int = n => new Intl.NumberFormat("es-ES").format(n ?? 0);
const pct = (a, b) => b ? (100 * a / b).toLocaleString("es-ES", {maximumFractionDigits: 1}) + " %" : "—";
const dfmt = s => s ? s.slice(8, 10) + "/" + s.slice(5, 7) + "/" + s.slice(2, 4) : "";

const EXC_ORDER = ["PARTIAL","OVERPAYMENT","SHORT_DISCOUNT","SHORT_BANK_CHARGES","SHORT_WITHHOLDING","SHORT_DISPUTE",
  "DUPLICATE_PAYMENT","UNIDENTIFIED","RETURNED","FX_DIFFERENCE"];
const TABS = [["all","Todos"],["exception","Con excepción"],["pending","Pendientes / UNIDENTIFIED"],["category","Categoría sin partida"],
  ["batch","Lotes / Remesas"],["docs","Avisos / PDF"],["reviewed","Revisados"],["golden_ko","Fallos vs golden"]];
const ST_ES = {auto: "Automático", pendiente: "Revisar", validado: "Validado", corregido: "Corregido"};
const CONF_ES = {alta: "Confianza alta", media: "Confianza media", baja: "Revisión humana"};
const ROW_H = 56, PAGE = 100;

const S = {phase: null, entity: "", f: {state: "all", exc: "", rule: "", conf: "", q: "", sort: "date"},
  total: 0, pages: new Map(), pending: new Set(), gen: 0, sel: -1, selId: null, summary: null, cats: null,
  details: new Map(), docIdx: 0, lastDetail: null};

async function api(path, opts = {}) {
  const sep = path.includes("?") ? "&" : "?";
  const r = await fetch(path + sep + "phase=" + encodeURIComponent(S.phase || ""), opts);
  const j = await r.json().catch(() => ({error: "Respuesta no válida"}));
  if (r.status === 202) { const e = new Error("loading"); e.loading = true; throw e; }
  if (!r.ok) { const e = new Error(j.error || r.statusText); e.status = r.status; e.body = j; throw e; }
  return j;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 2200); }
function qs(extra = {}) {
  const p = new URLSearchParams();
  const f = {...S.f, entity: S.entity, ...extra};
  for (const [k, v] of Object.entries(f)) if (v !== "" && v != null) p.set(k, v);
  return p.toString();
}
// ------------------------------------------------------------ resumen y cabecera
async function loadSummary() {
  const s = await api("/api/summary?" + new URLSearchParams(S.entity ? {entity: S.entity} : {}));
  S.summary = s;
  renderHeader(s); renderKpis(s); renderExcs(s); renderTabs(s); renderRules(s);
  return s;
}
function renderHeader(s) {
  $("#phase").innerHTML = s.phases.map(p => `<option value="${p.tag}" ${p.tag === S.phase ? "selected" : ""} ${p.available ? "" : "disabled"}>${esc(p.label)}${p.available ? "" : " · sin ejecutar"}</option>`).join("");
  const all = [{code: "", name: "Todas las sociedades", n: s.entities.reduce((a, e) => a + e.n, 0)}, ...s.entities];
  $("#entities").innerHTML = all.map(e => `<button aria-pressed="${e.code === S.entity}" data-ent="${e.code}" title="${esc(e.name)} · ${int(e.n)} movimientos">${e.code || "Todas"}</button>`).join("");
  $("#sub").textContent = `${s.phase_label} · ${dfmt(s.period[0])}–${dfmt(s.period[1])} · propuesta del motor ${s.generated_at.replace("T", " ")}`;
}
function renderKpis(s) {
  const n = s.txns || 1, st = s.status;
  const rev = st.validado + st.corregido;
  const m = s.score && s.score.metrics;
  const g = s.golden;
  $("#kpis").innerHTML = `
  <div class="kpi">
    <div style="display:flex;align-items:baseline;gap:10px"><span class="lbl">Cierre del mes</span>
      <span class="spacer"></span><span class="v num" style="font-size:18px">${pct(n - st.pendiente, n)}</span><span class="muted">cerrado</span></div>
    <div class="bar" role="img" aria-label="Progreso del cierre">
      <i class="c-auto" style="width:${100 * st.auto / n}%"></i><i class="c-rev" style="width:${100 * rev / n}%"></i><i class="c-pend" style="width:${100 * st.pendiente / n}%"></i></div>
    <div class="legend">
      <span data-tab="all"><i class="dot c-auto"></i>Automático <b class="num">${int(st.auto)}</b></span>
      <span data-tab="reviewed"><i class="dot c-rev"></i>Revisado por tesorería <b class="num">${int(rev)}</b></span>
      <span data-tab="pending"><i class="dot c-pend"></i>Pendiente de revisar <b class="num">${int(st.pendiente)}</b></span>
    </div>
  </div>
  <div class="kpi"><div class="lbl">Movimientos</div><div class="v num">${int(s.txns)}</div><div class="s">${int(s.rows)} filas de conciliación</div></div>
  <div class="kpi"><div class="lbl">Conciliado automático</div><div class="v num">${s.auto_pct.toLocaleString("es-ES")} %</div>
    <div class="minis"><span title="Confianza alta"><i class="dot c-auto"></i>alta <b class="num">${int(s.confidence.alta)}</b></span><span title="Confianza media"><i class="dot c-rev"></i>media <b class="num">${int(s.confidence.media)}</b></span><span title="Revisión humana"><i class="dot c-pend"></i>baja <b class="num">${int(s.confidence.baja)}</b></span></div></div>
  <div class="kpi"><div class="lbl">Importe conciliado</div><div class="v num">${eurC(s.amount_eur.reconciled)}</div>
    <div class="s">de ${eurC(s.amount_eur.total)} · pendiente ${eurC(s.amount_eur.pending)}</div></div>
  <div class="kpi">${m ? `<div class="lbl">Score oficial${S.phase === "sample" ? " (muestra)" : ""}</div><div class="v num">${s.score.score.toLocaleString("es-ES")}</div>
    <div class="s" title="F1 de pares · exactitud por movimiento · macro-F1 de excepciones">F1 ${(m.allocation_f1 * 100).toFixed(1)} · exact. ${(m.txn_accuracy * 100).toFixed(1)} · exc. ${(m.exception_macro_f1 * 100).toFixed(1)}</div>`
    : g ? `<div class="lbl">Contra golden</div><div class="v num">${pct(g.ok, g.n)}</div><div class="s">${int(g.ko)} movimientos distintos</div>`
    : `<div class="lbl">Score</div><div class="v muted">—</div><div class="s">Sin golden en esta fase</div>`}</div>`;
}
function renderExcs(s) {
  $("#excs").innerHTML = `<span class="lbl">Excepciones</span>` + s.exceptions.map((e, i) =>
    `<button class="chip ${e.n_txn ? "" : "zero"}" data-exc="${e.code}" aria-pressed="${S.f.exc === e.code}" title="${esc(e.code)} · ${esc(e.help)} · Atajo ${(i + 1) % 10}">${esc(e.label)} <b class="num">${int(e.n_txn)}</b></button>`).join("");
}
function renderTabs(s) {
  $("#tabs").innerHTML = TABS.filter(([k]) => s.tabs[k] != null).map(([k, l]) =>
    `<button class="tab" role="tab" data-state="${k}" aria-selected="${S.f.state === k}">${l}<span class="n num">${int(s.tabs[k])}</span></button>`).join("");
}
function renderRules(s) {
  const cur = S.f.rule;
  $("#rule").innerHTML = `<option value="">Todas las reglas</option>` + s.rules.map(r =>
    `<option value="${r.rule}" ${r.rule === cur ? "selected" : ""}>${esc(r.title)} · ${int(r.n)}</option>`).join("");
}
// ------------------------------------------------------------ lista virtualizada
function resetList() {
  S.gen++; S.pages.clear(); S.pending.clear(); S.total = 0; S.sel = -1; S.selId = null;
  $("#scroller").scrollTop = 0;
  return fetchPage(0).then(() => { if (S.total) select(0); else renderEmptyDetail(); });
}
function fetchPage(p) {
  if (S.pages.has(p) || S.pending.has(p)) return Promise.resolve();
  S.pending.add(p);
  const gen = S.gen;
  return api(`/api/transactions?${qs({offset: p * PAGE, limit: PAGE})}`).then(j => {
    if (gen !== S.gen) return;
    S.pending.delete(p);
    S.total = j.total; S.pages.set(p, j.rows);
    $("#lcount").textContent = `${int(j.total)} movimientos`;
    $("#lsum").textContent = j.total ? `${eurC(j.eur)} en valor absoluto` : "";
    $("#spacer").style.height = (j.total * ROW_H) + "px";
    renderList();
  }).catch(e => { S.pending.delete(p); if (!e.loading) toast(e.message); });
}
const rowAt = i => (S.pages.get(Math.floor(i / PAGE)) || [])[i % PAGE];
function propBadges(r) {
  let h = "";
  if (!r.n_items) h += r.cat === "UNIDENTIFIED" ? `<span class="badge b-unid">UNIDENTIFIED</span>` : `<span class="badge b-cat">${esc(r.cat)}</span>`;
  else h += `<span class="badge b-acc" title="${esc(r.rule_title)}">${r.n_items} ${r.n_items === 1 ? "partida" : "partidas"}</span>`;
  for (const e of r.exc) if (e !== "UNIDENTIFIED") h += `<span class="badge b-exc">${esc(e)}</span>`;
  return h;
}
function stHtml(st) { return `<span class="st ${st}"><i class="dot ${st === "auto" ? "c-auto" : st === "pendiente" ? "c-pend" : "c-rev"}"></i>${ST_ES[st]}</span>`; }
function renderList() {
  const sc = $("#scroller"), sp = $("#spacer");
  if (!S.total) { sp.innerHTML = `<div class="empty">Ningún movimiento con estos filtros.</div>`; sp.style.height = "auto"; return; }
  const top = sc.scrollTop, h = sc.clientHeight || 600;
  const a = Math.max(0, Math.floor(top / ROW_H) - 6), b = Math.min(S.total, Math.ceil((top + h) / ROW_H) + 6);
  let html = "";
  for (let i = a; i < b; i++) {
    const r = rowAt(i);
    if (!r) { fetchPage(Math.floor(i / PAGE)); html += `<div class="row" style="top:${i * ROW_H}px"><div class="skel"></div><div></div><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>`; continue; }
    html += `<div class="row ${i === S.sel ? "sel" : ""}" style="top:${i * ROW_H}px" data-i="${i}" role="option" aria-selected="${i === S.sel}">
      <div class="date num">${dfmt(r.date)}<br>${stHtml(r.status)}</div>
      <div><span class="badge b-ent">${r.entity}</span></div>
      <div style="min-width:0"><div class="cp">${esc(r.cp || "—")}</div><div class="tx">${esc(r.text)}</div></div>
      <div class="amt num ${r.amount > 0 ? "pos" : ""}">${money(r.amount, r.ccy, true)}${r.gold === false ? `<div><span class="badge b-bad" title="Distinto del golden">≠ golden</span></div>` : ""}</div>
      <div class="prop">${propBadges(r)}</div></div>`;
  }
  sp.innerHTML = html;
}
function ensureVisible(i) {
  const sc = $("#scroller"), y = i * ROW_H;
  if (y < sc.scrollTop) sc.scrollTop = y;
  else if (y + ROW_H > sc.scrollTop + sc.clientHeight) sc.scrollTop = y + ROW_H - sc.clientHeight;
}
let detailTimer;
async function select(i) {
  if (i < 0 || i >= S.total) return;
  S.sel = i;
  const p = Math.floor(i / PAGE);
  if (!S.pages.has(p)) await fetchPage(p);
  const r = rowAt(i);
  ensureVisible(i); renderList();
  if (!r) return;
  S.selId = r.id;
  clearTimeout(detailTimer);
  detailTimer = setTimeout(() => loadDetail(r.id), S.details.has(r.id) ? 0 : 70);
}
// ------------------------------------------------------------ ficha de auditoría
function renderEmptyDetail() {
  $("#detail").innerHTML = `<div class="placeholder"><div><h2>Ningún movimiento con estos filtros</h2>
    <div>La ficha explica por qué el motor propone cada cruce, con el cuadre al céntimo y el documento original.</div></div></div>`;
}
async function loadDetail(id) {
  let d = S.details.get(id);
  if (!d) {
    try { d = await api("/api/transaction/" + encodeURIComponent(id)); } catch (e) { if (!e.loading) toast(e.message); return; }
    S.details.set(id, d);
    if (S.details.size > 200) S.details.delete(S.details.keys().next().value);
  }
  if (id !== S.selId) return;
  S.lastDetail = d; S.docIdx = 0;
  renderDetail(d);
  $("#detail").scrollTop = 0;
}
function confBadge(c) { return `<span class="badge ${c === "alta" ? "b-ok" : c === "media" ? "b-acc" : "b-exc"}">${CONF_ES[c]}</span>`; }function renderDetail(d) {
  const s = d.summary, b = d.bank, ov = d.override;
  const why = d.why.map(w => `<div><div class="t">${esc(w.title)} ${confBadge(w.conf)} <span class="badge" title="Apartado de POLICIES.md">POLICIES ${esc(w.policy)}</span></div>
      <p>${esc(w.why)}</p>${w.evidence.length ? `<div class="ev">${w.evidence.map(e => `<div>${esc(e)}</div>`).join("")}</div>` : ""}</div>`).join("");
  const exh = Object.entries(d.exc_help).map(([k, [l, h]]) => `<div><span class="badge ${k === "UNIDENTIFIED" ? "b-unid" : "b-exc"}">${esc(k)}</span> <b>${esc(l)}.</b> ${esc(h)}</div>`).join("");
  $("#detail").innerHTML = `
  <div class="dhead">
    <div class="l1">
      <div style="min-width:0">
        <div class="big num ${s.amount > 0 ? "pos" : ""}">${money(s.amount, s.ccy, true)}</div>
        <div class="dmeta">${esc(b.booking_date)} · <span class="badge b-ent">${esc(b.entity)}</span> ${esc(b.entity_name)} · ${esc(b.bank_name || b.bank)} <span class="mono">${esc(b.account_id)}</span></div>
        <div class="dmeta"><span class="mono">${esc(s.id)}</span> · ${stHtml(s.status)} ${s.gold === true ? `<span class="badge b-ok">= golden</span>` : s.gold === false ? `<span class="badge b-bad">≠ golden</span>` : ""}</div>
      </div>
      <div class="actions">
        ${ov ? `<button class="btn" data-act="reset" title="Volver a la propuesta del motor">Restablecer</button>` : ""}
        <button class="btn" data-act="edit">Editar excepción / partida <kbd>E</kbd></button>
        <button class="btn primary" data-act="confirm" ${ov && ov.action === "confirm" ? "disabled" : ""}>Confirmar cruce <kbd>A</kbd></button>
      </div>
    </div>
    ${ov ? `<div class="note info" style="margin-top:8px">${ov.action === "confirm" ? "Validado" : "Corregido"} por tesorería el ${esc(ov.at.replace("T", " "))}${ov.note ? " · «" + esc(ov.note) + "»" : ""}${ov.action === "edit" ? " · la propuesta original del motor se conserva" : ""}</div>` : ""}
  </div>
  <div class="dbody">
    <div class="card"><h3>¿Por qué se propone este cruce?</h3><div class="cb why">${why || `<div class="muted">Sin regla registrada.</div>`}${exh ? `<div class="exhelp">${exh}</div>` : ""}</div></div>
    ${renderRecon(d)}
    ${renderDocs(d)}
    ${renderBank(b)}
    ${renderGolden(d)}
  </div>`;
}function renderRecon(d) {
  const s = d.summary;
  if (d.category_rows.length) {
    const c = d.category_rows[0];
    return `<div class="card"><h3>Sin partida · imputación a cuenta</h3><div class="cb"><dl class="kv">
      <dt>Categoría</dt><dd><span class="badge ${c.category === "UNIDENTIFIED" ? "b-unid" : "b-cat"}">${esc(c.category)}</span> ${esc(c.cat_desc)}</dd>
      <dt>Cuenta contable</dt><dd class="mono">${esc(c.gl)}</dd>
      <dt>Excepción</dt><dd>${c.exception ? `<span class="badge b-exc">${esc(c.exception)}</span>` : `<span class="muted">—</span>`}</dd>
      <dt>Importe</dt><dd class="num">${money(s.amount, s.ccy, true)}</dd></dl>
      ${c.category === "UNIDENTIFIED" ? `<div class="note" style="margin-top:8px">No se fuerza un cruce: ninguna partida abierta encaja con garantías (POLICIES &sect;3). Si conoces al pagador, usa <b>Editar</b> para asignar sus facturas.</div>` : ""}</div></div>`;
  }
  const r = d.recon; if (!r) return "";
  const LIM = 60, lines = d.lines, many = lines.length > LIM;
  const showAll = S.showAll === s.id;
  const L = many && !showAll ? lines.slice(0, LIM) : lines;
  const fx = lines.some(l => l.ccy !== r.ccy);
  const rows = L.map(l => {
    const it = l.item;
    return `<tr><td><b>${esc(it.number || it.id)}</b><div class="muted mono" style="font-size:10.5px">${esc(it.id)}</div></td>
      <td>${esc(it.kind_label)}${it.party_now ? `<div class="muted" style="font-size:11.5px">${esc(it.party_now)}</div>` : ""}</td>
      <td class="num">${dfmt(it.issue)}<div class="muted">${it.due ? "vto. " + dfmt(it.due) : ""}</div></td>
      <td class="r num">${money(it.total, l.ccy)}${it.open_start != null && it.open_start !== it.total ? `<div class="muted" title="Saldo abierto al inicio de la fase">abierto ${money(it.open_start, l.ccy)}</div>` : ""}</td>
      <td class="r num"><b>${l.sign < 0 ? "−" : ""}${money(l.amount, l.ccy)}</b>${l.remaining ? `<div class="muted">queda ${money(l.remaining, l.ccy)}</div>` : ""}</td>
      ${fx ? `<td class="r num">${l.sign < 0 ? "−" : ""}${money(l.conv, r.ccy)}</td>` : ""}
      <td>${l.exception ? `<span class="badge b-exc" title="${esc(l.exc_label)}">${esc(l.exception)}</span>` : ""}</td></tr>`;
  }).join("");
  const steps = r.steps.map((st, i) => `<span class="k">${i === 0 ? "" : st.v < 0 ? "− " : "+ "}${esc(st.k)}</span><span class="v num">${money(i === 0 ? st.v : Math.abs(st.v), r.ccy)}</span>`).join("");
  const icon = r.status === "ok" ? `<span class="badge b-ok">✓ Cuadra al céntimo</span>` : r.status === "explicado" ? `<span class="badge b-ok">✓ Diferencia explicada</span>` : `<span class="badge b-bad">Revisar</span>`;
  return `<div class="card"><h3>Partidas cruzadas y cuadre <span class="r muted">${int(lines.length)} ${lines.length === 1 ? "partida" : "partidas"}</span></h3>
    <div class="tscroll"><table class="t"><thead><tr><th>Documento</th><th>Tipo · tercero</th><th>Emisión</th><th class="r">Total doc.</th><th class="r">Aplicado</th>${fx ? `<th class="r">En ${esc(r.ccy)}</th>` : ""}<th>Excepción</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${many ? `<div style="padding:6px 12px"><button class="btn" data-act="showall">${showAll ? "Ver solo las primeras " + LIM : "Ver las " + int(lines.length) + " partidas"}</button></div>` : ""}
    <div class="eq">${steps}<span class="k tot">= Extracto bancario</span><span class="v num tot">${money(r.bank, r.ccy)}</span></div>
    <div class="verdict">${icon}${r.status === "ok" ? "" : `<span>${esc(r.label)}</span>`}${r.detail ? `<p>${esc(r.detail)}</p>` : ""}</div></div>`;
}
function renderBank(b) {
  const F = [["Texto del banco", b.text, "pre"], ["Contraparte", b.counterparty_name], ["IBAN contraparte", b.counterparty_account, "mono"],
    ["Información de remesa", b.remittance_info], ["Ref. 1 · Ref. 2", [b.ref1, b.ref2].filter(Boolean).join(" · "), "mono"],
    ["EndToEndId", b.end_to_end_id, "mono"], ["Fecha valor", b.value_date], ["Importe original", b.orig_amount ? b.orig_amount + " " + b.orig_currency : ""],
    ["Código banco", [b.bank_code, b.bank_subcode].filter(Boolean).join(" · "), "mono"], ["Extracto", `${b.statement_format} · ${b.statement_file}`, "mono"],
    ["Cuenta", `${b.account_id}${b.iban ? " · " + b.iban : ""}`, "mono"]];
  return `<div class="card"><h3>Movimiento bancario (extracto)</h3><div class="cb"><dl class="kv">${F.filter(f => f[1]).map(([k, v, t]) =>
    `<dt>${k}</dt><dd>${t === "pre" ? `<pre class="txt" style="max-height:none">${esc(v)}</pre>` : t === "mono" ? `<span class="mono">${esc(v)}</span>` : esc(v)}</dd>`).join("")}</dl></div></div>`;
}function renderDocs(d) {
  if (!d.docs.length) return "";
  const i = Math.min(S.docIdx, d.docs.length - 1), doc = d.docs[i];
  const tabs = d.docs.map((x, j) => `<button data-doc="${j}" aria-selected="${j === i}">${x.kind === "advice" ? "Aviso de pago" : "PDF"} · ${esc(x.about || x.file.split("/").pop())}</button>`).join("");
  let body = "";
  if (!doc.exists) body = `<div class="note">El fichero no está en data/${esc(doc.phase_dir)}.</div>`;
  else {
    const meta = [doc.doc_type, doc.payer, doc.date && "recibido " + doc.date].filter(Boolean).map(esc).join(" · ");
    body += `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="muted">${meta}</span><span class="spacer"></span>
      <button class="btn" data-act="docfull">Ampliar <kbd>D</kbd></button><a class="btn" href="${esc(doc.url)}" target="_blank" rel="noopener">Abrir original</a></div>`;
    if (doc.parsed && doc.parsed.lines.length) {
      const p = doc.parsed, cc = p.ccy || "EUR";
      body += `<div style="margin-top:8px" class="muted">Leído del aviso: ref. <span class="mono">${esc(p.ref || "—")}</span>${p.total != null ? " · total " + money(p.total, cc) : ""}${p.pay_date ? " · pago " + esc(p.pay_date) : ""} · ${int(p.lines.length)} documentos</div>
      <div class="tscroll" style="margin-top:6px;border:1px solid var(--line-2);border-radius:8px"><table class="t"><thead><tr><th>Documento</th><th>Fecha</th><th class="r">Bruto</th><th class="r">Deducción</th><th>Motivo</th><th class="r">Pagado</th></tr></thead><tbody>
      ${p.lines.map(l => `<tr><td class="mono">${esc(l.doc)}</td><td>${esc(l.date)}</td><td class="r num">${l.gross != null ? money(l.gross, cc) : ""}</td><td class="r num">${l.ded ? money(l.ded, cc) : ""}</td><td>${esc(l.reason)}</td><td class="r num"><b>${money(l.paid, cc)}</b></td></tr>`).join("")}</tbody></table></div>`;
    }
    if (doc.parse_error) body += `<div class="note" style="margin-top:8px">${esc(doc.parse_error)}</div>`;
    if (doc.clauses && doc.clauses.length) body += `<div style="margin-top:8px"><div class="lbl" style="margin-bottom:4px">Cláusulas detectadas en el PDF</div><dl class="kv">${doc.clauses.map(c => `<dt>${esc(c.k)}</dt><dd>${esc(c.v)} ${c.policy ? `<span class="badge">${esc(c.policy)}</span>` : ""}</dd>`).join("")}</dl></div>`;
    else if (doc.kind === "pdf" && doc.text_available === false) body += `<div class="note info" style="margin-top:8px">Texto del PDF no extraído en este equipo (falta <span class="mono">pypdf</span>): se muestra el original, sin lectura automática de cláusulas.</div>`;
    if (doc.fmt === "pdf") body += `<div class="viewer"><iframe title="Documento original" src="${esc(doc.url)}#view=FitH" loading="lazy"></iframe></div>`;
    else if (doc.preview != null) body += `<details ${doc.parsed && doc.parsed.lines.length ? "" : "open"} style="margin-top:8px"><summary class="muted" style="cursor:pointer">Fichero original</summary><pre class="txt" style="margin-top:6px">${esc(doc.preview)}</pre></details>`;
    else if (doc.fmt === "xlsx") body += `<div class="note info" style="margin-top:8px">Hoja de cálculo: ábrela con «Abrir original».</div>`;
  }
  return `<div class="card"><h3>Documentos de soporte <span class="r muted">${d.docs_total > d.docs.length ? `${d.docs.length} de ${d.docs_total}` : d.docs.length}</span></h3>
    <div class="doctabs" role="tablist">${tabs}</div><div class="cb">${body}</div></div>`;
}
function renderGolden(d) {
  if (!d.golden) return "";
  const g = d.golden;
  return `<details class="card" ${g.ok === false ? "open" : ""}><summary style="padding:9px 12px;cursor:pointer;font-weight:650;font-size:12.5px">Control de calidad contra golden (solo ${esc(S.phase)}) ${g.ok === true ? `<span class="badge b-ok">igual</span>` : g.ok === false ? `<span class="badge b-bad">distinto</span>` : ""}</summary>
    <div class="cb"><table class="t"><thead><tr><th>Partida o categoría</th><th class="r">Importe</th><th>Excepción</th><th>Segmento</th></tr></thead><tbody>
    ${g.rows.map(r => `<tr><td>${r.item_id ? `<b>${esc(r.number || r.item_id)}</b> <span class="muted mono">${esc(r.item_id)}</span>` : `<span class="badge b-cat">${esc(r.category)}</span>`}</td><td class="r num">${r.item_id ? (r.amount / 100).toLocaleString("es-ES", {minimumFractionDigits: 2}) : ""}</td><td>${r.exception ? `<span class="badge b-exc">${esc(r.exception)}</span>` : ""}</td><td class="muted">${esc(r.segment)} · dif. ${esc(r.difficulty)}</td></tr>`).join("")}
    </tbody></table></div></details>`;
}
function openDocFull() {
  const d = S.lastDetail; if (!d || !d.docs.length) return;
  const doc = d.docs[Math.min(S.docIdx, d.docs.length - 1)];
  $("#docFull").innerHTML = `<header><b>${esc(doc.file)}</b><span class="spacer"></span><a class="btn" href="${esc(doc.url)}" target="_blank" rel="noopener">Abrir en pestaña</a><button class="btn" data-close>Cerrar <kbd>Esc</kbd></button></header>
    ${doc.fmt === "pdf" ? `<iframe title="Documento" src="${esc(doc.url)}"></iframe>` : `<pre class="txt" style="max-height:none;border:0;border-radius:0">${esc(doc.preview || "")}</pre>`}`;
  $("#docOv").classList.add("on");
}
// ------------------------------------------------------------ acciones
async function override(body, msg, advance) {
  const id = S.selId;
  const j = await api("/api/override", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...body, txn_id: id, phase: S.phase})});
  const i = S.sel, arr = S.pages.get(Math.floor(i / PAGE));
  if (arr && arr[i % PAGE] && arr[i % PAGE].id === id) arr[i % PAGE] = j.summary;
  S.details.delete(id);
  toast(msg);
  loadSummary().catch(() => {});
  if (advance && S.sel < S.total - 1) select(S.sel + 1); else { renderList(); loadDetail(id); }
  return j;
}
const confirmCross = () => S.selId && override({action: "confirm"}, "Cruce confirmado · siguiente", true).catch(e => toast(e.message));

// ------------------------------------------------------------ edición
let ED = null;
async function openEdit() {
  const d = S.lastDetail; if (!d || d.summary.id !== S.selId) return;
  if (!S.cats) S.cats = await api("/api/categories");
  const rows = d.override && d.override.action === "edit" ? d.override.rows : d.engine_rows;
  const items = rows.filter(r => r.item_id), cat = rows.find(r => !r.item_id) || {};
  ED = {mode: items.length ? "items" : "cat", d, note: "", err: "", results: [], q: "",
    rows: items.map(r => ({item_id: r.item_id, amount: (r.amount / 100).toFixed(2), exception: r.exception,
      label: (d.lines.find(l => l.item.id === r.item_id) || {}).item})),
    category: cat.category || "UNIDENTIFIED", catExc: cat.exception || ""};
  renderEdit(); $("#editOv").classList.add("on");
  setTimeout(() => { const el = $("#drawer .body input, #drawer .body select"); if (el) el.focus(); }, 30);
}
function excOptions(cur) {
  return `<option value="">— sin excepción —</option>` + S.cats.exceptions.filter(e => e.code !== "UNIDENTIFIED")
    .map(e => `<option value="${e.code}" ${e.code === cur ? "selected" : ""}>${e.code} · ${esc(e.label)}</option>`).join("");
}
function renderEdit() {
  const d = ED.d, s = d.summary, bank = Math.abs(s.amount);
  let applied = 0;
  for (const r of ED.rows) { const v = Math.round(parseFloat(String(r.amount).replace(",", ".")) * 100) || 0; applied += (/^(ARC|APC)/.test(r.item_id) ? -1 : 1) * v; }
  const sameCcy = ED.rows.every(r => !r.label || r.label.ccy === s.ccy);
  $("#drawer").innerHTML = `
  <header><h2 id="editTitle">Editar conciliación</h2><span class="muted mono">${esc(s.id)}</span><span class="spacer"></span><span class="num" style="font-weight:700">${money(s.amount, s.ccy, true)}</span><button class="btn ghost" data-close aria-label="Cerrar">✕</button></header>
  <div class="body">
    <div class="seg" role="group" style="align-self:flex-start"><button data-mode="items" aria-pressed="${ED.mode === "items"}">Liquida partidas</button><button data-mode="cat" aria-pressed="${ED.mode === "cat"}">Sin partida (categoría)</button></div>
    ${ED.mode === "items" ? `
    <table class="t"><thead><tr><th>Partida</th><th class="r" style="width:130px">Importe (divisa partida)</th><th style="width:230px">Excepción</th><th></th></tr></thead><tbody>
      ${ED.rows.map((r, i) => `<tr><td><span class="mono">${esc(r.item_id)}</span>${r.label ? `<div class="muted" style="font-size:11.5px">${esc(r.label.number || "")} · ${esc(r.label.party_now || r.label.party || "")} · total ${money(r.label.total, r.label.ccy)}</div>` : ""}</td>
      <td><input class="inp num" style="text-align:right" data-i="${i}" data-f="amount" value="${esc(r.amount)}" inputmode="decimal" aria-label="Importe"></td>
      <td><select class="inp" data-i="${i}" data-f="exception" aria-label="Excepción">${excOptions(r.exception)}</select></td>
      <td><button class="btn ghost" data-drop="${i}" aria-label="Quitar">✕</button></td></tr>`).join("") || `<tr><td colspan="4" class="muted">Sin partidas: añade alguna abajo.</td></tr>`}
    </tbody></table>
    <div class="eq" style="border-radius:8px;border:1px solid var(--line)"><span class="k">Σ aplicado (facturas − abonos)</span><span class="v num">${money(applied, s.ccy)}</span>
      <span class="k">Extracto</span><span class="v num">${money(bank, s.ccy)}</span>
      <span class="k tot">Diferencia${sameCcy ? "" : " (divisas distintas: orientativa)"}</span><span class="v num tot" style="color:${Math.abs(applied - bank) <= 1 ? "var(--ok)" : "var(--warn)"}">${money(applied - bank, s.ccy, true)}</span></div>
    <div><div class="lbl" style="margin-bottom:4px">Añadir partida</div>
      <div style="display:flex;gap:6px"><input class="inp" id="itemQ" placeholder="Número de factura, cliente, importe…" value="${esc(ED.q)}" aria-label="Buscar partida">
      ${d.lines[0] ? `<button class="btn" data-party="${esc(d.lines[0].item.party)}">Mismo tercero</button>` : ""}</div>
      ${ED.results.length ? `<div class="results" style="margin-top:6px">${ED.results.map((it, j) => `<div data-add="${j}"><span class="mono">${esc(it.number)}</span><span>${esc(it.party_now || it.party)} <span class="muted">${esc(it.kind_label)} · ${dfmt(it.issue)}</span></span><span class="num" style="text-align:right">${money(it.open_start ?? it.total, it.ccy)}</span><span class="muted mono" style="font-size:10.5px">${esc(it.entity)}</span></div>`).join("")}</div>` : ""}
    </div>` : `
    <label><div class="lbl" style="margin-bottom:4px">Categoría</div><select class="inp" id="catSel">${S.cats.categories.map(c => `<option value="${c.code}" ${c.code === ED.category ? "selected" : ""}>${c.code} · ${esc(c.desc)} (${c.gl})</option>`).join("")}</select></label>
    <label><div class="lbl" style="margin-bottom:4px">Excepción</div><select class="inp" id="catExc" ${ED.category === "UNIDENTIFIED" ? "disabled" : ""}>${ED.category === "UNIDENTIFIED" ? `<option>UNIDENTIFIED (obligatoria con esta categoría)</option>` : excOptions(ED.catExc)}</select></label>`}
    <label><div class="lbl" style="margin-bottom:4px">Nota para auditoría (opcional)</div><textarea class="inp" id="note" rows="2" placeholder="Ej.: confirmado con el aviso del cliente recibido por correo">${esc(ED.note)}</textarea></label>
    ${ED.err ? `<div class="err" role="alert">${esc(ED.err)}</div>` : ""}
  </div>
  <footer><span class="muted">Se guarda en overrides y se re-exporta la entrega revisada · <kbd>Ctrl</kbd>+<kbd>Enter</kbd></span><span class="spacer"></span><button class="btn" data-close>Cancelar</button><button class="btn primary" data-act="save">Guardar corrección</button></footer>`;
}
let itemTimer;
async function searchItems(params) {
  try {
    const j = await api("/api/items?" + new URLSearchParams({...params, entity: ED.d.summary.entity, limit: 30}));
    ED.results = j.items; renderEdit();
    const q = $("#itemQ"); if (q && params.q != null) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); }
  } catch (e) { toast(e.message); }
}
async function saveEdit() {
  const body = {action: "edit", note: ED.note};
  body.rows = ED.mode === "items" ? ED.rows.map(r => ({item_id: r.item_id, amount: r.amount, exception: r.exception}))
    : [{category: ED.category, exception: ED.category === "UNIDENTIFIED" ? "UNIDENTIFIED" : ED.catExc}];
  try { await override(body, "Corrección guardada", false); closeOv(); }
  catch (e) { ED.err = e.message; renderEdit(); }
}
function closeOv() { document.querySelectorAll(".ov.on").forEach(o => o.classList.remove("on")); $("#scroller").focus({preventScroll: true}); }
// ------------------------------------------------------------ eventos
function setFilter(k, v) { S.f[k] = v; resetList(); if (k === "exc" || k === "state") { renderExcs(S.summary); renderTabs(S.summary); } }
document.addEventListener("click", e => {
  const t = e.target.closest("[data-ent],[data-exc],[data-state],[data-tab],.row,[data-act],[data-doc],[data-close],[data-mode],[data-drop],[data-add],[data-party]");
  if (!t) { if (e.target.classList.contains("ov")) closeOv(); return; }
  if (t.dataset.ent != null) { S.entity = t.dataset.ent; S.details.clear(); loadSummary(); resetList(); }
  else if (t.dataset.exc) setFilter("exc", S.f.exc === t.dataset.exc ? "" : t.dataset.exc);
  else if (t.dataset.state) setFilter("state", t.dataset.state);
  else if (t.dataset.tab) setFilter("state", t.dataset.tab);
  else if (t.classList.contains("row")) { if (t.dataset.i != null) select(+t.dataset.i); }
  else if (t.dataset.doc != null) { S.docIdx = +t.dataset.doc; renderDetail(S.lastDetail); }
  else if (t.dataset.close != null) closeOv();
  else if (t.dataset.mode) { ED.mode = t.dataset.mode; ED.err = ""; renderEdit(); }
  else if (t.dataset.drop != null) { ED.rows.splice(+t.dataset.drop, 1); renderEdit(); }
  else if (t.dataset.add != null) { const it = ED.results[+t.dataset.add]; if (!ED.rows.some(r => r.item_id === it.id)) ED.rows.push({item_id: it.id, amount: ((it.open_start ?? it.total) / 100).toFixed(2), exception: "", label: it}); ED.mode = "items"; renderEdit(); }
  else if (t.dataset.party != null) searchItems({party: t.dataset.party});
  else if (t.dataset.act) {
    const a = t.dataset.act;
    if (a === "confirm") confirmCross();
    else if (a === "edit") openEdit();
    else if (a === "save") saveEdit();
    else if (a === "reset") override({action: "reset"}, "Vuelve a la propuesta del motor", false).catch(err => toast(err.message));
    else if (a === "docfull") openDocFull();
    else if (a === "showall") { S.showAll = S.showAll === S.selId ? null : S.selId; renderDetail(S.lastDetail); }
  }
});
document.addEventListener("input", e => {
  const t = e.target;
  if (t.id === "q") { clearTimeout(t._h); t._h = setTimeout(() => setFilter("q", t.value.trim()), 160); }
  else if (t.dataset.f === "amount" && ED) {
    ED.rows[+t.dataset.i].amount = t.value; clearTimeout(t._h);
    t._h = setTimeout(() => { const pos = t.selectionStart, i = t.dataset.i; renderEdit(); const n = $(`#drawer input[data-i="${i}"][data-f="amount"]`); if (n) { n.focus(); n.setSelectionRange(pos, pos); } }, 500);
  }
  else if (t.id === "itemQ") { ED.q = t.value; clearTimeout(itemTimer); itemTimer = setTimeout(() => t.value.trim().length >= 2 && searchItems({q: t.value.trim()}), 200); }
  else if (t.id === "note" && ED) ED.note = t.value;
});
document.addEventListener("change", e => {
  const t = e.target;
  if (t.id === "rule" || t.id === "conf" || t.id === "sort") setFilter(t.id, t.value);
  else if (t.id === "phase") switchPhase(t.value);
  else if (t.id === "catSel") { ED.category = t.value; renderEdit(); }
  else if (t.id === "catExc") ED.catExc = t.value;
  else if (t.dataset.f === "exception" && ED) ED.rows[+t.dataset.i].exception = t.value;
});
document.addEventListener("keydown", e => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(e.target.tagName);
  const ovOn = document.querySelector(".ov.on");
  if (e.key === "Escape") { if (ovOn) closeOv(); else if (typing) { e.target.blur(); $("#scroller").focus(); } return; }
  if (ovOn) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && $("#editOv").classList.contains("on")) saveEdit(); return; }
  if (typing) { if (e.key === "Enter" && e.target.id === "q") { e.target.blur(); $("#scroller").focus(); } return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === "j" || e.key === "ArrowDown") { e.preventDefault(); select(Math.min(S.sel + 1, S.total - 1)); }
  else if (k === "k" || e.key === "ArrowUp") { e.preventDefault(); select(Math.max(S.sel - 1, 0)); }
  else if (k === "a") { e.preventDefault(); confirmCross(); }
  else if (k === "e") { e.preventDefault(); openEdit(); }
  else if (k === "f" || k === "/") { e.preventDefault(); $("#q").focus(); $("#q").select(); }
  else if (k === "d") openDocFull();
  else if (k === "?") $("#helpOv").classList.add("on");
  else if (/^[0-9]$/.test(k)) { const c = EXC_ORDER[(+k + 9) % 10]; setFilter("exc", S.f.exc === c ? "" : c); }
});
$("#scroller").addEventListener("scroll", () => requestAnimationFrame(renderList), {passive: true});
window.addEventListener("resize", () => renderList());
$("#helpBtn").onclick = () => $("#helpOv").classList.add("on");
$("#exportBtn").onclick = () => { window.location = "/api/export?phase=" + encodeURIComponent(S.phase); toast("Descargando entrega revisada…"); };
$("#themeBtn").onclick = () => {
  const r = document.documentElement, dark = r.dataset.theme ? r.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  r.dataset.theme = dark ? "light" : "dark"; try { localStorage.setItem("bankrec-theme", r.dataset.theme); } catch (_) {}
};
try { const th = localStorage.getItem("bankrec-theme"); if (th) document.documentElement.dataset.theme = th; } catch (_) {}

async function switchPhase(tag) {
  S.phase = tag; S.entity = ""; S.details.clear(); S.f = {state: "all", exc: "", rule: "", conf: "", q: "", sort: "date"};
  $("#q").value = ""; $("#conf").value = ""; $("#sort").value = "date";
  $("#loading").classList.add("on"); $("#loadingMsg").textContent = "Cargando la fase " + tag + "…";
  for (;;) {
    try { await loadSummary(); break; }
    catch (e) {
      if (!e.loading) { $("#loadingMsg").innerHTML = esc(e.message) + `<div style="margin-top:12px"><a class="btn" href="/">Volver</a></div>`; return; }
      $("#loadingMsg").textContent = `Cargando la fase ${tag} (la primera vez tarda: lee data y out)…`;
      await new Promise(r => setTimeout(r, 1500));
    }
  }
  $("#loading").classList.remove("on");
  try { history.replaceState(null, "", "?phase=" + tag); } catch (_) {}
  await resetList();
  $("#scroller").focus({preventScroll: true});
}
(async () => {
  const ph = await fetch("/api/phases").then(r => r.json());
  const want = new URLSearchParams(location.search).get("phase");
  await switchPhase(ph.phases.some(p => p.tag === want && p.available) ? want : ph.default);
})();