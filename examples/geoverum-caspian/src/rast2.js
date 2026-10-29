const sharp = require('/workspace/video-engine/vendor/npm/node_modules/sharp');
const g = '/workspace/open-assets/2d/icons/game-icons/icons/';
(async () => { for (const [p, n] of [["lorc/spears","gi_spears"],["delapouite/caveman","gi_caveman"],["delapouite/raft","gi_raft"]]) {
  await sharp(g + p + '.svg', {density: 144}).resize(512, 512).png().toFile(`assets/props/${n}.png`);
} console.log('ok'); })();
