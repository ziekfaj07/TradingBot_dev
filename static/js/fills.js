window.fillsModule = {
  rowHtml(fill) {
    return `
      <tr>
        <td>${fmtTs(fill.timestamp)}</td>
        <td>${fill.side ?? "-"}</td>
        <td>${fmtNum(fill.qty)}</td>
        <td>${fmtNum(fill.price)}</td>
        <td>${fmtNum(fill.fee)}</td>
        <td>${fmtNum(fill.realized_pnl)}</td>
      </tr>
    `;
  },

  async load(limit = 25) {
    const data = await api.getFills(limit, 0);
    const items = Array.isArray(data?.fills) ? data.fills : [];
    qs("fillsTableBody").innerHTML = items.map((fill) => this.rowHtml(fill)).join("");
  },
};