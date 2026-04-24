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
    if (fill?.pnl !== undefined && fill?.pnl !== null) {
      return fill.pnl;
    }
    if (fill?.realized_pnl !== undefined && fill?.realized_pnl !== null) {
      return fill.realized_pnl;
    }
    return null;
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
        <td>${fmtNum(this.pnlValue(fill))}</td>
      </tr>
    `;
  },

  async load(limit = 25) {
    const data = await api.getFills(limit, 0);
    const items = Array.isArray(data?.fills) ? data.fills : [];
    qs("fillsTableBody").innerHTML = items.map((fill) => this.rowHtml(fill)).join("");
  },
};