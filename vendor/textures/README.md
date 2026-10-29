# Engine textures

Map-grade layers of profile `[print]` (paper surface). HyperFrames builds copy only the files the scene uses
(`print.paper_texture`, `print.grain`) and hash them into the segment cache key.

| File | Size | Used for | Licence | Source |
|---|---|---|---|---|
| `paper-F1E8D4.jpg` | 1024 x 1024 RGB, tiles | Paper texture, multiplied over the map at `print.paper_alpha` | CC0 1.0 (ambientCG, https://docs.ambientcg.com/license/) | ambientCG `Paper003_1K-JPG_Color.jpg` (sha256 78a89e7a1f0da87ec00bfcd846d9cb9cb84af2180518186d25e9988015b77f9a), via open-assets `textures/ambientcg/Paper003` |
| `grain-512.png` | 512 x 512 L, tiles | Film grain, multiplied over the map at `print.grain_alpha` (mean darkening 2.5 %) | CC0 1.0 (generated for this repository) | Generated: `255 - abs(N(0, 1)) x 8`, numpy `default_rng(1945)` |

`paper-F1E8D4.jpg` derivation: the source converted to greyscale and normalised to mean 1, contrast x 1.6 around the
mean, clipped to <= 1.08, multiplied by the paper tint RGB (241, 232, 212) = `#F1E8D4`, saved as JPEG quality 90.

Adding a texture: CC0 or public domain only, a row above with the source file and its hash.
