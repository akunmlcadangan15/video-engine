# Proposal: Penyutradaraan Satu Gaya untuk Dua Rasio (9:16 dan 16:9)

Status: proposal final untuk ditinjau. Dokumen ini mandiri.

## 1. Ringkasan
Profil Geoverum saat ini menyasar format 9:16 (YouTube Shorts). Proposal ini menyiapkan dukungan untuk format
16:9 tanpa mengubah identitas gaya editing. Gagasan utamanya adalah memisahkan keputusan penyutradaraan menjadi
dua lapisan: lapisan intent (cerita) yang sama untuk semua rasio, dan lapisan adaptor format yang menempatkan
intent tersebut pada kanvas sesuai orientasi.

## 2. Latar belakang dan masalah
- Geoverum dikembangkan untuk 9:16; kebutuhan berikutnya mencakup 16:9.
- Keputusan penyutradaraan saat ini tercampur dengan geometri portrait (safe area, penempatan caption, slot overlay).
- Diperlukan model yang membuat satu gaya editing berlaku untuk kedua rasio tanpa memaksakan hasil.

## 3. Tujuan dan lingkup
Tujuan:
- Menyediakan satu gaya penyutradaraan yang berlaku untuk 9:16 dan 16:9.
- Memisahkan keputusan cerita (dibagi lintas rasio) dari keputusan framing (spesifik orientasi).
- Pastikan setiap format dirender dengan sengaja, bukan sekadar memotong hasil dari orientasi lain.

Lingkup yang tidak termasuk:
- Output 1:1 atau 4:5 sebagai format utama pada tahap awal.
- Pengubahan ulang identitas visual brand Geoverum.

## 4. Model yang diusulkan
### 4.1 Lapisan A: Directorial Intent (agnostik rasio)
Shot intent menyatakan tujuan komunikasi, bukan geometri: establish, track, reveal, compare, journey, hold/read,
reaction, contrast. Keputusan yang menyertainya (struktur beat, emphasis, ritme, kontinuitas, mood) bersifat sama
untuk kedua rasio.
### 4.2 Lapisan B: Format Adapter (spesifik orientasi)
Setiap intent dipetakan ke framing konkret per orientasi:
| Aspek | 9:16 | 16:9 |
|---|---|---|
| Posisi subjek | center-dominant | rule-of-thirds |
| FOV | ketat | lebar, menampilkan lingkungan |
| Alur baca | atas ke bawah | kiri ke kanan |
| Slot teks | judul atas, caption bawah | judul kiri-atas, caption lower-third |
| Gerak kamera | dolly/z dominan | pan lateral, crane |
| Watermark | kanan-atas | kanan-bawah |
### 4.3 Profil dua orientasi
Profil Geoverum membawa tabel nilai per orientasi (geometri + default framing), diadopsi dari pendekatan Units yang
seragam untuk panjang dan posisi.
### 4.4 Gate
- Gate cerita (hook, novelty, camera motion, silence, keyword, CTA) berbasis detik dan dibagi lintas rasio.
- Gate geometri (safe area, caption, layout, watermark) membaca nilai per orientasi.

## 5. Fitur yang diusulkan
| Fitur | Hasil |
|---|---|
| F1 | Render 16:9 pada resolusi pilihan tanpa mengubah output portrait |
| F2 | Teks, caption, dan safe area proporsional otomatis di 16:9 |
| F3 | Watermark dan overlay menyesuaikan orientasi |
| F4 | Gate geometri membaca Units per orientasi |
| F5 | Draft dan prompt agnostik rasio |
| F6 (opsional) | Layout zone/grid khusus peta untuk 16:9 |

## 6. Kompatibilitas
- Nilai default tetap portrait; proyek 9:16 yang ada tidak berubah.
- Profil landscape dikenali melalui deklarasi orientasi; ketidakcocokan profil-orientasi menghasilkan error.

## 7. Pengujian
- Contoh proyek landscape (native + satu scene hyperframes) dibangun hingga lolos gate non-geometri.
- Golden test per gate pada ukuran landscape.
- Perbandingan visual portrait vs landscape melalui contact sheet untuk tinjauan.

## 8. Risiko dan mitigasi
| Risiko | Mitigasi |
|---|---|
| Banyak situs memakai konstanta portrait | pemusatan pada modul Units dan penggantian mekanis |
| Layout vertikal diasumsikan oleh planner | penangguhan fitur layout khusus hingga fase lanjutan |
| Kualitas sumber untuk resolusi besar | kebijakan kualitas sumber ditetapkan per resolusi |

## 9. Keputusan desain yang masih terbuka
1. Apakah 16:9 memakai durasi long-form atau tetap pendek?
2. Apakah peta tetap menjadi bahasa visual utama di 16:9 atau boleh lebih banyak footage?
3. Apakah format 1:1/4:5 perlu didukung ke depan?