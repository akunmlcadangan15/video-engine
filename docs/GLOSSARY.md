# Glosarium

Istilah dan singkatan yang dipakai di kode dan dokumentasi. Istilah tetap dalam bahasa Inggris seperti di kode.

## Alur dan struktur

| Istilah | Arti |
|---|---|
| **Proyek** | Folder berisi `master_timeline.json`, `credits.txt`, `assets/`, dan voiceover. Berisi data saja. |
| **Timeline** | `master_timeline.json`: urutan segmen, pengaturan, caption, dan kredit. |
| **Segmen** | Satu potongan timeline yang dirender oleh satu jalur (native atau HyperFrames). Segmen berurutan tanpa celah dari frame 0. |
| **Native** | Jalur render ffmpeg dan Rust untuk foto dan footage (`NATIVE_RUST_FFMPEG`). |
| **HyperFrames** | Jalur render HTML dan GSAP di Chrome tanpa layar untuk peta, pin, kartu, dan judul (`HYPERFRAMES_GSAP`). |
| **Storyboard span** (`spans`) | Rentang kata narasi `from_word..to_word` dalam satu konsep storyboard, dengan `goal`, `focal` (satu hal yang harus dilihat mata), `pattern`, dan `cause` opsional (kata atau peristiwa yang memicu perubahan di layar). Alias lama `beats` diterima saat membaca. |
| **Payoff** (`hero_moment`) | Momen satu konsep storyboard yang menceritakan kisahnya sendiri sebagai satu frame diam; ditahan cukup lama untuk dibaca. |
| **Craft note** | Catatan lunak director (span generik, teks diulang, teks melebihi anggaran kata, lebih dari satu angka, pola semua konsep sama) yang menurunkan skor konsep tanpa menolaknya. Bukan gate. |
| **Fresh-eye review** | Review frame `engine draft --review`: reviewer hanya menerima frame, narasi bertimestamp, dan brief per segmen, bukan alasan builder; temuan harus menunjuk frame yang dikirim. |
| **Timeline `Beat`** | Unit isi layar per `at_frame` di scene peta (butuh `[layout]`). Bukan storyboard span; skala waktunya berbeda. |
| **Profil channel** | Berkas TOML di `profiles/` berisi gaya dan aturan satu channel. Prioritas: default, profil channel, proyek. |
| **Taste store** | `profiles/taste/<channel>.json`: hal yang disukai dan ditolak beserta alasannya, dipakai director saat draft. |
| **Director** | Tahap draft yang membuat tiga konsep storyboard dan memilih satu berdasarkan skor. |
| **Creative rules** | Aturan keras per channel di `profiles/<channel>/creative.md` (sumber tunggal, dimuat sebagai `[draft] rules`). Bukan taste, bukan guidance. |
| **Type scale** | Basis kanonis ukuran teks `[type]` di profil; `[theme]`/`[print]`/`[layout]` adalah override per-surface. |
| **Agent handoff** | Provider AI `agent`: mesin menulis paket permintaan ke berkas dan keluar dengan kode 3; agent menjawab lewat `response.json`. |
| **Build report** | `out/build_report.json` (sukses) atau `out/build_report.failed.json` (gagal). |
| **Manifest** | `out/build_manifest.json`: id build, hash input, toolchain, dan kunci tahap. |
| **Draft build** | `engine build --mode draft`: gate teknis tertentu hanya memperingatkan; hasil di `out/draft/` dan tidak bisa dikirim. |

## Gate

| Istilah | Arti |
|---|---|
| **Gate** | Pemeriksaan otomatis yang bisa lolos, memperingatkan, atau menggagalkan build. Daftarnya di [GATES.md](GATES.md). |
| **Pre-flight** | Gate yang berjalan sebelum render apa pun (lapis 1 sampai 3). |
| **Kernel gate** (`KERNEL_GATE_IDS`) | 8 gate integritas inti (`T02`, `M01`, `W01`, `L22`, `R38`, `A01`, `G01`, `M03`) yang selalu memblokir build bila gagal dan tidak dapat diturunkan. |
| **Lint gate** (`LINT_GATE_IDS`) | 51 gate non-kernel (mencakup style gate, tata letak `L01`–`L10`, anti-slop, serta margin/kamera/tabrakan/kontras) yang secara bawaan hanya memperingatkan (`warn`) kecuali dimasukkan ke `gates.require`. |
| **Technical gate** | Kelas gate pemeriksaan teknis (`TECHNICAL_GATE_IDS`); terdiri atas 8 kernel gate, 32 technical lint gate, serta `F01`/`AS04`. |
| **Style gate** | Kelas gate selera channel (`STYLE_GATE_IDS`, bagian dari `LINT_GATE_IDS`). Hanya memperingatkan kecuali masuk `gates.require`. |
| **Required** | Gate di dalam `LINT_GATE_IDS` yang didaftarkan di `[gates] require` atau `gates.require` sehingga kegagalannya memblokir build. |
| **Advisory** | Daftar `[gates] advisory` di profil untuk menurunkan gate teknis di luar `NEVER_ADVISORY` menjadi peringatan. |
| **Waive** | Menurunkan temuan gate menjadi informasi lewat timeline (`gates.waive`, atau `gates.waive_with_reason` dengan alasan tertulis). |
| **Finding** | Satu hasil gate dengan level `pass`, `fix`, `info`, `warn`, `skip`, atau `fail`. |
| **DOM probe** | Pengukuran kotak elemen HyperFrames di Chrome pada beberapa waktu, dipakai gate R63, E01, dan R64. |
| **Safe area** | Area aman teks: portrait 60 px dari sisi dan 100 px dari atas dan bawah (pada 1080×1920); landscape 96 px dari sisi dan 54 px dari atas dan bawah (pada 1920×1080). |
| **Kanvas referensi** | Ukuran tempat nilai px profil ditulis: 1080×1920 (portrait) atau 1920×1080 (landscape); `video_engine/units.py` menskalakannya ke ukuran output. |
| **Shorts safe zone** | Bagian layar yang tidak tertutup UI YouTube Shorts. Caption Geoverum berakhir di y ≤ 1480. |

## Waktu dan caption

| Istilah | Arti |
|---|---|
| **Frame** | Satuan waktu mesin: bilangan bulat. `start_frame` termasuk, `end_frame` tidak (rentang setengah terbuka). |
| **fps** | Frame per detik; bilangan bulat 1 sampai 99. |
| **Pinned captions** | Kata caption dari STT yang disimpan di timeline (`captions` dan `captions_meta`) supaya build bisa diulang. |
| **STT** | Speech-to-text: transkripsi voiceover menjadi kata berstempel waktu. |
| **`.ass`** | Format subtitle yang dibuat crate Rust dan dibakar oleh libass di ffmpeg. |
| **libass** | Pustaka penggambar subtitle `.ass` yang dipakai ffmpeg. |
| **Hook** | Beberapa detik pertama video. Gate H01 memeriksa kapan narasi, caption, dan elemen pertama muncul. |

## Audio

| Istilah | Arti |
|---|---|
| **LUFS** | Satuan kenyaringan terintegrasi. Target mesin: -14 LUFS. |
| **True peak / dBTP** | Puncak sinyal sebenarnya setelah interpolasi. Batas mesin: -1.5 dBTP. |
| **LRA** | Loudness range; hanya dilaporkan, tidak diperiksa. |
| **Loudnorm** | Filter ffmpeg yang menormalkan kenyaringan dalam dua lintasan. |
| **Ducking** | Menurunkan musik saat voiceover berbicara. |
| **SFX** | Efek suara yang dipicu kejadian render (pin, rute, judul, dan seterusnya). |
| **Epidemic Sound** | Sumber musik dan SFX yang diutamakan pada profil Geoverum (kredit dilampirkan otomatis; gate A02 hanya memperingatkan bila `[audio] require_source` disetel). |

## Visual

| Istilah | Arti |
|---|---|
| **APCA / Lc** | Metrik kontras teks (APCA-W3). Gate R64 memakai nilai Lc, bukan rasio WCAG 2. |
| **OKLCH** | Ruang warna dengan terang (L), chroma (C), dan hue (H). Palet region Geoverum disusun di sini. |
| **Entity** | Nama yang mendapat satu warna tetap sepanjang video (negara, laut, dan seterusnya). |
| **Region** | Poligon di peta yang diwarnai sesuai entitasnya. |
| **Pin** | Penanda tempat di peta, dengan label. |
| **`camera_path`** | Beberapa kunci kamera dalam satu segmen peta: gerak berpindah tempat tanpa cut. |
| **Whip / pulse / drift** | Gerak kamera cepat dengan blur / denyut zoom kecil / geser pelan antar gerakan. |
| **Tilt** | Memiringkan bidang peta secara perspektif. |
| **Texel** | Satu piksel tekstur peta. Gate M02 membandingkan texel dengan piksel layar. |
| **LUT** | Tabel warna 3D (`.cube`) untuk grade. |
| **Zone Z1 / Z2** | Zona tata letak: Z1 untuk judul atau kartu data, Z2 untuk fokus peta. |

## Cache dan build

| Istilah | Arti |
|---|---|
| **BLAKE3** | Fungsi hash untuk kunci cache dan hash berkas. |
| **Cache key** | Hash semua masukan sebuah hasil: berkas sumber, parameter, versi tahap, dan hash kode. |
| **`ALGO_VERSION`** | Versi crate Rust yang ikut semua kunci cache; naikkan bila output Rust berubah. |
| **Code hash** | Hash isi modul Python yang membentuk hasil cache. Dihitung dari pohon sintaks tanpa docstring, jadi komentar dan format tidak membuang cache. |
| **Lease** | Penanda bahwa sebuah berkas cache sedang dipakai build, supaya tidak dihapus. |
| **Stream copy** | Menyalin aliran video tanpa encode ulang. |
| **Concat demuxer / filter** | Dua cara ffmpeg menyambung segmen: tanpa encode ulang bila parameternya sama, dengan filter bila berbeda. |
| **Staging** | Folder sementara di `out/` tempat file jadi ditulis sebelum diterbitkan dengan `os.replace`. |

## Rust

| Istilah | Arti |
|---|---|
| **PyO3 / maturin** | Pustaka dan alat build untuk membuat modul Python dari Rust. |
| **abi3** | Antarmuka biner stabil Python; satu build berlaku untuk Python 3.10 ke atas. |
| **rayon** | Pustaka paralelisme data di Rust. |
| **GIL** | Kunci interpreter Python; fungsi berat di Rust melepasnya. |
