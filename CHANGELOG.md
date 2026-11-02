# Changelog

Perubahan penting pada mesin. Format mengikuti [Keep a Changelog](https://keepachangelog.com/id-ID/1.1.0/).
Versi paket belum diberi nomor rilis (`0.1.0`), jadi semua catatan ada di bagian "Belum dirilis".

## Belum dirilis

### Ditambahkan

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

### Diubah

- **Hash kode cache** dihitung dari pohon sintaks Python tanpa docstring (`util.code_hash`). Mengubah komentar,
  docstring, atau format modul yang masuk kunci cache tidak lagi membuang cache render. **Build pertama setelah
  perubahan ini merender ulang segmen yang ter-cache satu kali**, karena algoritma hash berubah.
- **Selera dan profil Geoverum diselaraskan:** caption 92 dan 104 px ditolak (`ab_sizes = [84]`), transisi
  page-turn dan perkamen dipindah ke daftar yang ditolak, dan true peak dinyatakan -1.5 dBTP.
- **README lama** dipecah menjadi `docs/ENVIRONMENT.md`, `CLI.md`, `TIMELINE.md`, `PROFILES.md`, `GATES.md`, `BUILD.md`,
  `DELIVERY.md`, `AI.md`, `TESTING.md`, dan `LIMITS.md`. Peta pindahnya ada di README.

- **Dokumentasi platform**: toolchain HyperFrames terkunci untuk Linux x64; `docs/ENVIRONMENT.md` dan
  `docs/LIMITS.md` kini menyebutnya.
- `cargo fmt` pada `ass.rs` (hanya format; output crate dan `ALGO_VERSION` tidak berubah).

### Diperbaiki

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
