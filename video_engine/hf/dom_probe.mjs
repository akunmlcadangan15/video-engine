// dom_probe.mjs <url> <out_dir|-> <t1,t2,...> <width> <height> [bg|bgonly]
// ENGINE_WEBGL=1 (globe scenes) enables SwiftShader WebGL.
// Seeks window.__timelines.main to each time, prints JSON {timing, times:[{t, rects:[{id,text,x,y,w,h,opacity,color,stroke_px,stroke,paint_order,ink_state}], paints, png, bg, mask}]}.
// bg (needs out_dir; bgonly = without snap_i.png): two more screenshots per time, clipped to the visible [data-ink] text elements + 64 px
// (clip {x,y,w,h}; none when no such element is visible): bg_i.png with those elements hidden (root class
// ink-measure) and mask_i.png with only those elements, black on white (root class ink-mask).
// paints: [{entity, kind, color}] of every [data-entity] element: the painted colour (fill for SVG, text colour else).
// rects also carry halo (the element's data-halo JSON: painted halo width, blur, em, font px, counters), halo_color
// (its ::after background), font_style and font_family. lines: [{id,x,y,w,h,opacity,ink}] of every [data-line] element (pins,
// routes, region outlines, leaders; opacity includes a polygon's stroke-opacity; ink = data-line-ink JSON colours).
// bg modes also write, when a line is visible, line_bg_i.png (lines hidden: root class line-measure) and
// line_mask_i.png (lines only, on white: root class line-mask) clipped to the visible lines + 16 px (line_clip).
// timing: wall ms of the probe phases {load (launch, page load, fonts, timeline ready), rects (seek + DOM reads),
// snaps (snap_i.png), bg (bg_i.png + line_bg_i.png), masks (mask_i.png + line_mask_i.png)}.
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const puppeteer = require(process.env.ENGINE_PUPPETEER);
const [url, outDir, ts, W, H, mode] = process.argv.slice(2);
const times = ts.split(',').map(Number);
const ms = { load: 0, rects: 0, snaps: 0, bg: 0, masks: 0 };
let t0 = performance.now();
const lap = k => { const t = performance.now(); ms[k] += t - t0; t0 = t; };
const browser = await puppeteer.launch({
  executablePath: process.env.ENGINE_CHROME, headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--mute-audio', '--font-render-hinting=none',
         ...(process.env.ENGINE_WEBGL === '1' ? ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] : [])],
});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('requestfailed', r => errors.push('request failed: ' + r.url()));
  await page.setViewport({ width: +W, height: +H, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  await page.evaluate(() => document.fonts.ready);
  try {
    await page.waitForFunction(() => !!(window.__timelines && window.__timelines.main), { timeout: 8000, polling: 50 });
  } catch (e) {
    throw new Error('timeline window.__timelines.main not ready; page errors: ' + errors.join(' | '));
  }
  lap('load');
  const inkIds = new Set(await page.evaluate(() => [...document.querySelectorAll('[data-ink]')].map(e => e.getAttribute('data-qa'))));
  const res = [];
  for (let i = 0; i < times.length; i++) {
    const t = times[i];
    const { rects, paints, lines } = await page.evaluate(async (t) => {
      window.__timelines.main.seek(t, false);
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      const paints = [...document.querySelectorAll('[data-entity]')].map(el => {
        const cs = getComputedStyle(el), kind = el.getAttribute('data-paint');
        return { entity: el.getAttribute('data-entity'), kind, color: kind === 'fill' || kind === 'block' ? cs.fill : cs.color };
      });
      const rects = [...document.querySelectorAll('[data-qa]')].map(el => {
        const r = el.getBoundingClientRect();
        let op = 1, vis = true;
        for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
          const cs = getComputedStyle(e);
          op *= parseFloat(cs.opacity);
          if (cs.visibility === 'hidden' || cs.display === 'none') vis = false;
        }
        return { id: el.getAttribute('data-qa'), text: el.textContent, x: r.x, y: r.y, w: r.width, h: r.height, opacity: vis ? op : 0,
                 color: getComputedStyle(el).color, stroke_px: parseFloat(getComputedStyle(el).webkitTextStrokeWidth) || 0,
                 stroke: getComputedStyle(el).webkitTextStrokeColor, paint_order: getComputedStyle(el).paintOrder,
                 ink_state: el.getAttribute('data-ink-state'), halo: el.getAttribute('data-halo'),
                 halo_color: getComputedStyle(el, '::after').backgroundColor, font_style: getComputedStyle(el).fontStyle,
                 font_family: getComputedStyle(el).fontFamily, bg: getComputedStyle(el).backgroundColor,
                 box: el.getAttribute('data-box'), box_dim: el.getAttribute('data-box-dim') };
      });
      const lines = [...document.querySelectorAll('[data-line]')].map(el => {
        const r = el.getBoundingClientRect();
        let op = el.tagName === 'polygon' ? parseFloat(el.getAttribute('stroke-opacity') || '1') : 1, vis = true;
        for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
          const cs = getComputedStyle(e);
          op *= parseFloat(cs.opacity);
          if (cs.visibility === 'hidden' || cs.display === 'none') vis = false;
        }
        return { id: el.getAttribute('data-line'), x: r.x, y: r.y, w: r.width, h: r.height, opacity: vis ? op : 0,
                 ink: el.getAttribute('data-line-ink') };
      });
      return { rects, paints, lines };
    }, t);
    lap('rects');
    let png = null;
    let bg = null, mask = null, clip = null;
    if (outDir !== '-' && mode !== 'bgonly') { png = `${outDir}/snap_${i}.png`; await page.screenshot({ path: png }); }
    lap('snaps');
    if (outDir !== '-' && (mode === 'bg' || mode === 'bgonly')) {
      const ink = rects.filter(r => r.opacity > 0 && r.w > 0 && r.h > 0 && inkIds.has(r.id));
      if (ink.length) {
        const P = 64, x0 = Math.max(0, Math.floor(Math.min(...ink.map(r => r.x)) - P)), y0 = Math.max(0, Math.floor(Math.min(...ink.map(r => r.y)) - P));
        const x1 = Math.min(+W, Math.ceil(Math.max(...ink.map(r => r.x + r.w)) + P)), y1 = Math.min(+H, Math.ceil(Math.max(...ink.map(r => r.y + r.h)) + P));
        if (x1 > x0 && y1 > y0) clip = { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
      }
    }
    const shot = async (cls, path, clip) => {
      await page.evaluate(async (cls) => {
        document.getElementById('root').classList.add(cls);
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      }, cls);
      await page.screenshot({ path, clip, optimizeForSpeed: true });
      await page.evaluate((cls) => document.getElementById('root').classList.remove(cls), cls);
    };
    if (clip) {
      bg = `${outDir}/bg_${i}.png`; await shot('ink-measure', bg, clip); lap('bg');
      mask = `${outDir}/mask_${i}.png`; await shot('ink-mask', mask, clip); lap('masks');
    }
    let lineBg = null, lineMask = null, lineClip = null;
    const vl = lines.filter(r => r.opacity > 0 && r.w > 0 && r.h > 0);
    if (outDir !== '-' && mode === 'bg' && vl.length) {
      const P = 16, x0 = Math.max(0, Math.floor(Math.min(...vl.map(r => r.x)) - P)), y0 = Math.max(0, Math.floor(Math.min(...vl.map(r => r.y)) - P));
      const x1 = Math.min(+W, Math.ceil(Math.max(...vl.map(r => r.x + r.w)) + P)), y1 = Math.min(+H, Math.ceil(Math.max(...vl.map(r => r.y + r.h)) + P));
      if (x1 > x0 && y1 > y0) {
        lineClip = { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
        lineBg = `${outDir}/line_bg_${i}.png`; await shot('line-measure', lineBg, lineClip); lap('bg');
        lineMask = `${outDir}/line_mask_${i}.png`; await shot('line-mask', lineMask, lineClip); lap('masks');
      }
    }
    res.push({ t, rects, paints, lines, png, bg, mask, clip: clip && { x: clip.x, y: clip.y, w: clip.width, h: clip.height },
               line_bg: lineBg, line_mask: lineMask, line_clip: lineClip && { x: lineClip.x, y: lineClip.y, w: lineClip.width, h: lineClip.height } });
  }
  const timing = Object.fromEntries(Object.entries(ms).map(([k, v]) => [k, Math.round(v)]));
  process.stdout.write(JSON.stringify({ times: res, timing }));
} finally {
  await browser.close();
}
