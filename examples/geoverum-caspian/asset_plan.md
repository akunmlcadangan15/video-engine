# Asset plan: Caspian Sea remake

The engine has no image/icon element on map segments (only regions, routes, pins, titles, figures, notes, headings); every 'ready_needs_icon_element' asset needs a new HyperFrames sprite element in video_engine/ (engine code, not done here). Photos/footage/historic maps can be used now as photo/footage segments.

3D render tools: No Blender or three.js on the box; geo-library/npm has only package.json (no node_modules). Rendering glTF/GLB to sprites needs a renderer that is not installed (installing = new download, not done). Kenney 3D Previews are 64x64 (too small on 1080x1920); Kenney nature-kit has 512 px Isometric and Side renders; Quaternius/KayKit/Poly Haven have no per-model stills.

## S1 0.0-5.4 s: title 'Caspian Sea' with yellow glow outline, 'Sea' struck through, 'Lake' gag

- `video-engine/examples/geoverum-caspian/assets/caspian_globe.jpg` (Public Domain (NASA BMNG); in_use): satellite globe basemap (already in timeline). Use: background 0-16.5 s.
- `open-assets/2d/emoji/fluent-emoji/assets/Cross mark/3D/cross_mark_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): red X. Use: optional small X beside the struck-through 'Sea' at 2.6-3.4 s.

## S1 5.6-8.9 s: LARGEST lake by surface area, yellow light burst at world scale

- `open-assets/2d/emoji/fluent-emoji/assets/Globe showing asia-australia/3D/globe_showing_asia-australia_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): 3D globe icon. Use: world-scale beat 5.6-8.4 s, small badge next to 'LARGEST'.
- `open-assets/maps/nasa/black-marble-2016/BlackMarble_2016_01deg.jpg` (Public Domain (NASA); ready): night-lights globe. Use: optional alternate world-scale basemap for the zoom-out.

## S1 9.0-10.4 s: 750 miles double arrow N-S

- `open-assets/2d/emoji/fluent-emoji/assets/Straight ruler/3D/straight_ruler_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): ruler. Use: badge at the arrow midpoint 9.0-10.4 s (the arrow itself is an engine route).

## S1 10.5-13.3 s: five country names, flat country fills

- `geo-library/geoboundaries/KAZ` (per country LICENSE.txt (mostly CC BY 4.0; geoBoundaries citation required); ready): ADM0/1 boundaries KAZ, RUS, TKM, IRN, AZE (vector). Use: alternative to Natural Earth country fills 10.5-13.3 s (current geo.json is Natural Earth, fine).
- `open-assets/2d/flags/flag-icons` (MIT; ready_needs_icon_element): country flag SVGs (kz, ru, tm, ir, az). Use: optional flag chips beside each country label 10.5-13.3 s.

## S1 12.5-16.4 s: storm flashes, cartoon breaking wave, red three-storey building for scale, '32 ft'

- `open-assets/2d/emoji/fluent-emoji/assets/Cloud with lightning and rain/3D/cloud_with_lightning_and_rain_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): storm cloud with lightning. Use: over the sea 12.5-14.0 s, with white frame flashes.
- `open-assets/2d/emoji/fluent-emoji/assets/High voltage/3D/high_voltage_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): lightning bolt. Use: flash accents 12.5-14.0 s.
- `open-assets/2d/emoji/fluent-emoji/assets/Water wave/3D/water_wave_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): breaking wave. Use: on the sea 14.0-16.4 s (reference: cartoon breaking wave).
- `open-assets/2d/emoji/fluent-emoji/assets/House/3D/house_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): small house (2-3 storeys). Use: scale prop beside the wave 14.4-16.4 s.
- `open-assets/3d/kenney-3d/city-kit-suburban/Previews/building-type-a.png` (CC0 (Kenney); needs_render): 3D suburban house, 64 px preview. Use: scale prop alternative; preview too small, needs re-render of building-type-*.glb.
- `asset-cache/footage/k10_px_rugged_coast_indonesia.mp4` (Pexels License; ready): waves on a rugged coast. Use: optional 1 s B-roll insert of real waves (not in reference).
- `asset-cache/sfx/am_lap.wav` (Epidemic Sound licence; ready): calm lapping sea waves loop. Use: ambience bed under 12.5-16.4 s (A02 ok: Epidemic).

## S2 16.5-22.7 s: 3D cross-section: sky, water layers, brown seabed, depth counter 3,363 ft, Burj Khalifa silhouette

- `geo-library/elevation/etopo2022/ETOPO_2022_v1_30s_N90W180_surface.tif` (Public Domain (NOAA NCEI, citation); data_only): global bathymetry: real Caspian depth profile (max ~1,025 m). Use: data for the cross-section profile line 16.5-22.7 s.
- `open-assets/textures/ambientcg/Ground037` (CC0 (ambientCG); ready): ground/seabed texture. Use: fill of the brown seabed band (any ambientCG Ground*/brown_mud*).
- `open-assets/2d/icons/tabler-icons/icons/outline/building-skyscraper.svg` (MIT (Tabler); ready_needs_icon_element): skyscraper outline. Use: white tower silhouette rising from the seabed 19.3-22.7 s (no Burj Khalifa asset owned).
- `open-assets/3d/kenney-3d/city-kit-commercial/Previews/building-skyscraper-c.png` (CC0 (Kenney); needs_render): 3D skyscraper, 64 px preview. Use: 3D alternative; needs render of building-skyscraper-*.glb.
- `open-assets/2d/emoji/fluent-emoji/assets/Straight ruler/3D/straight_ruler_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): ruler. Use: depth-counter badge 16.9-18.6 s.

## S3 22.8-25.4 s: 'Salty...', salt shaker pouring onto the lake

- `open-assets/2d/emoji/fluent-emoji/assets/Salt/3D/salt_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): salt shaker. Use: pouring over the lake 23.3-25.4 s (exact match).

## S3 25.5-28.6 s: 4 round icons: turtle/fish, waves, tap, salt

- `open-assets/2d/emoji/fluent-emoji/assets/Turtle/3D/turtle_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): turtle. Use: icon 1 at 26.0 s.
- `open-assets/2d/emoji/fluent-emoji/assets/Fish/3D/fish_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): fish. Use: icon 1 alt.
- `open-assets/2d/emoji/fluent-emoji/assets/Seal/3D/seal_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): Caspian seal stand-in. Use: icon 1 alt, topical (endemic Caspian seal).
- `open-assets/2d/emoji/fluent-emoji/assets/Water wave/3D/water_wave_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): wave. Use: icon 2 at 26.4 s.
- `open-assets/2d/emoji/fluent-emoji/assets/Potable water/3D/potable_water_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): tap filling a cup. Use: icon 3 at 26.8 s (exact match).
- `open-assets/2d/emoji/fluent-emoji/assets/Salt/3D/salt_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): salt shaker. Use: icon 4 at 27.2 s.

## S3 28.1-30.2 s: red X marks, dashed white arrows to the oceans, red tint on Eurasia

- `open-assets/2d/emoji/fluent-emoji/assets/Cross mark/3D/cross_mark_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): red X. Use: on each dashed arrow tip 28.4-30.2 s (arrows are engine routes).
- `open-assets/2d/emoji/fluent-emoji/assets/Prohibited/3D/prohibited_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): no-entry sign. Use: alternative to X.

## S3 30.0-40.8 s: Black Sea ring, Bosphorus, cyan route to Mediterranean, Gibraltar, Atlantic

- `video-engine/examples/geoverum-caspian/assets/caspian_straits.jpg` (Public Domain (NASA BMNG); in_use): straits basemap (in timeline). Use: 30.0-40.8 s.
- `open-assets/2d/emoji/fluent-emoji/assets/Ship/3D/ship_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): ship. Use: rides the route tip Black Sea -> Atlantic 34.0-40.8 s.
- `open-assets/3d/kenney-3d/watercraft-kit/Previews/ship-cargo-a.png` (CC0 (Kenney); needs_render): 3D cargo ship, 64 px. Use: route rider alternative; needs render of ship-cargo-a.glb.
- `open-assets/2d/kenney-2d/pirate-pack/PNG/Retina/Ships` (CC0 (Kenney); ready_needs_icon_element): top-down sailing ship sprites 66x113. Use: top-down route rider (small).
- `asset-cache/footage/k04_px_tallship_9x16.mp4` (Pexels License; ready): tall ship at sea, vertical. Use: optional B-roll at 'into the Atlantic' 38.9-40.2 s.

## S3-S4 41.3-43.6 s: golden padlock open at 42 s, closed after; red grade 'landlocked'

- `open-assets/2d/emoji/fluent-emoji/assets/Unlocked/3D/unlocked_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): open padlock. Use: 41.3-42.0 s.
- `open-assets/2d/emoji/fluent-emoji/assets/Locked/3D/locked_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): closed golden padlock. Use: 42.0-43.6 s (exact match).

## S4 44.4-49.0 s: 'LAKE' typed on, question marks + salt badge

- `open-assets/2d/emoji/fluent-emoji/assets/White question mark/3D/white_question_mark_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): white ?. Use: 46.8-49.0 s (exact match).
- `open-assets/2d/emoji/fluent-emoji/assets/Red question mark/3D/red_question_mark_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): red ?. Use: alt.
- `open-assets/2d/emoji/fluent-emoji/assets/Salt/3D/salt_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): salt. Use: salt badge 46.8-49.0 s.
- `sfx-library/user-typing/keystrokes` (user-owned (Arundaya recordings); ready): keystroke one-shots. Use: per-letter ticks for 'LAKE' 45.0-46.6 s and 'Salty...' 24.0-25.4 s.

## S5 49.0-59.0 s: whip to Eurasia, Paratethys extent shrinking, split into basins

- `video-engine/examples/geoverum-caspian/assets/caspian_paratethys.jpg` (Public Domain (NASA BMNG); in_use): Eurasia basemap (in timeline). Use: 49.0-59.0 s.
- `open-assets/maps/historical-maps/asia/Iran Turan map 1843.jpg` (Public domain (Stieler 1843); ready): historic map with the Caspian. Use: optional period-map wipe at 'once part of a much larger body' 49.4-51.5 s.
- `open-assets/2d/emoji/fluent-emoji/assets/Hourglass not done/3D/hourglass_not_done_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): hourglass. Use: 'over millions of years' 53.0-53.9 s.
- `open-assets/2d/emoji/fluent-emoji/assets/Spiral shell/3D/spiral_shell_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): fossil shell. Use: topical 'ancient sea' badge.
- `asset-cache/sfx/wh_soft.wav` (Epidemic Sound licence; ready): soft whoosh. Use: whip 49.0 s.

## S5 59.8-64.0 s: white salt crystal cubes in the lake, blue rivers (Volga, Ural) carrying salt

- `open-assets/2d/emoji/fluent-emoji/assets/Ice/3D/ice_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): translucent cube. Use: salt-crystal cubes 59.8-64.0 s (closest owned cube; tint white).
- `open-assets/2d/emoji/fluent-emoji/assets/Salt/3D/salt_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): salt. Use: alt.
- `asset-cache/sfx/am_river.mp3` (Epidemic Sound licence; ready): river ambience. Use: under 61.6-64.0 s.

## S5 64.0-68.4 s: ancient people with spears and boats on the west shore

- `open-assets/maps/historical-maps/world/Herodotus Map 430 BCE.jpg` (Public domain; ready): Herodotus' world map (Caspian drawn enclosed). Use: 'to ancient people...' 63.6-66.1 s, best topical fit.
- `open-assets/2d/icons/game-icons/icons/lorc/spears.svg` (CC BY 3.0 (Lorc, game-icons.net); ready_needs_icon_element): spears silhouette. Use: on the west shore 65.0-68.4 s.
- `open-assets/2d/icons/game-icons/icons/delapouite/caveman.svg` (CC BY 3.0 (Delapouite, game-icons.net); ready_needs_icon_element): primitive person silhouette. Use: figure beside the spears.
- `open-assets/2d/icons/game-icons/icons/delapouite/raft.svg` (CC BY 3.0 (Delapouite, game-icons.net); ready_needs_icon_element): raft. Use: boat on the shore.
- `open-assets/2d/emoji/fluent-emoji/assets/Canoe/3D/canoe_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): canoe. Use: boat alternative.
- `open-assets/3d/kenney-3d/pirate-kit/Previews/boat-row-small.png` (CC0 (Kenney); needs_render): 3D rowing boat, 64 px. Use: needs render of boat-row-small.glb.
- `open-assets/3d/quaternius/modular-character-outfits-fantasy` (CC0 (Quaternius); needs_render): Male/Female Peasant and Ranger glTF characters (no per-model stills). Use: ancient people as 3D figures; needs render.
- `open-assets/2d/kenney-2d/medieval-rts/PNG/Retina/Unit` (CC0 (Kenney); ready_needs_icon_element): 24 top-down medieval unit sprites. Use: tiny people from above.

## S5 67.4-72.7 s: giant fly-through title, LAKE + arrow down

- `video-engine/examples/geoverum-caspian/assets/caspian_globe.jpg` (Public Domain (NASA BMNG); in_use): basemap (in timeline). Use: 67.4-72.7 s.

## Topical assets without a reference slot

- `open-assets/2d/emoji/fluent-emoji/assets/Seal/3D/seal_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): Caspian seal (endemic). Use: topical: wildlife beat, no reference slot.
- `open-assets/2d/emoji/fluent-emoji/assets/Oil drum/3D/oil_drum_3d.png` (MIT (Microsoft Fluent Emoji); ready_needs_icon_element): oil barrel. Use: topical: Caspian oil (Baku), no reference slot.
- `asset-cache/photos/seria52.png` (Public domain; ready): oil field derricks c.1952 (Brunei, not Caspian). Use: generic oil-derrick look only; caption must not claim Caspian.
- `open-assets/2d/kenney-2d/fish-pack/PNG/Double/fish_grey_long_a.png` (CC0 (Kenney); ready_needs_icon_element): long grey fish 128 px. Use: sturgeon/caviar stand-in.
- `open-assets/maps/historical-maps/middle-east/Transcaucasia ~1924~.jpg` (Public domain; ready): 1924 map of the Caucasus/Caspian west shore. Use: period map insert.
- `open-assets/maps/historical-maps/asia/1855 Colton map of Persia ^ Arabia ( Saudi Arabia, Iraq, Israel and Afghanistan ) - Geographicus - PersiaArabia-c-55.jpg` (Public domain; ready): 1855 Persia map incl. south Caspian. Use: period map insert.
- `open-assets/maps/historical-maps/asia/CEM-15-Asia-Mercator-1595-Tartaria-2532.jpg` (Public domain; ready): Mercator 1595 Tartaria with early Caspian. Use: 'ancient name' beat.
- `open-assets/maps/historical-maps/world/Orbis veteribus notus 1763.jpg` (Public domain; ready): the world known to the ancients. Use: 'ancient people' alt.
- `open-assets/3d/poly-haven-models/ship_pinnace` (CC0 (Poly Haven); needs_render): 17th-c. pinnace sailing ship glTF (no stills). Use: ancient/period boat; needs render.
- `open-assets/3d/poly-haven-models/coastal_cliff_01` (CC0 (Poly Haven); needs_render): coastal cliff glTF. Use: shore relief; needs render.
- `asset-cache/footage/px_30820445.mp4` (Pexels License; ready): red arid desert aerial. Use: Turkmen/Kazakh desert shores B-roll.
- `asset-cache/footage/v_yantarny.webm` (CC BY 3.0 (Alexander Grebenkov); ready): seashore waves (Baltic). Use: wave B-roll, credit required.
- `asset-cache/photos/k03_carracks_pd.jpg` (Public domain; ready): carracks off a rocky coast (painting). Use: period ships.
- `open-assets/textures/aged-paper` (per file: PD / CC0 / CC BY 2.0 (credit); ready): aged paper scans. Use: paper texture under period-map inserts.

## Gaps

- Burj Khalifa: no owned asset; generic skyscraper silhouette only
- ancient people with spears and boats: only silhouettes (game-icons, CC BY 3.0) or unrendered 3D figures; no illustration
- salt crystal cubes: Fluent 'Ice' cube is the closest; no white salt-crystal asset
- cartoon breaking wave: Fluent 'Water wave' icon only
- sturgeon, oil rigs in the Caspian: no specific asset (generic fish, PD Brunei oil field photo)
- 3D-to-sprite: no Blender/three.js on the box
- icon element missing in engine

## Candidates per shot

- S1: 14
- S2: 5
- S3: 14
- S3-S4: 2
- S4: 4
- S5: 17
- no slot: 14
