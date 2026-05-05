window.chartModule = {
  formatPrice(value, fallbackDigits = null) {
    const digits = Number.isInteger(fallbackDigits)
      ? fallbackDigits
      : Math.max(0, Number(state.chartPrecision || 6));
    const num = Number(value);
    if (!Number.isFinite(num)) return "-";
    return num.toFixed(digits);
  },

  formatQty(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return "-";
    return num.toFixed(4);
  },

  setChartPrecision(precision, minMove) {
    const nextPrecision = Number.isFinite(Number(precision))
      ? Math.max(0, Math.min(10, Number(precision)))
      : 6;
    const nextMinMove = Number.isFinite(Number(minMove)) && Number(minMove) > 0
      ? Number(minMove)
      : Number((1 / (10 ** nextPrecision)).toFixed(Math.max(nextPrecision, 1)));

    state.chartPrecision = nextPrecision;
    state.chartMinMove = nextMinMove;

    const precisionEl = qs("chartPrecisionView");
    if (precisionEl) {
      precisionEl.textContent = `${nextPrecision} dp / tick ${nextMinMove}`;
    }

    if (state.chart) {
      state.chart.applyOptions({
        localization: {
          priceFormatter: (price) => this.formatPrice(price),
        },
      });
    }

    if (state.candleSeries) {
      state.candleSeries.applyOptions({
        priceFormat: {
          type: "price",
          precision: nextPrecision,
          minMove: nextMinMove,
        },
      });
    }
  },

  chartKey(symbol, interval) {
    return `${symbol || ""}__${interval || ""}`;
  },

  currentChartKeyFromStatus(status) {
    const runtime = status?.runtime || {};
    const symbol = runtime.chart_symbol || status?.config?.symbol || "";
    const interval = runtime.chart_interval || state.selectedTimeframe || status?.config?.interval || "";
    return this.chartKey(symbol, interval);
  },

  resolveStrategyConfig() {
    const cfg = state.latestStatus?.config || {};
    const name = String(cfg.strategy_name || "ema_crossover").trim().toLowerCase();
    const params = { ...(cfg.strategy_params || {}) };

    if (name === "ema_crossover") {
      if (params.short === undefined || params.short === null) {
        params.short = Number(cfg.ema_short ?? 9);
      }
      if (params.long === undefined || params.long === null) {
        params.long = Number(cfg.ema_long ?? 21);
      }
    }

    return { name, params };
  },

  ensureChart(forceRecreate = false) {
    const container = qs("liveCandleChart");
    if (!container) {
      throw new Error("Chart container not found");
    }

    if (forceRecreate && state.chart) {
      if (state.scrollAnimationFrame) {
        cancelAnimationFrame(state.scrollAnimationFrame);
        state.scrollAnimationFrame = null;
      }
      if (state.replayTimer) {
        clearInterval(state.replayTimer);
        state.replayTimer = null;
      }

      try {
        for (const series of Object.values(state.indicatorSeries || {})) {
          state.chart.removeSeries(series);
        }
      } catch (_) {}

      try {
        if (state.pnlSeries) {
          state.chart.removeSeries(state.pnlSeries);
        }
      } catch (_) {}

      try {
        state.chart.remove();
      } catch (_) {}

      state.chart = null;
      state.candleSeries = null;
      state.markerApi = null;
      state.chartMarkers = [];
      state.chartCandles = [];
      state.indicatorSeries = {};
      state.lastCandleTime = null;
      state.tooltipInitialized = false;
      state.visibleRangeSubscribed = false;
      state.pnlSeries = null;
      state.pnlCurveData = [];
      state.selectedMarker = null;
    }

    const width = Math.max(600, Math.floor(container.clientWidth || 900));
    const height = Math.max(420, Math.floor(container.clientHeight || 420));

    if (!state.chart) {
      state.chart = LightweightCharts.createChart(container, {
        width,
        height,
        autoSize: true,
        layout: {
          textColor: "#c7d2e5",
          background: {
            type: "solid",
            color: "#0f1726",
          },
          attributionLogo: false,
        },
        grid: {
          vertLines: { color: "rgba(148, 163, 184, 0.10)" },
          horzLines: { color: "rgba(148, 163, 184, 0.10)" },
        },
        rightPriceScale: {
          borderColor: "rgba(148, 163, 184, 0.22)",
          scaleMargins: {
            top: 0.12,
            bottom: 0.12,
          },
        },
        localization: {
          priceFormatter: (price) => this.formatPrice(price),
        },
        timeScale: {
          borderColor: "rgba(148, 163, 184, 0.22)",
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 10,
          barSpacing: 8,
        },
      });
    } else {
      state.chart.applyOptions({ width, height });
    }

    if (!state.candleSeries) {
      state.candleSeries = state.chart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderUpColor: "#22c55e",
        borderDownColor: "#ef4444",
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
        priceLineVisible: true,
        lastValueVisible: true,
      });

      state.candleSeries.applyOptions({
        priceFormat: {
          type: "price",
          precision: Number(state.chartPrecision || 6),
          minMove: Number(state.chartMinMove || 0.000001),
        },
      });

      if (!state.tooltipInitialized) {
        this.initTooltip();
        state.tooltipInitialized = true;
      }
    }

    this.ensurePnlSeries();

    if (!state.visibleRangeSubscribed) {
      state.chart.timeScale().subscribeVisibleTimeRangeChange(() => {
        const timeScale = state.chart?.timeScale();
        const range = timeScale?.getVisibleRange();

        if (!range || !state.chartCandles.length) return;

        const lastCandle = state.chartCandles[state.chartCandles.length - 1];
        const isNearRightEdge = range.to >= lastCandle.time - 5;

        state.autoFollow = isNearRightEdge;
        this.updateGoLiveButton();
        this.updateFollowState();
      });
      state.visibleRangeSubscribed = true;
    }

    return { chart: state.chart, candleSeries: state.candleSeries };
  },

  sanitizeCandles(candles) {
    if (!Array.isArray(candles)) return [];

    const map = new Map();

    for (const candle of candles) {
      const clean = {
        time: Number(candle?.time),
        open: Number(candle?.open),
        high: Number(candle?.high),
        low: Number(candle?.low),
        close: Number(candle?.close),
      };

      if (
        Number.isFinite(clean.time) &&
        Number.isFinite(clean.open) &&
        Number.isFinite(clean.high) &&
        Number.isFinite(clean.low) &&
        Number.isFinite(clean.close)
      ) {
        map.set(clean.time, clean);
      }
    }

    return [...map.values()].sort((a, b) => a.time - b.time);
  },

  sanitizeMarkers(markers) {
    if (!Array.isArray(markers)) return [];

    return markers
      .map((marker) => ({
        time: Number(marker?.time),
        position: marker?.position || "aboveBar",
        color: marker?.color || "#f59e0b",
        shape: marker?.shape || "circle",
        text: marker?.text || "FILL",
        fill_type: marker?.fill_type || marker?.type || "",
        side: marker?.side || "",
        price: marker?.price,
        qty: marker?.qty,
        pnl: marker?.pnl,
        fee: marker?.fee,
        trade_id: marker?.trade_id,
        symbol: marker?.symbol,
        timestamp: marker?.timestamp,
        items: Array.isArray(marker?.items) ? marker.items : null,
      }))
      .filter((marker) => Number.isFinite(marker.time))
      .sort((a, b) => a.time - b.time);
  },

  clusterMarkers(markers) {
    if (!Array.isArray(markers) || !markers.length) return [];

    const windowSec = Math.max(1, Number(state.markerClusterWindowSec || 45));
    const groups = [];

    for (const marker of markers) {
      const last = groups[groups.length - 1];
      if (
        last &&
        Math.abs(Number(last.time) - Number(marker.time)) <= windowSec &&
        String(last.position) === String(marker.position)
      ) {
        last.items.push(marker);
        last.time = Math.min(Number(last.time), Number(marker.time));
      } else {
        groups.push({
          time: Number(marker.time),
          position: marker.position,
          items: [marker],
        });
      }
    }

    return groups.map((group) => {
      const items = [...group.items].sort((a, b) => Number(a.time) - Number(b.time));
      if (items.length === 1) {
        return {
          ...items[0],
          items,
          cluster_count: 1,
        };
      }

      const exits = items.filter((item) => ["EXIT", "LIQUIDATION"].includes(String(item.fill_type).toUpperCase()));
      const pnl = exits.reduce((acc, item) => acc + (Number(item.pnl) || 0), 0);
      const tradeIds = [...new Set(items.map((item) => item.trade_id).filter((v) => v !== undefined && v !== null && v !== ""))];
      const summary = items.some((item) => String(item.fill_type).toUpperCase() === "LIQUIDATION") ? "LIQ" : `${items.length} fills`;

      return {
        time: Number(items[0].time),
        position: group.position,
        color: group.position === "belowBar" ? "#22c55e" : "#f59e0b",
        shape: "circle",
        text: summary,
        fill_type: "CLUSTER",
        side: items[0].side || "",
        price: items[items.length - 1].price,
        qty: items.reduce((acc, item) => acc + (Number(item.qty) || 0), 0),
        pnl,
        fee: items.reduce((acc, item) => acc + (Number(item.fee) || 0), 0),
        trade_id: tradeIds.join(", "),
        symbol: items[0].symbol || "",
        timestamp: items[0].timestamp,
        items,
        cluster_count: items.length,
      };
    });
  },

  setMeta(symbol, interval, barsCount, markerCount = 0) {
    qs("chartSymbolView").textContent = symbol || "-";
    qs("chartIntervalView").textContent = interval || "-";
    qs("chartBarsView").textContent = String(barsCount ?? 0);
    const marketEl = qs("chartMarketView");
    if (marketEl) {
      marketEl.textContent = state.latestStatus?.config?.market_type || "-";
    }

    const strategyView = qs("chartStrategyView");
    if (strategyView) {
      strategyView.textContent = this.resolveStrategyConfig().name || "-";
    }

    const markerEl = qs("chartMarkersView");
    if (markerEl) {
      markerEl.textContent = String(markerCount ?? 0);
    }

    const precisionEl = qs("chartPrecisionView");
    if (precisionEl && !precisionEl.textContent.trim()) {
      precisionEl.textContent = `${state.chartPrecision} dp / tick ${state.chartMinMove}`;
    }
  },

  renderIndicatorLegend(items) {
    const el = qs("indicatorLegend");
    if (!el) return;

    if (!Array.isArray(items) || items.length === 0) {
      el.innerHTML = `<span class="indicator-chip">No overlay for current strategy</span>`;
      return;
    }

    el.innerHTML = items
      .map((item) => {
        const enabled = state.indicatorVisibility[item.key] !== false;
        return `
          <button
            type="button"
            class="indicator-chip toggleable ${enabled ? "" : "disabled"}"
            data-indicator-key="${item.key}"
          >${item.label}</button>
        `;
      })
      .join("");

    el.querySelectorAll("[data-indicator-key]").forEach((node) => {
      node.addEventListener("click", () => {
        const key = node.getAttribute("data-indicator-key");
        if (!key) return;
        state.indicatorVisibility[key] = state.indicatorVisibility[key] === false;
        this.syncIndicators(state.chartCandles || []);
        this.applyMarkers(state.rawChartMarkers || []);
      });
    });
  },

  clearIndicators() {
    if (!state.chart) {
      state.indicatorSeries = {};
      return;
    }

    for (const [, series] of Object.entries(state.indicatorSeries || {})) {
      try {
        state.chart.removeSeries(series);
      } catch (_) {}
    }

    state.indicatorSeries = {};
  },

  addLineSeries(key, options) {
    if (!state.chart || state.indicatorVisibility[key] === false) return null;
    const series = state.chart.addSeries(LightweightCharts.LineSeries, options);
    state.indicatorSeries[key] = series;
    return series;
  },

  ensurePnlSeries() {
    if (!state.chart) return null;
    if (state.pnlSeries) return state.pnlSeries;

    try {
      state.chart.priceScale("pnl").applyOptions({
        visible: true,
        borderColor: "rgba(148, 163, 184, 0.15)",
        scaleMargins: {
          top: 0.70,
          bottom: 0.02,
        },
      });
    } catch (_) {}

    state.pnlSeries = state.chart.addSeries(LightweightCharts.LineSeries, {
      color: "#a78bfa",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      priceScaleId: "pnl",
    });
    return state.pnlSeries;
  },

  calculateEmaData(candles, period) {
    const p = Math.max(1, Number(period || 1));
    const alpha = 2 / (p + 1);
    let ema = null;
    const out = [];

    for (let i = 0; i < candles.length; i += 1) {
      const close = Number(candles[i].close);
      if (!Number.isFinite(close)) continue;

      ema = ema === null ? close : close * alpha + ema * (1 - alpha);

      if (i + 1 >= p) {
        out.push({
          time: candles[i].time,
          value: Number(ema.toFixed(8)),
        });
      }
    }

    return out;
  },

  calculateBollingerData(candles, length, stdDev) {
    const p = Math.max(2, Number(length || 20));
    const s = Math.max(0.000001, Number(stdDev || 2.0));

    const mid = [];
    const upper = [];
    const lower = [];

    for (let i = 0; i < candles.length; i += 1) {
      if (i + 1 < p) continue;

      const windowSlice = candles.slice(i + 1 - p, i + 1).map((c) => Number(c.close));
      const avg = windowSlice.reduce((acc, n) => acc + n, 0) / windowSlice.length;
      const variance =
        windowSlice.reduce((acc, n) => acc + (n - avg) ** 2, 0) / windowSlice.length;
      const stdev = Math.sqrt(variance);

      mid.push({ time: candles[i].time, value: Number(avg.toFixed(8)) });
      upper.push({
        time: candles[i].time,
        value: Number((avg + stdev * s).toFixed(8)),
      });
      lower.push({
        time: candles[i].time,
        value: Number((avg - stdev * s).toFixed(8)),
      });
    }

    return { mid, upper, lower };
  },

  computePnlCurve(markers) {
    const baseEquity = Number(state.latestStatus?.config?.initial_balance ?? 0);
    const exits = (Array.isArray(markers) ? markers : [])
      .flatMap((marker) => Array.isArray(marker.items) ? marker.items : [marker])
      .filter((item) => ["EXIT", "LIQUIDATION"].includes(String(item.fill_type).toUpperCase()))
      .sort((a, b) => Number(a.time) - Number(b.time));

    let running = baseEquity;
    const points = [];

    for (const item of exits) {
      const pnl = Number(item.pnl);
      if (!Number.isFinite(pnl)) continue;
      running += pnl;
      points.push({
        time: Number(item.time),
        value: Number(running.toFixed(8)),
      });
    }

    state.pnlCurveData = points;
    return points;
  },

  syncPnlOverlay(markers) {
    const series = this.ensurePnlSeries();
    if (!series) return;

    if (state.indicatorVisibility.pnlCurve === false) {
      series.setData([]);
      return;
    }

    series.setData(this.computePnlCurve(markers));
  },

  buildMarkerDetails(marker) {
    const items = Array.isArray(marker?.items) ? marker.items : [marker];
    const feeTotal = items.reduce((acc, item) => acc + (Number(item?.fee) || 0), 0);
    const qtyTotal = items.reduce((acc, item) => acc + (Number(item?.qty) || 0), 0);
    const pnlTotal = items.reduce((acc, item) => acc + (Number(item?.pnl) || 0), 0);
    const tradeIds = [...new Set(items.map((item) => item?.trade_id).filter((v) => v !== undefined && v !== null && String(v) !== ""))];

    return {
      tradeIds: tradeIds.length ? tradeIds.join(", ") : "-",
      fillCount: items.length,
      side: marker?.side || items[0]?.side || "-",
      type: marker?.fill_type || items[0]?.fill_type || "-",
      price: marker?.price ?? items[items.length - 1]?.price,
      qty: qtyTotal,
      fee: feeTotal,
      pnl: pnlTotal,
      time: marker?.time || items[0]?.time,
      symbol: marker?.symbol || items[0]?.symbol || state.latestStatus?.config?.symbol || "-",
      items,
    };
  },

  renderTradeDetails(marker) {
    const panel = qs("tradeDetailsPanel");
    if (!panel) return;

    if (!marker) {
      panel.innerHTML = `<div class="muted">Click a marker or step replay to inspect a trade.</div>`;
      return;
    }

    const details = this.buildMarkerDetails(marker);
    const pnlClass = Number(details.pnl) > 0 ? "good" : Number(details.pnl) < 0 ? "bad" : "";

    panel.innerHTML = `
      <div class="trade-detail-grid">
        <div class="trade-detail-card"><div class="label">Trade ID</div><div class="value">${details.tradeIds}</div></div>
        <div class="trade-detail-card"><div class="label">Action</div><div class="value">${details.type}</div></div>
        <div class="trade-detail-card"><div class="label">Side</div><div class="value">${String(details.side).toUpperCase()}</div></div>
        <div class="trade-detail-card"><div class="label">Time</div><div class="value">${fmtTs(details.time)}</div></div>
        <div class="trade-detail-card"><div class="label">Price</div><div class="value">${this.formatPrice(details.price)}</div></div>
        <div class="trade-detail-card"><div class="label">Qty</div><div class="value">${this.formatQty(details.qty)}</div></div>
        <div class="trade-detail-card"><div class="label">Fee</div><div class="value">${this.formatPrice(details.fee, 6)}</div></div>
        <div class="trade-detail-card"><div class="label">PnL</div><div class="value ${pnlClass}">${this.formatPrice(details.pnl, 4)}</div></div>
        <div class="trade-detail-card"><div class="label">Fills</div><div class="value">${details.fillCount}</div></div>
      </div>
    `;
  },

  syncIndicators(candles) {
    const { name, params } = this.resolveStrategyConfig();
    const strategyView = qs("chartStrategyView");
    if (strategyView) {
      strategyView.textContent = name || "-";
    }

    this.clearIndicators();

    if (!state.chart || !Array.isArray(candles) || candles.length === 0) {
      this.renderIndicatorLegend([]);
      return;
    }

    const legend = [{ key: "pnlCurve", label: "PnL Curve" }];

    if (name === "ema_crossover") {
      const shortPeriod = Math.max(1, Number(params.short ?? 9));
      const longPeriod = Math.max(shortPeriod + 1, Number(params.long ?? 21));

      const shortSeries = this.addLineSeries("emaShort", {
        color: "#60a5fa",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });

      const longSeries = this.addLineSeries("emaLong", {
        color: "#f59e0b",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });

      shortSeries?.setData(this.calculateEmaData(candles, shortPeriod));
      longSeries?.setData(this.calculateEmaData(candles, longPeriod));

      legend.push({ key: "emaShort", label: `EMA ${shortPeriod}` });
      legend.push({ key: "emaLong", label: `EMA ${longPeriod}` });
    } else if (name === "bollinger_mean_reversion") {
      const length = Math.max(2, Number(params.length ?? 20));
      const stdDev = Math.max(0.000001, Number(params.std_dev ?? 2.0));
      const bands = this.calculateBollingerData(candles, length, stdDev);

      const upperSeries = this.addLineSeries("bbUpper", {
        color: "#ef4444",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });

      const midSeries = this.addLineSeries("bbMid", {
        color: "#f59e0b",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });

      const lowerSeries = this.addLineSeries("bbLower", {
        color: "#22c55e",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });

      upperSeries?.setData(bands.upper);
      midSeries?.setData(bands.mid);
      lowerSeries?.setData(bands.lower);

      legend.push({ key: "bbUpper", label: `BB Upper (${length}, ${stdDev})` });
      legend.push({ key: "bbMid", label: `BB Mid (${length})` });
      legend.push({ key: "bbLower", label: `BB Lower (${length}, ${stdDev})` });
    }

    this.renderIndicatorLegend(legend);
  },

  applyMarkers(markers, options = {}) {
    const refs = this.ensureChart(false);
    const cleanMarkers = this.sanitizeMarkers(markers);
    if (!options.preserveRaw) {
      state.rawChartMarkers = cleanMarkers;
    }
    const clusteredMarkers = this.clusterMarkers(cleanMarkers);
    state.chartMarkers = clusteredMarkers;
    this.syncPnlOverlay(clusteredMarkers);

    if (typeof refs.candleSeries.setMarkers === "function") {
      refs.candleSeries.setMarkers(clusteredMarkers);
      return;
    }

    if (typeof LightweightCharts.createSeriesMarkers === "function") {
      if (!state.markerApi) {
        state.markerApi = LightweightCharts.createSeriesMarkers(
          refs.candleSeries,
          clusteredMarkers
        );
      } else if (typeof state.markerApi.setMarkers === "function") {
        state.markerApi.setMarkers(clusteredMarkers);
      } else {
        state.markerApi = LightweightCharts.createSeriesMarkers(
          refs.candleSeries,
          clusteredMarkers
        );
      }
    }
  },

  reset() {
    if (state.candleSeries) {
      state.candleSeries.setData([]);
    }

    state.chartCandles = [];
    state.chartMarkers = [];
    state.rawChartMarkers = [];
    state.replayIndex = -1;
    state.selectedMarker = null;
    this.clearIndicators();
    this.applyMarkers([]);

    qs("chartBarsView").textContent = "0";
    const markerEl = qs("chartMarkersView");
    if (markerEl) markerEl.textContent = "0";

    state.lastCandleTime = null;
    state.lastChartKey = null;
    this.renderIndicatorLegend([]);
    this.renderTradeDetails(null);
    this.updateFollowState();
  },

  async refreshSnapshot() {
    if (!state.latestStatus) return null;

    const refs = this.ensureChart(false);
    const data = await api.getChart(300);
    const candles = this.sanitizeCandles(data?.candles);
    const markers = this.sanitizeMarkers(data?.markers);

    refs.candleSeries.setData(candles);
    state.chartCandles = candles;

    this.applyMarkers(markers);
    this.syncIndicators(candles);
    this.autoScalePriceRange(candles, { force: true });

    state.lastCandleTime = candles.length ? candles[candles.length - 1].time : null;
    state.lastChartKey = this.chartKey(data?.symbol, data?.interval);

    this.setMeta(data?.symbol, data?.interval, candles.length, state.chartMarkers.length);
    this.updateGoLiveButton();
    this.updateFollowState();

    return { data, candles, markers };
  },

  async loadBootstrap(force = false) {
    if (!state.latestStatus) return;
    if (state.latestStatus?.mode === "backtest") {
      this.reset();
      return;
    }

    const nextKey = this.currentChartKeyFromStatus(state.latestStatus);
    if (!force && state.lastChartKey === nextKey && state.chartCandles.length) {
      return;
    }

    this.ensureChart(force);
    const result = await this.refreshSnapshot();

    if (state.chart) {
      state.chart.timeScale().fitContent();
      state.autoFollow = true;
      this.scrollToLatest();
      this.updateFollowState();
    }

    const candleCount = result?.candles?.length ?? 0;
    const markerCount = result?.markers?.length ?? 0;

    if (candleCount === 0) {
      uiController.setActionMessage("Chart loaded but no candles were returned.", "warn");
    } else {
      uiController.setActionMessage(
        `Chart loaded: ${candleCount} candles, ${markerCount} markers.`,
        "good"
      );
    }
  },

  applyLiveUpdate(msg) {
    if (!msg?.candle || !state.latestStatus) return;

    const activeKey = this.currentChartKeyFromStatus(state.latestStatus);
    const incomingKey = this.chartKey(msg.symbol, msg.interval);

    if (activeKey !== incomingKey) return;

    const refs = this.ensureChart(false);
    const candle = this.sanitizeCandles([msg.candle])[0];
    if (!candle) return;

    refs.candleSeries.update(candle);

    const candles = Array.isArray(state.chartCandles) ? [...state.chartCandles] : [];
    const existingIndex = candles.findIndex((item) => item.time === candle.time);

    if (existingIndex >= 0) {
      candles[existingIndex] = candle;
    } else {
      candles.push(candle);
      candles.sort((a, b) => a.time - b.time);
    }

    state.chartCandles = candles;
    state.lastCandleTime = candle.time;

    this.syncIndicators(candles);
    this.setMeta(msg.symbol, msg.interval, candles.length, state.chartMarkers.length);
    this.scrollToLatest();
    this.autoScalePriceRange(candles);
    this.updateGoLiveButton();
    this.updateFollowState();
  },

  resize() {
    if (!state.chart) return;

    const container = qs("liveCandleChart");
    if (!container) return;

    state.chart.applyOptions({
      width: Math.max(300, Math.floor(container.clientWidth || 900)),
      height: Math.max(420, Math.floor(container.clientHeight || 420)),
    });
  },

  addLiveMarkerFromFill(fill) {
    if (!fill) return;

    const time = Math.floor(new Date(fill.timestamp).getTime() / 1000);

    const type = String(fill.type || "").toUpperCase();
    const side = String(fill.side || "").toLowerCase();

    let marker;

    if (type === "ENTRY" && side === "long") {
      marker = { position: "belowBar", color: "#22c55e", shape: "arrowUp", text: "LONG" };
    } else if (type === "ENTRY" && side === "short") {
      marker = { position: "aboveBar", color: "#ef4444", shape: "arrowDown", text: "SHORT" };
    } else if (type === "EXIT" && side === "long") {
      marker = { position: "aboveBar", color: "#f59e0b", shape: "arrowDown", text: "EXIT" };
    } else if (type === "EXIT" && side === "short") {
      marker = { position: "belowBar", color: "#38bdf8", shape: "arrowUp", text: "COVER" };
    } else if (type === "LIQUIDATION") {
      marker = { position: "aboveBar", color: "#f97316", shape: "circle", text: "LIQ" };
    } else {
      marker = { position: "aboveBar", color: "#94a3b8", shape: "circle", text: type };
    }

    const fullMarker = {
      ...marker,
      time,
      text: `${marker.text}`,
      fill_type: fill.type,
      side: fill.side,
      price: fill.price,
      pnl: fill.pnl,
      qty: fill.qty,
      fee: fill.fee,
      trade_id: fill.trade_id,
      symbol: fill.symbol || state.latestStatus?.config?.symbol || "",
      timestamp: fill.timestamp,
    };

    state.rawChartMarkers.push(fullMarker);
    this.applyMarkers(state.rawChartMarkers, { preserveRaw: true });
    this.renderTradeDetails(fullMarker);
    this.autoScalePriceRange(state.chartCandles, { force: true, fromFill: true });

    const markerEl = qs("chartMarkersView");
    if (markerEl) markerEl.textContent = state.chartMarkers.length;
  },

  scrollToLatest() {
    if (!state.chart || !state.autoFollow) return;

    const timeScale = state.chart.timeScale();

    if (state.scrollAnimationFrame) {
      cancelAnimationFrame(state.scrollAnimationFrame);
      state.scrollAnimationFrame = null;
    }

    const step = () => {
      if (!state.autoFollow) return;

      const currentOffset = timeScale.getRightOffset();

      if (Math.abs(currentOffset) < 0.5) {
        timeScale.scrollToRealTime();
        state.scrollAnimationFrame = null;
        return;
      }

      const nextOffset = currentOffset * 0.85;

      timeScale.scrollToPosition(nextOffset, false);

      state.scrollAnimationFrame = requestAnimationFrame(step);
    };

    state.scrollAnimationFrame = requestAnimationFrame(step);
  },

  autoScalePriceRange(candles, options = {}) {
    if (!state.chart || !state.autoScale) return;
    const force = Boolean(options.force);
    if (!force && !state.autoFollow) return;
    if (!Array.isArray(candles) || candles.length < 20) return;

    const now = Date.now();
    if (!force && now - Number(state.lastAutoFitAt || 0) < 750) return;

    const lookback = Math.min(50, candles.length);
    const recent = candles.slice(-lookback);

    let min = Infinity;
    let max = -Infinity;

    for (const c of recent) {
      if (c.low < min) min = c.low;
      if (c.high > max) max = c.high;
    }

    if (!isFinite(min) || !isFinite(max) || min === max) return;

    const range = max - min;
    const padding = range * 0.15;

    const finalMin = min - padding;
    const finalMax = max + padding;

    state.chart.priceScale("right").setVisibleRange({
      from: finalMin,
      to: finalMax,
    });
    state.lastAutoFitAt = now;
  },

  isAtLiveEdge() {
    if (!state.chart || !state.chartCandles.length) return true;

    const timeScale = state.chart.timeScale();
    const range = timeScale.getVisibleRange();
    const last = state.chartCandles[state.chartCandles.length - 1];

    if (!range || !last) return true;

    return range.to >= last.time - 2;
  },

  updateGoLiveButton() {
    const btn = qs("goLiveBtn");
    if (!btn) return;

    if (this.isAtLiveEdge()) {
      btn.classList.add("hidden");
    } else {
      btn.classList.remove("hidden");
    }
  },

  updateFollowState() {
    const btn = qs("autoFollowToggle");
    const badge = qs("autoFollowStatus");
    if (btn) {
      btn.textContent = `Auto Follow: ${state.autoFollow ? "LIVE" : "PAUSED"}`;
    }
    if (badge) {
      badge.textContent = state.autoFollow ? "LIVE" : "PAUSED";
      badge.className = `follow-badge ${state.autoFollow ? "follow-live" : "follow-paused"}`;
    }
  },

  findMarkerNearTime(time) {
    const target = Number(time);
    if (!Number.isFinite(target) || !Array.isArray(state.chartMarkers)) return null;

    let closest = null;
    let minDiff = Infinity;

    for (const marker of state.chartMarkers) {
      const diff = Math.abs(Number(marker.time) - target);
      if (diff < minDiff && diff <= Math.max(60, Number(state.markerClusterWindowSec || 45))) {
        minDiff = diff;
        closest = marker;
      }
    }
    return closest;
  },

  focusMarker(marker) {
    if (!marker || !state.chart) return;
    state.selectedMarker = marker;
    this.renderTradeDetails(marker);

    const timeScale = state.chart.timeScale();
    const time = Number(marker.time);
    timeScale.setVisibleRange({
      from: Math.max(0, time - 20 * 60),
      to: time + 10 * 60,
    });
  },

  replayMarkers() {
    return Array.isArray(state.rawChartMarkers)
      ? this.clusterMarkers(this.sanitizeMarkers(state.rawChartMarkers))
        .filter((marker) => marker && Number.isFinite(Number(marker.time)))
      : [];
  },

  renderReplaySlice() {
    if (!state.chart || state.replayIndex < 0) return;
    const markers = this.replayMarkers();
    const current = markers[state.replayIndex];
    if (!current) return;

    const replayTime = Number(current.time);
    const candles = state.chartCandles.filter((candle) => Number(candle.time) <= replayTime);
    const markerSlice = (state.rawChartMarkers || []).filter((marker) => Number(marker.time) <= replayTime);

    state.candleSeries?.setData(candles);
    this.applyMarkers(markerSlice, { preserveRaw: true });
    this.focusMarker(current);
  },

  replayStep(delta) {
    const markers = this.replayMarkers();
    if (!markers.length) return;

    state.autoFollow = false;
    this.updateFollowState();
    state.replayIndex = Math.max(0, Math.min(markers.length - 1, Number(state.replayIndex) + Number(delta)));
    this.renderReplaySlice();
    qs("replayExitBtn")?.classList.remove("hidden");
  },

  toggleReplayPlay() {
    const btn = qs("replayPlayBtn");
    if (state.replayPlaying) {
      state.replayPlaying = false;
      if (state.replayTimer) {
        clearInterval(state.replayTimer);
        state.replayTimer = null;
      }
      if (btn) btn.textContent = "Play";
      return;
    }

    const markers = this.replayMarkers();
    if (!markers.length) return;
    if (state.replayIndex < 0) {
      state.replayIndex = 0;
      this.renderReplaySlice();
    }

    state.replayPlaying = true;
    if (btn) btn.textContent = "Pause";
    state.replayTimer = setInterval(() => {
      const list = this.replayMarkers();
      if (state.replayIndex >= list.length - 1) {
        this.toggleReplayPlay();
        return;
      }
      this.replayStep(1);
    }, 1200);
  },

  exitReplay() {
    state.replayIndex = -1;
    if (state.replayPlaying) {
      this.toggleReplayPlay();
    }
    qs("replayExitBtn")?.classList.add("hidden");
    state.candleSeries?.setData(state.chartCandles);
    this.applyMarkers(state.rawChartMarkers, { preserveRaw: true });
    this.renderTradeDetails(state.selectedMarker);
    if (state.autoFollow) {
      this.scrollToLatest();
      this.autoScalePriceRange(state.chartCandles, { force: true });
    }
  },

  initTooltip() {
    const tooltip = qs("chartTooltip");
    if (!tooltip || !state.chart) return;

    state.chart.subscribeClick((param) => {
      if (!param || !param.time) return;
      const marker = this.findMarkerNearTime(param.time);
      if (marker) {
        this.focusMarker(marker);
      }
    });

    state.chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.point) {
        tooltip.classList.add("hidden");
        return;
      }

      const closest = this.findMarkerNearTime(param.time);
      if (!closest) {
        tooltip.classList.add("hidden");
        return;
      }

      const details = this.buildMarkerDetails(closest);
      const pnlClass =
        details.pnl > 0 ? "pnl-pos" :
        details.pnl < 0 ? "pnl-neg" : "";

      tooltip.innerHTML = `
        <div class="row"><span class="label">Trade</span><span>${details.tradeIds}</span></div>
        <div class="row"><span class="label">Action</span><span>${details.type} ${details.side}</span></div>
        <div class="row"><span class="label">Price</span><span>${this.formatPrice(details.price)}</span></div>
        <div class="row"><span class="label">Qty</span><span>${this.formatQty(details.qty)}</span></div>
        <div class="row"><span class="label">Fee</span><span>${this.formatPrice(details.fee, 6)}</span></div>
        <div class="row"><span class="label">PnL</span><span class="value ${pnlClass}">${this.formatPrice(details.pnl, 4)}</span></div>
        <div class="row"><span class="label">Fills</span><span>${details.fillCount}</span></div>
      `;

      tooltip.style.left = `${Math.max(12, Math.min(param.point.x + 16, window.innerWidth - 300))}px`;
      tooltip.style.top = `${Math.max(12, param.point.y + 16)}px`;

      tooltip.classList.remove("hidden");
    });
  },
};
