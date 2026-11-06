# Dokumentasi video-engine

Indeks seluruh dokumentasi. Cari pertanyaan Anda di tabel pertama, lalu buka dokumen yang ditunjuk.

## Saya ingin...

| Saya ingin... | Buka |
|---|---|
| Menjalankan mesin untuk pertama kali | [README](../README.md#mulai-cepat) |
| Tahu semua perintah `engine` dan opsinya | [CLI.md](CLI.md) |
| Menyiapkan environment dan variabel `VIDEO_ENGINE_*` | [ENVIRONMENT.md](ENVIRONMENT.md) |
| Menulis atau memahami `master_timeline.json` dan caption | [TIMELINE.md](TIMELINE.md) |
| Mengatur gaya dan aturan sebuah channel | [PROFILES.md](PROFILES.md) |
| Memahami arsitektur menyeluruh (HyperFrames vs FFmpeg+Rust, Gate, AI, Profil) | [SPEC.md](SPEC.md) |
| Tahu pemisahan lapisan profil/konsep/struktur/presentasi/selera | [STRUCTURE.md](STRUCTURE.md) |
| Tahu arti sebuah gate (R63, S01, H01, ...) dan cara melonggarkannya | [GATES.md](GATES.md) |
| Memahami tahap build, cache, manifest, dan sumber daya | [BUILD.md](BUILD.md) |
| Menyiapkan paket unggah | [DELIVERY.md](DELIVERY.md) |
| Memakai AI untuk draf timeline atau menambah provider | [AI.md](AI.md) |
| Mencari sumber musik dan SFX | [SOUND.md](SOUND.md) |
| Menjalankan atau menulis tes | [TESTING.md](TESTING.md) |
| Tahu apa yang belum didukung | [LIMITS.md](LIMITS.md) |
| Mengubah ekstensi Rust | [RUST.md](RUST.md) |
| Tahu kenapa sesuatu didesain begitu | [RATIONALE.md](RATIONALE.md) |
| Membaca spesifikasi lengkap | [SPEC.md](SPEC.md) |
| Mengerti sebuah istilah atau singkatan | [GLOSSARY.md](GLOSSARY.md) |
| Ikut menulis kode | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| Melihat apa yang berubah | [CHANGELOG.md](../CHANGELOG.md) |
| Mengolah hasil bedah video menjadi profil | [REA-VIDEO.md](REA-VIDEO.md) |
| Memahami spesifikasi arsitektur visual, kamera, dan opsi gaya `[reference_style.*]` (hatch, count-up, bubble, ...) | [REFERENCE-STYLE.md](REFERENCE-STYLE.md) |

## Bahasa dan pembaca

- **README, panduan, glosarium, indeks ini, STRUCTURE, dan REA-VIDEO** ditulis dalam bahasa Indonesia untuk tim.
- **Dokumen referensi teknis** (CLI, ENVIRONMENT, TIMELINE, PROFILES, GATES, BUILD, DELIVERY, AI, TESTING, LIMITS,
  SPEC, RUST, SOUND, RATIONALE) ditulis dalam bahasa Inggris yang ringkas dan deklaratif. Pembacanya manusia dan agent
  AI. Istilah teknis tidak diterjemahkan; artinya ada di [GLOSSARY.md](GLOSSARY.md).
- Setiap fakta punya satu rumah. Dokumen lain menautkan ke rumah itu dan tidak mengulanginya.
- Kode, komentar, dan pesan error berbahasa Inggris.

## Menjaga dokumen tetap benar

- Tabel gate di [GATES.md](GATES.md) dibuat dari `video_engine/gate_registry.py` dengan
  `engine gates --format md`. Jangan ubah isinya dengan tangan; jalankan perintah itu dan tempel hasilnya.
- `python scripts/check_docs.py` memeriksa tautan, anchor, tabel gate yang dibuat otomatis, dan bahwa setiap dokumen
  ada di indeks ini. CI menjalankannya.
- Ubah dokumen dalam commit yang sama dengan perubahan kodenya.
