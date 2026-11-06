# Panduan Gaya Referensi — sembilan video bedah frame-per-frame

Dokumen ini merangkum hasil bedah sembilan video referensi (02–10) pada 30 FPS
aslinya: **17.792 frame diekstrak dan dilihat seluruhnya** (lembar kontak
berurutan tanpa loncatan + pengukuran per frame + verifikasi teks pada frame
penuh). Dokumen ini merupakan spesifikasi mandiri yang menjadi acuan konfigurasi `[reference_style.*]` pada profil channel.

## Prinsip dasar (berlaku untuk semua yang di bawah)

- **Tumpang tindih itu boleh.** Elemen boleh bertumpuk mengikuti urutan visual
  yang direncanakan kreator (caption di atas banner, angka di atas caption,
  ikon menutup region). Satu-satunya syarat adalah **keterbacaan**: teks yang
  bertumpuk harus tetap terbaca lewat kontras yang disengaja. Tidak ada gate
  yang melarang tumpang tindih.
- **Semua konsep di bawah adalah pilihan kreator**, bukan keharusan. Profil
  Geoverum (`profiles/geoverum.toml`) menyediakan opsi-opsi ini; kreator
  memilih per segmen.
- **12 gate wajib tidak tersentuh** oleh paket ini. Gate gaya baru apa pun
  masuk tier fleksibel/profil, tidak pernah memblokir.

## Kelompok konsep dan kapan dipakai

### A. Isian dan sorotan wilayah
- **Hatch per fase** — isian garis diagonal hanya pada fase konflik/dua pihak
  (referensi Korea); fase lain tetap polos.
- **Fill dua-lapis satu region** — satu wilayah boleh dua fill bertumpuk
  (referensi patch below-sea-level Belanda).
- **Fill kategorikal kebijakan** — warna berdasarkan kebijakan, bukan identitas
  (referensi zona NO SLAVERY / SLAVERY).
- **Spotlight dim satu sisi** — satu sisi dikedupkan, sorotan pada entitas
  (referensi Point Roberts).
- **Neon coastline glow** — garis pantai menyala (referensi renang garis lurus).
- **Heatmap kepadatan** — gradasi krem → oranye → merah (referensi Brazil).

### B. Tipografi
- **Judul sinematik raksasa** di atas region (referensi Netherlands, Dutch,
  Gulf Stream).
- **Label diketik huruf-per-huruf** (referensi Angle Inlet, Point Roberts,
  Stanstead).
- **Transformasi judul huruf-per-huruf** (referensi Deutsch → Dutch).
- **Tulisan tangan + panah penunjuk** (referensi Erik the Red).
- **Banner gelap + teks putih** (referensi North/South Korea).
- **Label miring mengikuti rute** dan **stempel kuning miring**
  (referensi ICELAND / GREENLAND).

### C. Angka sebagai tokoh
- **Count-up** populasi, jarak, dan lebar sungai (referensi 54; 1.191;
  7.100 km; 5 → 50 km).
- **Termometer menghitung** suhu (referensi −10°C; 28°C).
- **Statbox persentase** (referensi 4% / 40%).
- **Angka neon api** (referensi $42.000.000; 3.700 ac; 250 years).

### D. Ikon, karakter, dan gelembung
- **Speech bubble** teks hitam di atas putih (referensi "We're Danish!",
  "ICELAND!").
- **Karakter kartun**: mata, lengan berotot, wajah Viking, Erik the Red.
- **Kapal layar berlayar** + rute kurva + peti kargo (referensi ekspansi
  kolonial dan Viking).
- **Kincir berlabel kota**, ikon industri, medali emas, lencana tengkorak,
  segitiga peringatan, ikon cuaca (matahari, salju, salju dicoret).
- **Partikel salju** overlay penuh layar.

### E. Adegan non-peta sebagai latar penuh
- **Dokumen arsip** (referensi Homestead Act) dan **ilustrasi tinta**
  (referensi kereta wagon, rumah kayu).
- **Peta perkamen + potret lukisan** (referensi Belanda, Holy Roman Empire).
- **Footage live** — api, retakan tanah, hamparan arang (referensi Centralia).
- **Foto berbingkai krem**, **diagram penampang berlapis** + sungai bawah
  tanah (referensi Amazon/Hamza), **peta skematik HUD**, dan **vignette
  teleskop** (referensi "ICELAND!").

### F. Kamera dan transisi
- **Satu shot kontinyu gaya fly-through** tanpa potongan (referensi 49th
  parallel, Gulf Stream, New York/Washington).
- **Tilt penyamaan lintang** — kamera dimiringkan agar dua tempat sejajar
  lintang tampak sejajar (referensi Alaska–Norwegia).
- Transisi tambahan yang boleh dipakai: zoom-blur radial, warp-blur,
  zoom-whip, flash, wipe melingkar. Hard cut tetap menjadi default.

### G. Lapisan tumpuk
Lihat prinsip dasar di atas. Kebijakan ini tertulis di profil sebagai
`[overlap] allowed = true`, `rule = "readability"`, `forbid = []`.

## Yang sama dengan Geoverum sekarang

Peta satelit sebagai tokoh, kamera hampir tidak pernah diam, caption putih
frasa pendek tanpa kotak di pita bawah, satu fokus per adegan, durasi 45–100
detik, tanpa CTA/end-card, tanpa watermark channel pada referensi (Geoverum
memakai watermark "Geoverum" sebagai identitas channel).
