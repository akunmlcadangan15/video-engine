# video-engine

Mesin video vertikal hybrid milik Hypoverum. Masukannya satu folder proyek berisi `master_timeline.json`. Keluarannya
`out/final.mp4` (H.264 1080×1920, AAC 48 kHz) dan `out/build_report.json`. Kalau ada gate yang gagal, build tidak
menerbitkan apa pun dan hanya menulis `out/build_report.failed.json`.

Mesin ini punya dua jalur render:

- **Native** (ffmpeg dan ekstensi Rust): memotong, pan/zoom, dan menggabungkan footage dan foto.
- **HyperFrames** (HTML dan GSAP di Chrome tanpa layar): peta, pin, region, rute, kartu, dan judul.

Caption tidak digambar oleh keduanya. Rust membuat file `.ass` dari kata-kata voiceover, lalu ffmpeg membakarnya
sekali di akhir. Sebelum merender apa pun, **gate** memeriksa timeline dan aset (kredit, lisensi, margin, kamera, dan
seterusnya), karena render yang gagal jauh lebih mahal daripada pemeriksaannya.

## Mulai cepat

Syarat: Linux x64 atau macOS Apple Silicon (keduanya platform setara, lihat di bawah), Python 3.10 atau lebih baru, toolchain Rust, `ffmpeg` di `PATH`, dan sekitar 700 MB ruang disk untuk toolchain
HyperFrames. Mesin render memakai ffmpeg 7.1 sampai 9.0; `engine doctor` memeriksa bahwa ffmpeg punya filter `subtitles`,
`loudnorm`, `sidechaincompress` serta encoder `libx264` dan `aac`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" maturin
source ~/.cargo/env
(cd rust/hypoverum_core_rs && maturin develop --release)   # ekstensi Rust
engine doctor --install                                      # Node, HyperFrames, Chrome (versi terkunci di vendor/)
```

Coba proyek contoh yang tidak butuh jaringan:

```bash
cp -r examples/minimal /tmp/minimal && cd /tmp/minimal
./prepare.sh              # membuat peta dan narasi uji dengan ffmpeg
engine validate .         # cek skema
engine preflight .        # semua gate tanpa render
engine build .            # out/final.mp4 dan out/build_report.json (sekitar 20 detik)
engine sheet .            # out/sheet.png: satu kotak per segmen
```

### Instalasi di macOS (Apple Silicon)

macOS arm64 dan Linux x64 adalah platform setara: gate yang sama berlaku di keduanya, dan keduanya bisa membangun
serta menyerahkan video final (`engine deliver`). Spesifikasi keluaran sama (H.264 1080×1920, AAC 48 kHz, loudness
dan QA yang sama), tetapi file tidak identik bit demi bit antar-platform. Python harus build arm64 asli (bukan di
bawah Rosetta).

```bash
xcode-select --install                                       # compiler dan git
# Homebrew: https://brew.sh
brew install python@3.12 ffmpeg@7 rustup
export PATH="$(brew --prefix ffmpeg@7)/bin:$PATH"            # ffmpeg@7 keg-only; atau set VIDEO_ENGINE_FFMPEG / VIDEO_ENGINE_FFPROBE
rustup-init -y && source ~/.cargo/env
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" maturin
(cd rust/hypoverum_core_rs && maturin develop --release)     # ekstensi Rust
engine doctor --install                                      # Node arm64, HyperFrames, Chrome mac-arm64 (versi terkunci di vendor/)
engine doctor                                                # baris platform, ffmpeg mayor 7, filter dan encoder
```

Beda dengan Linux: folder scratch bawaan adalah `$TMPDIR/video_engine` (macOS tidak punya `/dev/shm`; atur dengan
`VIDEO_ENGINE_SHM`), dan Homebrew `ffmpeg@7` tidak masuk `PATH` sendiri. Tetap pakai ffmpeg mayor 7 karena `engine
doctor` menolak versi lain. Catatan platform lengkap ada di [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md#platforms).

### Wrapper build

`scripts/engine-build` dan `scripts/engine-batch` menjalankan `engine build` dan `engine batch` dengan penjagaan yang
sama di Linux dan macOS: muat `engine.env` (atau `VIDEO_ENGINE_ENV_FILE`), tolak bila ruang disk bebas di
filesystem `out/` kurang dari `VIDEO_ENGINE_MIN_FREE_GB` (bawaan 8), ambil kunci render global
(`VIDEO_ENGINE_RENDER_LOCK`) tanpa menunggu, lalu jalankan di bawah `nice -n 10`. Kode keluar engine diteruskan.
Letakkan folder `scripts/` di `PATH` atau panggil dengan path-nya. Detail dan variabelnya ada di
[docs/ENVIRONMENT.md](docs/ENVIRONMENT.md#build-wrappers).

Di mesin render Hypoverum, jangan panggil `engine build` langsung. Pakai wrapper `engine-build` (kunci render, cek
disk, environment). Lihat [AGENTS.md](AGENTS.md).

## Alur kerja

```
bahan ──► draft (AI) ──► master_timeline.json ──► validate ──► preflight (gate) ──► build ──► gate akhir ──► publish
 aset,      engine draft     dibuat atau diedit       skema        tanpa render        render      loudness,      final.mp4 +
 voiceover  (opsional)       manusia / agent                                          paralel     hening, QA     build_report.json
                                                                                                                 │
                                                                                                    engine deliver ──► paket unggah
```

1. **Bahan:** aset di `assets/`, `credits.txt`, voiceover, dan opsional skrip.
2. **Draft:** `engine draft` meminta AI membuat timeline (tiga konsep storyboard, satu dipilih). Hasilnya tidak
   menimpa `master_timeline.json`.
3. **Validasi dan pre-flight:** `engine validate`, `engine words` (kata voiceover dipin ke timeline), `engine preflight`.
4. **Build:** segmen dirender paralel dan di-cache, lalu komposit (sambung, transisi, caption, watermark), audio
   (mix, ducking, loudnorm), dan mux tanpa encode ulang.
5. **Gate akhir:** frame, QA piksel, loudness, jeda hening, dan sumber audio. File baru diterbitkan hanya bila semuanya
   lolos.
6. **Delivery:** `engine deliver --export` menyiapkan folder siap unggah. Mesin tidak mengunggah apa pun.

Penjelasan lengkap tiap tahap ada di [docs/BUILD.md](docs/BUILD.md).

## Perintah utama

| Perintah | Fungsi |
|---|---|
| `engine validate P` | Cek skema `master_timeline.json`. |
| `engine preflight P` | Semua gate tanpa render. Keluar dengan kode 1 bila ada yang gagal. |
| `engine build P` | Build penuh. Hasil di `P/out/`. Opsi `--mode draft` untuk build percobaan, `--repin` untuk memperbarui caption yang basi. |
| `engine gates` | Daftar gate, statusnya di sebuah profil (`--profile`), dan statistik dari laporan build (`--stats`). |
| `engine preview P --seg N --time S` | Satu PNG dari satu segmen, tanpa render video. |
| `engine sheet P` | Contact sheet dari `out/final.mp4`. |
| `engine words P` | Transkripsi voiceover dan pin kata ke timeline. |
| `engine draft P` | AI membuat draf timeline. |
| `engine deliver P --export DIR` | Paket siap unggah (video, JOIN, credits, SRT, thumbnail, metadata). |
| `engine taste CHANNEL` | Lihat atau ubah catatan selera channel; `--lint` membandingkannya dengan profil. |
| `engine doctor` | Cek toolchain di `vendor/`. |

Semua perintah dan opsinya ada di [docs/CLI.md](docs/CLI.md). Kode keluar: 0 sukses, 1 gate atau engine gagal, 2 salah
pakai, 3 menunggu jawaban agent.

## Profil channel

Gaya dan aturan per channel ada di `profiles/`: `default.toml` (nilai dasar mesin) dan satu berkas per channel
(sekarang `geoverum.toml`, dengan bagian besarnya di `profiles/geoverum/`). Urutan prioritas: default, lalu profil
channel, lalu nilai proyek. Catatan selera channel (yang disukai dan ditolak, beserta alasannya) ada di
`profiles/taste/`. Penjelasan: [docs/PROFILES.md](docs/PROFILES.md).

## Struktur repo

| Jalur | Isi |
|---|---|
| `video_engine/` | Paket Python: CLI, timeline, gate, renderer, audio, draft, cache. |
| `video_engine/ai/` | Provider AI (Gemini, agent lewat berkas, OpenAI-compatible). |
| `video_engine/gates/` | Modul gate. Daftar lengkap gate ada di `video_engine/gate_registry.py`. |
| `rust/hypoverum_core_rs/` | Ekstensi Rust (PyO3): kamera, caption `.ass`, jeda, metrik QA, hash. |
| `profiles/` | Profil channel dan catatan selera. |
| `docs/` | Dokumentasi. Mulai dari [docs/README.md](docs/README.md). |
| `tests/` | Tes pytest, dengan kasus GOOD dan BAD untuk tiap gate. |
| `examples/` | Proyek contoh yang berdiri sendiri dan tidak butuh jaringan. |
| `vendor/` | Toolchain terkunci: Node, HyperFrames, Chrome, font, GSAP. Yang terpasang tidak di-commit. |
| `ai_providers.toml` | Daftar provider AI. |

## Dokumentasi

| Dokumen | Isi |
|---|---|
| [docs/README.md](docs/README.md) | Indeks dan peta seluruh dokumentasi. |
| [docs/CLI.md](docs/CLI.md) | Semua perintah `engine` dan opsinya. |
| [docs/GATES.md](docs/GATES.md) | Semua gate: peran, ambang, dan konfigurasinya. |
| [docs/PROFILES.md](docs/PROFILES.md) | Format profil channel. |
| [docs/TIMELINE.md](docs/TIMELINE.md) | Aturan `master_timeline.json` dan caption. |
| [docs/BUILD.md](docs/BUILD.md) | Tahap build, manifest, resource, cache. |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Istilah dan singkatan. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Cara bekerja di repo ini. |
| [CHANGELOG.md](CHANGELOG.md) | Perubahan penting. |

Spesifikasi lengkap ada di [docs/SPEC.md](docs/SPEC.md), alasan desain di [docs/RATIONALE.md](docs/RATIONALE.md),
crate Rust di [docs/RUST.md](docs/RUST.md), dan sumber suara di [docs/SOUND.md](docs/SOUND.md).

## Bagian README lama

README ini dulu satu berkas panjang berbahasa Inggris. Isinya dipindahkan tanpa diubah maknanya.

| Bagian lama | Sekarang |
|---|---|
| Repository layout | [Struktur repo](#struktur-repo) di atas |
| Environment | [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) |
| CLI | [docs/CLI.md](docs/CLI.md) |
| Timeline rules, Captions | [docs/TIMELINE.md](docs/TIMELINE.md) |
| Profiles | [docs/PROFILES.md](docs/PROFILES.md) |
| Gates | [docs/GATES.md](docs/GATES.md) |
| Build pipeline, Build manifest, Resources, Stream-copy and concat rules, Cache leases | [docs/BUILD.md](docs/BUILD.md) |
| Delivery | [docs/DELIVERY.md](docs/DELIVERY.md) |
| AI providers, Gemini, Agent handoff, Adding a model, Director, Director stage, Draft | [docs/AI.md](docs/AI.md) |
| Tests | [docs/TESTING.md](docs/TESTING.md) |
| Limits and not implemented | [docs/LIMITS.md](docs/LIMITS.md) |
