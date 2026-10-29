// props_probe.mjs <url> <t1,t2,...> <width> <height>
// Seeks window.__timelines.main to each time; prints JSON {times:[{t, cam:{x,y,s}, props:[{id,x,y,w,h,opacity,visible}]}]}:
// cam = the computed #cam transform (translate x, y and scale s) and, per [data-prop] element, its layout size (w, h)
// and the centre of its transformed box (x, y); a turntable prop (sheet img inside) adds its shown cell (column, row)
// from the sheet offset.
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const puppeteer = require(process.env.ENGINE_PUPPETEER);
const [url, ts, W, H] = process.argv.slice(2);
const browser = await puppeteer.launch({
  executablePath: process.env.ENGINE_CHROME, headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--mute-audio', '--font-render-hinting=none'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({ width: +W, height: +H, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  await page.waitForFunction(() => !!(window.__timelines && window.__timelines.main), { timeout: 8000, polling: 50 });
  const res = [];
  for (const t of ts.split(',').map(Number)) {
    res.push(await page.evaluate(async (t) => {
      window.__timelines.main.seek(t, false);
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      const m = new DOMMatrix(getComputedStyle(document.getElementById('cam')).transform);
      const props = [...document.querySelectorAll('[data-prop]')].map(el => {
        const r = el.getBoundingClientRect(), cs = getComputedStyle(el), sh = el.querySelector('img');
        let cell = null;
        if (sh) { const o = el.getBoundingClientRect(), q = sh.getBoundingClientRect();
                  cell = [Math.round((o.x - q.x) / o.width), Math.round((o.y - q.y) / o.height)]; }
        return { cell, id: el.getAttribute('data-prop'), x: r.x + r.width / 2, y: r.y + r.height / 2, w: el.offsetWidth,
                 h: el.offsetHeight, bw: r.width, bh: r.height, opacity: parseFloat(cs.opacity), visible: cs.visibility === 'visible' };
      });
      return { t, cam: { x: m.e, y: m.f, s: m.a }, props };
    }, t));
  }
  console.log(JSON.stringify({ times: res }));
} finally {
  await browser.close();
}
