# Changelog

Perubahan penting pada mesin. Format mengikuti [Keep a Changelog](https://keepachangelog.com/id-ID/1.1.0/).
Versi paket belum diberi nomor rilis (`0.1.0`), jadi semua catatan ada di bagian "Belum dirilis".

Dalam dokumen ini, **gate** berarti pemeriksa otomatis yang dapat memblokir build, **profil** berarti aturan per
channel yang disimpan sebagai file TOML, **`require`** berarti gate tersebut wajib memblokir bila gagal, dan
**`advisory`** berarti gate tersebut hanya memberi peringatan. Definisi lengkap ada di `docs/GATES.md`.

## Belum dirilis

### Ditambahkan

- **`fit` pada kunci kamera** (alias `keep_in_frame`): daftar label atau indeks pin. Dorongan dan pulsa selama tunggu pada kunci itu diperkecil sampai setiap pin tetap di dalam area aman (margin 24 px pada kanvas referensi) untuk seluruh tunggu. Contoh: `"fit": ["Kupang", "Darwin"]`. Pengujian: `tests/test_fit_pins.py`.
- **`[gates.geo] coast_km`** (bawaan 25 km, tidak masuk hash profil pada nilai bawaan). Pin dalam jarak ini dari tepi daratan lolos pemeriksaan darat G02. Masker darat memakai berkas Natural Earth yang paling halus yang ada (`ne_10m`, lalu `ne_50m`, lalu `ne_110m`).

- **Pin berkas suara yang masih dikunci proyek.** LRU (`VIDEO_ENGINE_CACHE_MAX_MB`, bawaan 4096) tidak lagi menghapus musik atau SFX yang disebut `out/build_manifest.json`. Pemilik pin ada di `<cache>/sound/pins.json`. Salinan `<project>/out/locked-sounds/<sha256><ext>` dipakai bila berkas cache sudah hilang dan hash-nya masih cocok. `engine cache pin --project P` mencatat pin; `engine cache unpin --project P` melepas pin (salinan lokal tetap sampai dihapus). Pengujian: `tests/test_sound_pins.py`.
- **Provenance lisensi untuk `bgm_path`.** Sidecar di samping berkas, atau entri sound lock, dengan `source=epidemic`, id trek, dan `sha256` yang cocok membuat berkas itu lolos gate A02. Pengujian: `tests/test_bgm_provenance.py`.
- **Bitrate AAC dapat diatur.** `[audio] aac_bitrate` (64-320 kbps, bawaan 192). `[render] aac_bitrate` menimpanya, termasuk `profile_overrides.render`. Pengujian: `tests/test_aac_bitrate.py`.
- **Isyarat SFX manual dan jenis `rumble`.** `audio_chain.sfx`: satu `kind` atau `asset`, satu `at_frame` atau `time_s`, plus `gain_db`. Pengujian: `tests/test_manual_sfx.py`.
- **Audio pada `engine draft-preview`.** Narasi, atau campuran musik dan SFX bila dapat direncanakan dari berkas yang sudah ada. Laporan memuat `audio`: `mix`, `narration`, atau `none`.
- **Ornamen heading opsional.** `[print] heading_ornaments` bawaan false (teks polos). `true`, atau `ornaments` pada overlay, atau `heading_ornaments` pada adegan, mengembalikan garis dan wajik.
- **Termometer dapat mulai dari nilai sebelumnya.** `from` atau `continue_from` (nilai termometer sebelumnya pada adegan yang sama). Tanpa keduanya, isi tetap naik dari `min`.

- **Penemuan perkakas dan akar aset yang portabel** (`video_engine/discover.py`): mesin dan `engine doctor`
  memakai satu resolver. Urutan pencarian default: override lingkungan > akar npm global (`NODE_PATH`,
  `npm root -g`, direktori npm khas per platform) > `PATH` > direktori cache/data standar platform (XDG,
  `~/Library`, `%LOCALAPPDATA%` / `%APPDATA%`), direktori toolchain pengguna
  (`<user-data>/video-engine/toolchain`), cache browser Puppeteer, dan lokasi pemasangan yang umum >
  `vendor/` (cadangan, bukan kunci). `prefer_vendor=True` mengembalikan urutan lama untuk kebijakan `pinned`.
  Akar aset memakai default di bawah direktori data pengguna; path khusus mesin diletakkan di `engine.env`
  lokal. Pengujian (lingkungan palsu per platform): `tests/test_discover.py`.

- **`extends` pada profil**: sebuah profil dapat dibangun di atas profil lain (`extends = "geoverum"`). Urutan
  lapisan: default model < `default.toml` < rantai `extends` (tiap profil: file `include`, lalu filenya) <
  `profile_overrides` proyek. Loop, basis yang tidak ada, dan `extends` di file `include` adalah error.
  Pengujian: `tests/test_profile_layers.py`.
- **Cascade `[type]` yang sungguh berjalan**: ukuran yang ditulis channel di `[type]` kini mengalir ke
  `[theme] title_px` / `pin_label_px`, `[print] title_px`, dan `[layout.sizes] title` / `pin_label` yang tidak
  ditulis channel (`profiles.TYPE_CASCADE`). Sebelumnya tabel ini divalidasi tetapi tidak dibaca kode mana pun.
- **Placeholder di aturan kreatif**: baris `rules_file` boleh mengutip nilai profil sebagai `{{tabel.kunci}}`;
  mesin mengisinya dari profil efektif, sehingga profil yang berbagi satu `creative.md` mengutip angkanya sendiri.
- **`engine profile [NAMA] [--origin] [--table T]`** mencetak profil hasil resolusi dan file asal tiap kunci
  (`profiles.origins`); **`engine schema timeline|profile`** mencetak JSON Schema kontrak dari model Pydantic.
- **Validasi kunci `caption_style` saat muat** (`profiles.check_caption_style`): kunci yang tidak dikenal (dengan
  saran kunci terdekat), tipe nilai yang salah, dan `slot` yang tidak dikenal ditolak untuk profil dan timeline.
  `[reference_style.overlap]` kini bertipe, dan nama grup `[reference_style.*]` tetap.
- **Default per kunci untuk `[legibility]` dan `[sound]`**: tabel parsial kini sah dan sisanya memakai baseline
  mesin (sebelumnya tabel `[legibility]` parsial gagal dengan 52 error).
- **`docs/ARCHITECTURE.md`**: model kerja mesin untuk engineer dan agent AI apa pun (dua mode, urutan resolusi,
  alur data, peta modul, kontrak lapisan AI, jebakan). Menggantikan `CLAUDE.md` yang khusus satu agent.

- **Opsi gaya tema baru** (`[theme]`, tidak masuk hash profil saat bernilai default, jadi tampilan engine tidak
  berubah): `vivid_chroma_scale`/`vivid_l` (warna entitas jenuh dengan hue tetap hanya di peta satelit gelap;
  `entities.vivid_colour`, `entities.scene_colours`; gate E01 membandingkan fill dengan warna scene),
  `title_entrance = "track"` (huruf title merapat dengan lebar tetap; keluar merenggang pada exit layout),
  `title_anchor = "region"` (title scene tanpa beats masuk di atas wilayah `title_entity`),
  `label_box_entrance = "type"` + `label_type_cps` (pil tumbuh bersama teks yang diketik), `label_box_case`,
  `label_box_tilt_deg`, `figure_pop = "back"` (pop 0,45 s tanpa overshoot elastis; kembaran Python
  `hyperframes.FIGURE_POP_S`), dan `figure_accent` (warna angka tanpa entitas). Pengujian:
  `tests/test_geoverum_style.py` (termasuk render Chrome: lebar title konstan saat tracking-in, pil terketik,
  angka tidak keluar frame).

- **Opsi gaya referensi `[reference_style.*]` di profil Geoverum** (portrait + landscape), hasil bedah
  frame-per-frame sembilan video referensi 02–10 (17.792 frame dilihat seluruhnya; lihat
  `docs/REFERENCE-STYLE.md`). Opsi mencakup: isian hatch per fase dan fill dua-lapis, label ketikan/banner/
  tulisan tangan/stempel, count-up dan termometer, speech bubble dan karakter kartun, adegan dokumen/ilustrasi/
  footage/penampang, transisi zoom-blur/warp/whip/flash/wipe, dan satu shot kontinyu gaya fly-through.
  Semua opsi adalah **pilihan kreator per segmen**, bukan keharusan; 12 gate wajib tidak tersentuh dan tidak ada
  gate baru. Prinsip tumpang tindih ditegaskan di profil (`[reference_style.overlap] allowed = true`,
  `rule = "readability"`, `forbid = []`): elemen boleh bertumpuk, syaratnya hanya keterbacaan.
  `ProfileData` menerima tabel `reference_style` (dict) dan `Profile.reference_style` menampilkannya.
  Pengujian: `tests/test_reference_style.py`.

- **Pemisahan tingkat keparahan gate (`KERNEL` vs `LINT`) + lapisan kompatibilitas `gate_conformance`** (`video_engine/gate_registry.py`,
  `video_engine/gate_conformance.py`, `video_engine/gates/severity.py`). Penegakan gate saat *runtime* menggunakan
  `KERNEL_GATE_IDS` (8 gate integritas inti: `T02`, `M01`, `W01`, `L22`, `R38`, `A01`, `G01`, `M03`, juga diekspor
  sebagai alias `MANDATORY_GATE_IDS`) yang selalu memblokir *build* bila gagal, serta `LINT_GATE_IDS` (50 gate gaya,
  tata letak `L01`–`L10`, margin, dan kolisi) yang secara bawaan memberi peringatan dan menerbitkan video (*publish
  with warnings*) kecuali dicantumkan di `[gates] require` atau `gates.require`. Gate teknis di luar kedua daftar
  tersebut (`F01`, serta `AS04` bila dinaikkan ke `fail`) memblokir *release build* kecuali diturunkan lewat
  `[gates] advisory` atau `--mode draft`, sedangkan 12 gate di `NEVER_ADVISORY` ditolak bila dimasukkan ke
  `[gates] advisory`. Properti `Gate.tier` (`mandatory` / `profile` / `flexible`) dan helper di
  `video_engine/gate_conformance.py` dipertahankan sebagai lapisan kompatibilitas pelaporan untuk `engine gates`
  dan indeks Markdown di `docs/GATES.md` tanpa menolak pemuatan profil ketika tabel opsional tidak ditulis.
  Profil yang ada tidak berubah aturannya — misalnya Geoverum tetap `require = []`. Pengujian: `tests/test_gate_conformance.py`.

- **Kamera 3D penuh (opt-in)** untuk scene peta HyperFrames: `gsap_scene_config.camera_mode = "3d"` (atau profil
  `[camera] mode`) dengan kunci `camera3d` (`eye`/`target` [u, v, h], `roll`, `fov`, `move`, `hold`). Sampler murni
  per frame (`video_engine/camera_full3d.py`: Catmull-Rom ber-parameter panjang busur, SLERP quaternion, fov linear);
  state tiap frame dihitung sekali di Python dan dirender Three.js (`hf/scene3d.js`) sebagai bidang peta bertekstur,
  pin diproyeksikan per frame. Cermin proyeksi Python = Three.js (tes twin, selisih < 0,05 px; uji render Chromium:
  pin selisih 0,001 px). Gate: M02 memakai magnifikasi layar terbesar, M03 memeriksa keempat sudut layar tiap frame,
  R56 melaporkan gerak, kecepatan, percepatan dan kecepatan sudut. Mode planar tetap bawaan: scene, kunci cache dan
  hash profil yang ada tidak berubah. Proposal `docs/proposals/3d-camera.md` dihapus (isinya di docs/TIMELINE.md).

- **Penyutradaraan satu gaya untuk dua rasio**: `segments[].intent.shot` (establish, track, reveal, compare,
  journey, hold, reaction, contrast) adalah lapisan cerita yang sama untuk 9:16 dan 16:9; `video_engine/framing.py`
  (adaptor format) mengisi kamera yang dibiarkan kosong sesuai orientasi (portrait: push/dolly, ketat, subjek di
  tengah; landscape: pan lateral/crane, lebar, rule of thirds). Override per profil `[framing.<orientasi>.<shot>]`;
  prompt draft memuat kosakata shot dan framing orientasi proyek. Segmen tanpa `shot` dan hash profil tidak berubah.
  Proposal `docs/proposals/geoverum-direksi-dua-aspek.md` dihapus (isinya di docs/PROFILES.md dan docs/TIMELINE.md).

- **Output landscape 16:9** (1920×1080, 2560×1440, 3840×2160) di samping Shorts portrait. Modul baru
  `video_engine/units.py`: kanvas referensi per orientasi (1080×1920 / 1920×1080), skala seragam `k`, safe area
  per orientasi (landscape 96/54 px); semua situs `W/1080`, `H/1920` (gate R62/R63/O01/F04/B01/Z01, layout, overlay,
  watermark, fx, transisi, recipes, draft, twin JS) memakainya dengan urutan operasi yang sama, jadi render, hash
  profil dan kunci cache portrait tidak berubah (diverifikasi: MD5 video build native portrait identik). Profil
  `[format] orientation` (ketidakcocokan dengan frame ditolak), rentang `[print]` per orientasi, `[watermark] corner`
  (landscape: kanan bawah), caption landscape diskalakan `k` (58 px bawaan), gate advisory U01 untuk aspek di luar
  9:16/16:9, metadata delivery YouTube biasa + thumbnail 1280×720, slot overlay draft landscape, RAM per worker
  diskalakan jumlah piksel, cek level H.264 ≤ 5.2 pada final mux, `engine import-shots --format`. Profil
  `geoverum_landscape` dan include generik `profiles/landscape/format.toml`; contoh `examples/landscape`. Proposal
  `docs/proposals/landscape.md` dihapus (isinya di docs/PROFILES.md, Orientation).

- **Iterasi kreatif berbasis intent**: `segments[].intent` memisahkan static/moving dan negative space yang disengaja, dengan alasan lokal; `R38space` bersifat advisory, sementara black/decode dan integritas frame tetap wajib. Gerak rendah `R56px` hanya gagal untuk intent moving; static/read menjadi info. Kamera tidak lagi dinaikkan otomatis tanpa `creative_autofix` atau native `auto_intensity` eksplisit.
- **Kualitas proxy terpisah dari kebijakan**: build/render/batch menerima `--quality proxy` hanya bersama `--mode draft`; capture/encode lebih murah dengan dimensi/fps tetap, output terisolasi, kunci cache visual mengikuti setting, provenance kualitas di laporan/manifest. Kualitas default tetap pengaturan delivery profil.
- **Arahan kreatif fleksibel**: identitas/default/alternatif Geoverum, tiga strategi storyboard berbeda, review membaca intent; catatan selera lama dipertahankan sebagai preferensi historis, bukan aturan universal.

- **Struktur P0 (kejelasan pemisahan lapisan)**: creative rules Geoverum kini satu sumber di
  `profiles/geoverum/creative.md` (`[draft] rules_file`, saling eksklusif dengan `rules` inline);
  konsep storyboard memakai `spans` (alias lama `beats` diterima saat membaca); tabel kanonis
  `[type]` menjadi basis ukuran teks (`[theme]`/`[print]`/`[layout]` adalah override per-surface);
  check caption-box di `profiles/taste/geoverum.json` diperbaiki menjadi `caption_style.forbid_box == false`:
  kotak caption diperlakukan sebagai pilihan gaya, bukan pelanggaran. Lihat `docs/STRUCTURE.md` dan
  `tests/test_p0_structure.py`.

- **Versi HyperFrames bisa dipilih** (`video_engine/hfversion.py`, `engine toolchain [show|list|use SPEC]`): env
  `VIDEO_ENGINE_HYPERFRAMES_VERSION` atau `vendor/hyperframes.select` memilih `pinned` (bawaan), versi pasti `X.Y.Z`
  (dipasang ke `vendor/hf/X.Y.Z`), `latest` atau `system` (CLI global). `engine doctor` menampilkan versi aktif
  (`hyperframes-active`) dan hanya WARN bila bukan pin; `build_report.json` mencatat versi yang dipakai; versi dan
  sumbernya masuk kunci cache segmen HyperFrames.
- **Komponen native baru**: roll kamera 2D (`roll` pada camera key, peta diskalakan agar tetap menutupi sudut),
  termometer (`thermometers`: satu nilai menggerakkan isi, warna dan angka), aliran parametrik (`flows`: dash bergerak
  menurut waktu render, seed deterministik, legenda warna) dan split-screen (`split`: dua panel HyperFrames dengan
  kamera masing-masing, ditumpuk engine; gate R63, M02, R56 per panel). `compare_places split=True` dan `explain_flow`
  memakai komponen ini; draft menerima `map_flows`, `map_thermometers` dan `roll`. Profil demo `native_demo`.
- **Compiler pola cerita** (`video_engine/recipes.py`, draft `map_recipe`): `country_reveal`, `follow_route`,
  `compare_places`, `explain_flow`, `stat_emphasis` menyusun scene HyperFrames native dari fakta (ISO, lat/lng, nilai,
  waktu). Zoom, fokus kamera, pita teks dan layout perbandingan dihitung dari geometri, safe area, resolusi peta dan
  gerak minimum; geometri yang tidak muat ditolak. Pola director menyebut resepnya ke draft.
- **Paket geografi** (`video_engine/projection.py`, `video_engine/geodata.py`): scene punya `map_projection`
  (`equirectangular` atau `mercator`); pin, props, gate G02 dan garis graticule memakai satu fungsi transformasi.
  Region bisa punya `rings` (pulau lain dan holes, aturan even-odd) dengan satu fill, tepi dan domain bendera untuk
  seluruh negara kepulauan. `geodata.base_map` membuat raster Natural Earth + sidecar proyeksi, `country_region`
  mengubah fitur MultiPolygon menjadi region yang dipotong ke batas peta. Draft meneruskan `rings`, proyeksi sidecar
  dan `intensity` peta (sebelumnya diabaikan diam-diam).
- **Draft peta lengkap** (`video_engine/draft_scene.py`): `engine draft` kini meneruskan region berbendera, rute,
  border, figure/counter, flag character dan bendera pin ke `gsap_scene_config`; globe, props, morph dan beats lewat
  `[draft] scene` profil (Geoverum: globe, props, beats). Sebelumnya schema dan assembler membuang semua field ini
  sehingga draft turun menjadi peta + pin + kartu. Field yang tidak bisa dieksekusi ditolak dengan lokasi field,
  jawaban model dicek ke schema sebelum assembly, dan sidecar `map_bounds` peta mengizinkan pin lat/lng.
- **Transisi `zoom_through`**: zoom-through besar untuk pergantian adegan (gaya Short referensi Antarctica). Adegan
  lama zoom makin cepat sampai `through_scale` (default 4x) dengan blur radial, kilatan putih opsional
  (`through_flash`), lalu adegan baru mendarat (power4.out). Bisa dipilih per batas segmen (`transition_in`) atau
  lewat profil. Perilaku default tidak berubah.
- **Gerakan kamera `reveal`** (`camera_path` move): satu gerakan sepanjang jarak antar-key yang cepat di awal lalu
  melambat panjang ke key, dengan kurva `theme.reveal_ease` yang dikalibrasi dari Short referensi Pan-American.
  Draft AI juga bisa memilihnya.
- **Tes golden-frame** (`tests/test_golden_frames.py`, `tests/golden/`): regresi piksel untuk Ken Burns native,
  pan footage, transisi crossfade, state, zoom whip dan page turn, serta grade warna, dengan toleransi untuk beda
  build ffmpeg **di platform yang sama**: golden dibuat di Linux, jadi suite ini belum hijau di macOS.
- **Gate P03 (jalur kamera)**: per segmen melaporkan panjang lintasan kamera, kecepatan layar puncak, dan perubahan
  kecepatan terbesar dalam satu frame (info). Dengan `[gates.camera] step_warn` gate ini memperingatkan lompatan
  kecepatan; default mati karena whip memang berubah cepat.
- **`[render] final_bit_depth`** (8 | 10): file akhir 10-bit (yuv420p10le, x264 High 10) untuk mengurangi banding pada
  gradasi. File antara tetap 8-bit. Build menolak dengan pesan jelas bila libx264 di ffmpeg tidak mendukung 10-bit.
- **Profil `geoverum`**: file akhir kini 10-bit, preset `medium`, CRF 16, batas 40 Mb/s (sebelumnya 8-bit
  `slower`, 25 Mb/s).
- **Registry gate** (`video_engine/gate_registry.py`): satu definisi untuk setiap gate. Daftar id gate di
  `profiles.py` kini diturunkan darinya.
- **`engine gates`**: daftar gate, efeknya di sebuah profil (`--profile`: off, warns, blocks), indeks Markdown
  (`--format md`), dan statistik dari laporan build (`--stats`).
- **`engine build --mode draft`** dan **`engine preflight --mode draft`**: gate teknis yang boleh dibuat advisory hanya
  memperingatkan. Hasil build ke `out/draft/final.draft.mp4`; `out/final.mp4` tidak disentuh dan `engine deliver` menolak
  file draft.
- **`gates.waive_with_reason`** di timeline: gate L01, L06, dan O01 bisa diturunkan dengan alasan tertulis yang
  dicatat di `build_report.json`.
- **`engine build --repin`**: kata caption yang basi (gate W01) dipin ulang sebelum pre-flight. Pesan W01 kini
  menyebut perintah yang tepat.
- **Profil `include`**: profil channel bisa memuat berkas `.toml` lain. `geoverum.toml` dipecah menjadi berkas
  inti dan `profiles/geoverum/` (entities, legibility, print, sound, layout). Hash profil tidak berubah.
- **`engine taste CHANNEL --lint`** dan `--check`: entri selera boleh membawa kondisi yang harus dipenuhi profil.
- **`VIDEO_ENGINE_WORKSPACE`**: satu variabel untuk lokasi default geo library, open assets, stock, dan unduhan.
- **Dokumentasi**: README ringkas berbahasa Indonesia, indeks `docs/README.md`, glosarium, `CONTRIBUTING.md`, dan
  `scripts/check_docs.py` (tautan, anchor, indeks, indeks gate yang dibuat otomatis). Usulan yang belum dibangun ada
  di `docs/proposals/` (format dan frame rate lain; kunci render).
- **CI**: job `rust` (`cargo fmt --check`, `clippy -D warnings`, `cargo test`), job `docs`, dan ruff yang kini juga
  menolak import dan variabel yang tidak terpakai.
- **macOS Apple Silicon sebagai platform setara Linux x64**: keduanya bisa membangun dan menyerahkan video final,
  gate yang sama berlaku, keluaran punya spesifikasi yang sama (tidak identik bit demi bit), dan cache dipisah
  menurut identitas toolchain. `engine doctor --install` memasang Node `darwin-arm64` dan Chrome `mac_arm`,
  `engine doctor` mencetak baris platform dan menerima ffmpeg mayor 7 sampai 9, folder scratch bawaan di macOS adalah
  `$TMPDIR/video_engine`. Panduan instalasi ada di README dan [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md#platforms).
- **Wrapper `scripts/engine-build` dan `scripts/engine-batch`**: versi portabel (POSIX `sh`, Linux dan macOS) dari
  wrapper server render. Memuat `engine.env` (`VIDEO_ENGINE_ENV_FILE`), menolak bila disk bebas kurang dari
  `VIDEO_ENGINE_MIN_FREE_GB` (bawaan 8), mengambil kunci render global `VIDEO_ENGINE_RENDER_LOCK` lewat
  `fcntl.flock` (macOS tidak punya `flock(1)`), lalu menjalankan engine di bawah `nice`. Kode keluar engine
  diteruskan. Tes: `tests/test_wrapper_scripts.py`.

### Diubah


- **Default toolchain tidak lagi terkunci ke pin repositori.** Kebijakan HyperFrames default adalah `auto`:
  pakai yang sudah terpasang di mesin (penemuan portabel), dan `engine doctor --install` memasang rilis
  terbaru ke `<user-data>/video-engine/toolchain` hanya bila suatu alat tidak ada. `pinned` (kunci
  `vendor/VERSIONS.json` + `package-lock.json`) tetap ada sebagai pilihan
  (`VIDEO_ENGINE_HYPERFRAMES_VERSION=pinned` atau `engine toolchain use pinned`). puppeteer-core dan three
  dicari secara terpisah (global, vendor, atau toolchain pengguna) agar HyperFrames global yang tidak
  membawa keduanya tetap dipakai. `build_report.json` mencatat versi dan path yang benar-benar dipakai.
  Pengujian: `tests/test_hfversion.py`, `tests/test_discover.py`, `tests/test_doctor.py`.

- **Tidak ada provider AI bawaan**: urutan pemilihan menjadi `--provider` > `VIDEO_ENGINE_AI_PROVIDER` > `default`
  di `ai_providers.toml` > `agent`. Tanpa pilihan, permintaan menjadi bundel berkas untuk provider `agent`: model AI
  yang sedang menjalankan mesin yang menjawab (nama modelnya dicatat dari `VIDEO_ENGINE_AI_MODEL` atau
  `[agent] model_label`, bawaan `agent`). `ai_providers.toml` tidak lagi menulis `default`, dan entri fallback ke
  host tunnel sementara dihapus. `google-genai` pindah ke extra `gemini`.
- **Gemini bukan provider istimewa**: tidak ada fallback lintas layanan. Kunci `[gemini] fallback_provider` (dan
  kunci `[gemini]` lain selain `models` / `qa_models`) ditolak, dan kegagalan Gemini tidak pindah ke provider lain.
  Daftar modelnya di-retry seperti provider API lain. `AIResult.provider` tidak lagi bawaan `"gemini"`. Extra
  `test` tidak memasang `google-genai`; pengujian yang membangun klien SDK sungguhan melewati diri sendiri bila
  paket itu tidak ada, dan pengujian dengan klien palsu tetap berjalan. Gemini tetap opsional lewat
  `--provider gemini` dan extra `gemini`. Tanpa pilihan, provider tetap `agent`.
- **Retry AI mencakup status sementara**: HTTP 408, 500, 502, 504 kini di-retry seperti 429 dan 503; 400, 401, 403
  tetap tidak. Balasan Gemini tanpa teks dengan alasan berhenti selain normal menjadi error yang menyebut alasannya.
- **Profil Geoverum dibersihkan tanpa mengubah hash `geoverum`**: kunci yang hanya mengulang `default.toml` dihapus
  dari `geoverum/core.toml`; gaya caption, watermark, dan kebijakan gate yang sama untuk dua format dipindah dari
  kedua file channel ke `core.toml`; `title_px` ditulis sekali di `[type]`. Profil `geoverum_landscape` berubah hash
  hanya karena aturan kreatifnya kini mengutip angka landscape (caption 58 px, `fixed_y` 900, title 88 px).
- **Resolver profil dipisah dari skema**: model tabel ada di `video_engine/profile_schema.py`, `profiles.py` hanya
  meresolusi lapisan dan mengekspor ulang nama model. `profiles.load` melayani profil yang tidak berubah dari memo
  berbasis tanda-tangan berkas (path, mtime, ukuran, inode), dan perubahan `creative.md` kini terlihat dalam proses
  yang sama. `profiles.flatten` menggabungkan tabel yang ditulis beberapa file dan mendukung `include` multi-baris.
- **Helper kembar digabung**: satu `util.atomic_write_text`, `util.frame_jpeg`, `util.wav_16k_mono`, satu
  `profiles.merge`, satu pemeriksaan path berkas profil, dan satu rumah untuk nilai bawaan STT (`ai.STT_DEFAULTS`).
- **AI QA** hanya meluluskan segmen yang dilaporkan model: segmen yang tidak dijawab menjadi peringatan, dan entri
  tentang segmen yang tidak dikirim diabaikan. Prompt QA dan analisis footage menyebut orientasi frame yang benar.
- **Uji yang butuh toolchain HyperFrames atau pustaka open-assets** kini dilewati dengan alasannya saat tidak
  terpasang (gagal bila `VIDEO_ENGINE_REQUIRE_TOOLCHAIN=1`).

- **Gaya teks dan warna Geoverum** dari analisis frame demi frame Shorts referensi: title Archivo Black 104 px
  (sebelumnya Inter Bold 112) putih murni tanpa outline (`[legibility] light_c = 0`, `text_outline_px = 0`, bayangan
  lembut 4/14/0,55, glow entitas 18), masuk dengan tracking-in di atas wilayahnya; angka 96 px (sebelumnya 156)
  kuning `#FFD417` dengan pop `back`; pil label versi gelap warna entitas (`label_box_lc = 80`), 56 px, huruf
  kecil-besar, miring −3°, tumbuh bersama teks; pin `#E61129` (juga `signal_colors.pin`); warna wilayah jenuh di
  peta satelit (`vivid_chroma_scale = 1.8`); `[layout.fly]` 1,0/1,1/1,15 dengan `power3.out` (sebelumnya 2,6/2,2/3,0)
  dan `head_drop_s = 0.3`; tahun pada peta gelap putih. `creative.md` mendapat aturan urutan kemunculan
  (wilayah → pin → label → title/angka pada katanya, maksimal dua elemen masuk bersamaan); taste 34/34 cek ok.
- **Profil `geoverum` dikalibrasi ulang dari bedah forensik 48 Shorts referensi** (5 kompilasi 854×480, 30 fps;
  tabel lengkap di `docs/REFERENCE-STYLE.md`, "Pengukuran Ulang Referensi"). Caption portrait kini Inter Bold
  48 px (`size`/`ab_sizes`, sebelumnya 84; tinggi-x terukur 24 px), `fixed_y = 1366` (pusat tinta referensi,
  sebelumnya 1417), `max_words = 4` (sebelumnya 3); `[gates.duration]` portrait 30–120 s (sebelumnya 45–100 s);
  `[gates.camera]` `hold_warn_s = 3.0`, `hold_fail_s = 5.0`, `moving_warn = 0.65` (sebelumnya 1.7 / 2.0 / 0.8;
  referensi bergerak pada median 77 % frame dengan tahan terpanjang median 2,6 s); `[theme] region_fill_opacity =
  0.85` (sebelumnya 0.45; isian referensi nyaris padat); `[draft] max_seg_s = 30` (sebelumnya 20; panjang shot p90
  30,8 s); `[fx.bubble] max_bottom_pct = 0.68` agar bubble tetap 24 px di atas kotak caption baru (y 1330).
  `creative.md`, `taste/geoverum.json` (entri dan `check`), `docs/PROFILES.md`, `docs/SPEC.md`, `docs/RATIONALE.md`
  dan tes ikut diselaraskan; `engine taste geoverum --lint` 22/22 ok. Hash `profiles.load("default")` tidak berubah.
- **Gate Z01 menskalakan slack pita caption dengan ukuran font**: `caption_pad` mengurangi `24 px × size / 84`
  (bukan 24 px tetap), karena pada caption 48 px tinta bayangan libass yang dirender melewati kotak yang dihitung.
- **`legibility`: judul mengejar `lc_title_aim`** pada tiap tangga eskalasi sebelum puas dengan `lc_title`, sesuai
  `docs/SPEC.md` dan `profiles/geoverum/legibility.toml` (sebelumnya kunci ini hanya divalidasi).

- **HyperFrames 0.8.214 -> 0.9.13** (pin `vendor/VERSIONS.json`, `vendor/npm/package.json` + lockfile; dependensi
  baru `undici`). Flag `render` yang dipakai engine tidak berubah; breaking change 0.9.0 (Plan v1 distributed render)
  tidak menyentuh engine. Semua segmen HyperFrames dirender ulang sekali (toolchain identity berubah).
- **Dokumentasi platform**: pernyataan "hanya Linux x64" di README, `docs/LIMITS.md` dan `docs/ENVIRONMENT.md`
  diganti dengan kebijakan paritas Linux x64 dan macOS Apple Silicon.
- **Globe**: kamera kini bergerak di busur great-circle (jalur terpendek di bola) antar key, bukan garis lurus
  lintang/bujur. Contoh New York–Tokyo: 97,6° lewat Arktik, bukan 115°. Leg yang akan melewati kutub (> 85°)
  tetap memakai lintang/bujur. R56 dan P01 kini mengukur jalur yang benar-benar ditempuh. Segmen globe dirender ulang.
- **Ken Burns native**: zoom kini berporos. Titik yang seharusnya diam di layar tidak lagi bergeser selama zoom
  sambil pan (`camera_windows`, `ALGO_VERSION` 3). Segmen native dengan gerak kamera dirender ulang.
- **Hash kode cache** dihitung dari pohon sintaks Python tanpa docstring (`util.code_hash`). Mengubah komentar,
  docstring, atau format modul yang masuk kunci cache tidak lagi membuang cache render. **Build pertama setelah
  perubahan ini merender ulang segmen yang ter-cache satu kali**, karena algoritma hash berubah.
- **Selera dan profil Geoverum diselaraskan:** caption 92 dan 104 px ditolak (`ab_sizes = [84]`), transisi
  page-turn dan perkamen dipindah ke daftar yang ditolak, dan true peak dinyatakan -1.5 dBTP.
- **README lama** dipecah menjadi `docs/ENVIRONMENT.md`, `CLI.md`, `TIMELINE.md`, `PROFILES.md`, `GATES.md`, `BUILD.md`,
  `DELIVERY.md`, `AI.md`, `TESTING.md`, dan `LIMITS.md`. Peta pindahnya ada di README.

- `cargo fmt` pada `ass.rs` (hanya format; output crate dan `ALGO_VERSION` tidak berubah).

### Diperbaiki

- **Kredit musik Epidemic Sound dari `bgm_path`.** Sidecar atau sound lock yang membuktikan `source=epidemic`, id trek, dan `sha256` memenuhi R07 tanpa baris `credits_manifest`; build (proxy dan delivery) menulis baris kredit yang sama dengan musik dari cache. Lisensi `Epidemic Sound licence` masuk allowlist R14 bila `source_url` adalah `https://www.epidemicsound.com`.
- **Kredit suara pada build proxy.** Proxy tidak lagi berhenti dan meminta `python -m video_engine.sound P --credits`. Baris kredit yang belum ada ditulis sendiri, sama seperti build delivery. Berkas suara yang belum ada di cache tetap harus disiapkan terlebih dahulu.
- **Nada uji SFX tidak lagi dipakai pada build sungguhan.** `riser`, `rumble`, dan setiap kind lain tanpa berkas berlisensi di cache menghasilkan error (atau permintaan sound bila build dapat memintanya). Nada lavfi hanya bila `VIDEO_ENGINE_LOCAL_TONES=1` atau `allow_generated=True`.

- **`examples/*/prepare.sh` memakai interpreter yang punya Pillow**: skrip tidak lagi memanggil `python3` di PATH
  (yang di beberapa mesin tidak memasang Pillow). Urutan: `$PYTHON`, lalu `$VIRTUAL_ENV/bin/python`, lalu `python3`.
  Tes contoh mengirim interpreter venv lewat `PYTHON`.
- **Tes profil capture HyperFrames** (`test_good_render_uses_standard_capture_and_crf14`) menangkap `RuntimeError`
  setelah merekam perintah render, tetapi `EngineError` adalah turunan `RuntimeError`, jadi kegagalan "HyperFrames
  missing" ditelan dan tes gagal di daftar perintah kosong. Tes kini memakai pengecualian sentinel dan mencari
  perintah CLI yang memuat `-q` / `--crf`, sehingga toolchain yang belum terpasang di-skip oleh hook sesi, dan
  perintah render tetap diperiksa bila `run` tercatat.
- **`engine doctor`**: cek `node` gagal bila `bin/npm` hilang (pohon resmi memuat npm di `lib/`; symlink putus atau
  `lib/` terhapus membuat `--install` gagal sebelum `npm ci`). `--install` memasang ulang tarball node resmi dahulu.
  Pengujian: `tests/test_doctor.py`.

- **Review tidak lagi membatalkan draf yang sudah lolos gate**: balasan review yang melanggar skema atau provider
  yang gagal saat review dicatat di `draft_report.json` (`review[].errors`) dan draf tetap ditulis. Balasan
  storyboard yang melanggar skema tidak lagi diperingkat, melainkan dikirim balik untuk diperbaiki.
- **Bundel agent yang basi ditolak**: `response.json` yang ditulis untuk permintaan lain (input, profil, atau
  jawaban sebelumnya berubah) menjadi error, bukan diputar ulang terhadap prompt yang berbeda (`request.digest`).
- **Race pada kunci proyek** (`batch.ProjectLock`): file kunci kini hanya dihapus selagi `flock`-nya dipegang, dan
  kunci hanya dipercaya bila path masih menunjuk file yang dikunci, sehingga dua build tidak bisa memegang kunci
  pada inode yang berbeda.
- **STT**: respons dengan bentuk tak terduga menjadi `EngineError` yang menyebut kata bermasalah, transkrip kosong
  tidak lagi di-cache, dan `ai_providers.toml` yang rusak tidak lagi diam-diam diganti nilai bawaan.
- **Draf** tidak lagi menyatakan `watermark_free = true` untuk aset yang tidak dideklarasikan model; prompt menyebut
  bahasa narasi dari `audio_chain.language`.

- **`caption_fixes` tidak lagi menggantung atau crash**: `from` kosong membuat `audio.apply_fixes` berputar tanpa
  henti (pola kosong cocok di setiap posisi) dan `to` kosong berakhir `ZeroDivisionError`. Sekarang `from` kosong
  ditolak saat validasi timeline dan `to` kosong menghapus kata yang cocok. Pengujian:
  `tests/test_audio_loudness.py`.
- **Balasan model yang dibungkus prosa tidak lagi dibuang**: `ai.base.parse_json_text` menerima blok ```` ```json ````
  setelah kalimat pembuka, satu dokumen JSON lengkap yang diikuti prosa, dan `content` berbentuk daftar part
  (sebelumnya `AttributeError` tanpa retry). `NaN`/`Infinity` dan dokumen terpotong tetap dianggap JSON tidak valid
  (di-retry); fragmen di dalam balasan terpotong tidak pernah diambil. Pengujian: `tests/test_ai_providers.py`.
- **Redraft mempertahankan `profile_overrides` proyek**: `engine draft` pada proyek yang sudah punya timeline
  membuang `profile_overrides` (misalnya `camera.max_zoom` atau `watermark = null`) padahal `settings`,
  `audio_chain` dan `caption_style` dipertahankan; prompt, skema dan perakitan kini memakai profil efektif yang sama
  dengan validasi timeline (`draft._profile`). Pengujian: `tests/test_draft.py`.
- **Prompt storyboard menyebut format frame yang benar**: `director.prompt` selalu menulis "vertical documentary
  short", juga untuk profil landscape 16:9. Pengujian: `tests/test_director.py`.
- **Taste store**: file `profiles/taste/<channel>.json` yang rusak kini menjadi `EngineError` yang menyebut filenya,
  dan penyimpanan memakai penggantian atomik (file sementara + `os.replace`) sehingga draf lain pada channel yang
  sama tidak pernah membaca file setengah tertulis. Pengujian: `tests/test_director.py`.
- **Metadata paket**: `requires-python` menjadi `>=3.11` (kode memakai `tomllib`) dan `pydantic>=2.12` (profil
  memakai `Field(exclude_if=...)`; versi lebih lama mengabaikannya diam-diam dan mengubah hash profil).
- Kunci cache segmen native kini memuat `[motion] overlay_fade_s` (fade overlay yang dibakar ke piksel).
- Region `block` pada scene beats kini ikut meredup sesuai level `layout.regions` (template hanya menelusuri anak
  langsung `<svg>`).
- `assets.resolve_timeline_paths` dan `ensure_credits` menangani kedua panel segmen split (peta, morph, prop, layer).
- `audio_chain.true_peak` proyek yang lebih longgar dari batas global −1,5 dBTP kini ditolak (`profiles.loudness`).
- Isyarat SFX whip jatuh di tengah leg whip kamera setelah `hold` kunci sebelumnya.
- Kembaran Python↔JS: aturan `boxed` legibilitas, pembulatan warna termometer (half-up), batas frame partikel
  (`f_out` eksklusif), dan ease counter (`gsap_ease`, kembaran persis ease GSAP yang di-vendor).
- `tests/test_gates_layer3_qa.py`: uji R38 sumber gelap kini memasok sampel untuk setiap segmen.
- Dokumen: angka fly-through Geoverum (0,33 s `power2.inOut`/`power2.out`), `min_hold_s`, kotak aman teks layout,
  `timeline.ZOOM_LIMIT`, nama file paket delivery, komentar crossfade tinta, serta token `[motion]` dan zona `z3`
  yang tidak dibaca renderer.
- Field draft `[draft] scene` yang kosong tidak masuk hash profil, sehingga profil default yang tidak memakai
  scene opsional tetap menghasilkan hash yang sama dan cache lama tetap dapat dipakai.
- **Warna footage sumber**: footage tanpa tag warna kini didekode sesuai standar ukuran (HD ke atas = BT.709, SD =
  BT.601) dan tag yang ada dipakai, termasuk full range (`util.decode_colour`). Sebelumnya semua footage tanpa tag
  dibaca sebagai BT.601: merah jenuh di footage stok HD jadi ~18 level lebih gelap. Sumber HDR (PQ/HLG) kini
  ditolak gate M01 dengan pesan jelas, karena engine SDR BT.709 dan tidak melakukan tone-mapping.
- **Publish build**: `build_report.json` kini ditulis ke file sementara sebelum `out/final.mp4` diganti. Kegagalan
  tulis laporan (mis. disk penuh) tidak lagi meninggalkan `final.mp4` baru dengan laporan lama.
- **Warna transisi**: klip transisi (RGB) kini dikonversi sendiri ke BT.709 limited-range dan di-overlay dengan
  `overlay=format=yuv420`. Sebelumnya ffmpeg mengonversinya dengan matriks BT.601, sehingga warna jenuh bergeser
  ~15 level selama setiap transisi zoom_whip/page_turn. Cache composite yang lama tidak lagi dipakai.
- **`flag_for`**: jika folder aset bendera tidak ada, pesan error menyebut folder dan `VIDEO_ENGINE_OPEN_ASSETS`,
  bukan "unknown flag … closest: none".
- **Tes bendera** dilewati (skip) bila aset flag-icons tidak terpasang, seperti tes lain yang butuh sumber daya luar.

- `tests/test_build_publish.py` menganggap berkas kunci `.batch.lock` sebagai sisa build, sehingga dua tesnya gagal
  begitu toolchain HyperFrames terpasang.
- `tests/test_director.py` masih mengharapkan batas "hard cuts per 10 s" yang sudah dihapus dari catatan selera.

### Dihapus


- Alias `MANDATORY_GATE_IDS`, `PROFILE_GATE_IDS`, `FLEXIBLE_GATE_IDS` (pakai `KERNEL_GATE_IDS` dan `Gate.tier`),
  field `Profile.gate_warnings` yang selalu kosong, `profiles._drop_nulls` (identik dengan `merge`), dependensi
  Python `blake3` yang tidak diimpor (hashing lewat ekstensi Rust), dan `faster-whisper` dari instalasi CI.

- Perkakas konversi style-sheet REA (seluruh direktori `tools/`: template, skrip pembangun profil, dan README-nya)
  beserta dokumen alurnya di `docs/` dan seluruh rujukannya di `README.md`, `AGENTS.md`, dan `docs/README.md`.
- Kode mati tanpa rujukan: `hyperframes.build_timeline_dir`, `framing.as_dict`, `units.ref_canvas`,
  `gate_tools.render_profile_summary`, `deliver.write_package`, `blocks.render_svg`, `assets.register_used`
  (beserta loop `_USED` yang tak pernah terisi), dan konstanta `native.IMAGE_MAX_SIDE`, `ai.base.TASKS`,
  `draft_scene.GATED`, `overlays.FADE_S`; impor dan variabel lokal yang tak terpakai (lint `ruff` AGENTS.md lulus).
