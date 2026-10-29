# Reference frames: xGmyqt_Ild0.mp4 (72.79 s, 30 fps, 2182 frames)

Data: `reference_frames.json` (built by `tools/analyze_reference.py` + `tools/build_reference_frames.py`).

## Shots and camera (measured every frame)

| shot | t0-t1 s | scale at end | zoom ln/s mean (p05..p95) | pan w/s median | rot deg/s median (p95) | tilt/s median, p95 (v), p95 (h) | parallax px/s | still frames |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.0-16.5 | 15.776 | 0.168 (-0.922..1.949) | 0.0082 | 5.055 (27.0) | 0.2185, 1.8571, 0.4571 | 178.6 | 80/494 |
| 2 | 16.5-22.767 | 0.298 | -0.187 (-2.293..0.769) | 0.0077 | 1.007 (48.401) | 0.0369, 5.785, 0.7281 | 174.7 | 74/185 |
| 3 | 22.767-42.433 | 0.321 | -0.058 (-1.595..1.854) | 0.0367 | 4.878 (27.23) | 0.2748, 2.0833, 0.4651 | 207.6 | 52/589 |
| 4 | 42.433-49.033 | 1.32 | 0.08 (-0.774..1.396) | 0.0063 | 4.111 (19.128) | 0.0601, 1.8854, 0.2792 | 167.1 | 54/189 |
| 5 | 49.033-72.733 | 0.506 | -0.025 (-1.521..1.47) | 0.0116 | 2.224 (25.34) | 0.112, 1.0724, 0.5074 | 167.5 | 51/702 |

## Transitions

- 16.5 s: tilt-zoom into the water surface, then cut to the cross-section diagram (zoom -1.4 .. -4.9 ln/s over frames 491-501, keystone rate up to 1.8/s, blur falls to 210)
- 22.767 s: radial zoom blur out of the diagram (0.4 s), cut to satellite close-up (blur 112 (sharpness drop) at the cut, zoom -0.4 ln/s before it)
- 42.433 s: hard cut into a full-frame red grade (landlocked) (hist change 1.70, 18 inliers)
- 49.033 s: whip pan with motion blur (0.3 s) to the Eurasia view (blur 41-68 for frames 1468-1479, zoom |53| ln/s spike)

## Region fills (sampled colour)

- 3.0 s Caspian Sea: bright blue, opaque-ish, yellow glow edge `#2779b4`
- 12.0 s Russia: flat red, opaque `#bf1a0e`
- 12.0 s Kazakhstan: flat brown `#984a16`
- 12.0 s Turkmenistan: flat orange `#dea427`
- 12.0 s Iran: flat green `#5a9e1b`
- 12.0 s Azerbaijan: flat green/teal `#958060`
- 24.0 s Caspian Sea: light blue semi-transparent `#778d8e`
- 29.0 s Eurasia (no outlet): red semi-transparent wash `#d7100b`
- 31.0 s Black Sea: bright blue glow `#0588ed`
- 36.5 s Mediterranean Sea: bright cyan-blue glow `#34afdb`
- 42.8 s whole frame: red grade `#480b00`
- 52.0 s Paratethys: pale blue semi-transparent `#374a19`
- 61.0 s Caspian Sea: blue `#558cab`

## Texts (non-caption)

- 0.8-5.4 s: "Caspian Sea" (white bold sans, blue outer glow, inside the lake)
- 2.6-3.4 s: "Sea (struck through)" (red strike line drawn across 'Sea')
- 3.5-5.4 s: "Caspian Lake" ('Lake' in yellow bold)
- 6.0-8.4 s: "LARGEST lake" (yellow heavy caps 'LARGEST' + white 'lake', glow)
- 7.0-8.4 s: "by surface area" (white small bold)
- 8.4-8.9 s: "LARGEST lake by surface" (same, flies toward the camera (fly-through))
- 9.0-10.4 s: "750 miles (1,200 km)" (white bold rotated along a double arrow N-S)
- 10.5-13.3 s: "Russia / Kazakhstan / Turkmenistan / Iran / Azerbaijan" (white bold country names with shadow)
- 14.6-16.4 s: "32 ft" (white small, beside the building prop)
- 16.9-22.6 s: "counter 122 ft (37 m) -> 3,363 ft (1,025 m)" (yellow bold ft + white (m), counts up 16.9-18.6)
- 19.3-22.6 s: "Burj Khalifa 2,717 ft" (white bold + yellow figure)
- 24.0-25.4 s: "Salty..." (white heavy, written letter by letter in the lake)
- 30.8-32.8 s: "Black Sea" (white bold inside a glowing blue Black Sea, yellow ellipse ring)
- 33.0-35.6 s: "Black Sea (big)" (huge cyan-white bold in the sea, flies past)
- 33.5-35.6 s: "Bosphorus" (small white bold label)
- 35.5-37.0 s: "Mediterranean Sea" (huge cyan bold, flies past at the bottom)
- 38.0-40.8 s: "Atlantic Ocean" (cyan-white bold label, two lines)
- 45.0-46.6 s: "LAKE" (yellow bold caps, typed on letter by letter)
- 51.2-53.4 s: "Paratethys" (white italic serif-ish with a curved leader)
- 68.6-69.0 s: "Caspian Sea (giant)" (huge white/blue glow title flying out past the camera)
- 69.0-72.79 s: "Caspian Sea" (white bold blue glow over the lake)
- 71.4-72.79 s: "LAKE" (yellow bold caps with a small arrow down to the lake)

## Elements

- 0.7-9.0 s: yellow glow outline around the Caspian
- 6.0-8.4 s: yellow light burst on the Caspian at world scale
- 9.0-10.4 s: white double-headed arrow N-S along the lake
- 14.0-16.4 s: cartoon breaking wave (blue/white) on the sea
- 14.4-16.4 s: red three-story building icon for scale
- 16.5-22.7 s: cross-section: sky, water layers, brown seabed; vertical depth line
- 19.3-22.7 s: white Burj Khalifa silhouette rising from the seabed
- 23.3-25.4 s: salt shaker pouring onto the lake
- 26.0-28.6 s: 4 round icons: turtle/fish, waves, tap, salt (what a sea has)
- 28.4-30.2 s: red X marks + dashed white arrows to the oceans, red tint on Eurasia
- 30.8-32.6 s: yellow ellipse ring around the Black Sea
- 34.0-40.8 s: glowing cyan route line Black Sea -> Bosporus -> Mediterranean -> Gibraltar
- 41.3-43.6 s: golden padlock (open at 42, closed after)
- 42.43-43.6 s: full-frame red grade, Caspian outlined in glowing red
- 43.6-46.6 s: Caspian with red glow outline on satellite
- 46.8-49.0 s: white question marks + salt badge icon over the lake
- 51.3-56.0 s: Paratethys extent in light blue, shrinking (water levels change)
- 59.8-64.0 s: white salt crystal cubes inside the lake
- 61.6-64.0 s: blue river lines (Volga, Ural ...) carrying salt cubes into the lake
- 65.0-68.4 s: ancient people with spears and boats on the west shore

## Captions (90 chunks; rounded bold sans (Roboto-like), white, soft dark drop shadow, no box, centre y 1366 px)

- 0.2-1.5: Why is the Caspian Sea
- 1.6-2.2: actually
- 2.3-3.2: not a sea
- 3.4-4.3: but a lake
- 4.4-4.7: And not just
- 4.9-5.4: any lake
- 5.6-6.2: but the largest
- 6.2-6.9: lake on Earth
- 7.0-7.9: by surface area
- 8.1-8.8: It stretches for about
- 9.0-10.3: 750 miles
- 10.4-11.3: and five countries
- 11.3-12.4: share its shores
- 12.5-13.5: Powerful storms here
- 13.6-14.0: bring waves
- 14.2-14.7: as tall as
- 14.8-16.0: a three-story building
- 16.1-16.8: and in places
- 16.9-17.2: it's over
- 17.4-18.1: three thousand
- 18.3-18.9: feet deep
- 19.1-19.4: which is enough
- 19.4-19.7: to cover the world's
- 19.7-21.0: tallest building
- 21.0-22.6: And here's what makes
- 22.7-23.4: it even more confusing
- 23.5-24.1: the water here
- 24.2-25.1: is salty
- 25.1-25.8: It seems to have
- 25.9-26.4: everything a sea
- 26.5-27.0: should have
- 27.1-27.5: except
- 27.6-28.2: one thing
- 28.3-29.2: a natural connection
- 29.4-30.2: to the ocean
- 30.3-30.7: If you look
- 30.8-31.4: at the Black Sea
- 31.5-32.3: it might seem cut off
- 32.4-32.8: too
- 32.8-33.3: But through
- 33.4-33.9: the Bosporus
- 34.0-34.9: and a series of seas
- 35.0-35.7: and straits
- 35.9-36.2: it reaches
- 36.3-37.0: the Mediterranean
- 37.1-37.8: then passes through
- 37.9-38.8: the Strait of Gibraltar
- 38.9-40.2: into the Atlantic Ocean
- 40.4-40.8: The Caspian
- 41.0-41.4: however
- 41.5-42.3: is completely
- 42.4-43.1: landlocked
- 43.2-43.6: which is why
- 43.7-44.4: it's geographically
- 44.6-45.1: classified
- 45.2-46.0: as a lake
- 46.1-46.4: But then
- 46.6-47.4: why is the water here
- 47.5-48.3: salty
- 48.4-48.7: You see
- 48.8-49.3: the Caspian
- 49.4-49.9: was once part
- 50.1-51.0: of a much larger body
- 51.1-51.5: of water
- 51.6-51.8: called
- 51.9-52.8: the Paratethys
- 53.0-53.9: Over millions of years
- 54.0-54.8: the land rose
- 55.0-55.9: water levels changed
- 56.1-56.7: and this huge
- 56.8-57.4: body of water
- 57.5-57.8: split
- 57.9-59.0: into separate basins
- 59.1-60.1: Some of the Caspian's salt
- 60.2-60.7: comes from that
- 60.9-61.5: ancient sea
- 61.6-62.1: while rivers
- 62.2-63.4: continue to bring in more
- 63.6-64.4: And to ancient people
- 64.7-65.2: this huge body
- 65.4-66.1: of salty water
- 66.2-66.7: really seemed
- 66.8-67.6: like a sea
- 67.7-68.4: The name stuck
- 68.5-68.9: which is why
- 69.0-69.4: we still call it
- 69.5-70.2: a sea today
- 70.3-70.6: even though
- 70.7-71.5: geographically
- 71.6-72.7: it's a giant lake
