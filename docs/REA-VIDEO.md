# Alur Konversi Style-Sheet (`tools/rea-style`) ke Profil Channel

## Konsep
Direktori `tools/rea-style/` berisi skema antara (*intermediate style-sheet*) dan skrip kompilasi untuk menghasilkan atau memperbarui blok konfigurasi profil di dalam `profiles/`.

Struktur:
- `tools/rea-style/template.toml` : Skema kanonis parameter visual, tipografi, kamera, dan audio.
- `tools/rea-style/profile-builder.py` : Utilitas konversi dari `template.toml` (dan `breakdown.json` opsional) menjadi blok TOML profil channel.
- `profiles/` : Berisi profil resmi engine (`default.toml`, `geoverum.toml`, `geoverum_landscape.toml`, dan sub-tabel modular di `profiles/geoverum/`).

## Alur Penggunaan
1. Isi atau perbarui nilai agregasi metrik visual di `tools/rea-style/template.toml`.
2. Jalankan pratinjau patch konfigurasi:
   ```bash
   python tools/rea-style/profile-builder.py --style tools/rea-style/template.toml --out profiles/geoverum.toml --dry-run
   ```
3. Terapkan nilai yang dihasilkan ke `profiles/<channel>.toml` (atau `profiles/geoverum/core.toml`), lalu jalankan pemeriksaan keselarasan:
   ```bash
   engine taste geoverum --lint
   ```
