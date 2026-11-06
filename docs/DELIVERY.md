# Delivery

What `engine deliver` writes for upload.

**What this document answers**

- The folder and file names of the upload package and how large files are split.
- Which build checks must pass before a package is written.

Delivery goes to Google Drive; the Projects Manager bot arranges and handles the upload.
Files over 100 MB are split into parts of at most 100,000,000 bytes, never re-encoded.
The JOIN text carries the join command.
Once a project is finished, delete every asset downloaded for it; assets already on the box stay.

`engine deliver P --dry-run` prints:
- Root `NN - YYYY-MM-DD - Topik`, final dir `05 Final`, assets dir `03 Aset`.
- `NN - Topik - final.mp4`: one file when its size is at most 100,000,000 bytes (`deliver.PART_MAX_BYTES`);
    larger, `.part1` ... `.partN` byte ranges of at most that size each.
  `NN - JOIN.txt` names exactly those parts in order (cat / copy /b instructions), or says no join is needed for one file.
- `NN - credits.txt` from the project `credits.txt`.
Requires `settings.project_number`, `settings.topic`, `settings.date`.
Refuses a final file that is not the output of the last successful build: missing `build_report.json` / `build_manifest.json`, a `build_report.failed.json` next to it, `build_id` mismatch between report and manifest, or a final file whose sha256 differs from `final_sha256`.
The engine does not upload; the Projects Manager bot handles Google Drive delivery.
