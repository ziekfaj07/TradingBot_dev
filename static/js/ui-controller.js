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
      "exchange_name",
      "exchange_settle_currency",
      "enable_live_trading",
      "live_dry_run",
    ];

    for (const key of fields) {
      const el = qs(key);
      if (!el || cfg[key] === undefined || cfg[key] === null) continue;
      if (key === "market_type") {
        el.value = cfg.market_type_ui || cfg.market_type || "spot";
      } else {
        el.value = String(cfg[key]);
      }

    }

    this.syncModeDerivedFields();
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
      exchange_name: qs("exchange_name").value,
      exchange_settle_currency: qs("exchange_settle_currency").value,
      enable_live_trading: qs("enable_live_trading").value === "true",
      live_dry_run: qs("live_dry_run").value === "true",
    };

    const selectedMode = qs("mode").value;

    const payload = {
      symbol: qs("symbol").value,
      interval: qs("interval").value,
      market_type: qs("market_type").value,

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

      exchange_name: qs("exchange_name").value,
      exchange_settle_currency: qs("exchange_settle_currency").value,
      enable_live_trading: qs("enable_live_trading").value === "true",
      live_dry_run: qs("live_dry_run").value === "true",
    };

    if (selectedMode === "demo") {
      payload.exchange_testnet = true;
      payload.client_order_id_prefix = "tb-demo";
    } else if (selectedMode === "live") {
      payload.exchange_testnet = false;
      payload.client_order_id_prefix = "tb-live";
    } else if (selectedMode === "paper") {
      payload.exchange_testnet = false;
      payload.client_order_id_prefix = "tb-paper";
    } else {
      payload.exchange_testnet = false;
      payload.client_order_id_prefix = "tb-backtest";
    }

    return payload;    
  },

  renderStatus(data) {
    state.latestStatus = data;

    const runtime = data?.runtime || {};
    const paper = runtime.paper_state || {};
    const latestBar = runtime.latest_bar || {};

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

    qs("chartSymbolView").textContent =
      runtime.chart_symbol || data?.config?.symbol || "-";
    qs("chartIntervalView").textContent =
      runtime.chart_interval || data?.config?.interval || "-";

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
      const selectedMode = qs("mode").value;
      const payload = this.readConfigForm();

      if (selectedMode === "demo") {
        payload.exchange_testnet = true;
      }

      if (selectedMode === "live") {
        payload.exchange_testnet = false;

        const liveConfirmed = window.confirm(
          "LIVE MODE WARNING\n\n" +
          "This can submit real orders to the live exchange if exchange orders are enabled.\n\n" +
          "Checklist:\n" +
          "- You are using the correct live API key\n" +
          "- Position size and leverage are correct\n" +
          "- Market type is correct\n" +
          "- You accept the risk of real loss\n\n" +
          "Continue?"
        );

        if (!liveConfirmed) {
          this.setActionMessage("Live start cancelled.", "warn");
          return;
        }
      }

      if (selectedMode === "demo") {
        const demoConfirmed = window.confirm(
          "DEMO MODE CONFIRMATION\n\n" +
          "This should submit orders only to the exchange demo/testnet environment.\n\n" +
          "Continue?"
        );

        if (!demoConfirmed) {
          this.setActionMessage("Demo start cancelled.", "warn");
          return;
        }
      }

      await api.setMode(selectedMode);
      await api.configure(payload);

      const data = await api.start();
      this.renderStatus(data);
      await chartModule.loadBootstrap(true);

      this.setActionMessage(`${selectedMode.toUpperCase()} bot started.`, "good");
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

      try {
        const metrics = await api.getMetrics();
        if (metrics?.runtime) {
          data.runtime = { ...(data.runtime || {}), ...metrics.runtime };
          this.renderStatus(data);
        }
      } catch (metricsErr) {
        console.warn("Metrics load skipped:", metricsErr);
      }

      await fillsModule.load();
      await chartModule.loadBootstrap(true);      
    } catch (err) {
      console.error(err);
      this.setActionMessage(`Status load failed: ${err.message}`, "bad");
    }
  },

  syncModeDerivedFields() {
    const modeEl = qs("mode");
    const envEl = qs("exchange_environment_display");

    if (!modeEl || !envEl) return;

    const mode = modeEl.value;

    if (mode === "demo") {
      envEl.value = "Auto: Demo / Testnet";
    } else if (mode === "live") {
      envEl.value = "Auto: Live Exchange";
    } else if (mode === "paper") {
      envEl.value = "Auto: Paper / Local";
    } else {
      envEl.value = "Auto: Backtest / Local";
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
    const modeEl = qs("mode");
    if (modeEl) {
      modeEl.addEventListener("change", () => this.syncModeDerivedFields());
    }

    this.syncModeDerivedFields();    

    qs("stopBtn")?.addEventListener("click", () => this.stopBot());
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