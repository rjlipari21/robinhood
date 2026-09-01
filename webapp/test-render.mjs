/* Layout, geometry and injection checks for the dashboard's client code.
 *
 *   node webapp/test-render.mjs [snapshot.json]
 *
 * Run by scripts/test-dashboard.sh, which produces the snapshot first.
 *
 * Why this exists: there is no browser on this VM -- installing one filled the
 * disk once already -- so "render it and look at it" is done by executing the
 * real drawPL/drawCurve/md functions out of index.html against a real snapshot
 * and asserting the invariants an eyeball would otherwise catch: labels inside
 * the viewBox, one mark per datum, no NaN geometry, balanced tags, and journal
 * text escaped rather than rendered.
 *
 * The markdown checks matter most. Journal text is rendered as HTML, so if
 * escaping ever regresses, anything the agent wrote -- or anything a news
 * headline it quoted contained -- becomes markup on a page showing account
 * data. That path is exercised here with a real payload and an explicit probe.
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
  innerHTML: '', clientWidth: 560,
  querySelectorAll: () => [],
  querySelector: () => ({ addEventListener() {}, setAttribute() {}, style: {} }),
});
global.document = {
  querySelector: stubEl,
  documentElement: { getAttribute: () => null, hasAttribute: () => false },
};
global.addEventListener = () => {};
global.innerWidth = 1200;
global.innerHeight = 900;
global.matchMedia = () => ({ matches: false });

const M = new Function(src + '; return {drawPL, drawCurve, md, inline, pct, money};')();
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
