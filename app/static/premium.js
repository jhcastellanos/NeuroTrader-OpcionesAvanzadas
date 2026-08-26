(function () {
  var MAX_SELECTION = 3;
  var SESSION = {
    pre_market: "PRE-MERCADO",
    open: "MERCADO ABIERTO",
    after_hours: "FUERA DE HORARIO",
    closed: "MERCADO CERRADO"
  };
  var STATUS = {
    oportunidad_valida: "Oportunidad válida",
    aceptable_con_precaucion: "Aceptable con precaución",
    esperar: "Esperar",
    no_aplica: "No aplica"
  };
  var ROLE = { conservador: "Conservador", equilibrado: "Equilibrado", agresivo: "Agresivo" };
  var METRIC_LABELS = {
    premium: { label: "Prima", format: "usd" },
    prima_total: { label: "Prima total", format: "usd" },
    rendimiento_prima: { label: "Rendimiento de prima", format: "pct" },
    rendimiento_sobre_costo: { label: "Rendimiento s/costo", format: "pct" },
    ganancia_si_asignado: { label: "Ganancia si asignado", format: "usd" },
    retorno_si_asignado: { label: "Retorno si asignado", format: "pct" },
    break_even: { label: "Break-even", format: "usd" },
    upside_restante: { label: "Upside restante", format: "pct" },
    capital_requerido: { label: "Capital requerido", format: "usd" },
    descuento_efectivo: { label: "Descuento efectivo", format: "pct" },
    retorno_sobre_capital: { label: "Retorno s/capital", format: "pct" },
    retorno_anualizado_aprox: { label: "Retorno anualizado (aprox)", format: "pct" }
  };
  var COMPARE_ROWS = [
    ["contract_type", "Tipo", function (c) { return c.contract_type === "call" ? "CALL" : "PUT"; }],
    ["strike", "Strike", function (c) { return money(c.strike); }],
    ["expiration_date", "Vencimiento", function (c) { return c.expiration_date || "—"; }],
    ["dte", "DTE", function (c) { return String(c.dte == null ? "—" : c.dte); }],
    ["bid", "Bid", function (c) { return c.bid == null ? "—" : money(c.bid); }],
    ["ask", "Ask", function (c) { return c.ask == null ? "—" : money(c.ask); }],
    ["mid", "Mid (prima aprox.)", function (c) { return c.mid == null ? "—" : money(c.mid); }],
    ["delta", "Delta", function (c) { return c.delta == null ? "—" : Number(c.delta).toFixed(2); }],
    ["implied_volatility", "IV", function (c) { return c.implied_volatility == null ? "—" : (c.implied_volatility * 100).toFixed(0) + "%"; }],
    ["open_interest", "Open interest", function (c) { return c.open_interest == null ? "—" : Number(c.open_interest).toLocaleString("en-US"); }],
    ["volume", "Volumen", function (c) { return c.volume == null ? "—" : Number(c.volume).toLocaleString("en-US"); }],
    ["spread_percent", "Spread %", function (c) { return c.spread_percent == null ? "—" : Number(c.spread_percent).toFixed(1) + "%"; }]
  ];

  var CHAIN_REFRESH_MS = 30000;
  var state = {
    ticker: "",
    dashboard: null,
    selected: [],
    selectedBySymbol: {},
    expiration: "",
    requestedDte: 7,
    typeFilter: "all",
    sortKey: "strike",
    lastStrategy: null,
    loading: false,
    liveChain: null,
    chainLoading: false,
    chainError: false,
    chainReqId: 0,
    chainTimer: null,
    expirationKey: ""
  };

  function $(id) { return document.getElementById(id); }
  function money(v) {
    if (v == null || v === "") return "—";
    return "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function statusLabel(s) { return STATUS[s] || s || "—"; }
  function formatMetric(key, value) {
    if (value == null) return "—";
    var meta = METRIC_LABELS[key];
    if (!meta) return String(value);
    if (meta.format === "usd") return money(value);
    return (Number(value) * 100).toFixed(2) + "%";
  }
  function authDetail(d) {
    if (!d) return "No se pudo completar la solicitud.";
    if (typeof d.detail === "string") return d.detail;
    if (Array.isArray(d.detail) && d.detail[0] && d.detail[0].msg) return d.detail[0].msg;
    return "No se pudo completar la solicitud.";
  }
  async function api(path, options) {
    var r = await fetch(path, Object.assign({ credentials: "same-origin" }, options || {}));
    var d = await r.json().catch(function () { return {}; });
    if (r.status === 401) {
      window.dispatchEvent(new CustomEvent("nt-session-expired"));
      throw new Error("Sesión expirada. Inicia sesión de nuevo.");
    }
    if (!r.ok) throw new Error(authDetail(d));
    return d;
  }
  async function post(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
  }

  function scoreBar(label, points, max) {
    var pct = max ? Math.round((points / max) * 100) : 0;
    return '<div class="scorebar"><div class="row"><span>' + esc(label) + "</span><span>" +
      points + "/" + max + '</span></div><div class="meter"><i style="width:' + pct + '%"></i></div></div>';
  }
  function metric(label, value) {
    return "<div><span>" + esc(label) + "</span><strong>" + esc(value) + "</strong></div>";
  }

  function renderQuote(q, note) {
    if (!q) {
      $("quoteSession").textContent = "Cotización";
      $("quoteTicker").textContent = "Sin datos aún";
      $("quoteBadge").textContent = "—";
      $("quotePrice").textContent = "—";
      $("quoteChange").textContent = "—";
      $("quoteMetrics").innerHTML = "";
      $("quoteUpdated").textContent = "Busca un ticker para ver cotización, tendencia y estado de mercado.";
      return;
    }
    var up = Number(q.change_percent) >= 0;
    $("quoteSession").textContent = SESSION[q.market_session] || q.market_session || "Cotización";
    $("quoteTicker").textContent = q.ticker;
    $("quoteBadge").textContent = q.is_demo ? "DEMO DATA — NOT LIVE" : (q.data_source_status || "live");
    $("quotePrice").textContent = money(q.price);
    $("quoteChange").textContent = (up ? "+" : "") + Number(q.change).toFixed(2) + " (" + (up ? "+" : "") + Number(q.change_percent).toFixed(2) + "%)";
    $("quoteChange").className = up ? "green" : "red";
    $("quoteMetrics").innerHTML = [
      metric("Máx. del día", money(q.day_high)),
      metric("Mín. del día", money(q.day_low)),
      metric("Máx. 52 sem.", money(q.week52_high)),
      metric("Mín. 52 sem.", money(q.week52_low)),
      metric("Volumen", q.volume == null ? "—" : Number(q.volume).toLocaleString("en-US")),
      metric("Vol. relativo", q.relative_volume == null ? "—" : Number(q.relative_volume).toFixed(2) + "x")
    ].join("");
    $("quoteUpdated").textContent = "Actualizado " + (q.updated_at_ny || "") + " · America/New_York" + (note ? " · " + note : "");
  }

  function renderScore(underlying) {
    var bars = $("scoreBars");
    var metrics = $("scoreMetrics");
    var details = $("scoreDetails");
    if (!underlying) {
      $("scoreRegime").textContent = "—";
      $("scoreTotal").textContent = "—";
      bars.innerHTML = "";
      metrics.innerHTML = "";
      $("scoreLevels").textContent = "";
      details.style.display = "none";
      return;
    }
    var t = underlying.technical || {};
    var s = underlying.score || {};
    $("scoreRegime").textContent = String(t.regime || underlying.regime || "—").replace("_", " ");
    $("scoreRegime").className = "pill " + (t.regime === "alcista" ? "green" : t.regime === "bajista" ? "red" : "gold");
    $("scoreTotal").textContent = (s.total_score == null ? "—" : s.total_score) + "/100";
    bars.innerHTML =
      scoreBar("Tendencia", s.trend_score || 0, s.trend_max || 25) +
      scoreBar("Momentum", s.momentum_score || 0, s.momentum_max || 15) +
      scoreBar("Volatilidad", s.volatility_score || 0, s.volatility_max || 20) +
      scoreBar("Estructura técnica", s.structure_score || 0, s.structure_max || 20) +
      scoreBar("Riesgo de eventos", s.event_risk_score || 0, s.event_risk_max || 10) +
      scoreBar("Liquidez", s.liquidity_score || 0, s.liquidity_max || 10);
    metrics.innerHTML = [
      metric("RSI 14", t.rsi_14 == null ? "—" : Number(t.rsi_14).toFixed(1)),
      metric("EMA 20", t.ema_20 == null ? "—" : money(t.ema_20)),
      metric("SMA 200", t.sma_200 == null ? "—" : money(t.sma_200)),
      metric("ATR 14", t.atr_14 == null ? "—" : Number(t.atr_14).toFixed(2)),
      metric("Vol. histórica", t.historical_volatility_pct == null ? "—" : t.historical_volatility_pct + "%"),
      metric("Tendencia diaria", t.daily_trend || "—"),
      metric("Tendencia semanal", t.weekly_trend || "—"),
      metric("Barras usadas", t.bars_used == null ? "—" : String(t.bars_used))
    ].join("");
    var levels = [];
    if ((t.supports || []).length) levels.push("Soportes: " + t.supports.map(function (x) { return money(x); }).join(", "));
    if ((t.resistances || []).length) levels.push("Resistencias: " + t.resistances.map(function (x) { return money(x); }).join(", "));
    $("scoreLevels").textContent = levels.join("  ·  ");
    var reasons = s.reasons || [];
    $("scoreReasons").innerHTML = reasons.map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("");
    details.style.display = reasons.length ? "block" : "none";
    $("scoreSummaryLabel").textContent = "Ver explicación completa del score (" + reasons.length + " factores)";
  }

  function incomeVisible() {
    var el = $("incomeView");
    return !!(el && el.classList.contains("show"));
  }

  function mapLiveContract(c) {
    var bid = c.bid;
    var ask = c.ask;
    var mid = c.mid;
    if (mid == null && bid != null && ask != null) mid = (Number(bid) + Number(ask)) / 2;
    return {
      occ_symbol: c.symbol,
      contract_type: c.type === "CALL" ? "call" : "put",
      strike: c.strike,
      bid: bid,
      ask: ask,
      mid: mid,
      delta: c.delta,
      implied_volatility: c.impliedVolatility,
      open_interest: c.openInterest,
      expiration_date: c.expiration,
      dte: c.dte,
      spread_percent: c.spreadPercent,
      volume: c.volume == null ? null : c.volume
    };
  }

  function chainStatusLabel(chain) {
    if (!chain) return "";
    var status = chain.dataStatus;
    if (status === "realtime") return "LIVE DATA";
    if (status === "delayed") return "DELAYED DATA";
    if (status === "cached") return "CACHED DATA";
    if (status === "historical") return "HISTORICAL DATA";
    if ((chain.contracts || []).length) return "MARKET DATA";
    return "DATA UNAVAILABLE";
  }

  function chainExpirations() {
    return (state.liveChain && state.liveChain.expirations) || [];
  }

  function currentRows() {
    var rows = ((state.liveChain && state.liveChain.contracts) || []).slice();
    if (state.typeFilter !== "all") {
      rows = rows.filter(function (c) { return c.contract_type === state.typeFilter; });
    }
    var key = state.sortKey;
    rows.sort(function (a, b) {
      var av, bv;
      if (key === "delta") { av = Math.abs(a.delta || 0); bv = Math.abs(b.delta || 0); }
      else if (key === "iv") { av = a.implied_volatility || 0; bv = b.implied_volatility || 0; }
      else if (key === "oi") { av = a.open_interest || 0; bv = b.open_interest || 0; }
      else if (key === "spread") { av = a.spread_percent || 0; bv = b.spread_percent || 0; }
      else if (key === "dte") { av = a.dte || 0; bv = b.dte || 0; }
      else { av = a.strike || 0; bv = b.strike || 0; }
      if (av !== bv) return av - bv;
      var at = a.contract_type === "call" ? 0 : 1;
      var bt = b.contract_type === "call" ? 0 : 1;
      return at - bt;
    });
    return rows;
  }

  function selectedContracts() {
    return state.selected.map(function (id) { return state.selectedBySymbol[id]; }).filter(Boolean);
  }

  function stopChainTimer() {
    if (state.chainTimer) {
      clearInterval(state.chainTimer);
      state.chainTimer = null;
    }
  }

  function startChainTimer() {
    stopChainTimer();
    state.chainTimer = setInterval(function () {
      if (!incomeVisible() || !state.ticker) return;
      fetchChain({ silent: true });
    }, CHAIN_REFRESH_MS);
  }

  function bidAskText(c) {
    if (c.bid == null || c.ask == null) return "—";
    return Number(c.bid).toFixed(2) + "/" + Number(c.ask).toFixed(2);
  }

  function syncExpirationSelect(exps) {
    var expSel = $("chainExp");
    var key = (exps || []).map(function (e) { return e.dte + ":" + e.expiration; }).join("|");
    var current = String(state.requestedDte);
    if (key !== state.expirationKey) {
      state.expirationKey = key;
      expSel.innerHTML = (exps || []).map(function (e) {
        return '<option value="' + esc(String(e.dte)) + '"' + (String(e.dte) === current ? " selected" : "") + ">" +
          e.dte + " DTE · " + esc(e.expiration) + "</option>";
      }).join("");
    }
    if (expSel.value !== current) expSel.value = current;
  }

  function renderChain() {
    var title = $("chainTitle");
    var badge = $("chainBadge");
    var filters = $("chainFilters");
    var tableWrap = $("chainTable");
    if (!state.dashboard && !state.ticker) {
      title.textContent = "Sin datos aún";
      badge.textContent = "";
      filters.style.display = "none";
      tableWrap.innerHTML = '<p class="note">Busca un ticker para ver todos los contratos disponibles por vencimiento.</p>';
      renderComparador([]);
      return;
    }
    title.textContent = state.ticker || (state.dashboard && state.dashboard.ticker) || "";
    if (state.liveChain && (state.liveChain.contracts || []).length) {
      badge.textContent = chainStatusLabel(state.liveChain);
    } else if (state.ticker && !state.chainLoading) {
      badge.textContent = "DATA UNAVAILABLE";
    } else {
      badge.textContent = "";
    }
    var exps = chainExpirations();
    if (exps.length) {
      filters.style.display = "flex";
      syncExpirationSelect(exps);
    } else if (state.ticker) {
      filters.style.display = "flex";
    }
    var rows = currentRows();
    var keepTable = state.chainLoading && tableWrap.querySelector("table.income");
    if (state.chainLoading && !rows.length && !keepTable) {
      tableWrap.innerHTML = '<p class="note">Cargando contratos...</p>';
      renderComparador(selectedContracts());
      return;
    }
    if (state.chainError && !rows.length) {
      tableWrap.innerHTML = '<p class="note">No fue posible cargar los contratos.</p>';
      renderComparador(selectedContracts());
      return;
    }
    if (!rows.length && !state.chainLoading) {
      if (state.liveChain && (state.liveChain.contracts || []).length) {
        tableWrap.innerHTML = '<p class="note">No hay contratos para este vencimiento y filtro.</p>';
      } else {
        tableWrap.innerHTML = '<p class="note">No fue posible cargar los contratos.</p>';
      }
      renderComparador(selectedContracts());
      return;
    }
    if (!keepTable) {
      var html = '<div class="income-table-wrap' + (state.chainLoading ? " is-loading" : "") + '"><table class="income"><thead><tr>' +
        "<th>✓</th><th>Tipo</th><th>Strike</th><th>Bid/Ask</th><th>Delta</th><th>IV</th><th>OI</th><th>Spread %</th>" +
        "</tr></thead><tbody>";
      rows.forEach(function (c) {
        var sel = state.selected.indexOf(c.occ_symbol) >= 0;
        html += '<tr data-occ="' + esc(c.occ_symbol) + '" class="' + (sel ? "sel" : "") + '">' +
          '<td><input type="checkbox"' + (sel ? " checked" : "") + "></td>" +
          '<td class="' + (c.contract_type === "call" ? "green" : "red") + '">' + (c.contract_type === "call" ? "CALL" : "PUT") + "</td>" +
          "<td>" + money(c.strike) + "</td>" +
          "<td>" + bidAskText(c) + "</td>" +
          "<td>" + (c.delta == null ? "—" : Number(c.delta).toFixed(2)) + "</td>" +
          "<td>" + (c.implied_volatility == null ? "—" : (c.implied_volatility * 100).toFixed(0) + "%") + "</td>" +
          "<td>" + (c.open_interest == null ? "—" : Number(c.open_interest).toLocaleString("en-US")) + "</td>" +
          "<td>" + (c.spread_percent == null ? "—" : Number(c.spread_percent).toFixed(1)) + "</td>" +
          "</tr>";
      });
      html += "</tbody></table></div>";
      tableWrap.innerHTML = html;
    } else {
      var wrap = tableWrap.querySelector(".income-table-wrap");
      if (wrap) wrap.classList.toggle("is-loading", !!state.chainLoading);
    }
    renderComparador(selectedContracts());
  }

  function toggleOcc(occ) {
    var i = state.selected.indexOf(occ);
    if (i >= 0) {
      state.selected.splice(i, 1);
      delete state.selectedBySymbol[occ];
    } else if (state.selected.length < MAX_SELECTION) {
      var found = null;
      currentRows().some(function (c) { if (c.occ_symbol === occ) { found = c; return true; } return false; });
      if (!found) return;
      state.selected.push(occ);
      state.selectedBySymbol[occ] = found;
    }
    renderChain();
  }

  async function fetchChain(opts) {
    opts = opts || {};
    if (!state.ticker) return;
    var ticker = state.ticker;
    var dte = state.requestedDte == null ? 7 : state.requestedDte;
    var reqId = ++state.chainReqId;
    state.chainLoading = true;
    if (opts.tickerChange) {
      state.liveChain = null;
      state.chainError = false;
      state.expirationKey = "";
    }
    renderChain();
    try {
      var data = await api("/api/options/" + encodeURIComponent(ticker) + "?dte=" + encodeURIComponent(dte));
      if (reqId !== state.chainReqId || state.ticker !== ticker) return;
      var contracts = (data.contracts || []).map(mapLiveContract);
      state.liveChain = {
        live: data.dataStatus === "realtime",
        ok: data.ok !== false && contracts.length > 0,
        dataStatus: data.dataStatus || null,
        contracts: contracts,
        expirations: data.expirations || [],
        expiration: data.expiration,
        actualDte: data.actualDte,
        requestedDte: data.requestedDte,
        updated: data.updated
      };
      state.chainError = !contracts.length;
      if (data.actualDte != null) state.requestedDte = data.actualDte;
      startChainTimer();
    } catch (err) {
      if (reqId !== state.chainReqId || state.ticker !== ticker) return;
      state.chainError = true;
      if (!state.liveChain || opts.dteChange || opts.tickerChange) {
        state.liveChain = {
          live: false,
          contracts: [],
          expirations: (state.liveChain && state.liveChain.expirations) || []
        };
      }
    } finally {
      if (reqId === state.chainReqId) {
        state.chainLoading = false;
        renderChain();
      }
    }
  }

  function renderComparador(contracts) {
    var box = $("comparadorBody");
    var title = $("comparadorTitle");
    if (!contracts.length) {
      title.textContent = "Sin contratos seleccionados";
      box.innerHTML = '<p class="note">Selecciona hasta 3 contratos en la tabla (checkbox) para compararlos lado a lado.</p>';
      return;
    }
    title.textContent = "Comparando " + contracts.length + " contrato" + (contracts.length > 1 ? "s" : "");
    var html = '<div class="income-table-wrap"><table class="income"><thead><tr><th>Métrica</th>';
    contracts.forEach(function (c) { html += "<th>" + esc(c.occ_symbol) + "</th>"; });
    html += "</tr></thead><tbody>";
    COMPARE_ROWS.forEach(function (row) {
      html += "<tr><td>" + esc(row[1]) + "</td>";
      contracts.forEach(function (c) { html += "<td>" + esc(row[2](c)) + "</td>"; });
      html += "</tr>";
    });
    html += "</tbody></table></div>";
    box.innerHTML = html;
  }

  function contractCard(ev, best) {
    if (!ev) return '<div class="contract"><b>Sin contrato</b><div class="note">El motor no encontró un candidato para este perfil.</div></div>';
    var c = ev.contract || {};
    var m = ev.metrics || {};
    var score = ev.score || {};
    var rows = Object.keys(METRIC_LABELS).filter(function (k) { return Object.prototype.hasOwnProperty.call(m, k); }).map(function (k) {
      return '<div class="metric-row"><span>' + METRIC_LABELS[k].label + "</span><strong>" + formatMetric(k, m[k]) + "</strong></div>";
    }).join("");
    var warns = (ev.warnings || []).map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("");
    return '<div class="contract' + (best ? " best" : "") + '"><b>' + esc(ROLE[ev.role] || ev.role) +
      (best ? " · mejor equilibrio" : "") + "</b><strong>" + money(c.strike) + " · " + (c.dte || "—") + " DTE</strong>" +
      '<div class="note">Delta ' + (c.delta == null ? "—" : Number(c.delta).toFixed(2)) +
      " · IV " + (c.implied_volatility == null ? "—" : (c.implied_volatility * 100).toFixed(0) + "%") + "</div>" +
      '<div class="note gold">Score ' + (score.total_score || 0) + "/100 · " + esc(score.classification || "") + "</div>" +
      rows + (warns ? '<ul class="reasons">' + warns + "</ul>" : "") + "</div>";
  }

  function renderStrategyResult(targetId, result, explainKind) {
    var box = $(targetId);
    if (!result) { box.innerHTML = ""; return; }
    var best = result.best_balance;
    var html = '<div class="strat-result">' +
      '<div class="head"><span class="pill">' + esc(statusLabel(result.status)) + "</span>" +
      '<span class="note">Convicción: <b class="gold">' + (result.conviction_score || 0) + "/100</b></span>" +
      (result.is_demo ? '<span class="pill gold">DEMO DATA — NOT LIVE</span>' : "") + "</div>" +
      '<p class="note">' + esc(result.summary || "") + "</p>" +
      (result.next_important_event ? '<p class="note gold">' + esc(result.next_important_event) + "</p>" : "") +
      '<p class="note">' + esc((result.volatility && result.volatility.interpretation) || "") + "</p>" +
      '<div class="split-2"><div><p class="eyebrow green">A favor</p><ul class="reasons">' +
      (result.reasons_for || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") +
      '</ul></div><div><p class="eyebrow red">En contra</p><ul class="reasons">' +
      (result.reasons_against || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") +
      "</ul></div></div>" +
      '<div class="role-grid">' +
      contractCard(result.conservative, best === "conservador") +
      contractCard(result.balanced, best === "equilibrado") +
      contractCard(result.aggressive, best === "agresivo") +
      "</div>";
    if ((result.risks || []).length) {
      html += '<p class="eyebrow gold" style="margin-top:12px">Riesgos principales</p><ul class="reasons">' +
        result.risks.map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") + "</ul>";
    }
    html += '<p class="note">Actualizado ' + esc(result.updated_at_ny || "") +
      ". Herramienta educativa: no constituye asesoramiento financiero personalizado ni garantiza resultados.</p>" +
      '<button type="button" class="ghost" data-explain="' + explainKind + '">Explicar con IA</button>' +
      '<div class="note" data-explain-out></div></div>';
    box.innerHTML = html;
  }

  function ccPayoff(strike, costBasis, premium, S) {
    return Math.min(S, strike) - costBasis + premium;
  }
  function cspPayoff(strike, premium, S) {
    return S >= strike ? premium : S - strike + premium;
  }

  function renderRisk(result) {
    var title = $("riskTitle");
    var body = $("riskBody");
    if (!result) {
      title.textContent = "Sin datos aún";
      body.innerHTML = '<p class="note">Evalúa un Covered Call o un Cash-Secured Put arriba para ver el perfil de riesgo del contrato equilibrado al vencimiento.</p>';
      return;
    }
    if (!result.balanced) {
      title.textContent = result.ticker || "Vista de riesgo";
      body.innerHTML = '<p class="note">No hay un contrato equilibrado válido para graficar el riesgo con los datos actuales.</p>';
      return;
    }
    var contract = result.balanced.contract;
    var metrics = result.balanced.metrics || {};
    var premium = metrics.premium || 0;
    var breakEven = metrics.break_even || contract.strike;
    var strike = contract.strike;
    var isCC = result.strategy === "covered_call";
    var costBasis = isCC ? breakEven + premium : 0;
    var low = strike * 0.7;
    var high = strike * 1.3;
    var step = (high - low) / 30;
    var points = [];
    var minP = Infinity;
    var maxP = -Infinity;
    for (var s = low; s <= high + step / 2; s += step) {
      var pnl = isCC ? ccPayoff(strike, costBasis, premium, s) : cspPayoff(strike, premium, s);
      points.push({ price: s, pnl: pnl });
      if (pnl < minP) minP = pnl;
      if (pnl > maxP) maxP = pnl;
    }
    if (minP === maxP) { minP -= 1; maxP += 1; }
    var w = 820, h = 200, padL = 50, padR = 20, padT = 16, padB = 28;
    function x(p) { return padL + (p.price - low) / (high - low) * (w - padL - padR); }
    function y(v) { return padT + (1 - (v - minP) / (maxP - minP)) * (h - padT - padB); }
    var d = points.map(function (p, i) { return (i ? "L" : "M") + x(p).toFixed(1) + "," + y(p.pnl).toFixed(1); }).join(" ");
    var zero = y(0);
    var beX = x({ price: breakEven });
    var svg = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">' +
      '<line x1="' + padL + '" y1="' + zero + '" x2="' + (w - padR) + '" y2="' + zero + '" stroke="#334155"/>' +
      '<line x1="' + beX + '" y1="' + padT + '" x2="' + beX + '" y2="' + (h - padB) + '" stroke="#d4af37" stroke-dasharray="4 4"/>' +
      '<text x="' + Math.min(beX + 6, w - 90) + '" y="' + (padT + 12) + '" fill="#d4af37" font-size="11">Break-even</text>' +
      '<path d="' + d + '" fill="none" stroke="#d4af37" stroke-width="2"/>' +
      "</svg>";
    title.textContent = (result.ticker || "") + " · Payoff al vencimiento (contrato equilibrado)";
    var risks = (result.risks || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("");
    var inv = (result.invalidation_conditions || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("");
    body.innerHTML = '<p class="note">Ganancia/pérdida por acción, sin comisiones, asumiendo mantener la posición hasta el vencimiento. Escenario simplificado con fines educativos.</p>' +
      '<div class="payoff">' + svg + "</div>" +
      '<div class="quote-metrics">' + metric("Strike", money(strike)) + metric("Break-even", money(breakEven)) + metric("Prima", money(premium)) + "</div>" +
      (risks ? '<p class="eyebrow gold" style="margin-top:12px">Qué puede salir mal</p><ul class="reasons">' + risks + "</ul>" : "") +
      (inv ? '<p class="eyebrow red">Condiciones que invalidarían la operación</p><ul class="reasons">' + inv + "</ul>" : "");
  }

  function ccBody() {
    return {
      ticker: state.ticker,
      shares_owned: Number($("ccShares").value || 0),
      cost_basis: Number($("ccCost").value || 0),
      risk_profile: $("ccRisk").value,
      horizon: "ingreso_mensual",
      min_yield_pct: Number($("ccYield").value || 0),
      willing_to_sell_shares: true,
      strike_must_be_above_cost_basis: true,
      max_contracts: Number($("ccMax").value || 1),
      accept_earnings_before_expiration: $("ccEarnings").checked
    };
  }
  function cspBody() {
    return {
      ticker: state.ticker,
      capital_available: Number($("cspCapital").value || 0),
      risk_profile: $("cspRisk").value,
      horizon: "ingreso_mensual",
      min_yield_pct: Number($("cspYield").value || 0),
      willing_to_buy_shares: $("cspWilling").checked,
      max_contracts: Number($("cspMax").value || 1),
      accept_earnings_before_expiration: $("cspEarnings").checked
    };
  }

  function applyDefaults(quote) {
    if (!quote) return;
    $("ccShares").value = 100;
    $("ccCost").value = Number(quote.price).toFixed(2);
    $("ccYield").value = "1.0";
    $("ccMax").value = 1;
    $("ccRisk").value = "equilibrado";
    $("ccEarnings").checked = false;
    $("cspCapital").value = (Number(quote.price) * 100).toFixed(2);
    $("cspYield").value = "1.0";
    $("cspMax").value = 1;
    $("cspRisk").value = "equilibrado";
    $("cspWilling").checked = true;
    $("cspEarnings").checked = false;
    $("cmpShares").value = 100;
    $("cmpCost").value = Number(quote.price).toFixed(2);
    $("cmpCapital").value = (Number(quote.price) * 100).toFixed(2);
    $("cmpYield").value = "1.0";
    $("cmpRisk").value = "equilibrado";
  }

  function setStratTab(name) {
    var cc = name === "cc";
    $("btnTabCc").className = cc ? "active" : "";
    $("btnTabCsp").className = cc ? "" : "active";
    $("ccForm").style.display = cc ? "block" : "none";
    $("cspForm").style.display = cc ? "none" : "block";
    $("strategyTitle").textContent = "Evaluar estrategia — " + (state.ticker || "");
  }

  function renderDashboard(d) {
    state.dashboard = d;
    state.ticker = d.ticker;
    $("incomeNote").textContent = d.options_note || "";
    $("incomeDisclaimer").textContent = d.disclaimer || "";
    $("chainEyebrow").textContent = "TABLA DE CONTRATOS — " + d.ticker;
    $("strategyTitle").textContent = "Evaluar estrategia — " + d.ticker;
    $("compareTitle").textContent = "Comparar Covered Call vs. CSP — " + d.ticker;
    renderQuote(d.quote, d.options_live ? "" : d.options_note);
    renderScore(d.underlying);
    applyDefaults(d.quote);
    $("ccResult").innerHTML = "";
    $("cspResult").innerHTML = "";
    $("compareResult").innerHTML = "";
    state.lastStrategy = null;
    renderRisk(null);
    fetchChain({ tickerChange: true });
  }

  function emptyDashboard() {
    stopChainTimer();
    state.dashboard = null;
    state.ticker = "";
    state.selected = [];
    state.selectedBySymbol = {};
    state.expiration = "";
    state.requestedDte = 7;
    state.lastStrategy = null;
    state.liveChain = null;
    state.chainLoading = false;
    state.chainError = false;
    state.expirationKey = "";
    $("incomeNote").textContent = "Analiza un ticker para abrir el dashboard de Premium Income (Covered Call y Cash-Secured Put).";
    $("incomeDisclaimer").textContent = "";
    renderQuote(null);
    renderScore(null);
    renderChain();
    renderRisk(null);
    $("ccResult").innerHTML = "";
    $("cspResult").innerHTML = "";
    $("compareResult").innerHTML = "";
    $("strategyTitle").textContent = "Evaluar estrategia";
    $("compareTitle").textContent = "Comparar Covered Call vs. CSP";
  }

  async function load(symbol, force) {
    if (!symbol) { emptyDashboard(); return; }
    if (!force && state.dashboard && state.dashboard.ticker === symbol && !state.loading) {
      startChainTimer();
      if (!state.chainLoading && (!state.liveChain || !state.liveChain.live)) fetchChain();
      return;
    }
    $("incomeNote").textContent = "Cargando cotización, Underlying Score y cadena de contratos para " + symbol + "…";
    state.loading = true;
    try {
      var d = await api("/api/income/" + encodeURIComponent(symbol));
      state.selected = [];
      state.selectedBySymbol = {};
      state.expiration = "";
      renderDashboard(d);
    } catch (e) {
      $("incomeNote").textContent = e.message;
    } finally {
      state.loading = false;
    }
  }

  function bind() {
    if (!$("btnTabCc") || !$("ccFormEl")) return;
    $("btnTabCc").addEventListener("click", function () { setStratTab("cc"); });
    $("btnTabCsp").addEventListener("click", function () { setStratTab("csp"); });
    $("chainExp").addEventListener("change", function () {
      state.requestedDte = Number(this.value);
      fetchChain({ dteChange: true });
    });
    $("chainType").addEventListener("change", function () { state.typeFilter = this.value; renderChain(); });
    $("chainSort").addEventListener("change", function () { state.sortKey = this.value; renderChain(); });
    $("chainTable").addEventListener("click", function (e) {
      var tr = e.target.closest("tr[data-occ]");
      if (!tr) return;
      toggleOcc(tr.getAttribute("data-occ"));
    });
    $("ccFormEl").addEventListener("submit", async function (e) {
      e.preventDefault();
      if (!state.ticker) return;
      var btn = $("ccSubmit");
      btn.disabled = true;
      btn.textContent = "Evaluando…";
      $("ccErr").textContent = "";
      $("ccErr").style.display = "none";
      try {
        var res = await post("/api/income/covered-call", ccBody());
        state.lastStrategy = res;
        renderStrategyResult("ccResult", res, "cc");
        renderRisk(res);
      } catch (err) {
        $("ccErr").textContent = err.message;
        $("ccErr").style.display = "block";
      } finally {
        btn.disabled = false;
        btn.textContent = "Evaluar Covered Call";
      }
    });
    $("cspFormEl").addEventListener("submit", async function (e) {
      e.preventDefault();
      if (!state.ticker) return;
      var btn = $("cspSubmit");
      btn.disabled = true;
      btn.textContent = "Evaluando…";
      $("cspErr").textContent = "";
      $("cspErr").style.display = "none";
      try {
        var res = await post("/api/income/csp", cspBody());
        state.lastStrategy = res;
        renderStrategyResult("cspResult", res, "csp");
        renderRisk(res);
      } catch (err) {
        $("cspErr").textContent = err.message;
        $("cspErr").style.display = "block";
      } finally {
        btn.disabled = false;
        btn.textContent = "Evaluar Cash-Secured Put";
      }
    });
    $("ccResult").addEventListener("click", async function (e) {
      if (!e.target.getAttribute || e.target.getAttribute("data-explain") !== "cc") return;
      var out = $("ccResult").querySelector("[data-explain-out]");
      e.target.disabled = true;
      e.target.textContent = "Generando explicación…";
      try {
        var res = await post("/api/income/explain/covered-call", ccBody());
        out.innerHTML = '<span class="pill gold">IA · plantilla demo</span><p class="note">' + esc(res.explanation) + "</p>";
        e.target.style.display = "none";
      } catch (err) {
        out.textContent = err.message;
        e.target.disabled = false;
        e.target.textContent = "Explicar con IA";
      }
    });
    $("cspResult").addEventListener("click", async function (e) {
      if (!e.target.getAttribute || e.target.getAttribute("data-explain") !== "csp") return;
      var out = $("cspResult").querySelector("[data-explain-out]");
      e.target.disabled = true;
      e.target.textContent = "Generando explicación…";
      try {
        var res = await post("/api/income/explain/csp", cspBody());
        out.innerHTML = '<span class="pill gold">IA · plantilla demo</span><p class="note">' + esc(res.explanation) + "</p>";
        e.target.style.display = "none";
      } catch (err) {
        out.textContent = err.message;
        e.target.disabled = false;
        e.target.textContent = "Explicar con IA";
      }
    });
    $("cmpForm").addEventListener("submit", async function (e) {
      e.preventDefault();
      if (!state.ticker) return;
      var btn = $("cmpSubmit");
      btn.disabled = true;
      btn.textContent = "Comparando…";
      $("cmpErr").textContent = "";
      $("cmpErr").style.display = "none";
      try {
        var body = {
          ticker: state.ticker,
          risk_profile: $("cmpRisk").value,
          horizon: "ingreso_mensual",
          min_yield_pct: Number($("cmpYield").value || 0),
          accept_earnings_before_expiration: false,
          shares_owned: $("cmpShares").value ? Number($("cmpShares").value) : null,
          cost_basis: $("cmpCost").value ? Number($("cmpCost").value) : null,
          capital_available: $("cmpCapital").value ? Number($("cmpCapital").value) : null
        };
        var res = await post("/api/income/compare", body);
        var html = '<div class="split-2">';
        function side(label, data, winner) {
          if (!data) {
            return '<div class="contract"><b>' + label + '</b><p class="note">No evaluada (faltan datos para esta estrategia).</p></div>';
          }
          var c = data.balanced && data.balanced.contract;
          return '<div class="contract' + (winner ? " best" : "") + '"><b>' + label + (winner ? " · mejor opción" : "") +
            "</b><div>" + esc(statusLabel(data.status)) + ' · <span class="gold">' + data.conviction_score + "/100</span></div>" +
            (c ? '<p class="note">Contrato equilibrado: strike ' + money(c.strike) + " · " + c.dte + " DTE" +
              (data.balanced.metrics && data.balanced.metrics.premium != null ? " · prima " + money(data.balanced.metrics.premium) : "") + "</p>" : "") +
            "</div>";
        }
        html += side("Covered Call", res.covered_call, res.recommended_strategy === "covered_call");
        html += side("Cash-Secured Put", res.csp, res.recommended_strategy === "cash_secured_put");
        html += "</div><p class=\"note\">" + esc(res.recommendation_reason || "") + "</p>";
        if (res.ai_comparison) {
          html += '<div class="contract"><span class="pill gold">IA · plantilla demo</span><p class="note">' + esc(res.ai_comparison) + "</p></div>";
        }
        html += '<p class="note">Actualizado ' + esc(res.updated_at_ny || "") + ". Herramienta educativa: no constituye asesoramiento financiero personalizado ni garantiza resultados.</p>";
        $("compareResult").innerHTML = html;
      } catch (err) {
        $("cmpErr").textContent = err.message;
        $("cmpErr").style.display = "block";
      } finally {
        btn.disabled = false;
        btn.textContent = "Comparar con IA";
      }
    });
  }

  window.NTPremium = {
    load: load,
    reset: function () {
      stopChainTimer();
      state.dashboard = null;
      state.ticker = "";
      state.selected = [];
      state.selectedBySymbol = {};
      state.expiration = "";
      state.requestedDte = 7;
      state.liveChain = null;
      state.chainError = false;
      state.expirationKey = "";
      emptyDashboard();
    }
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
