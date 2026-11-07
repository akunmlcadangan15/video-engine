# Changelog

Perubahan penting pada mesin. Format mengikuti [Keep a Changelog](https://keepachangelog.com/id-ID/1.1.0/).
Versi paket belum diberi nomor rilis (`0.1.0`), jadi semua catatan ada di bagian "Belum dirilis".

Dalam dokumen ini, **gate** berarti pemeriksa otomatis yang dapat memblokir build, **profil** berarti aturan per
channel yang disimpan sebagai file TOML, **`require`** berarti gate tersebut wajib memblokir bila gagal, dan
**`advisory`** berarti gate tersebut hanya memberi peringatan. Definisi lengkap ada di `docs/GATES.md`.

## Belum dirilis

### Ditambahkan

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
