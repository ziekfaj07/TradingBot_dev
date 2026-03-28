window.equityModule = {
  push(value) {
    if (value === null || value === undefined) return;
    state.equityHistory.push(Number(value));
    if (state.equityHistory.length > 200) {
      state.equityHistory.shift();
    }
    this.draw();
  },

  clear() {
    state.equityHistory.length = 0;
    this.draw();
  },

  draw() {
    const canvas = qs("equityCanvas");
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const width = (canvas.width = canvas.clientWidth);
    const height = (canvas.height = canvas.clientHeight);

    ctx.clearRect(0, 0, width, height);
    if (state.equityHistory.length < 2) return;

    const values = state.equityHistory.map((x) => Number(x));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(1, max - min);

    ctx.beginPath();
    values.forEach((v, i) => {
      const x = (i / (values.length - 1)) * (width - 20) + 10;
      const y = height - 10 - ((v - min) / span) * (height - 20);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#5dade2";
    ctx.lineWidth = 2;
    ctx.stroke();
  },

  resize() {
    this.draw();
  },
};