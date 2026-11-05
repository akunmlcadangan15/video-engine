# Kamera 3D penuh untuk HyperFrames (dua fase pertama)

Status: proposal, belum dibangun. Untuk Arundaya, sebagai tindak lanjut riset sistem gerakan kamera
(dokumen: `camera-research.md` di root workspace) dan kendala bahwa kamera saat ini dibatasi ke tilt tunggal.

## Ringkasan

Kamera HyperFrames sekarang adalah transform **2.5D**: state `{x, y, s, tilt(rotateX), roll(rotateZ)}`
(`hyperframes.camera_plan`), diterapkan sebagai layer CSS 3D di `template/index.html` (`#tilt`/`#roll`),
dengan "cermin" Python `camera3d.py` untuk gate. Tidak ada `translateZ`, `rotateY`, maupun pose 3D penuh.

Proposal ini menambah **mode kamera 3D penuh (opt-in)** per segmen (default tetap **planar**, tanpa perubahan
perilaku), dengan state 6-DOF `(eye, target, roll, fov)` + orientasi quaternion, dirender via **scene Three.js**
(pola yang sudah dipakai `hf/globe.js`), dan dicerminkan Python untuk gate. Dua fase pertama: (1) model data +
sampler + cermin; (2) scene Three.js + capture determistik + integrasi.

## Tujuan
- Memberi **full 3D camera motion** (dolly/orbit/crane/truck, yaw/pitch/roll bebas) untuk segmen peta/kartu/globe.
- Mempertahankan kompatibilitas penuh dengan mode planar yang ada (tidak merusak build & gate saat ini).
- Menjaga **determinisme frame-akurat** (kontrak "sampleCamera(frameIndex, fps) murni", ala Remotion `useCurrentFrame()`).
- Membuat matematika kamera **dapat diperiksa** oleh gate (cermin Python + metrik).

## Non-tujuan (fase ini)
- Tidak mengganti model kamera native (footage) — tetap 2D crop-pan-zoom via Rust/ffmpeg.
- Tidak membangun **layer "direktur"** idiom sinematik (Virtual Cinematographer/Darshak) — fase 3, di luar lingkup.
- Tidak mengubah skema `master_timeline.json` yang ada secara breaking.

## 1. Model data (Fase 1)

### 1.1 Flag mode
Profil `[camera]` baru:
```toml
[camera]
mode = "planar"        # "planar" (default, perilaku sekarang) | "3d"
```
Bisa dioverride per segmen: `segments[].camera_mode = "3d"`.

### 1.2 CameraKey diperluas (opsional, hanya saat mode=3d)
Field baru di `timeline.CameraKey`:
```json
{ "at_frame": 0,
  "eye":   [0.0, 0.0, 10.0],   // posisi kamera, koordinat adegan 3D
  "target": [0.0, 0.0, 0.0],    // titik look-at
  "roll": 0.0,                  // (deg) sekitar sumbu forward, opsional
  "fov": 50.0,                  // (deg) opsional; default profil
  "move": "glide", "hold": 0 }
```
Field planar (focus/zoom/tilt) tetap valid di mode planar. Di mode 3d, `focus/zoom` dapat diturunkan dari
`eye/target` bila profil memintanya (map ke cover fit), tetapi bentuk kanonik adalah eye/target.

### 1.3 State kamera 3D (wadah serialisasi)
```json
{ "position": [x, y, z],
  "orientation": { "quaternion": [x, y, z, w] },
  "fovDeg": 50, "aspect": 0.5625, "near": 0.1, "far": 1000 }
```
Orientasi disimpan sebagai **quaternion** (hindari gimbal lock); Euler hanya untuk authoring/lapor.

### 1.4 Sampler determistik
Fungsi murni di Python yang menghasilkan state per frame:
`camera_plan_3d(scene, n, fps, theme) -> {start, legs, ...}` analog `camera_plan`, tetapi leg
menginterpolasi **posisi** (Catmull–Rom / cubic Bézier + parameterisasi arc-length) dan **orientasi**
(quaternion SLERP / spline), plus track roll/fov terpisah. Reuse easings yang ada (glide/whip/reveal/flow).
Kontrak: `sample_camera_3d(track, frame) -> state` murni (tanpa wall-clock).

## 2. Rendering Three.js (Fase 2)

### 2.1 Scene minimal yang layak (MV)
- Modul baru `hf/scene3d.js` (pola `hf/globe.js`): tekstur map yang sama (assets) dipasang pada **plane
  geometry 3D** (bukan CSS), dan `THREE.PerspectiveCamera` digerakkan dari `sample_camera_3d` tiap frame.
- Ini memberi 3D sejati di atas konten peta yang sudah ada (parallax, oklusi, roll/pitch/yaw, dolly/orbit) tanpa
  perlu membangun pipeline aset baru.
- Mode planar tetap lewat `template/index.html` yang ada (tanpa perubahan).

### 2.2 Capture determistik
- Ikuti pola globe: buat canvas, `THREE.WebGLRenderer`; untuk tiap frame: `cam.position.copy(eye); cam.lookAt(target); cam.applyQuaternion(rollQ)`; `renderer.render(scene, cam)`; baca piksel (screenshot).
- **Sub-frame motion blur** (opsional, profil): blend N sub-time per frame (shutter curve) untuk whip/pan cepat,
  sesuai riset (vawe/Unity accumulation). Default mati; aktif hanya dengan `[render] hf_motion_blur = true`.

### 2.3 Skema online (opsional, disarankan)
```text
POST /load-scene {scene, cameraTrack3d, seed, rendererConfig}
POST /render-frame {frame, subframe?}
GET  /frame/{n}.png
```

## 3. Cermin Python & dampak gate

### 3.1 Perluas `camera3d.py` (atau module baru `camera_project.py`)
- Tambah proyeksi kamera perspektif penuh (position + lookAt + fov → view-projection) untuk memetakan titik dunia →
  layar, dan nilai **texels/piksel** serta **posisi layar** elemen (analog `project/unproject/corners_off_map`).

### 3.2 Gate yang terdampak
| Gate | Sekarang | Usulan |
|---|---|---|
| `M02` Map resolution | texels/px pada zoom terdalam (2D) | hitung via view-projection 3D pada titik terdalam |
| `M03` Tilt cover | 4 sudut layar menampakkan peta (tilt CSS) | generalisasi: `corners_off_map` versi 3D (pojok layar ↔ sinar kamera memotong plane peta) |
| `R56` Camera motion | metrik gerak 2D (motion_px) | tambah metrik 3D: posisi + kecepatan/awcepatan sudut + jerk |
| `R56px` Measured motion (post) | motion luma terukur | tetap berlaku; pembanding baru untuk frame 3D |
- Gate baru (disarankan, opsional): *subject framing* (error posisi/ukuran subjek di layar) dan *occlusion*.

### 3.3 Benchmark
Suite tetap (dari riset): hold+drift, dolly linear, push-in eased, orbit, percakapan dua subjek (line-of-interest),
tracking+lead/lag, whip-pan+blur, transisi rail, oklusi, path tertutup. Rekam determinisme piksel, metrik kamera,
framing error, rasio visibilitas, waktu render/frame.

## 4. Kompatibilitas & migrasi
- Default `mode="planar"` → build & gate lama **tidak berubah**.
- `engine validate` menerima kedua bentuk; di mode planar, field 3D ditolak (dan sebaliknya) — error yang jelas.
- Cache render diperluas identitasnya dengan `mode` + toolchain (sudah ada `ffmpeg_version`/pin).
- Draft `engine draft` (map_path) tetap menulis field planar; mapping eye/target dari map_path ada di fase 3 opsional.

## 5. Rencana fase & pengujian
**Fase 1 (model + cermin):** CameraKey 3D, `camera_plan_3d` + `sample_camera_3d`, `camera_project.py`,
perluasan M02/M03/R56, unit test (`test_camera3d_full.py`): proyeksi vs referensi, quaternion SLERP, arc-length,
epsilon posisi/sudut Python vs browser (bila ada harness).
**Fase 2 (render):** `hf/scene3d.js`, integrasi segmen, capture frame determistik, path perakitan ffmpeg, golden
frames + benchmark, blur sub-frame opsional.

## 6. Risiko & mitigasi
| Risiko | Mitigasi |
|---|---|
| Perubahan gate berisiko mematahkan build planar | mode default planar; gate 3D hanya aktif saat mode 3d |
| Chrome headless/WebGL tidak deterministik | kontrak sampler murni + verifikasi hash frame; pola globe sudah membuktikan |
| Biaya render 3D lebih tinggi (WebGL tiap frame) | reuse tekstur/cache segmen; sub-frame blur hanya saat diminta |
| Math cermin melenceng dari browser | epsilon test lintas platform + golden frame |

## 7. Keputusan & pertanyaan tersisa
**Keputusan (2026-11-05, Arundaya):**
1. **Reuse aset yang ada** → plane map 3D bertekstur dari aset peta yang ada; bukan adegan three.js umum baru.
5. **Quaternion bebas penuh** → orientasi 3D bebas; roll diterapkan sebagai rotasi quaternion penuh (bukan sekadar bank).

**Tersisa (default usulan dalam kurung):**
2. FOV → default **linear/monotone** (hindari overshoot lensa); boleh dibuka per-key di fase lanjutan.
3. Gate subject framing/occlusion → default **fase lanjutan** (bukan fase 1).
4. Serialisasi track → default **dokumen JSON utuh** (kesederhanaan); opsi per-frame command bila kinerja menuntut.

