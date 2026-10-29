# Unused assets: Caspian v3

Rule: keep an asset only if it directly supports what the narration says in that beat.

## Dropped in the v3 prune (they were placed before)

| Asset | Was at | Reason dropped |
|---|---|---|
| tw_bolt (Twemoji U+26A1) | S1 12.7 s | Repeats the storm icon; narration only says "powerful storms" |
| tw_house (Twemoji U+1F3E0) | S1 14.4 s | Duplicate of the 3D house (Kenney building-type-a), which carries "three-story building" |
| tw_turtle (Twemoji U+1F422) | S3 26.0 s | Narration does not mention wildlife |
| tw_tap (Twemoji U+1F6B0) | S3 26.8 s | A tap suggests fresh water, which contradicts "salty"; not narrated |
| tw_noentry (Twemoji U+1F6AB) | S3 28.8 s | Repeats the red cross for "except one thing" |
| Kenney watercraft ship-cargo-a.glb | S3 36.0 s | A second ship for one route line; the Bosporus ship already carries it |
| kenney_pirate_ship_2.png | S3 37.5 s | Decorative only, not narrated (also the F02 upscale warning) |
| tw_unlocked (Twemoji U+1F513) | S3 41.2 s | Plays over "Atlantic Ocean / The Caspian however"; only the closed lock matches "landlocked" |
| tw_qwhite (Twemoji U+2754) | S4 46.8 s | Repeats the red question mark |
| tw_ice x3 (Twemoji U+1F9CA) | S5 59.8-60.2 s | Ice cubes are not salt; narration says "salt", and tw_salt is kept |
| medievalUnit_01/05/09.png (Kenney) | S5 64.2 s | Medieval soldiers; narration says "ancient people", and the caveman carries that (also the F02 warnings) |
| gi_spears (Lorc, game-icons) | S5 64.5 s | Weapons are not narrated |
| tw_canoe (Twemoji U+1F6F6) | S5 64.6 s | A third boat; one boat (the 3D rowboat) is enough for "seemed like a sea" |
| gi_raft (Delapouite, game-icons) | S5 64.7 s | Same as the canoe: a redundant boat |
| Kenney pirate-kit ship-small.glb | S5 65.0 s | A pirate ship is anachronistic for "ancient people"; redundant boat |

## Never placed

| Asset | Reason |
|---|---|
| Microsoft Fluent Emoji 3D PNGs (*_3d.png in assets/props, incl. seal and oil drum) | MIT licence is not in the R14 allowlist on master 7dbd017; replaced by Twemoji (CC BY 4.0) |
| tw_seal, tw_oildrum (Twemoji) | Seals and oil are not narrated |
| fish_grey_long_a.png (Kenney) | Sturgeon is not narrated |
| caspian_deep / globe / paratethys / relief / straits .jpg | Superseded by the three v3 maps (basin, westward, eurasia) that carry geo.json registration |
| Historic maps (Transcaucasia 1924, Colton 1855, Mercator 1595, Orbis veteribus 1763) | No narrated "old map" beat, and they would need a picture-in-picture segment the beat plan does not use |
| Poly Haven coastal_cliff_01 | No shore-relief beat in the narration |
| Footage px_30820445.mp4 (desert), v_yantarny.webm (waves); photos seria52.png (Brunei oil), k03_carracks_pd.jpg | Not narrated (desert, oil) or would break the one-map continuity that the reference keeps; the reference has no live footage |
| aged-paper textures | Only needed under historic-map inserts, which are not used |
| flag-icons | MIT licence (R14); the countries are shown as region fills and captions |
| Quaternius figures | No renderable 2D export; the game-icons caveman covers "ancient people" |
| Non-Epidemic SFX (sfx-library) | Brief: music and SFX from Epidemic only |
