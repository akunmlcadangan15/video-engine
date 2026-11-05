#!/usr/bin/env python3
"""profile-builder.py — bantu membuat/memprbaiki profil dari rea-style.
Bukan profil itu sendiri. Baca style-sheet + breakdown, tulis saran perubahan ke profiles/.
Contoh:
  python tools/rea-style/profile-builder.py --style tools/rea-style/template.toml --out profiles/<channel>.toml --dry-run
Saat ini: tampilkan rencana, belum menulis otomatis tanpa konfirmasi.
"""
import argparse, sys, pathlib
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--style", required=True, help="style-sheet rea-style (template.toml)")
    p.add_argument("--out", required=True, help="target profil di profiles/")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    print(f"style-sheet : {args.style}")
    print(f"target profil: {args.out}")
    print("dry-run: belum ada perubahan ditulis.")
    print("Langkah: isi style-sheet dari breakdown.json, lalu terapkan ke profil target.")
    if not pathlib.Path(args.style).exists():
        sys.exit(f"style-sheet tidak ada: {args.style}")
if __name__ == "__main__":
    main()
