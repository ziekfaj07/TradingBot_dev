window.fillsModule = {
  actionLabel(fill) {
    const type = String(fill?.type ?? "").toUpperCase();
    const side = String(fill?.side ?? "").toLowerCase();

    if (type === "ENTRY" && side === "long") return "ENTRY";
    if (type === "ENTRY" && side === "short") return "SHORT";
    if (type === "EXIT" && side === "long") return "EXIT";
    if (type === "EXIT" && side === "short") return "COVER";
    if (type === "LIQUIDATION") return "LIQUIDATION";

    return type || "-";
  },

  actionClass(fill) {
    const type = String(fill?.type ?? "").toUpperCase();
    const side = String(fill?.side ?? "").toLowerCase();

    if (type === "ENTRY" && side === "long") return "fill-badge entry-long";
    if (type === "ENTRY" && side === "short") return "fill-badge entry-short";
    if (type === "EXIT" && side === "long") return "fill-badge exit-long";
    if (type === "EXIT" && side === "short") return "fill-badge exit-short";
    if (type === "LIQUIDATION") return "fill-badge liquidation";

    return "fill-badge";
  },

  sideLabel(fill) {
    const side = String(fill?.side ?? "").toLowerCase();
    if (side === "long") return "LONG";
    if (side === "short") return "SHORT";
    return side || "-";
  },

  pnlValue(fill) {
    const meta = this.metaPayload(fill);
    const candidates = [
      fill?.pnl,
      fill?.realized_pnl,
      fill?.realizedPnl,
      fill?.realizedPnL,
      fill?.net_pnl,
      fill?.netPnl,
      fill?.profit,
      fill?.info?.pnl,
      fill?.info?.realized_pnl,
      fill?.info?.realizedPnl,
      fill?.data?.pnl,
      fill?.data?.realized_pnl,
      meta?.pnl,
      meta?.realized_pnl,
      meta?.realizedPnl,
      meta?.net_pnl,
      meta?.profit,
      meta?.info?.pnl,
      meta?.info?.realized_pnl,
      meta?.info?.realizedPnl,
      meta?.data?.pnl,
      meta?.data?.realized_pnl,
    ];

    for (const value of candidates) {
      if (value === undefined || value === null || value === "") continue;
      const n = Number(value);
      return Number.isFinite(n) ? n : value;
    }

    return null;
  },

  metaPayload(fill) {
    const raw = fill?.meta_json ?? fill?.metadata ?? fill?.meta;
    if (!raw) return null;
    if (typeof raw === "object") return raw;

    try {
      return JSON.parse(String(raw));
    } catch (_) {
      return null;
    }
  },

  pnlClass(fill) {
    const value = Number(this.pnlValue(fill));
    if (!Number.isFinite(value)) return "";
    if (value > 0) return "good";
    if (value < 0) return "bad";
    return "";
  },

  rowHtml(fill) {
    return `
      <tr>
        <td>${fmtTs(fill.timestamp)}</td>
        <td><span class="${this.actionClass(fill)}">${this.actionLabel(fill)}</span></td>
        <td>${this.sideLabel(fill)}</td>
        <td>${fmtNum(fill.qty)}</td>
        <td>${fmtNum(fill.price)}</td>
        <td>${fmtNum(fill.fee)}</td>
        <td class="${this.pnlClass(fill)}">${fmtNum(this.pnlValue(fill))}</td>
      </tr>
    `;
  },

  async load(limit = 25) {
    const data = await api.getFills(limit, 0);
    const items = Array.isArray(data?.fills) ? data.fills : [];
    qs("fillsTableBody").innerHTML = items.map((fill) => this.rowHtml(fill)).join("");
  },
};
