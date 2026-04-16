window.uiController = {
  setActionMessage(text, klass = "") {
    const el = qs("actionMessage");
    el.textContent = text;
    el.className = `action-message ${klass}`.trim();
  },

  applyConfigToForm(cfg = {}) {
    const fields = [
      "symbol",
      "interval",
      "market_type",
      "start",
      "end",
      "initial_balance",
      "fee_rate",
      "slippage_bps",
      "allow_short",
      "leverage",
      "maintenance_margin",
      "max_leverage",
      "max_qty",
      "include_equity",
      "equity_stride",
      "poll_seconds",
      "ema_short",
      "ema_long",
      "candle_limit",
    ];

    for (const key of fields) {
      const el = qs(key);
      if (!el || cfg[key] === undefined || cfg[key] === null) continue;
      el.value = String(cfg[key]);
    }
  },

  readConfigForm() {
    return {
      symbol: qs("symbol").value,
      interval: qs("interval").value,
      market_type: qs("market_type").value,
      start: qs("start").value || null,
      end: qs("end").value || null,
      initial_balance: Number(qs("initial_balance").value),
      fee_rate: Number(qs("fee_rate").value),
      slippage_bps: Number(qs("slippage_bps").value),
      allow_short: qs("allow_short").value === "true",
      leverage: Number(qs("leverage").value),
      maintenance_margin: Number(qs("maintenance_margin").value),
      max_leverage: Number(qs("max_leverage").value),
      max_qty: Number(qs("max_qty").value),
      include_equity: qs("include_equity").value === "true",
      equity_stride: Number(qs("equity_stride").value),
      poll_seconds: Number(qs("poll_seconds").value),
      ema_short: Number(qs("ema_short").value),
      ema_long: Number(qs("ema_long").value),
      candle_limit: Number(qs("candle_limit").value),
    };
  },


  _currentLivePositionInfo(data) {
    const positions = data?.runtime?.live?.positions || [];
    for (const position of positions) {
      const rawSide = String(position?.side || "").toLowerCase();
      const side = rawSide === "sell" ? "short" : rawSide === "buy" ? "long" : rawSide;
      const qty = Number(position?.base_qty || 0);
      if (qty > 0 && (side === "long" || side === "short")) {
        return { side, qty };
      }
    }
    return null;
  },

  updateForceExitButton(data) {
    const btn = qs("forceExitBtn");
    if (!btn) return;

    const isLiveMode = String(data?.mode || "").toLowerCase() === "live";
    const position = this._currentLivePositionInfo(data);
    const canForceExit = isLiveMode && !!position;

    btn.disabled = !canForceExit;
    btn.title = canForceExit
      ? `Force-exit ${position.side} position (${fmtNum(position.qty)} units) with a reduce-only market order.`
      : "No open live position to force exit.";
  },

  renderStatus(data) {
    state.latestStatus = data;

    const runtime = data?.runtime || {};
    const paper = runtime.paper_state || {};
    const latestBar = runtime.latest_bar || {};
    const live = runtime.live || {};
    const execution = live.execution || {};
    const lastLiveFill = execution.last_fill || {};    

    qs("botState").textContent = data?.state || "unknown";
    qs("lastSignal").textContent = String(runtime.last_signal ?? "WAIT");
    qs("modeView").textContent = data?.mode || "-";
    qs("runId").textContent = data?.run_id || "-";
    qs("fillCount").textContent = fmtNum(runtime.fill_count ?? 0, 0);
    qs("cash").textContent = fmtNum(paper.cash);
    qs("positionQty").textContent = fmtNum(paper.position_qty);
    qs("entryPrice").textContent = fmtNum(paper.entry_price);
    qs("equity").textContent = fmtNum(paper.equity);
    qs("realizedPnl").textContent = fmtNum(paper.realized_pnl);
    qs("liqPrice").textContent = fmtNum(paper.liquidation_price);
    qs("latestClose").textContent = fmtNum(latestBar.close);
    qs("latestBarTs").textContent = fmtTs(latestBar.timestamp);
    qs("lastProcessedTs").textContent = fmtTs(runtime.last_processed_bar_ts);
    qs("barCount").textContent = fmtNum(runtime.bar_count ?? 0, 0);
    qs("startedAt").textContent = fmtTs(data?.started_at);
    qs("liveExpectedPrice").textContent = fmtNum(lastLiveFill.expected_price);
    qs("liveFillPrice").textContent = fmtNum(lastLiveFill.price);
    qs("liveSlippageBps").textContent = fmtNum(lastLiveFill.price_slippage_bps);
    qs("liveExpectedQty").textContent = fmtNum(lastLiveFill.expected_qty);
    qs("liveFilledQty").textContent = fmtNum(lastLiveFill.qty);
    qs("liveQtyDeltaPct").textContent = fmtNum(lastLiveFill.qty_delta_pct);
    qs("liveAckMs").textContent = fmtNum(lastLiveFill.submit_to_ack_ms);
    qs("liveFillMs").textContent = fmtNum(lastLiveFill.submit_to_fill_ms);
    qs("liveAvgSlippageBps").textContent = fmtNum(execution.avg_slippage_bps);
    qs("liveMaxAbsSlippageBps").textContent = fmtNum(execution.max_abs_slippage_bps);
    qs("liveAvgAckMs").textContent = fmtNum(execution.avg_submit_to_ack_ms);
    qs("liveAvgFillMs").textContent = fmtNum(execution.avg_submit_to_fill_ms);

    qs("chartSymbolView").textContent =
      runtime.chart_symbol || data?.config?.symbol || "-";
    qs("chartIntervalView").textContent =
      runtime.chart_interval || data?.config?.interval || "-";

    this.updateForceExitButton(data);

    if (paper.equity !== null && paper.equity !== undefined) {
      equityModule.push(paper.equity);
    }

    const nextChartKey = chartModule.currentChartKeyFromStatus(data);
    if (nextChartKey && nextChartKey !== state.lastChartKey) {
      chartModule.loadBootstrap(true).catch((err) => {
        console.error("Chart bootstrap failed:", err);
        this.setActionMessage(`Chart bootstrap failed: ${err.message}`, "bad");
      });
    }
  },

  async configureBot() {
    try {
      const payload = this.readConfigForm();
      await api.configure(payload);
      const data = await api.getStatus();
      this.renderStatus(data);
      await chartModule.loadBootstrap(true);
      this.setActionMessage("Configuration updated.", "good");
    } catch (err) {
      console.error(err);
      this.setActionMessage(`Configure failed: ${err.message}`, "bad");
    }
  },

  async startBot() {
    try {
      await api.setMode(qs("mode").value);
      const data = await api.start();
      this.renderStatus(data);
      await chartModule.loadBootstrap(true);
      this.setActionMessage("Bot started.", "good");
    } catch (err) {
      console.error(err);
      this.setActionMessage(`Start failed: ${err.message}`, "bad");
    }
  },

  async stopBot() {
    try {
      const data = await api.stop();
      this.renderStatus(data);
      this.setActionMessage("Bot stopped.", "warn");
    } catch (err) {
      console.error(err);
      this.setActionMessage(`Stop failed: ${err.message}`, "bad");
    }
  },

  async forceExitLive() {
    const data = state.latestStatus || {};
    const position = this._currentLivePositionInfo(data);
    if (!position) {
      this.setActionMessage("No open live position to force exit.", "warn");
      return;
    }

    const ok = window.confirm(
      `Force-exit the current ${position.side.toUpperCase()} live position of ${fmtNum(position.qty)} units with a reduce-only market order? This sends a real order immediately.`
    );
    if (!ok) return;

    try {
      const response = await api.forceLiveExit(`Manual force exit from dashboard (${position.side})`);
      this.renderStatus(response);
      await fillsModule.load();
      await chartModule.loadBootstrap(true);
      this.setActionMessage(`Force exit submitted for ${position.side} live position.`, "warn");
    } catch (err) {
      console.error(err);
      this.setActionMessage(`Force exit failed: ${err.message}`, "bad");
    }
  },

  async resetPaper() {
    const ok = window.confirm(
      "This will delete the persisted paper session, fills, and snapshot for the current paper run. Continue?"
    );
    if (!ok) return;

    try {
      const data = await api.resetPaper();
      state.latestStatus = data;
      this.renderStatus(data);
      equityModule.clear();
      chartModule.reset();
      await fillsModule.load();
      await chartModule.loadBootstrap(true);
      this.setActionMessage("Paper account reset.", "warn");
    } catch (err) {
      console.error(err);
      this.setActionMessage(`Reset failed: ${err.message}`, "bad");
    }
  },

  async loadStatus() {
    try {
      const data = await api.getStatus();
      this.renderStatus(data);
      this.applyConfigToForm(data.config || {});
      await fillsModule.load();
      await chartModule.loadBootstrap(true);
    } catch (err) {
      console.error(err);
      this.setActionMessage(`Status load failed: ${err.message}`, "bad");
    }
  },

  connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    state.ws = new WebSocket(`${protocol}://${window.location.host}/ws`);

    state.ws.onopen = () => {
      this.setActionMessage("WebSocket connected.", "good");
    };

    state.ws.onclose = () => {
      this.setActionMessage("WebSocket disconnected. Reconnecting...", "warn");
      setTimeout(() => this.connectWebSocket(), 1500);
    };

    state.ws.onerror = (err) => {
      console.error("WS error:", err);
    };

    state.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);

        if (msg.type === "status") {
          this.renderStatus(msg.data);
          return;
        }

        if (msg.type === "equity") {
          if (msg.equity !== null && msg.equity !== undefined) {
            equityModule.push(msg.equity);
          }
          return;
        }

        if (msg.type === "candle") {
          chartModule.applyLiveUpdate(msg);
          return;
        }

        if (msg.type === "fills") {
          fillsModule.load().catch(console.error);
        }
      } catch (err) {
        console.error("Bad WS message:", err, event.data);
      }
    };
  },

  handleResize() {
    equityModule.resize();
    chartModule.resize();
  },

  bindEvents() {
    qs("configureBtn")?.addEventListener("click", () => this.configureBot());
    qs("startBtn")?.addEventListener("click", () => this.startBot());
    qs("stopBtn")?.addEventListener("click", () => this.stopBot());
    qs("forceExitBtn")?.addEventListener("click", () => this.forceExitLive());
    qs("refreshStatusBtn")?.addEventListener("click", () => this.loadStatus());
    qs("showFillsBtn")?.addEventListener("click", () => fillsModule.load());
    qs("resetPaperBtn")?.addEventListener("click", () => this.resetPaper());
    qs("exportCsvBtn")?.addEventListener("click", () => api.exportCsv());
  },

  async init() {
    this.bindEvents();
    window.addEventListener("resize", () => this.handleResize());
    await this.loadStatus();
    this.connectWebSocket();
  },
};

window.addEventListener("load", async () => {
  await uiController.init();
});