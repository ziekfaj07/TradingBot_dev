window.chartModule = {
  chartKey(symbol, interval) {
    return `${symbol || ""}__${interval || ""}`;
  },

  currentChartKeyFromStatus(status) {
    const runtime = status?.runtime || {};
    const symbol = runtime.chart_symbol || status?.config?.symbol || "";
    const interval = runtime.chart_interval || status?.config?.interval || "";
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
      try {
        for (const series of Object.values(state.indicatorSeries || {})) {
          state.chart.removeSeries(series);
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
          priceFormatter: (price) => {
            if (price >= 1000) return price.toFixed(2);
            if (price >= 1) return price.toFixed(4);
            if (price >= 0.01) return price.toFixed(6);
            return price.toFixed(8);
          },
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
          precision: 8,
          minMove: 0.00000001,
        },
      });

      if (!state.tooltipInitialized) {
        this.initTooltip();
        state.tooltipInitialized = true;
      };      
    }

    return { chart: state.chart, candleSeries: state.candleSeries };

    refs.chart.timeScale().subscribeVisibleTimeRangeChange(() => {
      const timeScale = refs.chart.timeScale();
      const range = timeScale.getVisibleRange();

      if (!range || !state.chartCandles.length) return;

      const lastCandle = state.chartCandles[state.chartCandles.length - 1];
      const isNearRightEdge = range.to >= lastCandle.time - 5;

      state.autoFollow = isNearRightEdge;

      this.updateGoLiveButton();
    });
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
      }))
      .filter((marker) => Number.isFinite(marker.time))
      .sort((a, b) => a.time - b.time);
  },

  setMeta(symbol,interval, barsCount, markerCount = 0) {
    qs("chartSymbolView").textContent = symbol || "-";
    qs("chartIntervalView").textContent = interval || "-";
    qs("chartBarsView").textContent = String(barsCount ?? 0);

    const strategyView = qs("chartStrategyView");
    if (strategyView) {
      strategyView.textContent = this.resolveStrategyConfig().name || "-";
    }

    const markerEl = qs("chartMarkersView");
    if (markerEl) {
      markerEl.textContent = String(markerCount ?? 0);
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
      .map((item) => `<span class="indicator-chip">${item}</span>`)
      .join("");
  },

  clearIndicators() {
    if (!state.chart) {
      state.indicatorSeries = {};
      return;
    }

    for (const [key, series] of Object.entries(state.indicatorSeries || {})) {
      try {
        state.chart.removeSeries(series);
      } catch (_) {}
    }

    state.indicatorSeries = {};
  },

  addLineSeries(key, options) {
    if (!state.chart) return null;
    const series = state.chart.addSeries(LightweightCharts.LineSeries, options);
    state.indicatorSeries[key] = series;
    return series;
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

    const legend = [];

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

      legend.push(`EMA ${shortPeriod}`);
      legend.push(`EMA ${longPeriod}`);
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

      legend.push(`BB Upper (${length}, ${stdDev})`);
      legend.push(`BB Mid (${length})`);
      legend.push(`BB Lower (${length}, ${stdDev})`);
    }

    this.renderIndicatorLegend(legend);
  },

  applyMarkers(markers) {
    const refs = this.ensureChart(false);
    const cleanMarkers = this.sanitizeMarkers(markers);
    state.chartMarkers = cleanMarkers;

    if (typeof refs.candleSeries.setMarkers === "function") {
      refs.candleSeries.setMarkers(cleanMarkers);
      return;
    }

    if (typeof LightweightCharts.createSeriesMarkers === "function") {
      if (!state.markerApi) {
        state.markerApi = LightweightCharts.createSeriesMarkers(
          refs.candleSeries,
          cleanMarkers
        );
      } else if (typeof state.markerApi.setMarkers === "function") {
        state.markerApi.setMarkers(cleanMarkers);
      } else {
        state.markerApi = LightweightCharts.createSeriesMarkers(
          refs.candleSeries,
          cleanMarkers
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
    this.clearIndicators();
    this.applyMarkers([]);

    qs("chartBarsView").textContent = "0";
    const markerEl = qs("chartMarkersView");
    if (markerEl) markerEl.textContent = "0";

    state.lastCandleTime = null;
    state.lastChartKey = null;
    this.renderIndicatorLegend([]);
  },

  async refreshSnapshot() {
    if (!state.latestStatus) return null;

    const refs = this.ensureChart(false);
    const data = await api.getChart(300);
    const candles = this.sanitizeCandles(data?.candles);
    const markers = this.sanitizeMarkers(data?.markers);

    refs.candleSeries.setData(candles);
    state.chartCandles = candles;
    this.scrollToLatest();
    state.chartCandles = candles;

    this.applyMarkers(markers);
    this.syncIndicators(candles);
    this.autoScalePriceRange(candles);

    state.lastCandleTime = candles.length ? candles[candles.length - 1].time : null;
    state.lastChartKey = this.chartKey(data?.symbol, data?.interval);

    this.setMeta(data?.symbol, data?.interval, candles.length, markers.length);
    this.updateGoLiveButton();

    return { data, candles, markers };
  },

  async loadBootstrap(force = false) {
    if (!state.latestStatus) return;

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
      marker = { position: "belowBar", color: "#f59e0b", shape: "arrowUp", text: "COVER" };
    } else {
      marker = { position: "aboveBar", color: "#94a3b8", shape: "circle", text: type };
    }

    const fullMarker = {
      ...marker,
      time,
      text: `${marker.text}`,
      meta: {
        type: fill.type,
        side: fill.side,
        price: fill.price,
        pnl: fill.pnl,
        qty: fill.qty,
        fee: fill.fee,
      }
    };

    state.chartMarkers.push(fullMarker);
    this.applyMarkers(state.chartMarkers);

    const markerEl = qs("chartMarkersView");
    if (markerEl) markerEl.textContent = state.chartMarkers.length;
  },

  scrollToLatest() {
    if (!state.chart || !state.autoFollow) return;

    const timeScale = state.chart.timeScale();

    // cancel previous animation if running
    if (state.scrollAnimationFrame) {
      cancelAnimationFrame(state.scrollAnimationFrame);
      state.scrollAnimationFrame = null;
    }

    const step = () => {
      if (!state.autoFollow) return;

      const currentOffset = timeScale.getRightOffset();

      // when close enough, snap and stop
      if (Math.abs(currentOffset) < 0.5) {
        timeScale.scrollToRealTime();
        state.scrollAnimationFrame = null;
        return;
      }

      // easing (smooth decay)
      const nextOffset = currentOffset * 0.85;

      timeScale.scrollToPosition(nextOffset, false);

      state.scrollAnimationFrame = requestAnimationFrame(step);
    };

    state.scrollAnimationFrame = requestAnimationFrame(step);
  },

  autoScalePriceRange(candles) {
    if (!state.chart || !state.autoFollow || !state.autoScale) return;
    if (!Array.isArray(candles) || candles.length < 20) return;

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

    // add breathing room
    const padding = range * 0.15;

    const finalMin = min - padding;
    const finalMax = max + padding;

    state.chart.priceScale("right").setVisibleRange({
      from: finalMin,
      to: finalMax,
    });
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

  initTooltip() {
    const tooltip = qs("chartTooltip");
    if (!tooltip || !state.chart) return;

    state.chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.point) {
        tooltip.classList.add("hidden");
        return;
      }

      const time = param.time;

      // find closest marker
      let closest = null;
      let minDiff = Infinity;

      for (const m of state.chartMarkers) {
        const diff = Math.abs(m.time - time);
        if (diff < minDiff && diff < 60) { // within 1 candle
          minDiff = diff;
          closest = m;
        }
      }

      if (!closest || !closest.meta) {
        tooltip.classList.add("hidden");
        return;
      }

      const m = closest.meta;

      const pnlClass =
        m.pnl > 0 ? "pnl-pos" :
        m.pnl < 0 ? "pnl-neg" : "";

      tooltip.innerHTML = `
        <div class="row"><span class="label">Action</span><span>${m.type} ${m.side}</span></div>
        <div class="row"><span class="label">Price</span><span>${Number(m.price).toFixed(6)}</span></div>
        <div class="row"><span class="label">Qty</span><span>${Number(m.qty).toFixed(4)}</span></div>
        <div class="row"><span class="label">Fee</span><span>${Number(m.fee).toFixed(6)}</span></div>
        <div class="row"><span class="label">PnL</span><span class="value ${pnlClass}">${Number(m.pnl || 0).toFixed(4)}</span></div>
      `;

      tooltip.style.left = param.point.x + 20 + "px";
      tooltip.style.top = param.point.y + 20 + "px";

      tooltip.classList.remove("hidden");
    });
  },
};
