//! hypoverum_core_rs: native core of the universal hybrid video engine (PyO3 bindings).
//!
//! Core modules return `Result<_, String>` and never panic on caller input; the `py` module maps
//! errors to Python exceptions (`ValueError`, `FileNotFoundError`, `OSError`, `TypeError`).

pub mod ass;
pub mod camera;
pub mod frames;
pub mod num;
pub mod pixel;
pub mod proxy;
pub mod silence;

/// Version of every algorithm whose output lands in a cache (caption .ass text, camera windows, warp).
/// Bump on any output-changing edit; Python mixes it into native-segment, HyperFrames and map cache keys.
pub const ALGO_VERSION: &str = "3";

/// Accepted range for [`py::set_threads`].
pub const THREADS_RANGE: std::ops::RangeInclusive<usize> = 1..=256;

/// BLAKE3 of one file via fixed 4 MiB chunked reads (no mmap: a file truncated mid-hash yields an
/// I/O result, never SIGBUS).
pub fn hash_file(p: &std::path::Path) -> std::io::Result<String> {
    use std::io::Read;
    const CHUNK: usize = 4 << 20;
    let mut f = std::fs::File::open(p)?;
    let mut h = blake3::Hasher::new();
    let mut buf = vec![0u8; CHUNK];
    loop {
        let mut filled = 0;
        while filled < CHUNK {
            match f.read(&mut buf[filled..]) {
                Ok(0) => break,
                Ok(n) => filled += n,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                Err(e) => return Err(e),
            }
        }
        if filled == 0 {
            break;
        }
        if filled >= 1 << 20 {
            h.update_rayon(&buf[..filled]);
        } else {
            h.update(&buf[..filled]);
        }
        if filled < CHUNK {
            break;
        }
    }
    Ok(h.finalize().to_hex().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunked_hash_matches_one_shot() {
        let dir = std::env::temp_dir().join(format!("hcr_hash_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        for len in [0usize, 1, (1 << 20) - 1, 4 << 20, (4 << 20) + 17, 9 << 20] {
            let data: Vec<u8> = (0..len).map(|i| u8::try_from(i % 251).unwrap()).collect();
            let p = dir.join(format!("f{len}"));
            std::fs::write(&p, &data).unwrap();
            assert_eq!(
                hash_file(&p).unwrap(),
                blake3::hash(&data).to_hex().to_string(),
                "len {len}"
            );
        }
        let missing = hash_file(&dir.join("missing")).unwrap_err();
        assert_eq!(missing.kind(), std::io::ErrorKind::NotFound);
        std::fs::remove_dir_all(&dir).unwrap();
    }
}

#[cfg(feature = "py")]
mod py {
    use super::{
        ass, camera, frames, hash_file, pixel, proxy, silence, ALGO_VERSION, THREADS_RANGE,
    };
    use pyo3::exceptions::{PyFileNotFoundError, PyIOError, PyTypeError, PyValueError};
    use pyo3::prelude::*;
    use pyo3::types::{PyBytes, PyDict, PyString};
    use rayon::prelude::*;
    use std::path::PathBuf;
    use std::sync::OnceLock;

    #[pyfunction]
    fn version() -> &'static str {
        env!("CARGO_PKG_VERSION")
    }

    /// Errors: ValueError on non-finite seconds, fps outside 1..=99, frame >= 2^40.
    #[pyfunction]
    fn sec_to_frame(seconds: f64, fps: u32) -> PyResult<i64> {
        frames::sec_to_frame(seconds, fps).map_err(PyValueError::new_err)
    }

    /// BLAKE3 hex of a bytes object (GIL released).
    #[pyfunction]
    fn blake3_bytes(py: Python<'_>, data: &[u8]) -> String {
        py.detach(|| blake3::hash(data).to_hex().to_string())
    }

    /// BLAKE3 of many files in parallel (GIL released). `paths`: iterable of str / os.PathLike.
    /// Order of the result = order of `paths`. Errors: FileNotFoundError, OSError, TypeError.
    #[pyfunction]
    fn blake3_files(py: Python<'_>, paths: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
        if paths.is_instance_of::<PyString>() || paths.hasattr("__fspath__")? {
            return Err(PyTypeError::new_err(
                "blake3_files expects an iterable of paths, not a single path",
            ));
        }
        let ps: Vec<PathBuf> = paths
            .try_iter()?
            .map(|p| p.and_then(|p| p.extract::<PathBuf>()))
            .collect::<PyResult<_>>()?;
        py.detach(|| {
            ps.par_iter()
                .map(|p| hash_file(p).map_err(|e| (e.kind(), format!("{}: {e}", p.display()))))
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(|(kind, msg)| {
            if kind == std::io::ErrorKind::NotFound {
                PyFileNotFoundError::new_err(msg)
            } else {
                PyIOError::new_err(msg)
            }
        })
    }

    static POOL: OnceLock<usize> = OnceLock::new();

    /// Size the global rayon pool once (first call wins; later calls and calls after the pool
    /// already started return the size in effect). Returns `rayon::current_num_threads()`.
    /// Errors: ValueError if `n` is outside 1..=256.
    #[pyfunction]
    fn set_threads(n: usize) -> PyResult<usize> {
        if !THREADS_RANGE.contains(&n) {
            return Err(PyValueError::new_err(format!(
                "threads must be within 1..=256, got {n}"
            )));
        }
        POOL.get_or_init(|| {
            // Err = the pool was already initialised (e.g. by an earlier parallel call): keep it.
            let _ = rayon::ThreadPoolBuilder::new()
                .num_threads(n)
                .build_global();
            rayon::current_num_threads()
        });
        Ok(rayon::current_num_threads())
    }

    fn d_get<'py, T: FromPyObjectOwned<'py>>(
        d: &Bound<'py, PyDict>,
        k: &str,
        dflt: T,
    ) -> PyResult<T> {
        match d.get_item(k)? {
            Some(v) if !v.is_none() => v
                .extract::<T>()
                .map_err(|e| PyValueError::new_err(format!("caption style '{k}': {}", e.into()))),
            _ => Ok(dflt),
        }
    }

    fn style_from(d: Option<&Bound<'_, PyDict>>) -> PyResult<ass::Style> {
        let s = ass::Style::default();
        let Some(d) = d else { return Ok(s) };
        let known = [
            "font",
            "size",
            "bold",
            "uppercase",
            "primary",
            "outline_colour",
            "outline",
            "shadow",
            "soft_shadow",
            "shadow_blur",
            "shadow_alpha",
            "shadow_dx",
            "shadow_dy",
            "box_bg",
            "box_colour",
            "slot",
            "margin_x",
            "max_words",
            "max_chars",
            "gap_break_frames",
            "hold_frames",
            "active_word_highlight",
            "highlight_colour",
            "emphasis_scale",
            "emphasis_colour",
            "pop",
        ];
        for k in d.keys() {
            let k: String = k.extract()?;
            if !known.contains(&k.as_str()) {
                return Err(PyValueError::new_err(format!(
                    "unknown caption style key '{k}'"
                )));
            }
        }
        Ok(ass::Style {
            font: d_get(d, "font", s.font)?,
            size: d_get(d, "size", s.size)?,
            bold: d_get(d, "bold", s.bold)?,
            uppercase: d_get(d, "uppercase", s.uppercase)?,
            primary: d_get(d, "primary", s.primary)?,
            outline_colour: d_get(d, "outline_colour", s.outline_colour)?,
            outline: d_get(d, "outline", s.outline)?,
            shadow: d_get(d, "shadow", s.shadow)?,
            soft_shadow: d_get(d, "soft_shadow", s.soft_shadow)?,
            shadow_blur: d_get(d, "shadow_blur", s.shadow_blur)?,
            shadow_alpha: d_get(d, "shadow_alpha", s.shadow_alpha)?,
            shadow_dx: d_get(d, "shadow_dx", s.shadow_dx)?,
            shadow_dy: d_get(d, "shadow_dy", s.shadow_dy)?,
            box_bg: d_get(d, "box_bg", s.box_bg)?,
            box_colour: d_get(d, "box_colour", s.box_colour)?,
            slot: d_get(d, "slot", s.slot)?,
            margin_x: d_get(d, "margin_x", s.margin_x)?,
            max_words: d_get(d, "max_words", s.max_words)?,
            max_chars: d_get(d, "max_chars", s.max_chars)?,
            gap_break_frames: d_get(d, "gap_break_frames", s.gap_break_frames)?,
            hold_frames: d_get(d, "hold_frames", s.hold_frames)?,
            active_word_highlight: d_get(d, "active_word_highlight", s.active_word_highlight)?,
            highlight_colour: d_get(d, "highlight_colour", s.highlight_colour)?,
            emphasis_scale: d_get(d, "emphasis_scale", s.emphasis_scale)?,
            emphasis_colour: d_get(d, "emphasis_colour", s.emphasis_colour)?,
            pop: d_get(d, "pop", s.pop)?,
        })
    }

    type AssEvent = (i64, i64, String, f64, f64, f64, f64);

    /// words: [(text, start_frame, end_frame, highlight)] -> (ass_text, [(start, end, text, font_size, est_width, x, y)])
    /// Errors: ValueError (see `ass::compile`).
    #[pyfunction]
    #[pyo3(signature = (words, width, height, fps, style=None, y_overrides=None))]
    #[allow(clippy::needless_pass_by_value)] // owned Vec is the PyO3 extraction target
    fn compile_ass(
        words: Vec<(String, i64, i64, bool)>,
        width: u32,
        height: u32,
        fps: u32,
        style: Option<&Bound<'_, PyDict>>,
        y_overrides: Option<Vec<(i64, i64, f64)>>,
    ) -> PyResult<(String, Vec<AssEvent>)> {
        let st = style_from(style)?;
        let ws: Vec<ass::Word> = words
            .into_iter()
            .map(|(text, start, end, highlight)| ass::Word {
                text,
                start,
                end,
                highlight,
            })
            .collect();
        let (txt, ev) = ass::compile(
            &ws,
            &st,
            width,
            height,
            fps,
            y_overrides.as_deref().unwrap_or_default(),
        )
        .map_err(PyValueError::new_err)?;
        Ok((
            txt,
            ev.into_iter()
                .map(|e| (e.start, e.end, e.text, e.font_size, e.est_width, e.x, e.y))
                .collect(),
        ))
    }

    #[pyfunction]
    fn caption_slots() -> Vec<(String, f64)> {
        ass::SLOTS
            .iter()
            .map(|(n, y)| ((*n).to_string(), *y))
            .collect()
    }

    #[pyfunction]
    fn camera_presets() -> Vec<String> {
        camera::PRESETS.iter().map(|s| (*s).to_string()).collect()
    }

    type W4 = (f64, f64, f64, f64);

    fn to_win(t: W4) -> camera::Win {
        camera::Win {
            x: t.0,
            y: t.1,
            w: t.2,
            h: t.3,
        }
    }

    /// (start_window, end_window, motion_px) of a camera preset; windows are (x, y, w, h) in source px.
    /// Errors: ValueError on unknown preset, bad intensity, non-positive/non-finite sizes, bad focus_box.
    #[pyfunction]
    #[pyo3(signature = (preset, intensity, src_w, src_h, out_w, out_h, focus_box=None))]
    fn camera_plan(
        preset: &str,
        intensity: f64,
        src_w: f64,
        src_h: f64,
        out_w: f64,
        out_h: f64,
        focus_box: Option<[f64; 4]>,
    ) -> PyResult<(W4, W4, f64)> {
        let (s, e) = camera::endpoints(preset, intensity, focus_box, src_w, src_h, out_w, out_h)
            .map_err(PyValueError::new_err)?;
        Ok((
            (s.x, s.y, s.w, s.h),
            (e.x, e.y, e.w, e.h),
            camera::motion_px(s, e, out_w, out_h),
        ))
    }

    /// Errors: ValueError on non-finite windows, w/h <= 0, n > 2^24.
    #[pyfunction]
    fn camera_windows(start: W4, end: W4, n: usize) -> PyResult<Vec<W4>> {
        let v = camera::windows(to_win(start), to_win(end), n).map_err(PyValueError::new_err)?;
        Ok(v.into_iter().map(|v| (v.x, v.y, v.w, v.h)).collect())
    }

    /// Bilinear crop+scale of one RGB24 frame (GIL released, rows in parallel).
    /// Errors: ValueError on zero/oversized sizes, buffer length != src_w*src_h*3, bad window.
    #[pyfunction]
    fn warp_frame<'py>(
        py: Python<'py>,
        src: &[u8],
        src_w: usize,
        src_h: usize,
        win: W4,
        out_w: usize,
        out_h: usize,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let out = py
            .detach(|| camera::warp(src, src_w, src_h, to_win(win), out_w, out_h))
            .map_err(PyValueError::new_err)?;
        Ok(PyBytes::new(py, &out))
    }

    /// Borrow an iterable of bytes-like frames: `bytes` zero-copy, anything else copied via `Vec<u8>`.
    fn with_frames<R>(frames: &Bound<'_, PyAny>, f: impl FnOnce(&[&[u8]]) -> R) -> PyResult<R> {
        let items: Vec<Bound<'_, PyAny>> = frames.try_iter()?.collect::<PyResult<_>>()?;
        let mut owned: Vec<Vec<u8>> = Vec::new();
        let mut slots: Vec<Option<usize>> = Vec::with_capacity(items.len());
        for it in &items {
            if it.is_instance_of::<PyBytes>() {
                slots.push(None);
            } else {
                owned.push(it.extract::<Vec<u8>>()?);
                slots.push(Some(owned.len() - 1));
            }
        }
        let refs: Vec<&[u8]> = items
            .iter()
            .zip(&slots)
            .map(|(it, s)| match s {
                Some(i) => owned[*i].as_slice(),
                None => it.cast::<PyBytes>().map_or(&[][..], |b| b.as_bytes()),
            })
            .collect();
        Ok(f(&refs))
    }

    type StatsRow = (f64, f64, [f64; 3], [f64; 3]);

    /// Per-frame stats of RGB24 frames: [(mean_luma, black_frac, [std x3], [detail x3])].
    /// Errors: ValueError if width/height < 4 or a frame length != width*height*3.
    #[pyfunction]
    fn frame_stats(
        py: Python<'_>,
        frames: &Bound<'_, PyAny>,
        width: usize,
        height: usize,
    ) -> PyResult<Vec<StatsRow>> {
        let r = with_frames(frames, |refs| {
            py.detach(|| pixel::stats_many(refs, width, height))
        })?;
        Ok(r.map_err(PyValueError::new_err)?
            .into_iter()
            .map(|s| (s.mean_luma, s.black_frac, s.third_std, s.third_detail))
            .collect())
    }

    /// Mean absolute luma difference of consecutive frames. Errors: as `frame_stats`.
    #[pyfunction]
    fn frame_motion(
        py: Python<'_>,
        frames: &Bound<'_, PyAny>,
        width: usize,
        height: usize,
    ) -> PyResult<Vec<f64>> {
        with_frames(frames, |refs| {
            py.detach(|| pixel::motion(refs, width, height))
        })?
        .map_err(PyValueError::new_err)
    }

    fn io_err(msg: String) -> PyErr {
        if msg.contains("not found") {
            PyFileNotFoundError::new_err(msg)
        } else if msg.starts_with("cannot run")
            || msg.starts_with("ffmpeg")
            || msg.starts_with("rename")
        {
            PyIOError::new_err(msg)
        } else {
            PyValueError::new_err(msg)
        }
    }

    /// Render a low-res low-fps analysis proxy with ffmpeg (spawned from Rust, GIL released).
    /// Writes `<out>.part` then renames. Returns the output path.
    /// Errors: ValueError (spec), FileNotFoundError (src), OSError (ffmpeg failure).
    #[pyfunction]
    #[pyo3(signature = (src, out, height=360, fps=5, start_s=0.0, seconds=None, crf=30, threads=2, ffmpeg="ffmpeg"))]
    #[allow(clippy::too_many_arguments)]
    fn make_proxy(
        py: Python<'_>,
        src: PathBuf,
        out: PathBuf,
        height: u32,
        fps: u32,
        start_s: f64,
        seconds: Option<f64>,
        crf: u32,
        threads: u32,
        ffmpeg: &str,
    ) -> PyResult<PathBuf> {
        let spec = proxy::ProxySpec {
            src,
            out,
            height,
            fps,
            start_s,
            seconds,
            crf,
            threads,
        };
        py.detach(|| proxy::make_proxy(ffmpeg, &spec))
            .map_err(io_err)
    }

    type PauseRow = (i64, i64, f64);

    fn params(threshold_db: Option<f64>, min_frames: i64) -> silence::PauseParams {
        silence::PauseParams {
            threshold_db,
            min_frames,
        }
    }

    /// Pauses in mono s16le PCM bytes: ([(start_frame, end_frame, mean_db)], threshold_db).
    /// Errors: ValueError (odd byte length, rate/fps/min_frames/threshold out of range).
    #[pyfunction]
    #[pyo3(signature = (pcm, rate, fps, threshold_db=None, min_frames=6))]
    fn detect_pauses_pcm(
        py: Python<'_>,
        pcm: &[u8],
        rate: u32,
        fps: u32,
        threshold_db: Option<f64>,
        min_frames: i64,
    ) -> PyResult<(Vec<PauseRow>, f64)> {
        if !pcm.len().is_multiple_of(2) {
            return Err(PyValueError::new_err(
                "pcm byte length must be even (s16le)",
            ));
        }
        let samples: Vec<i16> = pcm
            .as_chunks::<2>()
            .0
            .iter()
            .map(|c| i16::from_le_bytes(*c))
            .collect();
        let (p, t) = py
            .detach(|| {
                silence::detect_pauses(&samples, rate, fps, params(threshold_db, min_frames))
            })
            .map_err(PyValueError::new_err)?;
        Ok((
            p.into_iter().map(|p| (p.start, p.end, p.mean_db)).collect(),
            t,
        ))
    }

    /// Decode `path` with ffmpeg (mono, `rate` Hz) and detect pauses:
    /// (pauses, threshold_db, total_frames). Errors: FileNotFoundError, OSError, ValueError.
    #[pyfunction]
    #[pyo3(signature = (path, fps, threshold_db=None, min_frames=6, rate=16000, ffmpeg="ffmpeg"))]
    fn detect_pauses_file(
        py: Python<'_>,
        path: PathBuf,
        fps: u32,
        threshold_db: Option<f64>,
        min_frames: i64,
        rate: u32,
        ffmpeg: &str,
    ) -> PyResult<(Vec<PauseRow>, f64, i64)> {
        py.detach(|| {
            frames::check_fps(fps)?;
            let pcm = proxy::decode_pcm(ffmpeg, &path, rate)?;
            let levels = silence::frame_levels(&pcm, rate, fps)?;
            let (p, t) = silence::pauses_from_levels(&levels, params(threshold_db, min_frames))?;
            let total = i64::try_from(levels.len()).map_err(|e| e.to_string())?;
            Ok((
                p.into_iter().map(|p| (p.start, p.end, p.mean_db)).collect(),
                t,
                total,
            ))
        })
        .map_err(io_err)
    }

    /// Interior cut frames from pauses so each segment is within [min_len, max_len] where pauses
    /// allow (forced cut at prev+max_len otherwise). Errors: ValueError on bad lengths.
    #[pyfunction]
    fn suggest_boundaries(
        pauses: Vec<PauseRow>,
        total: i64,
        min_len: i64,
        max_len: i64,
    ) -> PyResult<Vec<i64>> {
        let ps: Vec<silence::Pause> = pauses
            .into_iter()
            .map(|(start, end, mean_db)| silence::Pause {
                start,
                end,
                mean_db,
            })
            .collect();
        silence::suggest_boundaries(&ps, total, min_len, max_len).map_err(PyValueError::new_err)
    }

    #[pymodule]
    fn hypoverum_core_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add("ALGO_VERSION", ALGO_VERSION)?;
        m.add_function(wrap_pyfunction!(version, m)?)?;
        m.add_function(wrap_pyfunction!(set_threads, m)?)?;
        m.add_function(wrap_pyfunction!(sec_to_frame, m)?)?;
        m.add_function(wrap_pyfunction!(blake3_bytes, m)?)?;
        m.add_function(wrap_pyfunction!(blake3_files, m)?)?;
        m.add_function(wrap_pyfunction!(compile_ass, m)?)?;
        m.add_function(wrap_pyfunction!(caption_slots, m)?)?;
        m.add_function(wrap_pyfunction!(camera_presets, m)?)?;
        m.add_function(wrap_pyfunction!(camera_plan, m)?)?;
        m.add_function(wrap_pyfunction!(camera_windows, m)?)?;
        m.add_function(wrap_pyfunction!(warp_frame, m)?)?;
        m.add_function(wrap_pyfunction!(frame_stats, m)?)?;
        m.add_function(wrap_pyfunction!(frame_motion, m)?)?;
        m.add_function(wrap_pyfunction!(make_proxy, m)?)?;
        m.add_function(wrap_pyfunction!(detect_pauses_pcm, m)?)?;
        m.add_function(wrap_pyfunction!(detect_pauses_file, m)?)?;
        m.add_function(wrap_pyfunction!(suggest_boundaries, m)?)?;
        Ok(())
    }
}
