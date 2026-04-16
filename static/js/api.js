window.qs = (id) => document.getElementById(id);

window.state = {
  ws: null,
  latestStatus: null,
  autoRefreshTimer: null,
  equityHistory: [],
  chart: null,
  candleSeries: null,
  lastChartKey: null,
  lastCandleTime: null,
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
  async request(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
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

  async start() {
    return await this.request("/api/run/start", { method: "POST" });
  },

  async stop() {
    return await this.request("/api/run/stop", { method: "POST" });
  },

  async resetPaper() {
    return await this.request("/api/run/paper/reset", { method: "POST" });
  },

  async forceLiveExit(note = "") {
    return await this.request("/api/run/live/force-exit", {
      method: "POST",
      body: JSON.stringify({ confirm: true, note }),
    });
  },

  async getFills(limit = 25, offset = 0) {
    return await this.request(`/api/run/paper/fills?limit=${limit}&offset=${offset}`);
  },

  async getChart(limit = 300) {
    return await this.request(`/api/run/paper/chart?limit=${limit}`);
  },

  exportCsv() {
    window.location.href = "/api/run/paper/fills/export.csv";
  },
};