window.uiController = {
  setActionMessage(text, klass = "") {
    const el = qs("actionMessage");
    el.textContent = text;
    el.className = `action-message ${klass}`.trim();
  },

  hydrateApiKeyField() {
    const input = qs("apiKey");
    if (!input) return;
    input.value = api.getStoredApiKey();
  },

  saveApiKey() {
    const input = qs("apiKey");
    if (!input) return;

    const clean = api.setStoredApiKey(input.value);
    input.value = clean;

    if (clean) {
      this.setActionMessage("API key saved locally in this browser.", "good");
    } else {
      this.setActionMessage("API key cleared from this browser.", "warn");
    }
  },

  clearApiKey() {
    const input = qs("apiKey");
    api.setStoredApiKey("");
    if (input) input.value = "";
    this.setActionMessage("API key cleared from this browser.", "warn");
  },

  async syncExchangePrecision() {
    if (!state.latestStatus?.config) return;

    const cfg = state.latestStatus.config;
    try {
      const payload = {
        exchange_name: cfg.exchange_name || "gateio",
        market_type: cfg.market_type || "spot",
        symbol: cfg.symbol || "BTCUSDT",
        enable_live_trading: Boolean(cfg.enable_live_trading),
        dry_run_live: cfg.live_dry_run !== false,
        testnet: Boolean(cfg.exchange_testnet),
        base_url: cfg.exchange_base_url || null,
        api_key_env: cfg.exchange_api_key_env || "GATEIO_API_KEY",
        api_secret_env: cfg.exchange_api_secret_env || "GATEIO_API_SECRET",
        api_passphrase_env: cfg.exchange_api_passphrase_env || "GATEIO_API_PASSPHRASE",
      };
      const result = await api.validateExchangeConfig(payload);
      const precision = result?.symbol_validation?.precision?.price;
      if (precision !== undefined && precision !== null) {
        const digits = Number(precision);
        const minMove = Number((1 / (10 ** digits)).toFixed(Math.max(0, digits)));
        chartModule.setChartPrecision(digits, minMove);
        state.exchangePrecisionLoaded = true;
      }
    } catch (err) {
      console.warn("Precision sync skipped:", err);
    }
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
      "position_sizing_mode",
      "position_size_value",
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
    const selectedMode = qs("mode").value;
    const payload = {
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
      position_sizing_mode: qs("position_sizing_mode")?.value || "all_in",
      position_size_value: qs("position_size_value")?.value
        ? Number(qs("position_size_value").value)
        : null,

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
    const live = runtime.live || {};

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

    const visualMode = data?.mode || "-";
    const visualState = data?.state || "unknown";
    const cfg = data?.config || {};
    const rawMarketType = cfg.market_type || "-";
    const marketLabel = rawMarketType === "swap" ? "futures" : rawMarketType;

    const visualModeBadge = qs("visualModeBadge");
    const visualStateBadge = qs("visualStateBadge");

    if (visualModeBadge) {
      visualModeBadge.textContent = String(visualMode).toUpperCase();
      visualModeBadge.className = `mode-badge mode-${String(visualMode).toLowerCase()}`;
    }

    if (visualStateBadge) {
      visualStateBadge.textContent = String(visualState).toUpperCase();
      visualStateBadge.className = `state-badge state-${String(visualState).toLowerCase()}`;
    }

    if (qs("visualRunLabel")) {
      qs("visualRunLabel").textContent = data?.run_label || data?.run_id || "-";
    }

    if (qs("visualSymbol")) {
      qs("visualSymbol").textContent = cfg.symbol || runtime.chart_symbol || "-";
    }

    if (qs("visualMarket")) {
      qs("visualMarket").textContent = String(marketLabel).toUpperCase();
    }

    if (qs("visualPosition")) {
      const qty = Number(paper.position_qty || 0);
      if (!Number.isFinite(qty) || qty === 0) {
        qs("visualPosition").textContent = "FLAT";
      } else if (qty > 0) {
        qs("visualPosition").textContent = `LONG ${fmtNum(qty)}`;
      } else {
        qs("visualPosition").textContent = `SHORT ${fmtNum(Math.abs(qty))}`;
      }
    }

    if (qs("visualLiveSafety")) {
      if (visualMode === "live") {
        if (live.enable_live_trading && !live.live_dry_run) {
          qs("visualLiveSafety").textContent =
            "LIVE EXECUTION ENABLED - real orders may be submitted when backend safety guards allow it.";
          qs("visualLiveSafety").className = "live-safety-note danger";
        } else {
          qs("visualLiveSafety").textContent =
            "Live mode is armed for monitoring / dry-run only. Real order submission is not active.";
          qs("visualLiveSafety").className = "live-safety-note warn-note";
        }
      } else if (visualMode === "demo") {
        qs("visualLiveSafety").textContent =
          "Demo mode: exchange rehearsal environment / sandbox-style execution path.";
        qs("visualLiveSafety").className = "live-safety-note demo-note";
      } else if (visualMode === "paper") {
        qs("visualLiveSafety").textContent =
          "Paper mode: simulated execution and local accounting only.";
        qs("visualLiveSafety").className = "live-safety-note paper-note";
      } else {
        qs("visualLiveSafety").textContent =
          "Backtest / idle mode: no live order submission.";
        qs("visualLiveSafety").className = "live-safety-note";
      }
    }

    const flowBadge = qs("visualFlowBadge");
    const summary = qs("visualHeaderSummary");
    if (flowBadge) {
      const flow = visualMode === "live"
        ? "LIVE"
        : visualMode === "demo"
          ? "DEMO"
          : visualMode === "paper"
            ? "PAPER"
            : "IDLE";
      const flowClass = visualMode === "live"
        ? "flow-live"
        : visualMode === "demo"
          ? "flow-demo"
          : visualMode === "paper"
            ? "flow-paper"
            : "flow-idle";
      flowBadge.textContent = flow;
      flowBadge.className = `flow-badge ${flowClass}`;
    }
    if (summary) {
      const equity = paper.equity !== undefined && paper.equity !== null ? fmtNum(paper.equity) : "-";
      const lastPrice = latestBar.close !== undefined && latestBar.close !== null ? fmtNum(latestBar.close) : "-";
      const autoState = state.autoFollow ? "LIVE" : "PAUSED";
      summary.textContent = `${String(visualMode).toUpperCase()} | ${String(visualState).toUpperCase()} | Equity ${equity} | Last ${lastPrice} | Follow ${autoState}`;
    }

    qs("chartSymbolView").textContent =
      runtime.chart_symbol || data?.config?.symbol || "-";
    qs("chartIntervalView").textContent =
      runtime.chart_interval || data?.config?.interval || "-";

    const chartStrategyEl = qs("chartStrategyView");
    if (chartStrategyEl) {
      chartStrategyEl.textContent = data?.config?.strategy_name || "ema_crossover";
    }

    if (paper.equity !== null && paper.equity !== undefined) {
      equityModule.push(paper.equity);
    }

    if (data?.mode === "backtest") {
      chartModule.reset();
      qs("fillsTableBody").innerHTML = "";
      chartModule.updateFollowState();
      return;
    }

    const nextChartKey = chartModule.currentChartKeyFromStatus(data);
    if (nextChartKey && nextChartKey !== state.lastChartKey) {
      chartModule.loadBootstrap(true).catch((err) => {
        console.error("Chart bootstrap failed:", err);
        this.setActionMessage(`Chart bootstrap failed: ${err.message}`, "bad");
      });
    }

    chartModule.updateFollowState();
  },

  async configureBot() {
    try {
      const selectedMode = qs("mode").value;
      const payload = this.readConfigForm();
      await api.setMode(selectedMode);
      await api.configure(payload);
      const data = await api.getStatus();
      this.renderStatus(data);
      if (selectedMode === "backtest") {
        chartModule.reset();
        qs("fillsTableBody").innerHTML = "";
      } else {
        await this.syncExchangePrecision();
        await chartModule.loadBootstrap(true);
      }
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

      if (selectedMode === "backtest") {
        await api.setMode("backtest");
        const response = await api.runBacktest(payload);
        if (response?.status) {
          this.renderStatus(response.status);
        }
        chartModule.reset();
        qs("fillsTableBody").innerHTML = "";
        this.setActionMessage(
          `BACKTEST completed. Final equity: ${fmtNum(response?.result?.final_equity)}.`,
          "good"
        );
        return;
      }

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
      await this.syncExchangePrecision();
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
      await this.syncExchangePrecision();
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

      if (data?.config?.interval) {
        state.selectedTimeframe = data.config.interval;
        document.querySelectorAll(".tf-btn").forEach((btn) => {
          btn.classList.toggle("active", btn.dataset.tf === state.selectedTimeframe);
        });
      }

      if (data?.mode === "backtest") {
        chartModule.reset();
        qs("fillsTableBody").innerHTML = "";
        return;
      }

      await this.syncExchangePrecision();

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

          const fills = msg.payload?.fills || [];
          for (const fill of fills) {
            chartModule.addLiveMarkerFromFill(fill);
          }

          return;
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
    this.hydrateApiKeyField();

    qs("configureBtn")?.addEventListener("click", () => this.configureBot());
    qs("startBtn")?.addEventListener("click", () => this.startBot());
    qs("saveApiKeyBtn")?.addEventListener("click", () => this.saveApiKey());
    qs("clearApiKeyBtn")?.addEventListener("click", () => this.clearApiKey());
    qs("apiKey")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        this.saveApiKey();
      }
    });

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

    qs("autoFollowToggle")?.addEventListener("click", () => {
      state.autoFollow = !state.autoFollow;
      chartModule.updateFollowState();
      if (state.autoFollow) {
        chartModule.scrollToLatest();
        chartModule.autoScalePriceRange(state.chartCandles, { force: true });
      }
    });

    qs("goLiveBtn")?.addEventListener("click", () => {
      state.autoFollow = true;

      chartModule.scrollToLatest();

      if (state.chartCandles.length) {
        chartModule.autoScalePriceRange(state.chartCandles, { force: true });
      }

      chartModule.updateGoLiveButton();
      chartModule.updateFollowState();
    });

    qs("replayPrevBtn")?.addEventListener("click", () => chartModule.replayStep(-1));
    qs("replayPlayBtn")?.addEventListener("click", () => chartModule.toggleReplayPlay());
    qs("replayNextBtn")?.addEventListener("click", () => chartModule.replayStep(1));
    qs("replayExitBtn")?.addEventListener("click", () => chartModule.exitReplay());

    document.querySelectorAll(".tf-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const tf = btn.dataset.tf;

        if (tf === state.selectedTimeframe) return;

        state.selectedTimeframe = tf;
        chartModule.exitReplay();

        document.querySelectorAll(".tf-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");

        chartModule.reset();
        await chartModule.loadBootstrap(true);
      });
    });
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
