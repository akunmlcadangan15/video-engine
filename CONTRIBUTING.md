# Berkontribusi

Panduan bekerja di repo ini: menyiapkan environment, menjalankan pemeriksaan, dan aturan yang harus dijaga.
Aturan khusus agent AI ada di [AGENTS.md](AGENTS.md).

## Menyiapkan environment

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]" maturin pytest-xdist
source ~/.cargo/env
export CC=/usr/bin/gcc
(cd rust/hypoverum_core_rs && maturin develop --release)   # pasang ekstensi Rust ke venv
engine doctor --install                                      # Node, HyperFrames, Chrome ke vendor/
```

Python memakai modul Rust yang terpasang, bukan sumbernya. Setelah mengubah kode Rust, jalankan ulang
`maturin develop --release`. Selalu pakai `--release`: build debug jauh lebih lambat dan berhenti pada integer
overflow. Detail dan jebakan build Rust: [docs/RUST.md](docs/RUST.md).

## Pemeriksaan sebelum commit

| Yang diubah | Jalankan |
|---|---|
| Satu area Python | `pytest -q tests/test_<area>.py` |
| Banyak area, atau sebelum push | `pytest -q -n 4` (sekitar 8 menit dengan Chrome terpasang) |
| Python | `ruff check --select E9,F63,F7,F82 video_engine tests` dan `python -m compileall -q video_engine tests` |
| Dokumentasi | `python scripts/check_docs.py` |
| Rust | `cargo fmt --check`, `cargo clippy -j 4 --all-targets -- -D warnings`, `cargo test -j 4 --no-default-features` di `rust/hypoverum_core_rs/` |

Tes yang butuh toolchain HyperFrames dilewati dengan petunjuk bila `vendor/node` dan `vendor/chrome` belum terpasang.
`VIDEO_ENGINE_REQUIRE_TOOLCHAIN=1` membuatnya gagal, seperti di CI malam. Seluruh scratch tes ada di
`/tmp/video_engine_test_shm/` (atur dengan `VIDEO_ENGINE_SHM_TEST`). Panduan tes: [docs/TESTING.md](docs/TESTING.md).

## Aturan yang harus dijaga

**Gate.** Setiap gate punya satu entri di `video_engine/gate_registry.py` (id, kelas, lapis, ringkasan, saklar).
Menambah gate berarti:

1. menambah entrinya di registry dan menulis logikanya di modul gate;
2. menulis tes dengan kasus GOOD dan BAD yang diberi label;
3. menjalankan `engine gates --format md` dan menempel hasilnya di blok yang dibuat otomatis di
   [docs/GATES.md](docs/GATES.md);
4. menuliskan aturan lengkapnya (ambang dan kunci konfigurasi) di bagian detail dokumen itu.

`tests/test_gate_registry.py` gagal bila sebuah id yang dikeluarkan kode tidak terdaftar, atau sebaliknya.

**Kunci cache.** Hasil yang di-cache harus punya kunci yang memuat semua masukannya. Aturan lengkapnya ada di
[docs/RUST.md](docs/RUST.md#cache-key-rules). Perubahan Rust yang mengubah output (kamera, warp, teks `.ass`)
wajib menaikkan `ALGO_VERSION` di `lib.rs`. Hash kode Python dihitung dari pohon sintaksnya tanpa docstring, jadi
mengubah komentar, docstring, atau format tidak membuang cache; mengubah perilaku membuangnya.

**Profil.** Nilai profil gabungan menentukan hash profil. Memecah atau memindahkan bagian profil dengan `include`
tidak boleh mengubah hash itu: bandingkan `profiles.load(nama).blake3` sebelum dan sesudah.

**Waktu.** Semua waktu di mesin adalah frame bilangan bulat. Konversi detik ke frame hanya lewat `sec_to_frame`.

**Fakta punya satu rumah.** Jangan menyalin angka atau aturan ke dokumen lain; tautkan ke rumahnya. Ubah
dokumentasi dalam commit yang sama dengan kodenya.

## Commit

- Satu perubahan yang utuh per commit; pesan commit dalam bahasa Inggris, kalimat perintah, dengan alasan di badan
  pesan.
- Jangan menyimpan cache, hasil render, atau toolchain terpasang (`vendor/node`, `vendor/chrome`,
  `vendor/npm/node_modules`) di commit; semuanya ada di `.gitignore`.
- Jangan mencetak atau menyimpan `GEMINI_API_KEY` atau kunci provider lain.

## Mesin render Hypoverum

Hanya berlaku di mesin render, bukan di checkout biasa:

- Jalankan build lewat `engine-build` dan `engine-batch`, bukan `engine build`. Versi portabel (Linux dan macOS) ada di `scripts/`; tesnya `tests/test_wrapper_scripts.py`.
- Jangan menjalankan `engine clean` atau `engine cache --prune` tanpa izin pemilik repo.
- Render uji ke `/workspace/_scratch/`.
