# rea-video - dari bedahan ke style untuk video-engine

Skill rea-video ada di skills/rea-video/SKILL.md.
File ini menjelaskan cara hasil bedahan dipakai di video-engine tanpa mengulang bedah tiap build.

## 1. Bedah sekali, distilasi jadi style
Satu breakdown berisi per-shot:
- asal gambar (real / AI / digital human / 3D / 2D / stop-motion / code / screen / still)
- editing & packaging: caption, flower text, sticker/GIF/PiP, template layout
- audio: TTS vs real voice, BGM (gaya, BPM), SFX + cut beat
- fotografi (jika real): framing, kamera, lighting, grading
- rencana remake per shot

Yang diulang menjadi style:
- struktur storyboard (hook -> masalah -> bukti -> CTA)
- metrik timing: durasi shot rata-rata, kepadatan cut, cut mengikuti beat
- gaya caption/overlay: warna, outline, posisi, animasi masuk
- audio: rentang BPM, voice vs music, kapan SFX menempel
- aturan aset: mana wajib asli, mana boleh AI

## 2. Simpan style di profiles/
Format mengikuti profiles/default.toml + profiles/geoverum.toml.
Gunakan template profiles/rea-style-template.toml, salin ke profiles/<channel>.toml.
Prioritas tetap: default.toml < profil channel < nilai proyek.

## 3. Dari style ke master_timeline.json
Setiap segmen = satu shot hasil bedahan (atau gabungan shot sefungsi).
Pilih jalur render per segmen:
- footage/foto/pan-zoom -> Native (ffmpeg + Rust)
- peta, pin, region, rute, kartu, judul -> HyperFrames (HTML+GSAP)
Caption ditulis sebagai .ass via Rust (engine words), bukan digambar ulang di HTML.
Perintah tidak berubah: engine validate, engine preflight, scripts/engine-build, engine sheet.

## 4. Batch banyak video (mis. 160)
Satu video = satu contoh. Banyak video = pola statistik.
  bash scripts/rea-video-batch.sh referensi/ _analysis/
Script itu hanya memanggil rea-video decompose + summary per file.
Setelahnya agregasi breakdown.json: median durasi shot, frekuensi taksonomi,
distribusi BPM, gaya caption dominan. Hasilnya ditulis ke profiles/.

## 5. Audit final
Setelah build, bedah lagi out/final.mp4 dengan rea-video untuk QA apakah style terwujud.
