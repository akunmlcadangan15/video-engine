const sharp = require('/workspace/video-engine/vendor/npm/node_modules/sharp');
const src = '/workspace/open-assets/2d/emoji/twemoji/assets/svg/';
const map = {"274c":"cross","1f30f":"globe","1f4cf":"ruler","26c8":"storm","26a1":"bolt","1f30a":"wave","1f3e0":"house",
 "1f9c2":"salt","1f422":"turtle","1f41f":"fish","1f6b0":"tap","1f6ab":"noentry","1f6a2":"ship","1f513":"unlocked",
 "1f512":"locked","2754":"qwhite","2753":"qred","23f3":"hourglass","1f41a":"shell","1f9ca":"ice","1f6f6":"canoe",
 "1f9ad":"seal","1f6e2":"oildrum"};
(async () => { for (const [cp, n] of Object.entries(map)) {
  await sharp(src + cp + '.svg', {density: 512 * 72 / 36}).resize(512, 512).png().toFile(`assets/props/tw_${n}_${cp}.png`);
} console.log('ok', Object.keys(map).length); })();
