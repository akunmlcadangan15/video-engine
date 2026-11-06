# Utilitas Kompilasi Profil (`tools/rea-style`)

Direktori ini menyimpan skema style-sheet antara (`template.toml`) dan utilitas `profile-builder.py` untuk mengonversi hasil agregasi metrik video menjadi konfigurasi profil channel di `profiles/`.

## Alur Kerja
1. Perbarui nilai parameter di `tools/rea-style/template.toml` (lihat [docs/REA-VIDEO.md](../../docs/REA-VIDEO.md)).
2. Tinjau blok konfigurasi TOML yang dihasilkan tanpa mengubah berkas target:
   ```bash
   python tools/rea-style/profile-builder.py --style tools/rea-style/template.toml --out profiles/geoverum.toml --dry-run
   ```
3. Salin atau tulis blok konfigurasi yang dihasilkan ke profil target di `profiles/<channel>.toml`.
