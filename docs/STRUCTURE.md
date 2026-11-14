# Struktur repo: lima lapisan, satu sumber kebenaran per lapisan

Repo ini diatur dalam lima lapisan. Setiap keputusan punya tepat satu tempat yang benar;
tempat lain hanya mereferensikan, bukan menyalin.

| Lapisan | Pertanyaan | Sumber kebenaran | Bukan di sini |
|---|---|---|---|
| **Profil** (`profiles/*.toml`) | Gaya merek, geometri & batas teknis? | warna, font, animasi, gate teknis/legibilitas, `[type]` | aturan cerita/durasi wajib |
| **Kreatif / Sutradara** (`video_engine/director/`) | Cerita & pola apa? | `guidance.md` (prinsip global), `patterns.toml`, dan opsional `[draft] rules`/`rules_file` per channel | nilai render, ukuran teks |
| **Struktur** (`master_timeline.json`) | Kapan & apa urutannya? | segmen, `goal`, caption, kredit | gaya visual |
| **Presentasi** (renderer) | Penampilannya bagaimana? | `[theme]`, `[print]`, `[legibility]`, `[layout]` sebagai override di atas `[type]` | keputusan cerita |
| **Selera** (`profiles/taste/<channel>.json`) | Apa yang disukai channel? | `liked`/`rejected` + `check` mesin | aturan keras (itu profil/rules opsional) |

## Istilah yang tidak boleh tertukar

- **Storyboard span** (`spans` di konsep director): rentang kata narasi `from_word..to_word` dengan `goal`+`pattern`.
  Alias lama `beats` masih diterima saat membaca, tapi output baru selalu `spans`.
- **Timeline `Beat`**: unit isi layar per `at_frame` di scene peta (butuh `[layout]`). Beda unit, beda skala waktu.
- **Konsep**: satu dari 3 usulan storyboard (`grounded`/`contrast`/`bold`) atas narasi yang sama.
- **Profil vs taste**: profil = aturan keras yang divalidasi; taste = preferensi terpelajar yang hanya yang ber-`check` bisa dilint.
- **Kejelasan (clarity)**: skor konsep director (self-score AI) ≠ legibilitas adaptif (APCA, gate R64) ≠ type scale (`[type]`). Jangan pakai kata yang sama untuk ketiganya tanpa kualifikasi.

## Artefak kanonis (draft → build)

| Artefak | Peran |
|---|---|
| `<project>/draft/storyboards.json` | Konsep storyboard (input) |
| `<project>/master_timeline.draft.json` | Usulan timeline dari draft |
| `<project>/master_timeline.json` | **Kanonis** — hanya ini yang dibaca build; ditulis draft hanya dengan `--force` |
| `<project>/draft/timeline.vN.json` | Arsip versi review (bukan input build) |
| `out/final.mp4` + `out/build_report.json` | Hasil build; `out/draft/final.draft.mp4` tidak bisa dikirim |

## Precedence profil

Default field model (`video_engine/profile_schema.py`) < `profiles/default.toml` < rantai `extends` (untuk tiap
profil: file `include` sesuai urutan, lalu file profilnya) < `profile_overrides` proyek < nilai proyek
(`caption_style`, `audio_chain`). Fallback berlaku per kunci; profil yang gagal dibaca atau divalidasi adalah
error, tidak pernah diganti diam-diam dengan profil default. `engine profile NAMA --origin` menunjukkan file
asal tiap kunci. `profile_overrides` proyek hanya boleh untuk tabel di
`OVERRIDABLE` di `video_engine/profiles.py`. Safety limit (`true_peak`, `tolerance_lu`)
hanya boleh diketatkan, tidak dilonggarkan.

## Type scale

`[type]` adalah basis kanonis (`title_px`, `title_px_paper`, `pin_label_px`). Ukuran yang ditulis channel di
`[type]` mengalir otomatis ke setiap surface yang tidak ditulis channel (`profiles.TYPE_CASCADE`):
`[theme] title_px`/`pin_label_px`, `[print] title_px`, dan `[layout.sizes] title`/`pin_label`. Nilai surface
yang ditulis eksplisit tetap menang sebagai override per-surface. Ukuran caption tetap di `[caption_style]` (aturan caption, bukan scene type).

## Creative rules (opsional)

Profil merek seperti `geoverum` tidak mengunci arahan kreatif; aturan kreatif tambahan hanya berlaku bila channel
menyatakan `[draft] rules` atau `[draft] rules_file` (mis. file tunggal `profiles/<channel>/creative.md`). Bila dipakai,
angka yang berbeda per orientasi ditulis sebagai placeholder `{{tabel.kunci}}` dan diisi dari profil efektif untuk
orientasi tersebut. Dilarang menyalin aturan yang sama ke `taste/*.json` (`rules` di sana adalah preferensi) atau ke
`guidance.md` (prinsip global). `[draft] rules` inline dan `rules_file` saling eksklusif — keduanya bersama adalah error.
