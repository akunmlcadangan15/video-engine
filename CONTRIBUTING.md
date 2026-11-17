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

Initializer paket ringan tidak berarti seluruh operasi bebas Rust. Hash profil/cache, konversi frame,
dan render tetap dapat membutuhkan ekstensi. Jangan mengganti algoritme native dengan fallback diam-diam.
Perubahan lazy runtime harus mengatur Rayon sebelum operasi native pertama dan diuji dalam subprocess
yang memblokir import ekstensi, termasuk urutan import yang berbeda.

## Pemeriksaan sebelum commit

| Yang diubah | Jalankan |
|---|---|
| Satu area Python | `pytest -q tests/test_<area>.py` |
| Banyak area, atau sebelum push | `pytest -q -n 4` (sekitar 8 menit dengan Chrome terpasang) |
| Python | `ruff check --select E9,F63,F7,F82,F401,F841 video_engine tests scripts` dan `python -m compileall -q video_engine tests` |
| Dokumentasi | `python scripts/check_docs.py` |
| Rust | `cargo fmt --check`, `cargo clippy -j 4 --all-targets -- -D warnings`, `cargo test -j 4 --no-default-features` di `rust/hypoverum_core_rs/` |

Tes yang butuh toolchain HyperFrames dilewati dengan petunjuk bila `vendor/node` dan `vendor/chrome` belum terpasang.
`VIDEO_ENGINE_REQUIRE_TOOLCHAIN=1` membuatnya gagal, seperti di CI malam. Seluruh scratch tes ada di
`/tmp/video_engine_test_shm/` (atur dengan `VIDEO_ENGINE_SHM_TEST`). Panduan tes: [docs/TESTING.md](docs/TESTING.md).

## Aturan yang harus dijaga

**Gate.** Setiap gate punya satu entri di `video_engine/gates/registry.py` (id, kelas, lapis, ringkasan, saklar).
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

Saat relokasi, daftar sumber hash harus menunjuk implementasi kanonis dan helper hasil ekstraksi.
Perubahan path/import dapat menginvalidasi cache satu kali meskipun kontrak output tidak berubah.
Jangan menghitung hash hanya dari shim kompatibilitas atau menghapus cache pengguna sebagai langkah migrasi.

**Profil.** Nilai profil gabungan menentukan hash profil. Memecah atau memindahkan bagian profil dengan `include`
tidak boleh mengubah hash itu: bandingkan `profiles.load(nama).blake3` sebelum dan sesudah.

**Waktu.** Semua waktu di mesin adalah frame bilangan bulat. Konversi detik ke frame hanya lewat `sec_to_frame`.

**Fakta punya satu rumah.** Jangan menyalin angka atau aturan ke dokumen lain; tautkan ke rumahnya. Ubah
dokumentasi dalam commit yang sama dengan kodenya.

## Batas paket dan verifikasi migrasi

Baca [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) sebelum memilih lokasi kode. Lima lapisan keputusan
di [docs/STRUCTURE.md](docs/STRUCTURE.md) tetap berlaku meskipun jumlah paket Python lebih banyak.
Import internal memakai lokasi kanonis (`core`, `profiles`, `timeline`, `layout`, `render`, `gates`,
`draft`, `sound`, `cloud`). Modul flat lama yang masih tersedia adalah alias modul: jangan mengubahnya
menjadi salinan implementasi atau re-export yang memutus identitas modul dan monkeypatch.

Model profil/layout/grade tidak mengimpor eksekusi renderer; layout memakai pemeriksaan murni miliknya
sendiri, sedangkan gate memiliki ID, severity, dan findings. Registry gate tidak boleh menarik runner
render saat diimpor. Entrypoint dan orkestrator root bukan dependency paket domain.

Sebelum menyatakan migrasi selesai, periksa:

- Schema/API/CLI, hash profil efektif, gate IDs, serta identitas alias dan propagasi monkeypatch.
- Boundary import termasuk import lokal di dalam fungsi; pengecualian transisi harus eksplisit dan dikurangi.
- Kunci cache segmen/stage/transisi/DOM, resource `render/template/` dan `render/hf/`, serta wheel terpasang
  di luar editable checkout dengan akar `profiles/` dan `vendor/` yang didukung.
- Bootstrap cloud tanpa Rust/Modal eager, konfigurasi environment sebelum aplikasi Modal dibuat,
  serta closure `/src` dan aset eksternal. Build Modal berbayar tidak diperlukan untuk uji relokasi.
- Jalur informasi CLI/model tanpa ekstensi, kegagalan native berupa `EngineError`, pengaturan thread,
  validate/preflight serta smoke render native dan HyperFrames pada toolchain lengkap.

Jalankan tes area terkait, lint wajib dan pemeriksaan dokumen; untuk migrasi lintas paket jalankan full suite
dan laporkan skip secara eksplisit. Keberhasilan import saja tidak membuktikan kompatibilitas hasil render.

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
