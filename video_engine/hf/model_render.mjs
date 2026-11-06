// model_render.mjs <url of model_page.html> <params json> <out dir>
// Renders a model sprite sequence (models.py): writes <out>/0000.png ... (canvas PNGs at size x ss).
import { createRequire } from 'module';
import fs from 'fs';
const require = createRequire(import.meta.url);
const puppeteer = require(process.env.ENGINE_PUPPETEER);
const [url, params, out] = process.argv.slice(2);
const P = JSON.parse(params);
const browser = await puppeteer.launch({
  executablePath: process.env.ENGINE_CHROME, headless: true,
  args: ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars', '--mute-audio',
         '--disable-gpu-rasterization', '--num-raster-threads=1'],
});
try {
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e)));
  await page.setViewport({ width: P.size * P.ss, height: P.size * P.ss, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: 'load', timeout: 60000 });
  await page.waitForFunction(() => window.__ready === true, { timeout: 30000, polling: 50 }).catch(() => {
    throw new Error('model page did not load: ' + errs.join('; '));
  });
  const frames = await page.evaluate(async (P) => {
    try { return await window.__render(P); } catch (e) { return { error: String(e && e.message || e) }; }
  }, P);
  if (frames.error) throw new Error(frames.error);
  fs.mkdirSync(out, { recursive: true });
  frames.forEach((d, i) => fs.writeFileSync(`${out}/${String(i).padStart(4, '0')}.png`, Buffer.from(d.split(',')[1], 'base64')));
  console.log(JSON.stringify({ frames: frames.length }));
} finally {
  await browser.close();
}
