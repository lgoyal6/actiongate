// Draws docs/data/sweep.json, which scripts/make_page_data.py builds by calling
// the package's own sweep() and choose_by_cost() with the committed weights.
//
// The threshold grid is the one evaluate.run() uses, not a finer one. A finer
// grid finds the same cost-minimising plateau and reports its lower edge, which
// would put a different threshold on the page than the repository publishes.

const el = (id) => document.getElementById(id);
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

const state = { data: null, split: 'test', i: 13 };

const SERIES = [
  { key: 'fp', label: 'wrong writes', dash: [], width: 2.4, bad: true },
  { key: 'tp', label: 'auto-committed', dash: [7, 4], width: 1.8, bad: false },
  { key: 'fn', label: 'sent to review', dash: [2, 3], width: 1.5, bad: false },
];

const curve = () => state.data.curves[state.split];
const point = () => curve()[state.i];
const chosen = () => state.data.chosen_on_dev[String(state.data.default_cost_ratio)];

function labelOnPaper(ctx, text, x, y, align = 'center') {
  const w = ctx.measureText(text).width;
  const left = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x;
  const prev = ctx.fillStyle;
  ctx.fillStyle = css('--paper');
  ctx.fillRect(left - 3, y - 11, w + 6, 14);
  ctx.fillStyle = prev;
  ctx.textAlign = align;
  ctx.fillText(text, x, y);
}

function fitCanvas(canvas, h0) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w0 = canvas.clientWidth || 1200;
  canvas.width = Math.round(w0 * dpr);
  canvas.height = Math.round(h0 * dpr);
  canvas.style.height = h0 + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w0, h0);
  return { ctx, w: w0, h: h0 };
}

function draw() {
  const rows = curve();
  const { ctx, w, h } = fitCanvas(el('plot'), 280);
  const pad = { l: 58, r: 132, t: 22, b: 48 };
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  const top = Math.max(...rows.flatMap((r) => SERIES.map((s) => r[s.key]))) * 1.12;
  const X = (i) => pad.l + (i / (rows.length - 1)) * iw;
  const Y = (v) => pad.t + ih - (v / top) * ih;

  ctx.strokeStyle = css('--hair');
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t); ctx.lineTo(pad.l, pad.t + ih); ctx.lineTo(pad.l + iw, pad.t + ih);
  ctx.stroke();
  ctx.font = "11px 'Courier New', monospace";
  ctx.textAlign = 'right';
  const step = top > 20 ? 10 : 5;
  for (let v = 0; v <= top; v += step) {
    ctx.fillStyle = css('--faint');
    ctx.fillText(String(v), pad.l - 8, Y(v) + 3);
    if (v) {
      ctx.strokeStyle = '#e8e3d6';
      ctx.beginPath(); ctx.moveTo(pad.l, Y(v)); ctx.lineTo(pad.l + iw, Y(v)); ctx.stroke();
    }
  }
  ctx.textAlign = 'center';
  rows.forEach((r, i) => {
    if (i % 4) return;
    ctx.fillStyle = css('--faint');
    ctx.fillText(r.threshold.toFixed(2), X(i), pad.t + ih + 16);
  });
  ctx.fillStyle = css('--faint');
  ctx.fillText('confidence threshold to auto-commit', pad.l + iw / 2, h - 8);

  SERIES.forEach((s) => {
    ctx.save();
    ctx.setLineDash(s.dash);
    ctx.strokeStyle = s.bad ? css('--bad') : css('--ox');
    ctx.lineWidth = s.width;
    ctx.beginPath();
    rows.forEach((r, i) => (i ? ctx.lineTo(X(i), Y(r[s.key])) : ctx.moveTo(X(i), Y(r[s.key]))));
    ctx.stroke();
    ctx.restore();
  });

  // Labels de-collided: several series land on or near zero at high thresholds.
  const last = rows[rows.length - 1];
  const placed = SERIES.map((s) => ({ label: s.label, bad: s.bad, y: Y(last[s.key]) }))
    .sort((a, b) => a.y - b.y);
  placed.forEach((p, i) => {
    if (i && p.y - placed[i - 1].y < 15) p.y = placed[i - 1].y + 15;
  });
  ctx.textAlign = 'left';
  ctx.font = "12px 'Times New Roman', serif";
  placed.forEach((p) => {
    ctx.fillStyle = p.bad ? css('--bad') : css('--sub');
    ctx.fillText(p.label, pad.l + iw + 8, p.y + 4);
  });

  // Where the cost argument put the threshold, marked on both splits, because
  // it was chosen once on dev and not re-chosen here.
  const ci = rows.findIndex((r) => Math.abs(r.threshold - chosen()) < 1e-9);
  if (ci >= 0) {
    ctx.save();
    ctx.strokeStyle = css('--ok');
    ctx.lineWidth = 1.4;
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(X(ci), pad.t); ctx.lineTo(X(ci), pad.t + ih); ctx.stroke();
    ctx.restore();
    ctx.font = "12px 'Times New Roman', serif";
    ctx.fillStyle = css('--ok');
    labelOnPaper(ctx, `chosen on dev: ${chosen().toFixed(2)}`, X(ci), pad.t + 12);
  }

  ctx.save();
  ctx.strokeStyle = css('--ink');
  ctx.setLineDash([2, 3]);
  ctx.beginPath(); ctx.moveTo(X(state.i), pad.t); ctx.lineTo(X(state.i), pad.t + ih); ctx.stroke();
  ctx.restore();
  SERIES.forEach((s) => {
    ctx.beginPath(); ctx.arc(X(state.i), Y(point()[s.key]), 3.5, 0, Math.PI * 2);
    ctx.fillStyle = s.bad ? css('--bad') : css('--ox'); ctx.fill();
  });
}

function render() {
  const p = point();
  const total = p.tp + p.fp + p.fn + p.tn;
  el('r-thr').textContent = p.threshold.toFixed(2);
  el('r-fp').textContent = `${p.fp} of ${total}`;
  el('r-tp').textContent = `${p.tp} of ${p.tp + p.fn}`;
  el('r-rev').textContent = String(p.fn + p.tn);
  el('r-prec').textContent = p.tp + p.fp ? p.precision.toFixed(3) : 'nothing written';
  el('cap-what').textContent = `${total} action items, ${p.tp + p.fn} of them genuine`;
  el('cap-split').textContent =
    state.split === 'test' ? 'held-out split' : 'dev split, where the threshold was chosen';
  draw();

  const base = curve()[0];
  const b = el('banner');
  if (p.threshold === 0) {
    b.className = 'banner alarm';
    b.textContent =
      `No gate. Everything extracted goes straight into the CRM, including ${p.fp} action ` +
      `items that nobody committed to.`;
  } else if (p.tp + p.fp === 0) {
    b.className = 'banner alarm';
    b.textContent =
      `Nothing clears this threshold, so nothing is auto-committed and all ${total} items go ` +
      `to a person. Safe, and it is not a gate any more.`;
  } else {
    const cut = base.fp ? (1 - p.fp / base.fp) * 100 : 0;
    b.className = p.fp <= 2 ? 'banner calm' : 'banner';
    b.textContent =
      `${p.fp} wrong writes instead of ${base.fp}, which is ${cut.toFixed(0)}% of the damage ` +
      `removed, and ${p.tp} of the ${p.tp + p.fn} genuine items still auto-committed.`;
  }
}

function ratios() {
  const rows = state.data.cost_ratios.map((k) => ({ k, thr: state.data.chosen_on_dev[String(k)] }));
  const head = '<tr><th>a wrong write is worth</th><th>threshold chosen</th><th>what that means</th></tr>';
  const body = rows
    .map(({ k, thr }) => {
      const note = thr === 0
        ? 'no gate: writing everything is the cheaper policy'
        : 'the same threshold, again';
      return (
        `<tr class="${k === state.data.default_cost_ratio ? 'chosen' : ''}">` +
        `<td class="num">${k} review${k === 1 ? '' : 's'}</td>` +
        `<td class="num">${thr.toFixed(2)}</td><td>${note}</td></tr>`
      );
    })
    .join('');
  el('ratios').innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
  el('cap-ratio').textContent = `default is ${state.data.default_cost_ratio}`;
  const stable = rows.filter((r) => r.thr === chosen()).map((r) => r.k);
  el('ratio-banner').className = 'banner calm';
  el('ratio-banner').textContent =
    `Ratios ${stable[0]} through ${stable[stable.length - 1]} all pick ${chosen().toFixed(2)}. ` +
    `The one that does not is a ratio of 1, where gating has nothing to buy.`;
}

function picker(node, items, current, onPick) {
  node.innerHTML = '';
  items.forEach(({ key, label }) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.setAttribute('aria-pressed', String(key === current()));
    b.addEventListener('click', () => {
      onPick(key);
      [...node.children].forEach((c) => c.setAttribute('aria-pressed', String(c === b)));
    });
    node.appendChild(b);
  });
}

async function main() {
  const res = await fetch('./data/sweep.json');
  if (!res.ok) {
    el('banner').textContent = `Could not load the sweep (HTTP ${res.status}).`;
    return;
  }
  state.data = await res.json();
  state.i = curve().findIndex((r) => Math.abs(r.threshold - chosen()) < 1e-9);

  picker(
    el('splits'),
    [{ key: 'test', label: 'Held out' }, { key: 'dev', label: 'Dev' }],
    () => state.split,
    (k) => { state.split = k; render(); },
  );
  const thr = el('thr');
  thr.max = String(curve().length - 1);
  thr.value = String(state.i);
  thr.addEventListener('input', (e) => { state.i = Number(e.target.value); render(); });
  window.addEventListener('resize', draw);

  render();
  ratios();
}

main();
