# rea-style — bukan profil, tapi cara membuat / memperbaiki profil

Konsepnya: rea-style adalah alat/style-sheet dari hasil bedahan rea-video.
Dia TIDAK menjadi profil ke-4 di profiles/. Tugasnya memperbaiki atau membuat profil channel.

Cara pakai:
1. Bedah referensi (lihat docs/REA-VIDEO.md) -> breakdown.json, report.html
2. Distilasi breakdown -> template di folder ini (style-sheet, bukan profil)
3. Terapkan style-sheet itu ke profil yang ada / buat profil baru di profiles/
   - contoh: profiles/geoverum.toml + subfolder geoverum/
   - atau buat profiles/<channel-baru>.toml

File di folder ini:
- template.toml : format style-sheet dari rea-video (isi dari agregasi breakdown)
- profile-builder.py : bantu generate profil dari style-sheet + breakdown (dry-run dulu)
