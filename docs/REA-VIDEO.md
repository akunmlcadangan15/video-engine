# rea-video → rea-style → profil

## Konsep
rea-style BUKAN profil. rea-style adalah style-sheet dari hasil bedahan rea-video
yang dipakai untuk MEMBUAT atau MEMPERBAIKI profil di profiles/.

Struktur:
- skills/rea-video/          : skill bedah video (analisis, bukan profil)
- tools/rea-style/           : style-sheet + builder (bukan profil)
  - template.toml            : format style-sheet dari breakdown
  - profile-builder.py       : bantu generate profil dari style-sheet
- profiles/                  : hanya profil engine (default, geoverum, native_demo)
  - rea-style TIDAK ada di sini

## Alur
1. Bedah: rea-video decompose -> breakdown.json
2. Distilasi: breakdown -> tools/rea-style/template.toml (diisi manual/agent)
3. Terapkan: template -> profiles/<channel>.toml (buat baru atau perbaiki yang ada)
4. Build seperti biasa: engine validate -> preflight -> scripts/engine-build
5. Audit (opsional): bedah lagi out/final.mp4 dengan rea-video

## Batch banyak video
  bash scripts/rea-video-batch.sh referensi/ _analysis/
Agregasi breakdown.json -> isi template.toml -> terapkan ke profil.
