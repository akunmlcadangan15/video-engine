# Geoverum: satu gaya penyutradaraan untuk 9:16 dan 16:9

Status: proposal konsep (analisis + desain), belum dibangun. Terkait: docs/proposals/landscape.md (teknis/geometri).

## 1. Masalah
- Geoverum sekarang 9:16 (YouTube Shorts). Target berikutnya: 16:9 (video biasa).
- Keinginan: **satu gaya editing/penyutradaraan** berlaku untuk dua rasio.
- Kebingungan: apa sebenarnya konsep penyutradaraan bila kanvas-nya berbeda ukuran.

## 2. Temuan riset (Exa)
- Grabyo (vertical video production): vertikal ≠ horizontal. 16:9 memberi lebar, field of view, wide shot, lower-third;
  9:16 memberi tinggi dan sedikit lebar, jadi komposisi **harus berubah**, bukan sekadar memotong sisi horizontal.
  Poin kunci: video vertikal harus **diproduksi untuk platform-nya** (deliberate output), bukan diselamatkan dari feed horizontal.
- VidFuego (center vs rule-of-thirds): di vertikal, **center framing** cocok untuk direct-address & demonstrasi simetris;
  off-center memberi ruang untuk subjek kedua/objek/arah pandang. Komposisi diuji pada ukuran layar telepon, sisakan
  ruang untuk caption & UI platform; jangan letakkan detail penting di tepi.
- MediaScaleUp & StudioCut: strategi multi-format = **satu ide kreatif → banyak format**, dan tiap format disutradarai
  dengan sengaja (bukan sekadar crop).

Kesimpulan riset: yang bisa di-share antar rasio adalah **keputusan cerita/shot** (apa yang dikomunikasikan dan kapan);
yang harus berbeda adalah **framing/komposisi** (bagaimana menempatkan-nya di kanvas).

## 3. Akar kebingungan
Di engine sekarang, keputusan sutradara **tercampur** dengan geometri portrait:
- draft prompt menulis `vertical documentary short`, OVERLAY_SLOTS 4 kotak portrait, fixed_y 1382, safe area 60/100,
  zona layout vertikal, dan gate yang mengukur geometri portrait.
- Sementara keputusan cerita (beat, hook, novelty, camera hold, cut, silence, keyword) sebenarnya **agnostik rasio**.

Jadi tidak ada pemisahan eksplisit antara apa yang disutradarai (cerita) dan bagaimana ditempatkan (framing).

## 4. Model yang diusulkan: Sutradara = Direksi (Intent) + Adaptor Format

### Lapisan A — Directorial Intent (format-agnostik): APA yang disutradarai
Kosa kata shot-intent yang **sama** untuk kedua rasio, menyatakan tujuan komunikasi, bukan geometri:
| Intent | Arti kamera | Contoh |
|---|---|---|
| establish | memberi konteks/lingkungan | buka dengan peta luas |
| track | mengikuti subjek yang bergerak | rute, perjalanan |
| reveal | membuka fokus dari luas ke spesifik | zoom-in ke negara |
| compare | menyandingkan dua hal (split) | dua panel, sebelum/sesudah |
| journey | kamera bepergian (path multi-key) | terbang di sepanjang rute |
| hold/read | diam agar teks terbaca | judul, fakta, peta statis |
| reaction | menyorot respons/akibat | peta berubah warna |
| contrast | menjentikkan kontras naratif | slow vs cepat, besar vs kecil |

Keputusan yang ikut lapisan A (dibagi silang rasio):
- struktur beat & urutan (draft), skor importance per elemen,
- ritme: hook, novelty gap, camera hold, cut urgency, silence, keyword return (gate style P01/N01/H01/D01/S01/K*),
- kontinuitas (line-of-interest sides, cut avoidance), mood & gaya visual (profile brand).

### Lapisan B — Format Adapter (per orientasi): BAGAIMANA menempatkan intent
Pemetaan tiap intent ke framing konkret per kanvas. Tabel usulan (nilai = proposal, bukan hasil ukur):
| Aspek | 9:16 (portrait) | 16:9 (landscape) |
|---|---|---|
| Posisi subjek | center-dominant (direct-address, simetris) | rule-of-thirds; subjek di titik kuat; ruang arah pandang |
| FOV / kedekatan | lebih ketat (subjek mengisi tinggi) | lebih lebar (tampilkan lingkungan) |
| Alur baca | atas → bawah (vertikal) | kiri → kanan (horizontal) |
| Ruang negatif | sedikit (layar sempit), dipakai untuk teks | lebih banyak; dipakai untuk atmosfer |
| Slot teks/overlay | title atas, peta tengah, caption band bawah (0.70-0.82 H) | title kiri-atas, peta tengah, caption tengah-bawah (lower-third) |
| Zona aman | 60 sisi / 100 atas-bawah; batas bawah UI Shorts | 96 sisi / 54 atas-bawah (5%) |
| Gerak kamera | dominan dolly/z (masuk-keluar), pan kecil | pan kiri-kanan, crane, dolly lateral, lebih banyak ruang |
| Watermark | kanan-atas | kanan-bawah (corner) |

### Profil dua-orientasi
Profil Geoverum menjadi **kumpulan tabel per orientasi**:
- `[format] orientation` (default portrait), dan `[landscape]` overlay (langkah B landscape.md) untuk nilai geometri.
- Tambah tabel framing adaptor (mis. `[framing.portrait]`, `[framing.landscape]`) yang memetakan intent → default
  FOV/posisi/slot. Ini yang membedakan konsep sutradara adaptor dari sekadar daftar px.

### Gate
- **Gate cerita (bersama):** H01, N01, P01, P02, P03, D01, S01, C02, K01, K02, V01 — berbasis detik;
  ambangnya milik ritme brand (diisi per orientasi), bukan logika geometri.
- **Gate geometri (per orientasi):** O01, R62, R63, Z01, L01-L10, B01, F04 — membaca safe area/zona dari Units per orientasi.
- **Gate kualitas (bersama):** M01, M02, M03, M04, G01, G02, R38, R56px, A01, A02, T02, R07, R14, W01, L22.

## 5. Rekomendasi untuk Arundaya
1. **Terima abstraksi Intent + Adapter** sebagai konsep penyutradaraan: keputusan cerita dibuat sekali (di-share),
   framing diturunkan per orientasi. Ini yang membuat **satu gaya editing berlaku untuk dua rasio** tanpa memaksakan.
2. **Jangan salvage/crop**: tiap format diauthor & dirender dengan sengaja (landscape.md memastikan itu).
3. **Tentukan format utama** untuk nilai default (Shorts dulu) — landscape tinggal menumpang framework yang sama.
4. **Tulis ulang draft prompt & creative.md agar agnostik rasio**: sebutkan intent, bukan `vertical documentary short`;
   angka tempo dirunut ke profil per orientasi.
5. **Urutan implementasi**: (a) units + profile per orientasi (landscape.md) → (b) adaptor framing (tabel di atas) →
   (c) gate geometri baca Units. Semua tanpa menyentuh kode master sebelum disetujui.

## 6. Pertanyaan terbuka
- 1. Satu kanvas master untuk authoring narasi lalu render ganda, atau author per format? (Rekomendasi: intent sekali, framing per format.)
- 2. Apakah 16:9 memakai durasi long-form (menit) atau tetap pendek? Ini menentukan ambang D01/H01/N01.
- 3. Untuk 16:9, apakah peta tetap menjadi bahasa utama, atau boleh lebih banyak footage? (Brand vs bentuk.)
- 4. Apakah perlu format 1:1 / 4:5 ke depan?

## Sumber
- Grabyo, What is vertical video production: https://about.grabyo.com/what-is-vertical-video-production/
- VidFuego, Rule of Thirds vs Center Framing in Vertical Video: https://www.vidfuego.com/blog/thirds-center-vertical-framing/
- MediaScaleUp, Aspect Ratios and Framing for Social Video: https://mediascaleup.com/post/aspect-ratios-and-framing-for-social-video-what-actually-works-video
- StudioCut, Multi-Format Videos (16:9, 9:16, 1:1): https://studiocut.video/help-multi-format.html
- Repo intern: docs/proposals/landscape.md; profiles/geoverum/creative.md; geoverum.toml; gate_registry.py.

