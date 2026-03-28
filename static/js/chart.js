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

  ensureChart(forceRecreate = false) {
    const container = qs("liveCandleChart");
    if (!container) {
      throw new Error("Chart container not found");
    }

    if (forceRecreate && state.chart) {
      try {
        state.chart.remove();
      } catch (_) {}
      state.chart = null;
      state.candleSeries = null;
      state.lastCandleTime = null;
    }

    const width = Math.max(600, Math.floor(container.clientWidth || 900));
    const height = Math.max(420, Math.floor(container.clientHeight || 420));

    if (!state.chart) {
      state.chart = LightweightCharts.createChart(container, {
        width,
        height,
        layout: {
          textColor: "#c7d2e5",
          background: {
            type: "solid",
            color: "#0f1726",
          },
        },
        grid: {
          vertLines: { color: "#1d2940" },
          horzLines: { color: "#1d2940" },
        },
        rightPriceScale: {
          borderColor: "#2a3a58",
        },
        timeScale: {
          borderColor: "#2a3a58",
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 8,
          barSpacing: 8,
        },
      });
    } else {
      state.chart.applyOptions({ width, height });
    }

    if (!state.candleSeries) {
      state.candleSeries = state.chart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: "#26a69a",
        downColor: "#ef5350",
        borderVisible: false,
        wickUpColor: "#26a69a",
        wickDownColor: "#ef5350",
      });
    }

    return { chart: state.chart, candleSeries: state.candleSeries };
  },

  reset() {
    if (state.candleSeries) {
      state.candleSeries.setData([]);
    }
    qs("chartBarsView").textContent = "0";
    state.lastCandleTime = null;
    state.lastChartKey = null;
  },

  setMeta(symbol, interval, barsCount) {
    qs("chartSymbolView").textContent = symbol || "-";
    qs("chartIntervalView").textContent = interval || "-";
    qs("chartBarsView").textContent = String(barsCount ?? 0);
  },

  async loadBootstrap(force = false) {
    if (!state.latestStatus) return;

    const nextKey = this.currentChartKeyFromStatus(state.latestStatus);
    if (!force && state.lastChartKey === nextKey) return;

    const refs = this.ensureChart(force);

    const data = await api.getChart(300);
    const candles = Array.isArray(data?.candles) ? data.candles : [];

    console.log("[chart] candles received:", candles.length);
    console.log("[chart] first candle:", candles[0]);
    console.log("[chart] last candle:", candles[candles.length - 1]);

    refs.candleSeries.setData(candles);
    refs.chart.timeScale().fitContent();

    state.lastCandleTime = candles.length ? candles[candles.length - 1].time : null;
    state.lastChartKey = this.chartKey(data.symbol, data.interval);

    this.setMeta(data.symbol, data.interval, candles.length);

    if (candles.length === 0) {
      uiController.setActionMessage("Chart loaded but no candles were returned.", "warn");
    } else {
      uiController.setActionMessage(`Chart loaded: ${candles.length} candles.`, "good");
    }
  },

  applyLiveUpdate(msg) {
    if (!msg?.candle || !state.latestStatus) return;

    const activeKey = this.currentChartKeyFromStatus(state.latestStatus);
    const incomingKey = this.chartKey(msg.symbol, msg.interval);

    if (activeKey !== incomingKey) return;

    const refs = this.ensureChart(false);
    refs.candleSeries.update(msg.candle);

    let bars = Number(qs("chartBarsView").textContent || "0");
    if (state.lastCandleTime === null) {
      bars = Math.max(1, bars);
    } else if (msg.candle.time !== state.lastCandleTime) {
      bars += 1;
    }

    state.lastCandleTime = msg.candle.time;
    this.setMeta(msg.symbol, msg.interval, bars);
  },

  resize() {
    if (!state.chart) return;

    const container = qs("liveCandleChart");
    if (!container) return;

    state.chart.applyOptions({
      width: Math.max(300, Math.floor(container.clientWidth || 900)),
      height: 420,
    });
  },
};