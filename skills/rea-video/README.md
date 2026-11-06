# rea-video - skill analisis video (terpasang di video-engine)

Skill disalin utuh dari https://github.com/JuneYaooo/rea-video - struktur dipertahankan:

skills/rea-video/
  SKILL.md                 - entry point untuk agent, baca ini dulu
  references/              - 8 referensi rinci sesuai kebutuhan
    setup.md, taxonomy.md, review.md, audio.md, materials.md
    output-template.md, remake.md, memory.md
  README.md                - peta ke video-engine (file ini)

Kapan dipakai:
- Ada video referensi dan mau tahu asal shot, cut, caption, SFX, sumber suara.
- Hasil bedahan mau dijadikan style/profil agar build berikutnya tidak perlu bedah lagi.
- Audit out/final.mp4 hasil engine build.

Yang TIDAK dilakukan:
- Tidak me-render video. Outputnya breakdown.json, report.html/md, storyboard, stems, packaging.
- Tidak menulis master_timeline.json langsung. Penerjemahan ke timeline lihat docs/REA-VIDEO.md.

Instalasi CLI (satu kali):
  pip install "rea-video[ocr] @ git+https://github.com/JuneYaooo/rea-video"
  rea-video --help
Syarat: Python >= 3.10, ffmpeg di PATH. VLM opsional.

Alur cepat:
  rea-video decompose <video.mp4> -o _analysis/<nama> [--vlm]
  rea-video summary _analysis/<nama>/breakdown.json

Lalu baca docs/REA-VIDEO.md untuk mengubah breakdown -> style -> profiles.
