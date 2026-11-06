# Tests

126 file tes di direktori ini (`test_*.py`), 1.583 fungsi `test_*`. Jalankan dengan
`pytest -q tests/test_<area>.py` untuk satu area, atau `pytest -q` untuk seluruh suite (berat).

## Konvensi nama: GOOD dan BAD

- **GOOD** (`test_good_...`) berarti input yang harus diterima atau lolos gate.
- **BAD** (`test_bad_...`) berarti input yang harus ditolak, memicu peringatan, atau memblokir build.
- ID seperti `A01`, `T02`, `R64` adalah ID gate; definisi lengkap setiap gate ada di
  [docs/GATES.md](../docs/GATES.md). Nama tes yang memakai ID gate (mis. `test_A01_good`) merujuk ke gate itu.

## Bahasa

Docstring tes berbahasa Inggris; dokumentasi pengguna (README, CHANGELOG, CONTRIBUTING) berbahasa Indonesia.
Lihat [docs/README.md](../docs/README.md) untuk daftar bahasa per dokumen.

## Fixture bersama

`conftest.py` menyediakan fixture bersama (`project`, `assets`, `base_doc`, `strict_geoverum`, `W`, `ff`,
`engine_clip`). Helper lokal yang diduplikasi antar file (`_scene`, `_probe`, `_theme`) belum dipusatkan —
saat menambah helper baru, periksa dulu apakah sudah ada di `conftest.py`.
