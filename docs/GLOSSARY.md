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
| **Beats** | Cara menulis scene peta sebagai urutan kejadian; planner tata letak menurunkan kamera dan posisi elemen darinya. |
| **Profil channel** | Berkas TOML di `profiles/` berisi gaya dan aturan satu channel. Prioritas: default, profil channel, proyek. |
| **Taste store** | `profiles/taste/<channel>.json`: hal yang disukai dan ditolak beserta alasannya, dipakai director saat draft. |
| **Director** | Tahap draft yang membuat tiga konsep storyboard dan memilih satu berdasarkan skor. |
| **Agent handoff** | Provider AI `agent`: mesin menulis paket permintaan ke berkas dan keluar dengan kode 3; agent menjawab lewat `response.json`. |
| **Build report** | `out/build_report.json` (sukses) atau `out/build_report.failed.json` (gagal). |
| **Manifest** | `out/build_manifest.json`: id build, hash input, toolchain, dan kunci tahap. |
| **Draft build** | `engine build --mode draft`: gate teknis tertentu hanya memperingatkan; hasil di `out/draft/` dan tidak bisa dikirim. |

## Gate

| Istilah | Arti |
|---|---|
| **Gate** | Pemeriksaan otomatis yang bisa lolos, memperingatkan, atau menggagalkan build. Daftarnya di [GATES.md](GATES.md). |
| **Pre-flight** | Gate yang berjalan sebelum render apa pun (lapis 1 sampai 3). |
| **Technical gate** | Gate yang memblokir build bila gagal, kecuali profil menjadikannya advisory. |
| **Style gate** | Gate selera channel. Hanya memperingatkan kecuali masuk `[gates] require`. |
| **Required** | Style gate yang kegagalannya memblokir build. |
| **Advisory** | Technical gate yang profil turunkan dari gagal menjadi peringatan. Sebagian gate tidak boleh advisory. |
| **Waive** | Menurunkan temuan gate menjadi informasi lewat timeline (`gates.waive`, atau `gates.waive_with_reason` dengan alasan tertulis). |
| **Finding** | Satu hasil gate dengan level `pass`, `fix`, `info`, `warn`, `skip`, atau `fail`. |
| **DOM probe** | Pengukuran kotak elemen HyperFrames di Chrome pada beberapa waktu, dipakai gate R63, E01, dan R64. |
| **Safe area** | Area aman teks: 60 px dari sisi dan 100 px dari atas dan bawah (pada 1080×1920). |
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
| **Epidemic Sound** | Satu-satunya sumber musik dan SFX yang diizinkan profil Geoverum (gate A02). |

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
