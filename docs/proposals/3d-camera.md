# Proposal: Kamera 3D Penuh untuk HyperFrames

Status: proposal final untuk ditinjau. Dokumen ini mandiri.

## 1. Ringkasan
Mesin video saat ini memakai model kamera planar 2.5D dengan state `{x, y, s, tilt(rotateX), roll(rotateZ)}`,
diterapkan sebagai layer CSS 3D pada template HyperFrames dan dicerminkan di Python untuk kebutuhan gate. Model ini
tidak mendukung translasi kedalaman (`translateZ`), rotasi `rotateY`, maupun pose 3D bebas.

Proposal ini menambahkan **mode kamera 3D penuh (opt-in)** per segmen. Mode baru ini dirender melalui scene
Three.js di dalam Chrome headless, dengan matematika yang dicerminkan di Python agar gate tetap dapat memvalidasi
hasilnya. Mode default tetap planar sehingga tidak ada perubahan perilaku untuk proyek yang sudah ada.

## 2. Latar belakang dan masalah
- State kamera saat ini hanya memiliki dua derajat rotasi terbatas (tilt dan roll) dan translasi 2D.
- Perluasan ke 3D penuh dibutuhkan untuk gerakan kamera sinematik: dolly, orbit, crane, truck, serta yaw/pitch/roll
  bebas pada konten peta, kartu, dan globe.
- Karena gate memvalidasi komposisi (misalnya sudut layar yang menampakkan peta), setiap perubahan model kamera harus
  disertai cermin matematis yang setara di Python.

## 3. Tujuan dan lingkup
Tujuan:
- Menyediakan full 3D camera motion untuk segmen HyperFrames dengan tetap mempertahankan kompatibilitas penuh
  terhadap mode planar yang ada.
- Memastikan determinisme frame-akurat (sampling berbasis indeks frame).
- Menjaga setiap transform kamera dapat diperiksa oleh gate melalui cermin Python.

Lingkup yang tidak termasuk:
- Model kamera native untuk footage (tetap 2D crop-pan-zoom).
- Lapisan direktur sinematik otomatis (idiom/planning tingkat lanjut).
- Perubahan skema `master_timeline.json` yang bersifat breaking.

## 4. Desain
### 4.1 Model data
Profil baru: `[camera] mode = "planar" | "3d"` (default planar, dapat dioverride per segmen).
Kunci kamera (`CameraKey`) menerima field opsional saat mode 3D:
```json
{ "at_frame": 0,
  "eye": [0.0, 0.0, 10.0],
  "target": [0.0, 0.0, 0.0],
  "roll": 0.0,
  "fov": 50.0,
  "move": "glide", "hold": 0 }
```
State kamera 3D diserialisasi sebagai posisi, orientasi quaternion, dan fov:
```json
{ "position": [x, y, z], "orientation": {"quaternion": [x, y, z, w]}, "fovDeg": 50 }
```
### 4.2 Sampler determistik
Fungsi murni `sample_camera_3d(track, frame)` menghasilkan state kamera per frame. Interpolasi posisi memakai
spline Catmull–Rom atau cubic Bézier dengan parameterisasi arc-length; orientasi memakai quaternion SLERP.
Tidak ada ketergantungan pada waktu nyata.
### 4.3 Rendering
Modul `hf/scene3d.js` dirender memakai Three.js: peta dipasang sebagai plane 3D bertekstur (reuse aset yang ada),
dan `THREE.PerspectiveCamera` digerakkan dari sampler setiap frame. Sub-frame motion blur bersifat opsional.
### 4.4 Cermin Python dan gate
Cermin proyeksi perspektif penuh ditambahkan (position + lookAt + fov). Gate yang terdampak:
| Gate | Dampak |
|---|---|
| M02 (resolusi peta) | dihitung lewat view-projection pada titik terdalam |
| M03 (tilt cover) | digeneralisasi ke cek sudut layar pada proyeksi 3D |
| R56 (gerak kamera) | ditambah metrik kecepatan/percepatan serta kecepatan sudut |

## 5. Fitur yang diusulkan
| Fitur | Hasil |
|---|---|
| FP1 | Mode kamera 3D dasar per segmen: peta sebagai plane 3D, kamera eye/target/fov dapat diatur |
| FP2 | Sampler determistik beserta lintasan sederhana antar kunci |
| FP3 | Cermin gate 3D minimal agar segmen 3D tetap tervalidasi |
| FP4 (opsional) | Orientasi quaternion bebas penuh dan sub-frame motion blur |

## 6. Kompatibilitas
- Default `mode="planar"` => build dan gate yang ada tidak berubah.
- `engine validate` memisahkan kedua bentuk; penggunaan field 3D di luar mode 3D menghasilkan error yang jelas.
- Identitas cache render diperluas dengan mode dan toolchain.

## 7. Pengujian
- Unit test proyeksi terhadap referensi, quaternion SLERP, dan parameterisasi arc-length.
- Golden frame untuk segmen 3D; perbandingan posisi/sudut Python vs browser dengan epsilon.
- Benchmark: hold+drift, dolly, orbit, whip-pan, transisi rail, dan path tertutup.

## 8. Risiko dan mitigasi
| Risiko | Mitigasi |
|---|---|
| Perubahan gate berpotensi memengaruhi build planar | mode default planar; gate 3D hanya aktif saat mode 3D |
| Chrome headless/WebGL tidak deterministik | kontrak sampler murni + verifikasi hash frame |
| Biaya render 3D lebih tinggi | reuse tekstur dan cache segmen; blur sub-frame hanya saat diminta |
| Matematika cermin berbeda dari browser | tes epsilon lintas platform dan golden frame |

## 9. Keputusan desain yang masih terbuka
1. Apakah `fov` boleh bervariasi per kunci (efek lensa) atau harus tetap linear/monotone?
2. Apakah gate subject framing/occlusion diperlukan pada fase awal atau cukup pada fase lanjutan?
3. Bentuk serialisasi track: dokumen JSON utuh versus perintah per-frame.