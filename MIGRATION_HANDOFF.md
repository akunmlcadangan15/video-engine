# Handoff: selesaikan kegagalan restrukturisasi video-engine

## Instruksi untuk sesi baru

Baca dokumen ini dan `AGENTS.md`, lalu kerjakan bertahap. Jangan mengulang restrukturisasi atau full suite dari awal. Utamakan input/output singkat agar hemat kredit. Gunakan satu pelaksana tanpa subagent. Jangan push tanpa izin eksplisit.

Repo: `/Users/brianrayaguna/video-engine`
Branch: `master`

### Status Git

- `acebf68`: checkpoint restrukturisasi, belum terverifikasi penuh.
- `bf92019`: menyatukan `EngineError`, memperbaiki dokumentasi resolver dan whitespace.
- Ada perubahan **belum di-commit** di `tests/test_motion.py`. Jangan ditimpa.
- Dokumen handoff ini juga belum di-commit.
- `tmp-6267b2772333d8d8/` adalah folder sementara untracked, jangan ikut commit.
- Tidak ada push yang dilakukan. Permintaan push sebelumnya dibatalkan pengguna.
- Sebelum mulai, periksa `git status --short` dan `git log -2 --oneline`; jangan mengasumsikan snapshot tidak berubah.

## Apa yang sudah selesai

Implementasi dipindahkan ke paket `core`, `profiles`, `timeline`, `layout`, `render`, `gates`, `draft`, `sound`, dan `cloud`. Modul flat kompatibilitas tetap ada. Lazy Rust, pemisahan model, boundary tests, resource packaging, dan dokumentasi sudah diterapkan.

Jangan mendesain ulang struktur ini. Lima lapisan konseptual tetap mengikuti `docs/STRUCTURE.md`.

Verifikasi terarah sebelumnya:
- 45 tes migration/boundary/contracts lulus.
- Lint, diagnostik IDE, pemeriksaan dokumentasi, dan whitespace lulus.
- Wheel pernah dibangun dan diimpor di luar checkout dengan resource root eksplisit.
- Chrome/HyperFrames smoke render lulus.

## Hasil full suite terakhir

Source dibekukan pada `bf92019`, dengan toolchain uji yang berfungsi:

**2.466 passed, 19 failed, 58 skipped, 4 warnings; sekitar 9 menit 25 detik.**

Laporan JUnit: `/tmp/video-engine-final-tests.xml`.
Log lengkap lokal: `/Users/brianrayaguna/.cursor/projects/Users-brianrayaguna-video-engine/terminals/777335.txt`.

Jangan menyebut full suite lulus. Alasan 58 skip belum diringkas; ambil dari JUnit saat handoff final.

### Sembilan kegagalan sudah diperbaiki, belum di-commit

`tests/test_motion.py`:
1. `_js_helpers()` sekarang membaca `config.TEMPLATE_DIR / "index.html"`, bukan lokasi lama `video_engine/template/index.html`.
2. Tes drift planner sekarang monkeypatch `video_engine.layout.constraints.zones`, bukan wrapper `gates.layout_gates.zones`.

Tidak ada assertion yang dihapus atau ambang yang dilonggarkan.

Pengujian ulang **seluruh `tests/test_motion.py`: 85 passed**, diikuti lint dan check_docs lulus.
Log: `/Users/brianrayaguna/.cursor/projects/Users-brianrayaguna-video-engine/terminals/777336.txt`.

**Hitungan: 19 gagal pada full suite awal; 9 sudah lulus pada retest; 10 tersisa.**

## Sepuluh kegagalan tersisa

### Kelompok A — sembilan tes, akar masalah API render key

`video_engine.render.hyperframes` tidak memiliki `render_key`, tetapi cache pruning dan tes sudah menggunakannya:

1. `tests/test_dom_cache.py::test_good_referenced_names_the_hf_segment_key_for_dom_entries`
2. `tests/test_grade.py::test_good_prune_keeps_the_graded_file_of_a_cached_render`
3. `tests/test_cache_lease.py::test_good_engine_cache_prune_cli_lists_the_skipped_leased_entries`
4. `tests/test_hf_concurrency.py::test_good_worker_count_is_part_of_the_render_identity_only`
5. `tests/test_hf_concurrency.py::test_good_build_and_prune_use_one_worker_count`
6. `tests/test_import_sheet_prune.py::test_good_prune_keeps_referenced_only`
7. `tests/test_models.py::test_good_prune_keeps_the_sprites_of_model_props`
8. `tests/test_sound_build_wiring.py::test_good_cache_prune_includes_the_sound_cache`
9. `tests/test_max_zoom.py::test_good_prune_keeps_the_deep_tiles`

Bukti baseline: `git show a6ac33d:video_engine/hyperframes.py` tidak mendefinisikan `render_key`. Tes cache-prune yang sama gagal di checkout baseline. Ini masalah lama yang kini secara eksplisit diminta pengguna untuk diperbaiki.

File yang perlu dibaca secukupnya:
- `video_engine/render/hyperframes.py`: `keys`, `cache_key`, `render`, `_render_split`, `__all__`.
- `video_engine/cache_prune.py`: `referenced`; pemanggil `render_key(..., grade_pass=False)` untuk split panel.
- `video_engine/build.py`: pemilihan workers dan penerapan grade.
- `tests/test_hf_concurrency.py`, sekitar baris 238–288: kontrak render identity.
- `video_engine/core/resources.py` dan adapter resource timeline bila diperlukan.

Kontrak yang harus dipertahankan:
- Scene key tetap terpisah dari render key; mengganti worker count tidak mengubah scene/build-dir/DOM identity.
- Render key harus berbeda untuk worker 1, 2, 4.
- `render()` lookup/store memakai render key yang sama dengan pruning.
- Build dan pruning menggunakan worker allocation yang sama.
- Tentukan makna `grade_pass=False` berdasarkan alur grading aktual; jangan sekadar menerima parameter lalu mengabaikannya.
- Split panels, rendered files, grade derivatives, dan DOM entries tetap terlindungi dari pruning.
- Jangan membuat alias `render_key = cache_key`: itu melanggar kontrak worker identity.

### Kelompok B — satu tes layout

`tests/test_layout.py::test_good_planner_plan_passes_every_layout_gate`

Gejala:
```
[FAIL] L06 seg 2: frame 207: fig:0 '1945' ink (64, 350, 315, 441) outside Z1
```
Ada tujuh finding pada assertion `fails == []`.

Kasus ini juga direproduksi pada baseline `a6ac33d` dengan hasil sama.

File utama:
- `tests/test_layout.py` sekitar baris 270–290 dan fixture `beats_doc`.
- `video_engine/layout/planner.py`
- `video_engine/layout/constraints.py`
- `video_engine/layout/geometry.py`, `camera.py`, atau konfigurasi layout hanya bila bukti menunjuk ke sana.

Diagnosis dahulu: geometri ink figure versus Z1, skala/font/resource fixture, dan perbedaan environment. Jangan memperbesar zona, menurunkan gate, mengubah profil merek, atau menghapus assertion hanya agar tes lolos.

## Environment yang sudah tersedia

- Python: `.venv/bin/python` (3.13), pytest/xdist dan native Rust release terpasang.
- Gunakan `uv` untuk packaging. Environment ini tidak memiliki `pip` module; jangan mengulang kegagalan `python -m pip wheel`.
- Toolchain:
  - `VIDEO_ENGINE_WORKSPACE=/tmp/video-engine-verification-workspace`
  - Chrome for Testing 160.0.8167.0
  - Puppeteer di workspace uji tersebut.
  - HyperFrames ditemukan pada instalasi Node global.
- Jangan menjalankan `doctor --install` ulang. Workspace user default bermasalah; environment uji di atas sudah membuktikan smoke render berhasil.
- `/tmp/video-engine-baseline-check` adalah ekstraksi baseline untuk pembandingan, bukan repo kerja.
- Seluruh tes/build sebelumnya telah selesai atau dihentikan; cek proses hanya sekali bila diperlukan.

## Rencana eksekusi hemat kredit

### Tahap A — perbaiki render/cache key
1. Periksa diff tes motion dan status Git, jangan mengubahnya tanpa alasan.
2. Baca fungsi terkait saja; implementasikan render key dan wiring cache/prune sesuai kontrak.
3. Jalankan dua tes worker identity pada `test_hf_concurrency.py` dahulu.
4. Setelah lulus, jalankan sembilan nodeid Kelompok A saja.
5. Maksimal dua percobaan per akar masalah. Jika masih gagal, berhenti dan laporkan sebab spesifik sebelum memperluas pencarian.
6. Laporkan hasil singkat; jangan otomatis menjalankan full suite.

### Tahap B — perbaiki layout
1. Reproduksi satu nodeid Kelompok B.
2. Cetak/inspeksi geometri yang relevan saja; perbaiki penyebab.
3. Ulangi nodeid itu, lalu `tests/test_layout.py` dan tes motion terkait bila perilaku kamera/geometri berubah.
4. Jangan mengubah threshold/golden agar lolos.

### Tahap C — verifikasi dan commit
1. Ulangi semua 10 nodeid gagal pada snapshot final.
2. Jalankan:
```bash
.venv/bin/python -m pytest -q --tb=short \
  tests/test_dependency_boundaries.py \
  tests/test_migration_contracts.py \
  tests/test_package_migration.py
.venv/bin/python scripts/check_docs.py
ruff check --select E9,F63,F7,F82,F401,F841 video_engine tests scripts
git diff --check
```
3. Update changelog/dokumentasi yang benar-benar terdampak. Cache harus mencakup perubahan perilaku.
4. Periksa diagnostik IDE file yang diedit.
5. Commit hanya perubahan terkait, termasuk perbaikan tes motion yang tertunda. Jangan commit folder sementara atau laporan tes.
6. Jangan push.
7. Full suite kedua hanya setelah izin eksplisit pengguna. Jika disetujui, bekukan source dan jalankan sekali:
```bash
VIDEO_ENGINE_WORKSPACE=/tmp/video-engine-verification-workspace \
.venv/bin/python -m pytest -q -n 4 --tb=short \
  --junitxml=/tmp/video-engine-final-tests-v2.xml
```

Untuk seluruh tes yang menggunakan renderer, prefix dengan:
```bash
VIDEO_ENGINE_WORKSPACE=/tmp/video-engine-verification-workspace
```

## Aturan laporan

- Ringkas: perubahan, pass/fail, blocker, SHA bila ada.
- Jangan membaca ulang seluruh repo/transkrip atau menggunakan subagent.
- Jangan mengklaim “tinggal satu masalah” hanya dari jumlah kelompok penyebab.
- Bedakan full-suite lama dan hasil retest; jangan menjumlahkannya menjadi hasil full-suite baru.
- Jangan menganggap migrasi siap produksi hingga gate yang diwajibkan benar-benar terpenuhi.

## Prompt singkat untuk sesi baru

> Baca `MIGRATION_HANDOFF.md`. Lanjutkan hanya Tahap A terlebih dahulu, satu pelaksana tanpa subagent. Fokus sembilan kegagalan render_key/cache-prune; jangan ulang restrukturisasi atau full suite. Pertahankan diff tes motion. Setelah Tahap A, laporkan hasil dan jeda. Tidak boleh push.
