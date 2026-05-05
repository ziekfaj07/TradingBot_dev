window.qs = (id) => document.getElementById(id);

window.state = {
  ws: null,
  latestStatus: null,
  autoRefreshTimer: null,
  equityHistory: [],
  chart: null,
  candleSeries: null,
  markerApi: null,
  chartMarkers: [],
  rawChartMarkers: [],
  chartCandles: [],
  indicatorSeries: {},
  lastChartKey: null,
  lastCandleTime: null,
  autoFollow: true,
  scrollAnimationFrame: null,
  autoScale: true,
  tooltipInitialized: false,
  visibleRangeSubscribed: false,
  selectedTimeframe: "1m",
  chartPrecision: 6,
  chartMinMove: 0.000001,
  exchangePrecisionLoaded: false,
  indicatorVisibility: {
    emaShort: true,
    emaLong: true,
    bbUpper: true,
    bbMid: true,
    bbLower: true,
    pnlCurve: true,
  },
  pnlSeries: null,
  pnlCurveData: [],
  replayIndex: -1,
  replayPlaying: false,
  replayTimer: null,
  lastAutoFitAt: 0,
  selectedMarker: null,
  markerClusterWindowSec: 45,
};

window.fmtNum = function fmtNum(v, digits = 4) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
};

window.fmtTs = function fmtTs(v) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  const d = new Date(n * 1000);
  return d.toLocaleString();
};

window.api = {
  storageKey: "tradingbot_api_key",

  getStoredApiKey() {
    try {
      return String(window.localStorage.getItem(this.storageKey) || "").trim();
    } catch (_) {
      return "";
    }
  },

  setStoredApiKey(value) {
    const clean = String(value || "").trim();
    try {
      if (clean) {
        window.localStorage.setItem(this.storageKey, clean);
      } else {
        window.localStorage.removeItem(this.storageKey);
      }
    } catch (_) {}
    return clean;
  },

  buildHeaders(extraHeaders = {}) {
    const headers = {
      "Content-Type": "application/json",
      ...extraHeaders,
    };

    const apiKey = this.getStoredApiKey();
    if (apiKey) {
      headers["X-API-Key"] = apiKey;
    }

    return headers;
  },

  async request(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: this.buildHeaders(options.headers || {}),
    });

    if (!res.ok) {
      let message = "";
      const ct = res.headers.get("content-type") || "";

      try {
        if (ct.includes("application/json")) {
          const data = await res.json();
          message = data?.detail || JSON.stringify(data);
        } else {
          message = await res.text();
        }
      } catch (_) {
        message = "";
      }

      if (res.status === 401) {
        if (this.getStoredApiKey()) {
          throw new Error(message || "API key rejected by backend.");
        }
        throw new Error(
          "API key required. Enter the TradingBot API key in the dashboard, save it, then retry."
        );
      }

      throw new Error(message || `HTTP ${res.status}`);
    }

    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      return await res.json();
    }
    return await res.text();
  },

  async getStatus() {
    return await this.request("/api/run/status");
  },

  async getMetrics() {
    return await this.request("/api/run/metrics");
  },  

  async setMode(mode) {
    return await this.request("/api/run/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
  },

  async configure(payload) {
    return await this.request("/api/run/configure", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  async runBacktest(payload) {
    return await this.request("/api/run/backtest", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  async start() {
    return await this.request("/api/run/start", { method: "POST" });
  },

  async stop() {
    return await this.request("/api/run/stop", { method: "POST" });
  },

  async resetPaper() {
    return await this.request("/api/run/paper/reset", { method: "POST" });
  },

  async getFills(limit = 25, offset = 0) {
    return await this.request(`/api/run/paper/fills?limit=${limit}&offset=${offset}`);
  },

  async getChart(limit = 300) {
    return await this.request(
      `/api/run/paper/chart?limit=${limit}&interval=${encodeURIComponent(state.selectedTimeframe)}`
    );
  },

  async validateExchangeConfig(payload) {
    return await this.request("/api/exchange/validate-live-config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  exportCsv() {
    window.location.href = "/api/run/paper/fills/export.csv";
  },
};
