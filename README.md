# video-engine

Mesin produksi video hybrid untuk pembuatan video dokumenter geografi dan *motion-graphics* berbasis data. Menerima input satu folder proyek berisi `master_timeline.json` dan menghasilkan `out/final.mp4` (H.264 8-bit atau 10-bit High 10; portrait 9:16 `1080×1920` maupun landscape 16:9 hingga `3840×2160`; audio AAC 48 kHz) beserta laporan terstruktur `out/build_report.json`. Jika terdapat kegagalan pada *kernel gate*, *build* tidak menerbitkan video dan hanya menulis `out/build_report.failed.json`.

Mesin ini menggabungkan dua jalur render deterministik:

- **Native (`NATIVE_RUST_FFMPEG`)**: Pemotongan segmen, gerakan kamera Ken Burns sub-piksel (diinterpolasi di Rust), *overlay* kartu/label terukur, dan *color grading* LUT 3D melalui FFmpeg.
- **HyperFrames (`HYPERFRAMES_GSAP`)**: Adegan *motion-graphics* berbasis HTML, SVG, GSAP, dan Three.js yang dirender per frame secara deterministik di dalam *headless Chrome* (peta satelit gelap maupun peta arsip kertas, bola dunia 3D, poligon wilayah dengan *neon glow* atau arsiran *hatch*, rute animasi, *beats layout*, diagram penampang *cutaway*, termometer, dan partikel).

Caption tidak digambar di dalam HTML, melainkan dikompilasi oleh ekstensi Rust menjadi berkas `.ass` dari *word timestamps* narasi, lalu di-*burn-in* oleh FFmpeg pada tahap komposit akhir setelah lolos pemeriksaan penempatan otomatis (gate `R63` dan `Z01`).

## Mulai cepat

Syarat sistem: Linux x64 atau macOS Apple Silicon, Python 3.11+, toolchain Rust (`cargo`), `ffmpeg` (versi 7+ dengan filter `subtitles`, `loudnorm`, `sidechaincompress`, serta encoder `libx264` dan `aac`), dan ruang disk ~700 MB untuk toolchain HyperFrames.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" maturin
source ~/.cargo/env
(cd rust/hypoverum_core_rs && maturin develop --release)
engine doctor --install
```

Menjalankan proyek contoh secara *offline*:

```bash
cp -r examples/minimal /tmp/minimal && cd /tmp/minimal
./prepare.sh
engine validate .
engine preflight .
engine build .
engine sheet .
```

### macOS Apple Silicon

Didukung setara dengan Linux x64 (spesifikasi output dan gate identik; lihat [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md#platforms)):

```bash
xcode-select --install
brew install python@3.12 ffmpeg rustup
rustup-init -y && source ~/.cargo/env
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" maturin
(cd rust/hypoverum_core_rs && maturin develop --release)
engine doctor --install
```

### Wrapper build

Untuk eksekusi produksi yang terproteksi, gunakan `scripts/engine-build` atau `scripts/engine-batch` yang secara otomatis memuat `engine.env`, memeriksa sisa ruang disk (minimal 8 GB), dan mengunci *render lock* global. Lihat [AGENTS.md](AGENTS.md) dan [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md#build-wrappers).

## Alur kerja

```
bahan (aset, voiceover) ──► draft AI ──► master_timeline.json ──► validate ──► preflight (gate) ──► build ──► QA ──► publish
                                                                                          │              │
                                                                                       final.mp4    build_report.json
```

1. **Bahan**: Siapkan `assets/`, `credits.txt`, rekaman *voiceover*, dan naskah opsional.
2. **Draft AI (opsional)**: `engine draft` menyusun 3 konsep *storyboard*, memilih konsep terbaik, dan merakit `master_timeline.draft.json`.
3. **Preflight**: `engine validate`, `engine words`, dan `engine preflight` memverifikasi seluruh gate sebelum merender video.
4. **Build**: Segmen dirender secara paralel dan disimpan di cache berbasis konten (`BLAKE3`), dilanjutkan dengan komposit transisi, *burn-in* caption, *watermark*, *mixing* musik/SFX dengan *ducking*, dan normalisasi `loudnorm`.
5. **Gate Akhir**: Memeriksa jumlah frame, distribusi piksel (`R38`), *loudness* (`A01`), dan jeda hening (`S01`).
6. **Delivery**: `engine deliver --export` menyiapkan paket distribusi akhir. Detail tahap build: [docs/BUILD.md](docs/BUILD.md).

## Perintah utama

| Perintah | Fungsi |
|---|---|
| `engine validate P` | Memeriksa skema `master_timeline.json`. |
| `engine preflight P` | Menjalankan seluruh gate pra-render (statis, matematika, dan probe DOM). |
| `engine build P` | Membangun video penuh ke `P/out/`. Gunakan `--mode draft` untuk pratinjau cepat atau `--repin` untuk memperbarui pin caption. |
| `engine gates` | Menampilkan daftar gate dan efeknya per profil (`KERNEL` vs `LINT`). |
| `engine preview P --seg N --time S` | Merender satu frame PNG dari segmen tertentu untuk inspeksi cepat. |
| `engine sheet P` | Membuat *contact sheet* dari `out/final.mp4`. |
| `engine words P` | Mentranskripsi *voiceover* dan menyematkan *word timestamps* ke timeline. |
| `engine draft P` | Menghasilkan draf timeline menggunakan AI *director*. |
| `engine deliver P --export DIR` | Menyiapkan paket siap unggah. |
| `engine taste CHANNEL [--lint]` | Menampilkan atau memverifikasi keselarasan catatan selera channel terhadap profil TOML. |
| `engine doctor [--install]` | Memeriksa atau memasang toolchain Node, HyperFrames, dan Chrome di `vendor/`. |
| `engine toolchain [use SPEC]` | Melihat atau mengganti versi HyperFrames yang aktif. |
| `engine draft-preview P` | Merender 5 segmen pertama secara cepat tanpa komposit akhir. |

Referensi lengkap seluruh perintah dan opsi: [docs/CLI.md](docs/CLI.md).

## Struktur repo

| Jalur | Isi |
|---|---|
| `video_engine/` | Paket utama Python (CLI, skema timeline, renderer native & HyperFrames, layout, legibility, audio, cache). |
| `video_engine/ai/` & `director/` | Lapisan provider AI (Gemini, Agent, OpenAI-compatible) serta kompilator pola *storyboard*. |
| `video_engine/gates/` | Modul gate 3-lapis; definisi terpusat di `video_engine/gate_registry.py`. |
| `rust/hypoverum_core_rs/` | Ekstensi Rust (PyO3): interpolasi kamera, kompilator `.ass`, deteksi jeda suara, dan statistik piksel. |
| `profiles/` | Profil channel (`default.toml`, `geoverum.toml`, `geoverum_landscape.toml`, sub-tabel `geoverum/`, dan `taste/`). |
| `scripts/` | Skrip pemeriksaan dokumentasi (`check_docs.py`) dan *wrapper* eksekusi (`engine-build`, `engine-batch`, `engine-draft`). |
| `docs/` | Dokumentasi teknis lengkap. Mulai dari [docs/README.md](docs/README.md). |
| `tests/` | Rangkaian pengujian `pytest` untuk seluruh modul dan gate. |
| `examples/` | Proyek contoh mandiri (`minimal` 9:16 dan `landscape` 16:9). |
| `vendor/` | Aset font, tekstur kertas/grain, GSAP, dan konfigurasi versi toolchain. |

## Dokumentasi

| Dokumen | Isi |
|---|---|
| [docs/README.md](docs/README.md) | Indeks lengkap seluruh dokumentasi. |
| [docs/SPEC.md](docs/SPEC.md) | Spesifikasi teknis menyeluruh. |
| [docs/STRUCTURE.md](docs/STRUCTURE.md) | Pemisahan lima lapisan arsitektur dan satu sumber kebenaran per lapisan. |
| [docs/REFERENCE-STYLE.md](docs/REFERENCE-STYLE.md) | Spesifikasi arsitektur visual, kamera, dan katalog opsi `[reference_style.*]` Geoverum. |
| [docs/PROFILES.md](docs/PROFILES.md) | Panduan konfigurasi profil channel, orientasi 9:16 vs 16:9, dan mekanisme `include`. |
| [docs/GATES.md](docs/GATES.md) | Katalog lengkap seluruh gate (`KERNEL` vs `LINT`) dan cara mengonfigurasinya. |
| [docs/TIMELINE.md](docs/TIMELINE.md) | Kontrak data `master_timeline.json`, skema adegan, dan aturan caption. |
| [docs/CLI.md](docs/CLI.md) | Referensi seluruh sub-perintah `engine`. |
| [docs/BUILD.md](docs/BUILD.md) | Tahapan build, manifest terkunci, cache `BLAKE3`, dan manajemen sumber daya. |
| [docs/AI.md](docs/AI.md) | Konfigurasi provider AI (`ai_providers.toml`), *director*, dan alur *agent handoff*. |
| [docs/SOUND.md](docs/SOUND.md) | Pencarian musik/SFX, *ducking*, dan integrasi lisensi. |
| [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) | Variabel lingkungan `VIDEO_ENGINE_*` dan dukungan lintas platform. |
| [docs/DELIVERY.md](docs/DELIVERY.md) | Konvensi penamaan dan pemaketan hasil akhir. |
| [docs/TESTING.md](docs/TESTING.md) | Cara menjalankan pengujian, *golden frames*, dan konvensi tes. |
| [docs/RUST.md](docs/RUST.md) | Panduan pengembangan ekstensi Rust `hypoverum_core_rs`. |
| [docs/RATIONALE.md](docs/RATIONALE.md) | Alasan rekayasa di balik setiap keputusan desain. |
| [docs/LIMITS.md](docs/LIMITS.md) | Batasan sistem saat ini. |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Glosarium istilah teknis. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Panduan kontribusi dan pemeriksaan pra-commit. |
| [CHANGELOG.md](CHANGELOG.md) | Riwayat perubahan fitur dan perbaikan. |
