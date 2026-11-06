# Spesifikasi Arsitektur Visual, Kamera, dan Tipografi Geoverum

Dokumen ini merupakan spesifikasi teknis mandiri yang mendefinisikan bahasa visual,
kinematika kamera, sistem warna semantik, tipografi adaptif, dan komponen bukti
visual untuk profil `geoverum` (9:16 portrait) dan `geoverum_landscape` (16:9 widescreen).
Seluruh parameter di dalam dokumen ini dirancang sebagai acuan bagi pihak ketiga
(engineer, desainer gerak, maupun agen penyusun storyboard otomatis) agar setiap
keputusan konfigurasi pada `[theme]`, `[camera]`, `[print]`, `[legibility]`, `[fx.*]`,
dan `[reference_style.*]` dapat dipahami secara presisi tanpa ambiguitas.

## 1. Karakteristik Kuantitatif & Tolok Ukur Produksi

Tabel berikut merangkum sepuluh pola komposisi produksi yang menjadi dasar kalibrasi
parameter kamera, luminansi peta, ritme pemotongan adegan, dan kepadatan audio pada
profil Geoverum (diukur pada kecepatan 30 frame per detik):

| Pola Produksi | Tema & Struktur Geografis | Durasi (detik) | Rasio Frame Bergerak | Durasi Tahan Maksimum (`max_hold`) | Jumlah Hard Cut (`scdet`) | Onset Suara Narator (`VO Onset`) | Celah Hening Maksimum (`<-40 dB`) | Median Luminansi Peta (`APCA Y`) |
|---|---|---|---|---|---|---|---|---|
| **Pola 01** | Konflik Semenanjung & Pergeseran Garis Depan (38th Parallel) | 88,06 s | 99,8 % | 0,13 s | 0 (1 shot kontinyu) | 0,13 s | 0,43 s | 0,085 |
| **Pola 02** | Geometri Perbatasan & Bukti Dokumen Arsip (36,5° Parallel) | 60,02 s | 97,3 % | 0,47 s | 1 (cut ke dokumen/ilustrasi) | 0,05 s | 0,45 s | 0,208 |
| **Pola 03** | Anomali Garis Lintang & Enklave Mikro (49th Parallel) | 60,04 s | 99,3 % | 0,17 s | 0 (1 shot kontinyu) | 0,27 s | 0,53 s | 0,077 |
| **Pola 04** | Lintasan Geodesik Jarak Jauh (Peta Datar ke Bola Dunia 3D) | 60,02 s | 98,6 % | 0,73 s | 0 (transisi kontinu ke globe 3D) | 0,02 s | 0,48 s | 0,068 |
| **Pola 05** | Etimologi Wilayah, Topografi Bawah Laut, & Ekspansi Maritim | 80,76 s | 98,8 % | 0,60 s | 2 (cut ke peta arsip sejarah) | 0,16 s | 0,58 s | 0,085 |
| **Pola 06** | Bencana Geologi Bawah Tanah, Penampang 2D, & Dokumentasi Lapangan | 60,04 s | 98,3 % | 0,33 s | 5 (cut ke footage & diagram penampang) | 0,10 s | 0,23 s | 0,064 |
| **Pola 07** | Perbandingan Demografi Negara Bagian vs Kota & Skematik Grid | 60,04 s | 95,9 % | 0,77 s | 0 (1 shot kontinyu) | 0,04 s | 0,30 s | 0,052 |
| **Pola 08** | Perbandingan Iklim Lintas Benua & Rotasi Penyamaan Lintang | 85,80 s | 100,0 % | 0,00 s | 0 (1 shot kontinyu) | 0,09 s | 0,59 s | 0,085 |
| **Pola 09** | Hidrologi Daerah Aliran Sungai, Choropleth Kepadatan, & Akuifer | 60,02 s | 96,6 % | 0,90 s | 6 (cut ke penampang & peta kepadatan) | 0,00 s | 0,43 s | 0,063 |
| **Pola 10** | Kontras Iklim Kutub, Navigasi Maritim Sejarah, & Efek Vignette | 59,92 s | 96,6 % | 0,83 s | 2 (cut ke vignette teleskop & foto) | 0,04 s | 0,59 s | 0,089 |

### Kesimpulan Kalibrasi Parameter dari Data Kuantitatif:
1. **Permukaan Peta Satelit Gelap (`median APCA Y ≈ 0,08`)**: Sembilan dari sepuluh pola produksi menggunakan citra satelit gelap dengan luminansi median antara `0,052` dan `0,089` (dan hingga `0,208` saat menyorot daratan benua bersalju atau gurun terang). Oleh sebab itu, batas klasifikasi kertas (`[print] paper_min_y`) ditetapkan pada `0.52` agar peta satelit tidak pernah secara keliru diberi tekstur kertas antik atau mode blend `multiply`, melainkan selalu mempertahankan warna isian wilayah yang hidup (`region_fill_opacity = 0.45`) dan garis batas bersinar (`region_edge = "glow"`, `region_glow_px = 18`).
2. **Kontinuitas Gerak Kamera (`95,9% – 100%` Frame Bergerak)**: Kamera tidak pernah berhenti statis lebih dari `0,90 detik`. Di antara perpindahan lokasi utama, kamera mempertahankan drift logaritmik sebesar `0.06 ln(scale)/s` (`camera_drift_per_s = 0.06`) dan denyut dorong halus (`pulse_zoom = 0.18`, `pulse_s = 0.85`).
3. **Hook Instan Tanpa Jeda (`VO Onset 0,00 – 0,27 s`)**: Suara narator dan caption pertama langsung masuk sejak awal video di atas peta yang sudah bergerak (`open_on_moving_map = true`), dengan celah hening maksimum di seluruh durasi video di bawah `0,60 detik` (dijaga oleh gate `S01` pada `max_gap_s = 0.8`).

## 2. Prinsip Dasar Arsitektur Visual

- **Tumpang Tindih Terencana Diizinkan (`[reference_style.overlap]`)**: Elemen visual diperbolehkan saling bertumpuk mengikuti hierarki komposisi yang direncanakan (misalnya caption berada di atas banner wilayah, angka statistik utama melintas di atas batas poligon, atau ikon cutout menutup sebagian garis rute). Satu-satunya syarat teknis yang diberlakukan adalah **keterbacaan (`rule = "readability"`)**: teks di lapisan depan harus mempertahankan kontras luminansi yang memadai terhadap latar di belakangnya. Daftar larangan tumpang tindih (`forbid = []`) sengaja dikosongkan.
- **Katalog Opsi Modular per Segmen**: Seluruh kapabilitas di bawah `[reference_style.*]` merupakan perangkat ekspresi visual yang dapat dipilih per segmen oleh penyusun timeline atau agen AI sesuai kebutuhan naratif, bukan aturan kaku yang memaksa seluruh efek tampil bersamaan.
- **Pemisahan Gate Integritas vs Gate Gaya**: Dua belas gate integritas inti (`MANDATORY_GATE_IDS`: `T02`, `R07`, `R14`, `A01`, `A02`, `W01`, `R38`, `R56px`, `M01`, `M03`, `G01`, `L22`) selalu aktif untuk menjamin kebenaran teknis render dan sinkronisasi audio-visual, sedangkan seluruh gate gaya disetel sebagai pengingat non-pemblokir (`[gates] require = []`).

## 3. Spesifikasi Tujuh Kelompok Kapabilitas Visual (`[reference_style.*]`)

### A. Isian Poligon Wilayah dan Sorotan Perbatasan (`[reference_style.fill]`)
- **Isian Garis Diagonal (`hatch = true`, `hatch_angle_deg = 45`)**: Menampilkan pola arsiran garis diagonal 45° di atas poligon wilayah untuk menandai fase pendudukan militer, zona sengketa, wilayah yang berada di bawah permukaan laut, atau cakupan negara bagian yang dibandingkan terhadap kota di dalamnya.
- **Pergeseran Garis Depan Dinamis (`territory_sweep = true`) & Perbatasan Membara (`dmz_fire_border = true`)**: Menganimasikan perubahan luas penguasaan wilayah dari waktu ke waktu disertai garis perbatasan atau garis lintang yang memancarkan cahaya intensitas tinggi.
- **Isian Dua Lapis (`double_layer = true`)**: Mengizinkan dua poligon bertumpuk pada satu wilayah geografis yang sama (misalnya warna dasar wilayah kedaulatan negara bagian bawah ditambah lapisan kedua berarsir untuk bagian daratan yang berada di bawah permukaan laut).
- **Isian Kategorikal Kebijakan (`policy_fill = true`)**: Mewarnai sekumpulan wilayah berdasarkan status hukum atau kebijakan biner (misalnya zona hijau untuk wilayah bebas vs zona merah untuk wilayah terlarang) alih-alih warna identitas masing-masing entitas.
- **Peredupan Fokus Satu Sisi (`spotlight_dim = true`)**: Meredupkan area di luar fokus sambil memberikan sorotan kontras tinggi berwarna kuning sinyal pada enklave atau semenanjung mikro yang sedang dibahas.
- **Pendaran Garis Pantai & Aliran Sungai (`coast_glow = true`)**: Menambahkan garis pendaran cyan (`#00B0FF` / `#7EC8FF`) mengikuti lekuk garis pantai atau jaringan daerah aliran sungai utama.
- **Peta Kepadatan Choropleth (`heatmap = true`)**: Menampilkan gradasi kepadatan demografis atau intensitas secara bertahap dari krem terang, oranye, hingga merah tua pada subdivisi wilayah.

### B. Tipografi Peta dan Perlakuan Label (`[reference_style.label]`)
- **Lima Tipe Perlakuan Label (`types = ["plain", "banner", "typing", "handwritten", "slanted_route"]`)**:
  1. `plain`: Teks putih tegas dengan bayangan lembut dan garis tepi tipis adaptif di atas peta satelit.
  2. `banner`: Teks putih tebal di dalam kotak pil berwarna arang gelap (`banner_bg = "#18181c"`, `label_box_radius_px = 4`) untuk penamaan entitas utama atau status darurat.
  3. `typing`: Efek pengetikan huruf demi huruf (`typing_chars_per_s = 20`) untuk memunculkan nama lokasi mikro atau koordinat secara berurutan.
  4. `handwritten`: Catatan bertuliskan tangan berwarna kuning sinyal (`#FFD417`) atau putih yang dipadukan dengan panah lengkung gambar-tangan (`handwritten_arrow = true`).
  5. `slanted_route`: Label yang dimiringkan sejajar dengan sudut kemiringan garis rute atau garis lintang.
- **Stempel Miring (`stamp = true`)**: Kotak teks berbingkai miring berwarna kontras tinggi untuk penegasan julukan wilayah.
- **Judul Sinematik Raksasa (`title_cinematic = true`)**: Judul berukuran besar (`title_px = 112`) dengan pendaran cahaya lembut (`title_glow_px = 28`) yang meluncur masuk di atas wilayah fokus.
- **Transformasi Ejaan Judul (`title_morph = true`)**: Animasi perubahan huruf demi huruf pada judul untuk memperlihatkan evolusi etimologi atau perubahan status nama wilayah.
- **Coretan Rumus Perbandingan (`equation_slash = true`)**: Menampilkan persamaan dua istilah geografis di mana tanda sama dengan (`=`) dicoret secara dinamis dengan garis miring merah menjadi tidak sama dengan (`≠`).

### C. Visualisasi Data dan Angka Sebagai Aktor Utama (`[reference_style.data_viz]`)
- **Animasi Hitung Naik (`count_up = true`)**: Angka populasi, jarak, atau lebar geografis yang menghitung naik secara dinamis selama `counter_s = 1.2` detik untuk membangun skala secara dramatis.
- **Garis Rentang Kronologis (`timeline_ruler = true`) & Garis Ukur Geodesik (`measure_bar = true`)**: Garis horizontal penanda rentang tahun sejarah di atas peta, serta garis ukur berpanah ganda berwarna kuning sinyal (`#FFD417`) yang mencantumkan satuan metrik dan imperial sekaligus (`km` dan `mi`).
- **Termometer Animasi (`thermometer = true`)**: Komponen pengukur suhu vertikal yang ketinggian raksanya bergerak naik/turun secara sinkron dengan angka derajat Celsius/Fahrenheit untuk memperlihatkan anomali iklim di lintang yang sama.
- **Kotak Pil Statistik (`stat_box = true`)**: Kotak data ringkas yang menyandingkan persentase luas daratan terhadap persentase jumlah penduduk atau perbandingan populasi dua wilayah.
- **Angka Berpendar Api/Biaya (`flame_numbers = true`)**: Penampilan angka nilai kerugian finansial, luas kebakaran, atau durasi waktu bencana dengan warna neon hangat berintensitas tinggi.

### D. Ikon Cutout, Karakter Antropomorfik, dan Lapisan Lingkungan (`[reference_style.overlay]`)
- **Gelembung Dialog Komik (`speech_bubble = true`)**: Gelembung bicara putih dengan teks hitam tebal (`[fx.bubble] font_px = 46`, `pop_frames = 5`) untuk menyampaikan kalimat dialog singkat yang mewakili perspektif historis suatu wilayah.
- **Karakter & Ekspresi Wilayah (`characters = true`)**: Elemen ilustrasi ekspresif (seperti mata, lengan berotot, atau siluet tokoh sejarah) yang ditempelkan di atas poligon wilayah.
- **Lencana Bendera Bergerak (`flag_badges_moving = true`)**: Ikon bendera berbentuk lingkaran yang bergerak melintasi peta mengikuti arah pergerakan aliansi atau garis depan.
- **Kapal Layar & Kendaraan Rute (`ships = true`)**: Ikon kapal, kereta, atau kendaraan berbingkai putih yang bergerak menyusuri garis rute lengkung disertai indikator muatan.
- **Ikon Kota, Peringatan, & Cuaca (`city_icons = true`, `weather_icons = true`, `medal = true`)**: Stiker berbingkai putih tegas (kincir angin, lencana bahaya, medali peringkat `#1`, matahari, awan salju) yang ditempatkan langsung pada titik koordinat.
- **Partikel Lingkungan Penuh Layar (`snow_particles = true`)**: Lapisan partikel salju turun atau bara api beterbangan (`[fx.particles] max_count = 220`) untuk memperkuat suasana iklim atau bencana pada segmen terkait.

### E. Variasi Adegan Bukti Non-Peta (`[reference_style.scene_variants]`)
- **Dokumen Arsip (`document = true`) & Ilustrasi Tinta (`ink_illustration = true`)**: Adegan layar penuh yang menampilkan naskah undang-undang/perjanjian sejarah dengan sorotan kalimat penting, atau ilustrasi tinta di atas tekstur kertas.
- **Peta Perkamen Sejarah (`parchment_map = true`)**: Adegan peta kartografi abad pertengahan/kolonial bertekstur kertas (`paper_min_y >= 0.52`) lengkap dengan garis graticule dan bingkai lukisan tokoh sejarah.
- **Footage Dokumentasi Lapangan (`live_footage = true`) & Foto Berbingkai (`framed_photo = true`)**: Potongan video dokumentasi nyata atau foto berbingkai putih/krem (`inset_border_px = 6`) yang memperlihatkan kondisi fisik lokasi di dunia nyata.
- **Diagram Penampang Melintang 2D/3D (`cross_section = true`)**: Visualisasi irisan vertikal bawah tanah atau perbandingan kedalaman sungai/tambang (`[fx.cutaway] height_ratio = 0.58`) yang memperjelas dimensi vertikal.
- **Skematik Peta Kota HUD (`schematic_hud = true`) & Vignette Teleskop (`telescope = true`)**: Peta vektor petak jalan berlatar gelap untuk memperlihatkan penyusutan populasi/kode pos, serta pembingkaian melingkar bergaya teropong laut.

### F. Kinematika Kamera dan Transisi Antar-Segmen (`[reference_style.transitions]`)
- **Terbang Kamera Kontinyu (`continuous_shot = true`)**: Mengutamakan satu lintasan kamera panjang yang mengunjungi beberapa titik fokus secara berurutan di atas peta yang sama (`map_path = true` dengan gerakan `flow`, `glide`, `reveal`, `settle`, atau `whip`).
- **Rotasi Penyamaan Lintang (`tilt_latitude = true`) & Drone Banking (`bank_roll = 12`, `bank_tilt = 9`)**: Kamera dapat memutar sudut roll dan pitch secara mulus untuk menyejajarkan dua wilayah di lintang yang sama secara horizontal, atau memiringkan badan kamera mengikuti belokan sungai/rute.
- **Kosakata Transisi Tambahan (`extra_kinds`)**: Selain `cut` dan `state` dissolve (`0,35 s`), penyusun timeline dapat memilih `zoom_blur`, `warp_blur`, `zoom_whip`, `flash`, atau `wipe_circle` pada pergantian babak. Transisi lipatan kertas (`page_turn = false`) dinonaktifkan secara permanen.

### G. Konstanta Bahasa Visual Terpadu (`[reference_style.style.unified]`)
- **Integrasi Langsung Tanpa Kartu Lower-Third (`lower_third_card = false`)**: Seluruh informasi teks, tahun sejarah (`year_big = true`), dan pil statistik menyatu langsung dengan ruang peta tanpa menggunakan kartu lower-third televisi generik.
- **Penguncian Warna Sinyal Semantik (`signal_colors_locked = true`)**:
  - `distance = "#FFD417"` (Kuning Sinyal — garis ukur jarak, garis lintang utama, panah catatan tangan)
  - `usa = "#00B0FF"` (Biru Sian Terang — sorotan wilayah utama / garis pantai / aliansi barat)
  - `pin = "#C4302A"` (Merah Teardrop — pin penanda titik lokasi)
  - `text = "#FFFFFF"` (Putih Bersih — teks utama dan caption)
  - `conflict_red = "#D81E1E"` (Merah Konflik — wilayah lawan / zona bahaya / larangan)
  - `alliance_blue = "#1868E8"` (Biru Aliansi — wilayah koalisi / perairan dalam)
  - `empire_orange = "#F57C00"` (Oranye — wilayah historis / kepadatan menengah)
  - `safe_green = "#28A828"` (Hijau — zona aman / diizinkan / daratan subur)
  - `empire_purple = "#6828E8"` (Ungu — entitas kekaisaran / pembeda wilayah sekunder)
