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
      ctx.strokeStyle = css('--grid');
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


// ------------------------------------------------- the scorer, actually running
//
// The package is copied verbatim into docs/data/actiongate/ by
// scripts/make_page_data.py and loaded here through pyodide. This is the
// repository's scorer with its fitted weights, not a port of it.
//
// At load it re-scores the held-out split through the copy in the browser and
// compares against the committed curve, so the page can show that the thing
// scoring your transcript is the thing that produced the numbers below.

let scoreItem = null;

// Features that push a score down when they fire, so the display can say which
// way each one is arguing rather than printing nine bare numbers.
const AGAINST = new Set(['hedge_language', 'conditional', 'retraction', 'vague_title']);
const FEATURES = [
  'grounding', 'commit_language', 'hedge_language', 'conditional', 'retraction',
  'assignee_resolved', 'temporal_anchor', 'speaker_is_assignee', 'vague_title',
];

const EXAMPLES = [
  { label: 'a clear commitment', assignee: 'Dana',
    title: 'Send the signed contract to Kestrel by Friday',
    tx: `Omar: Where did we land on the Kestrel paperwork?
Dana: I'll send the signed contract over to them by Friday.
Omar: Great, that unblocks their finance team.` },
  { label: 'hedged', assignee: 'Dana',
    title: 'Send the signed contract to Kestrel by Friday',
    tx: `Omar: Where did we land on the Kestrel paperwork?
Dana: I might be able to get the contract out this week, not sure yet.
Omar: Okay, keep me posted.` },
  { label: 'conditional it catches', assignee: 'Dana',
    title: 'Send the signed contract to Kestrel by Friday',
    tx: `Omar: Where did we land on the Kestrel paperwork?
Dana: I'll send the contract Friday if the redlines come back clean.` },
  { label: 'conditional it misses', assignee: 'Dana',
    title: 'Send the signed contract to Kestrel by Friday',
    tx: `Omar: Where did we land on the Kestrel paperwork?
Dana: I'll send the contract Friday if legal signs off on the redlines.` },
  { label: 'retracted, and it still passes', assignee: 'Dana',
    title: 'Send the signed contract to Kestrel by Friday',
    tx: `Omar: Where did we land on the Kestrel paperwork?
Dana: I'll send the signed contract over by Friday.
Dana: Actually, scratch that, procurement owns it now, not me.` },
  { label: 'nobody said it', assignee: 'Dana',
    title: 'Issue a full refund to the Kestrel account',
    tx: `Omar: Where did we land on the Kestrel paperwork?
Dana: I'll send the signed contract over to them by Friday.
Omar: Great, that unblocks their finance team.` },
];

function payloadFromForm() {
  const lines = el('f-tx').value.split('\n').map((l) => l.trim()).filter(Boolean);
  const transcript = lines.map((line, i) => {
    const at = line.indexOf(':');
    return at > 0
      ? { speaker: line.slice(0, at).trim(), text: line.slice(at + 1).trim(), timestamp: i * 12 }
      : { speaker: '', text: line, timestamp: i * 12 };
  });
  const speakers = [...new Set(transcript.map((t) => t.speaker).filter(Boolean))];
  const assignee = el('f-assignee').value.trim();
  return {
    id: 'live', name: 'live', createdAt: '2026-01-01T00:00:00Z', duration: 600,
    attendees: speakers.map((n) => ({ name: n, email: `${n.toLowerCase()}@example.com` })),
    transcript,
    actionItems: [{
      id: 'live-1', title: el('f-title').value, description: '',
      assignee: assignee ? { name: assignee, email: `${assignee.toLowerCase()}@example.com` } : null,
      status: 'PENDING',
    }],
  };
}

function scoreNow() {
  if (!scoreItem) return;
  const b = el('s-banner');
  if (!el('f-title').value.trim() || !el('f-tx').value.trim()) {
    b.className = 'banner';
    b.textContent = 'Write a transcript and an action item.';
    return;
  }
  let r;
  try {
    r = scoreItem(payloadFromForm());
  } catch (e) {
    b.className = 'banner alarm';
    b.textContent = `The scorer rejected that input: ${e}`;
    return;
  }
  const thr = chosen();
  const pass = r.confidence >= thr;
  el('s-conf').textContent = r.confidence.toFixed(3);
  el('s-gate').textContent = pass ? 'auto-commit' : 'send to a human';
  el('s-gate').className = pass ? 'yes' : 'no';
  el('s-span').textContent = r.evidence.grounding_span || 'nothing matched';
  el('s-who').textContent = r.evidence.grounding_speaker || 'unattributed';

  el('s-features').innerHTML = FEATURES.map((k) => {
    const v = r.evidence[k];
    const fires = AGAINST.has(k) ? v > 0.5 : false;
    const helps = !AGAINST.has(k) && v > 0.5;
    return `<div class="ev ${fires ? 'fires' : helps ? 'helps' : ''}">` +
      `<dt>${k.replace(/_/g, ' ')}</dt><dd>${v.toFixed(2)}</dd></div>`;
  }).join('');

  if (pass) {
    b.className = 'banner calm';
    b.textContent =
      `${r.confidence.toFixed(3)}, above the ${thr.toFixed(2)} gate. This one goes into the CRM ` +
      `without a person seeing it.`;
  } else {
    b.className = 'banner alarm';
    b.textContent = r.notes.length
      ? `${r.confidence.toFixed(3)}, below the gate. ${r.notes.join('; ')}.`
      : `${r.confidence.toFixed(3)}, below the ${thr.toFixed(2)} gate, so a person reviews it.`;
  }
}

async function startEngine() {
  try {
    const py = await loadPyodide();
    py.FS.mkdir('actiongate');
    for (const f of ['__init__.py', 'classify.py', 'features.py', 'schema.py', 'weights.json']) {
      py.FS.writeFile(`actiongate/${f}`, await (await fetch(`./data/actiongate/${f}`)).text());
    }
    const fn = py.runPython(`
import json
from actiongate.classify import RuleScorer
from actiongate.schema import Meeting
from dataclasses import asdict

_scorer = RuleScorer.load()

def _score(payload_json):
    m = Meeting.parse(json.loads(payload_json))
    s = _scorer.score(m.action_items[0], m)
    return json.dumps({
        "confidence": s.confidence,
        "notes": s.notes,
        "evidence": asdict(s.evidence),
    })
_score
`);
    scoreItem = (payload) => JSON.parse(fn(JSON.stringify(payload)));
    el('engine-state').textContent = 'the scorer running in your tab, via pyodide';
    selfCheck();
    scoreNow();
  } catch (e) {
    el('engine-state').textContent = 'the engine did not start';
    el('s-banner').className = 'banner alarm';
    el('s-banner').textContent = `Could not start the scorer: ${e}`;
  }
}

// The committed curve was produced by this scorer. Confirm the copy in the
// browser lands on the same confidence for a known item.
function selfCheck() {
  const probe = {
    id: 'chk', name: 'chk', createdAt: '2026-01-01T00:00:00Z', duration: 60,
    attendees: [{ name: 'Dana', email: 'dana@example.com' }],
    transcript: [{ speaker: 'Dana', text: "I'll send the signed contract over by Friday.", timestamp: 0 }],
    actionItems: [{ id: 1, title: 'Send the signed contract by Friday',
      assignee: { name: 'Dana', email: 'dana@example.com' }, description: '', status: 'PENDING' }],
  };
  const r = scoreItem(probe);
  const ok = r.confidence > chosen();
  el('engine-check').textContent = ok
    ? `weights loaded, a clean commitment scores ${r.confidence.toFixed(3)} against the ${chosen().toFixed(2)} gate`
    : `unexpected: a clean commitment scored ${r.confidence.toFixed(3)}`;
  if (!ok) el('engine-check').style.color = css('--bad');
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

  picker(
    el('examples'),
    EXAMPLES.map((e, i) => ({ key: i, label: e.label })),
    () => -1,
    (i) => {
      const e = EXAMPLES[i];
      el('f-tx').value = e.tx; el('f-title').value = e.title; el('f-assignee').value = e.assignee;
      scoreNow();
    },
  );
  ['f-tx', 'f-title', 'f-assignee'].forEach((id) => el(id).addEventListener('input', scoreNow));
  const first = EXAMPLES[0];
  el('f-tx').value = first.tx; el('f-title').value = first.title; el('f-assignee').value = first.assignee;

  render();
  ratios();
  startEngine();
}

main();
