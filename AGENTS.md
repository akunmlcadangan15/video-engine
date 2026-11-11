# Panduan Arsitektur & Operasional Agent (`video-engine`)

Dokumen ini merupakan panduan operasional teknis bagi *engineer* maupun *AI agent* (model apa pun) yang mengembangkan, mengaudit, atau menjalankan `video-engine`. Model kerja mesin (mode default vs profil, urutan resolusi profil, alur data, peta modul, kontrak lapisan AI, jebakan yang diketahui) ada di [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): baca itu lebih dulu sebelum mengubah kode. Setiap perubahan pada kode sumber, skema timeline, maupun profil channel wajib mengikuti arsitektur lima lapisan ([docs/STRUCTURE.md](docs/STRUCTURE.md)) serta lulus verifikasi *pre-flight* dan dokumentasi.

## 1. Tujuan & Spesifikasi Output
- **Input**: Direktori proyek yang memuat `master_timeline.json`, `credits.txt`, dan folder `assets/`.
- **Jalur Render Hybrid**:
  - `NATIVE_RUST_FFMPEG`: Pemotongan, *pan/zoom* Ken Burns sub-piksel (ekstensi Rust `hypoverum_core_rs`), *overlay* PNG terukur, dan *color grading* LUT 3D.
  - `HYPERFRAMES_GSAP`: Adegan *motion-graphics* berbasis HTML/SVG/GSAP/Three.js di dalam *headless Chrome* terisolasi (peta satelit/perkamen, globe 3D, poligon wilayah, rute animasi, *beats layout*, *cutaway* 2D/3D, termometer, aliran parametrik, dan partikel).
- **Output**: `<project>/out/final.mp4` (H.264 8-bit atau 10-bit High 10, resolusi portrait 9:16 `1080×1920` atau landscape 16:9 hingga `3840×2160`, audio AAC 48 kHz ternormalisasi `-14.0 LUFS` / `-1.5 dBTP`) beserta `<project>/out/build_report.json`. Jika terdapat kegagalan pada *kernel gate*, *build* tidak menerbitkan `final.mp4` dan hanya menulis `out/build_report.failed.json`.

## 2. Peta Direktori Repositori
- `video_engine/` — Paket utama Python: CLI (`cli.py`), kontrak data (`timeline.py`), skema tabel profil (`profile_schema.py`), resolver profil (`profiles.py`), kompilator adegan (`hyperframes.py`, `native.py`, `layout.py`, `legibility.py`, `printstyle.py`, `transitions.py`), audio & SFX (`audio.py`, `mixer.py`, `sfx.py`, `sound/`), serta penyusun draf AI (`draft.py`, `draft_scene.py`, `recipes.py`, `director/`, `ai/`).
- `video_engine/gates/` — Sistem *pre-flight* & *post-render* gate (terdaftar terpusat di `video_engine/gate_registry.py`).
- `rust/hypoverum_core_rs/` — Ekstensi native Rust (PyO3): interpolasi kamera, kompilator subtitle `.ass`, deteksi jeda suara, dan perhitungan metrik piksel cepat (`BLAKE3`, `frame_stats`).
- `profiles/` — Konfigurasi profil berbasis TOML:
  - `default.toml`: Nilai dasar mesin.
  - `geoverum.toml` & `geoverum_landscape.toml`: Profil channel Geoverum (portrait 9:16 & landscape 16:9) yang berbagi aturan inti melalui `profiles/geoverum/core.toml` serta sub-tabel modular (`entities.toml`, `legibility.toml`, `print.toml`, `sound.toml`, `layout.toml`, `creative.md`).
  - `taste/geoverum.json`: Basis data preferensi desain (*liked* / *rejected*) dengan pemeriksaan otomatis (`engine taste geoverum --lint`).
- `scripts/` — Skrip pemeriksaan dokumentasi (`check_docs.py`) dan pembungkus eksekusi aman (`engine-build`, `engine-batch`, `engine-draft`).
- `docs/` — Dokumentasi teknis terindeks di [docs/README.md](docs/README.md).
- `tests/` — Rangkaian pengujian otomatis `pytest` (kasus `GOOD` dan `BAD` untuk setiap gate dan fitur).
- `vendor/` — Aset font terlisensi, tekstur kertas/grain, GSAP, dan penanda versi toolchain (`VERSIONS.json`).

## 3. Perintah Kunci
- **Validasi & Pre-flight**: `engine validate <PROJECT>` · `engine preflight <PROJECT>`
- **Build Produksi & Draft**: `scripts/engine-build <PROJECT>` · `engine build <PROJECT> --mode draft [--quality proxy]` · `engine build <PROJECT> --repin`
- **Inspeksi Gate & Selera**: `engine gates [--profile geoverum]` · `engine taste geoverum --lint`
- **Pratinjau & Aset**: `engine preview <PROJECT> --seg N --time S` · `engine sheet <PROJECT>` · `engine doctor`
- **Penyusunan Draf AI**: `engine draft <PROJECT> [--provider NAME] [--review] [--resume]` (tanpa `--provider`: provider `agent`, yaitu model AI yang sedang menjalankan engine menjawab bundel di `<PROJECT>/ai_request/`)
- **Inspeksi Profil & Kontrak**: `engine profile [NAME] [--origin] [--table T]` · `engine schema timeline|profile`
- **Verifikasi Kode & Dokumen**:
  - `python scripts/check_docs.py`
  - `ruff check --select E9,F63,F7,F82,F401,F841 video_engine tests scripts`
  - `pytest -q tests/test_<area>.py`
- **Kode Keluar (Exit Codes)**: `0` berhasil, `1` gate/engine gagal, `2` kesalahan argumen CLI, `3` menunggu berkas respons agent (`response.json`).

## 4. Prinsip Pengembangan Wajib
- **Satu Sumber Kebenaran**: Jangan menduplikasi aturan kreatif di luar `profiles/<channel>/creative.md` atau menduplikasi tabel bersama di luar `profiles/geoverum/core.toml`.
- **Integritas Kunci Cache**: Setiap perubahan perilaku pada kode Python atau Rust (`ALGO_VERSION`) harus tercermin pada kunci cache segmen terkait. Memecah file profil dengan `include` tidak boleh mengubah nilai `profiles.load(name).blake3`.
- **Tanpa Default Vendor AI**: Mesin tidak menunjuk provider AI bawaan. Tanpa `--provider`, env `VIDEO_ENGINE_AI_PROVIDER`, atau `default` di `ai_providers.toml`, permintaan menjadi bundel berkas untuk provider `agent`: model AI yang sedang menjalankan mesin yang menjawab. Jangan menambah fallback ke layanan lain: tiap provider hanya mencoba daftar modelnya sendiri. Gemini adalah provider opsional (`--provider gemini`, extra `gemini`), bukan pilihan bawaan dan bukan fallback.
- **Keamanan Kredensial**: Jangan pernah mencetak atau menyimpan nilai `GEMINI_API_KEY`, `XAI_API_KEY`, atau kunci API lainnya ke dalam log maupun laporan *build*.
