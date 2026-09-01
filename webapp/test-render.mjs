/* Layout, geometry and injection checks for the dashboard's client code.
 *
 *   node webapp/test-render.mjs [snapshot.json]
 *
 * Run by scripts/test-dashboard.sh, which produces the snapshot first.
 *
 * Why this exists: there is no browser on this VM -- installing one filled the
 * disk once already -- so "render it and look at it" is done by executing the
 * page's own drawing and rendering functions out of index.html against a real
 * snapshot and asserting the invariants an eyeball would otherwise catch:
 * labels inside the viewBox, one mark per datum, no NaN geometry, no column
 * overlapping its neighbour, balanced tags, and agent-written text escaped
 * rather than rendered.
 *
 * The escaping checks matter most. Two separate paths render agent-written
 * text as HTML -- journal entries through md(), fill notes through
 * renderActivity() -- so if escaping regresses in either, anything the agent
 * wrote, or anything a news headline it quoted contained, becomes markup on a
 * page showing account data. Both are exercised with a real payload and an
 * explicit hostile probe.
 */
import fs from 'fs';

const htmlPath = new URL('./index.html', import.meta.url).pathname;
const snapPath = process.argv[2] || '/tmp/dashboard-snapshot.json';

// Pull the page's own script and keep only the pure functions -- everything
// from the DOM-bootstrap block onward needs a live document.
const html = fs.readFileSync(htmlPath, 'utf8');
const whole = html.match(/<script>\n([\s\S]*?)<\/script>/)[1];
const src = whole.slice(0, whole.indexOf('$("#fAll").onclick'));

const stubEl = () => ({
  innerHTML: '', textContent: '', clientWidth: 560,
  querySelectorAll: () => [],
  querySelector: () => ({ addEventListener() {}, setAttribute() {}, style: {} }),
});
// Keyed, so a renderer that looks up its own container can be read back after
// it writes -- renderActivity and renderRealized both do that.
const els = new Map();
const byId = sel => {
  if (!els.has(sel)) els.set(sel, stubEl());
  return els.get(sel);
};
global.document = {
  querySelector: byId,
  documentElement: { getAttribute: () => null, hasAttribute: () => false },
};
global.addEventListener = () => {};
global.innerWidth = 1200;
global.innerHeight = 900;
global.matchMedia = () => ({ matches: false });

const M = new Function(src + '; return {drawPL, drawCurve, drawRealized, renderRealized, renderActivity, md, inline, pct, money};')();
const snap = JSON.parse(fs.readFileSync(snapPath, 'utf8'));

let fails = 0;
const chk = (ok, msg) => { console.log((ok ? '  PASS  ' : '  FAIL  ') + msg); if (!ok) fails++; };

/* ---- P&L diverging bars ------------------------------------------------ */
const el = stubEl();
M.drawPL(el, snap.positions);
const svg = el.innerHTML;
const n = snap.positions.length;

if (n) {
  const [, ws, hs] = svg.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
  const W = +ws, H = +hs;
  console.log(`\nP&L chart  ${W}x${H}  (${n} positions)`);

  const tx = [...svg.matchAll(/<text[^>]*x="([-\d.]+)"/g)].map(m => +m[1]);
  const ty = [...svg.matchAll(/<text[^>]*y="([-\d.]+)"/g)].map(m => +m[1]);
  chk(Math.min(...tx) >= -2, `text stays off the left edge (min x ${Math.min(...tx).toFixed(1)})`);
  chk(Math.max(...tx) <= W + 2, `text stays off the right edge (max x ${Math.max(...tx).toFixed(1)} vs W ${W})`);
  chk(Math.min(...ty) >= -14, `threshold captions fit the top margin (min y ${Math.min(...ty).toFixed(1)})`);
  chk(Math.max(...ty) <= H + 2, `axis ticks fit the bottom margin (max y ${Math.max(...ty).toFixed(1)} vs H ${H})`);
  chk((svg.match(/<path d="M/g) || []).length === n, `one bar per position (${n})`);
  chk(!/NaN|Infinity|undefined/.test(svg), 'no NaN/Infinity/undefined geometry');
  chk((svg.match(/class="thresh"/g) || []).length === 2, 'both the -5% exit and +3% target rules are drawn');
  // Colour is never the only channel: every mark is directly labelled.
  chk((svg.match(/class="barlabel"/g) || []).length === n, 'every bar carries its numeric value');
  chk((svg.match(/class="symlabel"/g) || []).length === n, 'every bar carries its ticker');
} else {
  console.log('\nP&L chart  (no positions)');
  chk(/No open positions/.test(svg), 'empty state renders instead of an axis');
}

/* ---- equity curve ------------------------------------------------------ */
const el2 = stubEl();
M.drawCurve(el2, snap.equity_curve);
const svg2 = el2.innerHTML;
console.log(`\nEquity curve  (${snap.equity_curve.length} readings)`);
if (snap.equity_curve.length >= 2) {
  chk(!/NaN|Infinity|undefined/.test(svg2), 'no NaN/Infinity/undefined geometry');
  const verts = (svg2.match(/[ML][\d.]+,[\d.]+/g) || []).length;
  chk(verts === snap.equity_curve.length, `${verts} vertices for ${snap.equity_curve.length} readings`);
  const H2 = +svg2.match(/viewBox="0 0 [\d.]+ ([\d.]+)"/)[1];
  const cy = [...svg2.matchAll(/[ML][\d.]+,([\d.]+)/g)].map(m => +m[1]);
  chk(Math.min(...cy) >= 0 && Math.max(...cy) <= H2, `line stays inside 0..${H2}`);
} else {
  chk(/Not enough journal entries/.test(svg2), 'empty state renders instead of a broken axis');
}

/* ---- realized outcomes ------------------------------------------------- */
const closed = ((snap.activity || {}).events || [])
  .filter(e => e.side === 'sell' && e.pnl_pct != null).slice().reverse();
const el3 = stubEl();
M.drawRealized(el3, closed);
const svg3 = el3.innerHTML;
console.log(`\nRealized outcomes  (${closed.length} closed trades)`);
if (closed.length) {
  const [, ws3, hs3] = svg3.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
  const W3 = +ws3, H3 = +hs3;
  chk(!/NaN|Infinity|undefined/.test(svg3), 'no NaN/Infinity/undefined geometry');
  chk((svg3.match(/<path d="M/g) || []).length === closed.length,
      `one column per closed trade (${closed.length})`);
  chk((svg3.match(/class="thresh"/g) || []).length === 2,
      'both the -5% exit and +3% target rules are drawn');

  const tx3 = [...svg3.matchAll(/<text[^>]*x="([-\d.]+)"/g)].map(m => +m[1]);
  const ty3 = [...svg3.matchAll(/<text[^>]*y="([-\d.]+)"/g)].map(m => +m[1]);
  chk(Math.min(...tx3) >= -2, `text stays off the left edge (min x ${Math.min(...tx3).toFixed(1)})`);
  chk(Math.max(...tx3) <= W3 + 2, `threshold captions fit the right margin (max x ${Math.max(...tx3).toFixed(1)} vs W ${W3})`);
  chk(Math.min(...ty3) >= -2, 'value labels fit the top margin');

  // Columns must not overlap. Each bar path starts at its left edge and the
  // first H command sits one corner-radius short of its right edge, so the
  // right edge is bounded by that H plus the 4px radius.
  const lefts = [...svg3.matchAll(/<path d="M([\d.]+),/g)].map(m => +m[1]);
  const rights = [...svg3.matchAll(/<path d="M[\d.]+,[\d.]+ V[\d.]+ a[\d.,\s-]+ H([\d.]+)/g)]
    .map(m => +m[1] + 4);
  chk(rights.length === lefts.length, 'every column has a measurable right edge');
  let overlap = 0;
  for (let i = 1; i < lefts.length; i++) if (lefts[i] < rights[i - 1] - 0.01) overlap++;
  chk(overlap === 0, `no column overlaps its neighbour (${lefts.length} columns)`);
  chk(lefts.every((x, i) => i === 0 || x > lefts[i - 1]), 'columns run oldest to newest, left to right');

  // Rotated tickers hang downward from the axis; they must not run off the
  // bottom. ~6.3px per character at the 10px tick size.
  const syms = [...svg3.matchAll(/class="tick symtick"[^>]*y="([\d.]+)"[^>]*>([^<]+)</g)];
  chk(syms.length > 0, `ticker labels drawn (${syms.length} of ${closed.length} columns)`);
  const deepest = Math.max(...syms.map(m => +m[1] + m[2].length * 6.3));
  chk(deepest <= H3 + 2, `rotated tickers fit the bottom margin (deepest ${deepest.toFixed(1)} vs H ${H3})`);

  // Selective direct labels, not a number on every column.
  const nlab = (svg3.match(/class="barlabel"/g) || []).length;
  chk(nlab >= 1 && nlab <= 3, `direct labels are selective (${nlab}: best, worst, latest)`);
} else {
  chk(/No closed trades/.test(svg3), 'empty state renders instead of an axis');
}

/* ---- stat tiles and the activity list ---------------------------------- */
console.log('\nOrder activity');
M.renderRealized(snap);
const tiles = byId('#realkpis').innerHTML;
chk(!/NaN|undefined|null/.test(tiles), 'stat tiles carry no NaN/undefined/null');
chk((tiles.match(/class="kpi"/g) || []).length >= 1, 'stat tiles rendered');

M.renderActivity(snap);
const act = byId('#activity').innerHTML;
const nev = ((snap.activity || {}).events || []).length;
chk((act.match(/<details/g) || []).length === nev, `one row per order/fill (${nev})`);
chk(!/NaN|undefined/.test(act), 'activity rows carry no NaN/undefined');
for (const tag of ['details', 'summary', 'div']) {
  const o = (act.match(new RegExp(`<${tag}[ >]`, 'g')) || []).length;
  const c = (act.match(new RegExp(`</${tag}>`, 'g')) || []).length;
  chk(o === c, `<${tag}> tags balanced across the list (${o}/${c})`);
}
chk(/#activity/.test('#activity') && byId('#actmeta').textContent.length > 0,
    'the meta line states how many orders filled');

// Fill notes are agent-written prose rendered into HTML, and a note quotes
// whatever the agent read -- headlines included. Escaping is the only thing
// standing between that and markup on a page showing account data.
M.renderActivity({ window_days: 7, open_orders: [], activity: { placed: 1, matched: 1,
  events: [{ kind: 'fill', symbol: '<b>X</b>', side: 'buy', quantity: 1, limit_price: 5,
             price: 5, notional: 5, at: 'x', date_et: '2026-01-01', time_et: '10:00',
             vs_limit_pct: 0, pnl_pct: null, ref_id: 'r"1', order_id: null,
             note: '<img src=x onerror=alert(1)><script>alert(2)<\/script>' }] } });
const evil2 = byId('#activity').innerHTML;
chk(!/<img/i.test(evil2) && /&lt;img/.test(evil2), 'fill notes are escaped, not rendered');
chk(!/<script/i.test(evil2), 'script tags in a note cannot survive');
chk(!/<b>X<\/b>/.test(evil2), 'a hostile symbol is escaped too');

/* ---- markdown ---------------------------------------------------------- */
console.log('\nMarkdown renderer');
const evil = M.md('- <img src=x onerror=alert(1)>\n\n**bold** and `code`\n\n<script>alert(2)<\/script>');
chk(!/<img/i.test(evil) && /&lt;img/.test(evil), 'raw HTML is escaped, not rendered');
chk(!/<script/i.test(evil), 'script tags cannot survive the renderer');
chk(!/onerror=/.test(evil) || /&quot;|&#39;|&lt;/.test(evil), 'event handlers are neutralised');
chk(/<strong>bold<\/strong>/.test(evil) && /<code>code<\/code>/.test(evil), 'bold and code still render');

if (snap.journal.length) {
  const longest = snap.journal.reduce((a, b) => (b.body.length > a.body.length ? b : a));
  const out = M.md(longest.body);
  for (const tag of ['ul', 'ol', 'table', 'p', 'blockquote']) {
    const o = (out.match(new RegExp(`<${tag}>`, 'g')) || []).length;
    const c = (out.match(new RegExp(`</${tag}>`, 'g')) || []).length;
    chk(o === c, `<${tag}> tags balanced on the longest entry (${o}/${c})`);
  }
  let threw = 0;
  for (const e of snap.journal) { try { M.md(e.body); } catch { threw++; } }
  chk(threw === 0, `all ${snap.journal.length} journal entries render without throwing`);
}

console.log(fails ? `\n${fails} CHECK(S) FAILED` : '\nall checks pass');
process.exit(fails ? 1 : 0);
