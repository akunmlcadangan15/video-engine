# video-engine

Mesin video hybrid milik Hypoverum. Inputnya satu folder proyek berisi `master_timeline.json`. Hasilnya `out/final.mp4` (H.264 1080×1920 atau landscape 16:9 hingga 3840×2160; AAC 48 kHz) dan `out/build_report.json`. Jika ada gate gagal, build tidak menerbitkan apa pun dan hanya menulis `out/build_report.failed.json`.

Mesin ini memiliki dua jalur render:

- **Native** (ffmpeg + Rust): potong, pan/zoom, gabung footage dan foto.
- **HyperFrames** (HTML, GSAP, dan Three.js di Chrome tanpa layar): segmen motion-graphics peta, globe, props, beats, region, rute, kartu, grafik, dan judul.

Caption tidak digambar oleh keduanya. Rust membuat file `.ass` dari voiceover, lalu ffmpeg membakarnya di akhir. Sebelum render, **gate** memeriksa timeline dan aset (kredit, lisensi, margin, kamera, dll.), karena render gagal jauh lebih mahal daripada pemeriksaan.

## Mulai cepat

Syarat: Linux x64 atau macOS Apple Silicon, Python 3.10+, toolchain Rust, `ffmpeg` di `PATH`, dan ~700 MB untuk toolchain HyperFrames. Mesin memerlukan ffmpeg 7+ dengan filter `subtitles`, `loudnorm`, `sidechaincompress` serta encoder `libx264` dan `aac` (dicek oleh `engine doctor`).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" maturin
source ~/.cargo/env
(cd rust/hypoverum_core_rs && maturin develop --release)
engine doctor --install
```

Coba proyek contoh tanpa jaringan:

```bash
cp -r examples/minimal /tmp/minimal && cd /tmp/minimal
./prepare.sh
engine validate .
engine preflight .
engine build .
engine sheet .
```

### macOS Apple Silicon

Platform setara Linux x64: gate dan spesifikasi output sama, hanya file tidak bit-identik. Python harus arm64 asli (bukan Rosetta). Detail lengkap di [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md#platforms).

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

Di mesin render Hypoverum jangan panggil `engine build` langsung. Gunakan `scripts/engine-build` — ia memeriksa `engine.env`, ruang disk (min 8 GB), dan kunci render global. Lihat [AGENTS.md](AGENTS.md) dan [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md#build-wrappers).

## Alur kerja

```
bahan (aset, voiceover) ──► draft AI ──► master_timeline.json ──► validate ──► preflight (gate) ──► build ──► QA ──► publish
                                                                                          │              │
                                                                                       final.mp4    build_report.json
```

1. **Bahan:** `assets/`, `credits.txt`, voiceover, opsional skrip.
2. **Draft (opsional):** `engine draft` membuat timeline dari AI (3 konsep, 1 dipilih) dan tidak menimpa `master_timeline.json`.
3. **Preflight:** `engine validate`, `engine words`, `engine preflight` — semua gate tanpa render.
4. **Build:** segmen render paralel dan di-cache, lalu komposit (sambung, transisi, caption, watermark), audio (mix, ducking, loudnorm), dan mux.
5. **Gate akhir:** frame, QA piksel, loudness, jeda hening, sumber audio. Hanya diterbitkan bila semuanya lolos.
6. **Delivery:** `engine deliver --export` menyiapkan paket siap unggah. Mesin tidak mengunggah apa pun.

Detail tiap tahap: [docs/BUILD.md](docs/BUILD.md).

## Perintah utama

| Perintah | Fungsi |
|---|---|
| `engine validate P` | Cek skema `master_timeline.json`. |
| `engine preflight P` | Semua gate tanpa render. |
| `engine build P` | Build penuh ke `P/out/`. `--mode draft` untuk uji; `--repin` untuk caption basi. |
| `engine gates` | Daftar gate dan statusnya per profil. |
| `engine preview P --seg N --time S` | Satu PNG dari satu segmen. |
| `engine sheet P` | Contact sheet dari `out/final.mp4`. |
| `engine words P` | Transkripsi voiceover dan pin kata ke timeline. |
| `engine draft P` | AI membuat draft timeline. |
| `engine deliver P --export DIR` | Paket siap unggah. |
| `engine taste CHANNEL` | Selera channel (like/reject). |
| `engine doctor` | Cek toolchain di `vendor/`. |
| `engine toolchain [use SPEC]` | Lihat/ganti versi HyperFrames. |
| `engine draft-preview P` | Preview awal via render segmen normal. |

Semua perintah dan opsinya: [docs/CLI.md](docs/CLI.md). Kode keluar: 0 sukses, 1 gate/engine gagal, 2 salah pakai, 3 menunggu jawaban agent.

## Profil channel

Gaya dan aturan per channel ada di `profiles/`: `default.toml` (dasar mesin) dan satu file per channel (mis. `geoverum.toml` + `profiles/geoverum/`). Prioritas: default < profil channel < nilai proyek. Catatan selera ada di `profiles/taste/`. Penjelasan: [docs/PROFILES.md](docs/PROFILES.md).

Gate integritas (decode, frame, sumber, audio) selalu wajib; gate gaya/lint hanya peringatan kecuali profil mewajibkan. Detail: [docs/GATES.md](docs/GATES.md) dan [docs/TIMELINE.md](docs/TIMELINE.md).

## Struktur repo

| Jalur | Isi |
|---|---|
| `video_engine/` | Paket Python (CLI, timeline, gate, renderer, audio). |
| `video_engine/ai/` | Provider AI (Gemini, agent, OpenAI-compatible). |
| `video_engine/gates/` | Modul gate; daftar lengkap di `video_engine/gate_registry.py`. |
| `rust/hypoverum_core_rs/` | Ekstensi Rust: kamera, caption `.ass`, jeda, QA. |
| `profiles/` | Profil channel dan catatan selera. |
| `docs/` | Dokumentasi. Mulai dari [docs/README.md](docs/README.md). |
| `tests/` | Pytest (kasus GOOD/BAD per gate). |
| `examples/` | Contoh offline. |
| `vendor/` | Toolchain terkunci (Node, HyperFrames, Chrome). |

## Dokumentasi

| Dokumen | Isi |
|---|---|
| [docs/README.md](docs/README.md) | Indeks dokumentasi. |
| [docs/CLI.md](docs/CLI.md) | Semua perintah `engine`. |
| [docs/GATES.md](docs/GATES.md) | Semua gate. |
| [docs/PROFILES.md](docs/PROFILES.md) | Format profil. |
| [docs/TIMELINE.md](docs/TIMELINE.md) | Aturan timeline dan caption. |
| [docs/BUILD.md](docs/BUILD.md) | Build, manifest, cache. |
| [docs/SPEC.md](docs/SPEC.md) | Spesifikasi lengkap. |
| [docs/RATIONALE.md](docs/RATIONALE.md) | Alasan desain. |
| [docs/RUST.md](docs/RUST.md) | Crate Rust. |
| [docs/SOUND.md](docs/SOUND.md) | Sumber suara. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Cara bekerja di repo. |
| [CHANGELOG.md](CHANGELOG.md) | Perubahan penting. |
