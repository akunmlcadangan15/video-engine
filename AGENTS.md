# video-engine — konteks untuk agent

Panduan operasional teknis untuk pengembangan dan eksekusi `video-engine`. Setiap perubahan pada kode sumber, skema timeline, maupun profil channel harus mengikuti struktur lima lapisan dan lolos verifikasi pre-flight serta dokumentasi.

Tujuan: engine video vertikal hybrid. Input: folder proyek berisi `master_timeline.json`. Output: `<project>/out/final.mp4` (H.264 1080x1920, AAC 48 kHz) + `out/build_report.json`; build gagal hanya menulis `out/build_report.failed.json`.

## Struktur
- `video_engine/` — paket Python (CLI `engine`, timeline, gates, renderer, audio, draft, cache); `video_engine/ai/` provider AI. Semua gate terdaftar di `video_engine/gate_registry.py` (`engine gates`).
- `rust/hypoverum_core_rs/` — ekstensi Rust (PyO3): kamera, caption `.ass`, pauses, QA metrics.
- `tests/` — pytest (kasus GOOD/BAD per gate). `examples/minimal` — proyek contoh offline.
- `docs/` — mulai dari `docs/README.md` (indeks). Referensi: `CLI.md`, `ENVIRONMENT.md`, `TIMELINE.md`, `PROFILES.md`, `GATES.md`, `BUILD.md`, `DELIVERY.md`, `AI.md`; juga `SPEC.md`, `RUST.md`, `SOUND.md`, `RATIONALE.md`, `GLOSSARY.md`. `profiles/` — profil channel (`default.toml`, `geoverum.toml` + `geoverum/` lewat `include`, `taste/`).
- `vendor/` — toolchain terkunci (Node, HyperFrames, Chrome); `.cache` — symlink ke `/workspace/cache/engine` (cache BLAKE3, besar; `VIDEO_ENGINE_CACHE` di `engine.env`).
- `README.md` — ringkasan dan mulai cepat. Referensi lengkap (CLI, env var, gate, profil) ada di `docs/`; baca bagian yang perlu saja (`rg -n`), jangan seluruhnya. `CONTRIBUTING.md` — cara bekerja; `CHANGELOG.md` — perubahan.

## Perintah kunci
- Env: `source /workspace/engine-venv/bin/activate`; Rust: `source ~/.cargo/env`, build dengan `CC=/usr/bin/gcc`.
- `engine validate P` · `engine preflight P` · `engine-build P` / `engine-batch ...` (wrapper wajib: lock global `/workspace/.render.lock`, tolak jika disk < 8G, env `engine.env`, nice; jangan panggil `engine build` langsung).
- `engine preview P --seg N --time S` · `engine sheet P` · `engine doctor`.
- `engine gates [--profile NAME]` (daftar gate dan efeknya) · `engine gates --stats P...` (hitung temuan dari laporan build) · build percobaan: `engine-build P --mode draft` (hasil di `out/draft/`, tidak bisa dikirim).
- `engine assets --query WORDS` · `engine draft P --provider agent --resume`.
- Tes cepat: `pytest -q tests/test_<area>.py` (jangan full suite kecuali perlu; berat). Dokumen: `python scripts/check_docs.py`.
- Exit code: 0 sukses, 1 gate/engine error, 2 usage error, 3 menunggu respons agent.

## Aturan
- Tidak ada worktree (folder `/workspace/video-engine-worktrees/` sudah tidak ada); salinan di `/workspace/exports/` hanya arsip, bukan kode aktif. Jangan buat worktree/branch tanpa izin.
- Cek render berjalan: `flock -n /workspace/.render.lock true && echo bebas || echo sibuk`; jangan pakai `pgrep -f` (cocok dengan cmdline agent sendiri).
- Jangan hapus/prune cache (`engine clean`, `engine cache --prune`) tanpa izin; backup (hardlink) di `/workspace/cache/engine-backup/`.
- Disk ~80% penuh (cek `df -h /`): render uji ke `/workspace/_scratch/`, jangan duplikasi aset/cache.
- Jangan cetak `GEMINI_API_KEY` atau key provider lain.
- Log build panjang: baca dengan `tail -n 50` / `rg -n 'FAIL|ERROR'`, jangan dump.

Aturan efisiensi token: /workspace/ai-helpers/EFFICIENCY.md (wajib).
