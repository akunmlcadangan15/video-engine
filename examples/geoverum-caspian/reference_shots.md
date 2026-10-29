# Reference shot map: "Why the Caspian Sea is Not a Sea" (GeoGlobeTales Short)

Source: `/workspace/style-lab/geoverum/xGmyqt_Ild0.mp4`, 72.79 s, 1080x1920, 30 fps, 2182 frames.
Full data: `reference_shots.json`. Method: scene scores checked frame by frame, plus ORB/RANSAC camera motion per 0.5 s. Codex (gpt-6-astra, max effort, read-only) agreed on the boundaries. Its run hit the usage limit before it wrote its own JSON.

| Shot | Frames | Time (s) | Picture | What happens | Camera (ln zoom) |
|---|---|---|---|---|---|
| S1 | 0-495 | 0.00-16.50 | Satellite globe | Title on the lake, "Lake" gag, largest lake, 750 mi, 5 countries, 32 ft waves, storm flashes | in +1.7 / out -1.8 (5-7 s) / in +2.0 (8-10 s) / in +2.0 (13-14.5 s) |
| S2 | 495-683 | 16.50-22.77 | 3D cross-section (continuous tilt, no cut) | Depth counter 3,363 ft, Burj Khalifa 2,717 ft | small push-ins, then zoom-blur out |
| S3 | 683-1273 | 22.77-42.43 | Satellite (zoom blur in) | Salty, recap, no ocean link, Black Sea, Bosphorus, Mediterranean, Gibraltar, Atlantic, back to Caspian | alternating out/in legs every ~2 s |
| S4 | 1273-1471 | 42.43-49.03 | Same map, red grade (hard cut) | Lock / landlocked, LAKE, "so why is it salty?" | out -0.9, in +1.35 |
| S5 | 1471-2182 | 49.03-72.73 | Satellite (zoom-blur whip) | Paratethys, Black Sea/Caspian split, rivers bring salt, ancient people, giant fly-through "Caspian Sea" title, LAKE + arrow | out -1.2, in +1.6, in +2.8, out -3.4 |

The reference has 4 boundaries: a 3D tilt at 16.5 s, a zoom blur at 22.767 s, a red grade cut at 42.433 s and a zoom-blur whip at 49.033 s. Codex counts 4 continuous takes, because the red grade at 42.433 s keeps the same registered map. The remake uses 5 segments with the same frame boundaries, because the engine can only change picture treatment at a segment boundary.
