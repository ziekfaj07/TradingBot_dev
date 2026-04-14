window.fillsModule = {
  rowHtml(fill) {
    return `
      <tr>
        <td>${fmtTs(fill.timestamp)}</td>
        <td>${fill.type ?? "-"}</td>
        <td>${fill.side ?? "-"}</td>
        <td>${fmtNum(fill.expected_qty)}</td>
        <td>${fmtNum(fill.qty)}</td>
        <td>${fmtNum(fill.expected_price)}</td>
        <td>${fmtNum(fill.price)}</td>
        <td>${fmtNum(fill.price_slippage_bps)}</td>
        <td>${fmtNum(fill.submit_to_ack_ms)}</td>
        <td>${fmtNum(fill.submit_to_fill_ms)}</td>
        <td>${fmtNum(fill.pnl)}</td>
      </tr>
    `;
  },

  async load(limit = 25) {
    const data = await api.getFills(limit, 0);
    const items = Array.isArray(data?.fills) ? data.fills : [];
    qs("fillsTableBody").innerHTML = items.map((fill) => this.rowHtml(fill)).join("");
  },
};