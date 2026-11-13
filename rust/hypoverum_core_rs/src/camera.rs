//! Camera moves on stills/footage: per-frame view windows (sub-pixel, eased) and a bilinear crop-scale warp.
//! A window is (x, y, w, h) in source pixels with the output aspect ratio.
//! All public functions validate their inputs and return `Err(String)`; none panics on user data.

use std::io::{ErrorKind, Read, Write};

use rayon::prelude::*;

use crate::num::{clampf, f64_to_index, finite, positive, rgb_len, usize_to_f64, weight256};

/// Largest accepted number of windows in one call.
pub const MAX_WINDOWS: usize = 1 << 24;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Win {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl Win {
    /// `Ok` when all fields are finite and `w > 0`, `h > 0`.
    pub fn check(self, what: &str) -> Result<Self, String> {
        for v in [self.x, self.y] {
            finite(v, what)?;
        }
        positive(self.w, &format!("{what} width"))?;
        positive(self.h, &format!("{what} height"))?;
        Ok(self)
    }
}

/// Sine in-out with a minimum velocity (never stands still, never jerks at the cut).
#[must_use]
pub fn glide(t: f64) -> f64 {
    const G: f64 = 0.35;
    let t = clampf(t, 0.0, 1.0);
    G * t + (1.0 - G) * (1.0 - (std::f64::consts::PI * t).cos()) / 2.0
}

pub const PRESETS: &[&str] = &[
    "static",
    "ken_burns_zoom_in",
    "ken_burns_zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
];

fn clamp_win(c: (f64, f64), w: f64, h: f64, sw: f64, sh: f64) -> Win {
    let x = clampf(c.0 - w / 2.0, 0.0, (sw - w).max(0.0));
    let y = clampf(c.1 - h / 2.0, 0.0, (sh - h).max(0.0));
    Win { x, y, w, h }
}

/// Validates `focus_box = [ymin, xmin, ymax, xmax]` on the 0..=1000 grid: all finite, inside the grid,
/// `ymin < ymax`, `xmin < xmax`.
pub fn check_focus_box(b: [f64; 4]) -> Result<[f64; 4], String> {
    let [y0, x0, y1, x1] = b;
    let in_grid = b
        .iter()
        .all(|v| v.is_finite() && (0.0..=1000.0).contains(v));
    if !in_grid || y1 <= y0 || x1 <= x0 {
        return Err(format!(
            "focus_box {b:?} must be [ymin,xmin,ymax,xmax], finite, within 0..=1000, min < max"
        ));
    }
    Ok(b)
}

/// Start and end windows of a preset. `focus_box` is `[ymin, xmin, ymax, xmax]` on a 0..=1000 grid.
/// Errors: unknown preset, intensity not finite or `< 0`, source/output sizes not finite or `<= 0`, bad focus box.
pub fn endpoints(
    preset: &str,
    intensity: f64,
    focus_box: Option<[f64; 4]>,
    sw: f64,
    sh: f64,
    ow: f64,
    oh: f64,
) -> Result<(Win, Win), String> {
    if !PRESETS.contains(&preset) {
        return Err(format!(
            "unknown camera preset '{preset}' (known: {})",
            PRESETS.join(", ")
        ));
    }
    if !(intensity.is_finite() && intensity >= 0.0) {
        return Err(format!(
            "intensity must be finite and >= 0, got {intensity}"
        ));
    }
    positive(sw, "source width")?;
    positive(sh, "source height")?;
    positive(ow, "output width")?;
    positive(oh, "output height")?;
    let a = ow / oh;
    let (bw, bh) = if sw / sh > a {
        (sh * a, sh)
    } else {
        (sw, sw / a)
    };
    let centre = (sw / 2.0, sh / 2.0);
    let focus = match focus_box {
        Some(b) => {
            let [y0, x0, y1, x1] = check_focus_box(b)?;
            ((x0 + x1) / 2000.0 * sw, (y0 + y1) / 2000.0 * sh)
        }
        None => centre,
    };
    let i = intensity.max(0.0);
    let zoom = 1.0 + 0.15 * i; // ken burns end zoom
    let pz = 1.0 + 0.12 * i.max(0.5); // pans need slack; zoom in a little
    let r = match preset {
        "ken_burns_zoom_in" | "ken_burns_zoom_out" => {
            let s = clamp_win(centre, bw, bh, sw, sh);
            let e = clamp_win(focus, bw / zoom, bh / zoom, sw, sh);
            if preset == "ken_burns_zoom_in" {
                (s, e)
            } else {
                (e, s)
            }
        }
        "pan_left" | "pan_right" | "pan_up" | "pan_down" => {
            let (w, h) = (bw / pz, bh / pz);
            let horiz = preset == "pan_left" || preset == "pan_right";
            let slack = if horiz { sw - w } else { sh - h };
            let travel = (0.30 * i * if horiz { w } else { h }).min(slack.max(0.0));
            let (d0, d1) = if horiz {
                ((travel / 2.0, 0.0), (-travel / 2.0, 0.0))
            } else {
                ((0.0, travel / 2.0), (0.0, -travel / 2.0))
            };
            // pan_left = the camera looks further left over time = content moves right
            let (from, to) = if preset == "pan_left" || preset == "pan_up" {
                (d0, d1)
            } else {
                (d1, d0)
            };
            // keep the whole travel inside the source around the focus
            let mut c = focus;
            if horiz {
                c.0 = clampf(c.0, w / 2.0 + travel / 2.0, sw - w / 2.0 - travel / 2.0);
            } else {
                c.1 = clampf(c.1, h / 2.0 + travel / 2.0, sh - h / 2.0 - travel / 2.0);
            }
            (
                clamp_win((c.0 + from.0, c.1 + from.1), w, h, sw, sh),
                clamp_win((c.0 + to.0, c.1 + to.1), w, h, sw, sh),
            )
        }
        _ => {
            let w = clamp_win(focus, bw, bh, sw, sh);
            (w, w)
        }
    };
    Ok(r)
}

/// `n` per-frame windows: zoom interpolated in log space with the glide ease; the centre follows the zoom so the
/// pivot point of the two windows stays put on screen (a pure pan: centre linear with the ease).
/// Errors: a window not finite or with `w <= 0` / `h <= 0`; `n > MAX_WINDOWS`.
pub fn windows(s: Win, e: Win, n: usize) -> Result<Vec<Win>, String> {
    let s = s.check("start window")?;
    let e = e.check("end window")?;
    if n > MAX_WINDOWS {
        return Err(format!("n = {n} exceeds {MAX_WINDOWS}"));
    }
    let (cs, ce) = (
        (s.x + s.w / 2.0, s.y + s.h / 2.0),
        (e.x + e.w / 2.0, e.y + e.h / 2.0),
    );
    let (lw0, lw1) = (s.w.ln(), e.w.ln());
    let ar = s.h / s.w;
    let last = usize_to_f64(n.saturating_sub(1)).max(1.0);
    Ok((0..n)
        .map(|i| {
            let t = if n > 1 {
                glide(usize_to_f64(i) / last)
            } else {
                0.0
            };
            let w = (lw0 + (lw1 - lw0) * t).exp();
            let h = w * ar;
            // Pivoted zoom: the centre moves with the zoom, q = (ws - w) / (ws - we), so the point that stays
            // fixed on screen (the pivot of the two windows) does not slide; a pure pan moves the centre with t.
            // Every window edge is affine in q (and in t for a pan), so the path stays inside both end windows' hull.
            let q = if (s.w - e.w).abs() > 1e-9 * s.w {
                (s.w - w) / (s.w - e.w)
            } else {
                t
            };
            let c = (cs.0 + (ce.0 - cs.0) * q, cs.1 + (ce.1 - cs.1) * q);
            Win {
                x: c.0 - w / 2.0,
                y: c.1 - h / 2.0,
                w,
                h,
            }
        })
        .collect())
}

/// Visible motion of a move in OUTPUT pixels: max(centre shift, zoom change at the frame corner).
/// Inputs come from [`endpoints`] (validated, `w > 0`).
#[must_use]
pub fn motion_px(s: Win, e: Win, ow: f64, oh: f64) -> f64 {
    let wa = f64::midpoint(s.w, e.w);
    let dc = ((s.x + s.w / 2.0 - e.x - e.w / 2.0).powi(2)
        + (s.y + s.h / 2.0 - e.y - e.h / 2.0).powi(2))
    .sqrt();
    let shift = dc / wa * ow;
    let dz = (s.w / e.w).ln().abs() * ow.hypot(oh) / 2.0;
    shift.max(dz)
}

/// Error returned by streaming/batch camera warp helpers (`Value` -> `ValueError`, `Io` -> `OSError`).
#[derive(Debug)]
pub enum WarpErr {
    Value(String),
    Io(std::io::Error),
}

impl From<String> for WarpErr {
    fn from(s: String) -> Self {
        Self::Value(s)
    }
}

impl From<std::io::Error> for WarpErr {
    fn from(e: std::io::Error) -> Self {
        Self::Io(e)
    }
}

struct RgbSrc<'a> {
    bytes: &'a [u8],
    w: usize,
    h: usize,
}

struct RgbDst<'a> {
    bytes: &'a mut [u8],
    w: usize,
    h: usize,
}

/// Core validated bilinear crop+scale kernel writing into caller-owned `dst` and `xt` buffers.
fn warp_into_inner(src: RgbSrc<'_>, win: Win, dst: RgbDst<'_>, xt: &mut Vec<(usize, usize, u32)>) {
    let sx = win.w / usize_to_f64(dst.w);
    let sy = win.h / usize_to_f64(dst.h);
    let (maxx, maxy) = (src.w - 1, src.h - 1); // sw, sh >= 1 (rgb_len)
    let (maxx_f, maxy_f) = (usize_to_f64(maxx), usize_to_f64(maxy));
    let row_bytes = src.w * 3;
    xt.clear();
    xt.reserve(dst.w);
    for ox in 0..dst.w {
        let fx = clampf(win.x + (usize_to_f64(ox) + 0.5) * sx - 0.5, 0.0, maxx_f);
        let x0 = f64_to_index(fx, maxx);
        let x1 = (x0 + 1).min(maxx);
        xt.push((x0 * 3, x1 * 3, weight256(fx - usize_to_f64(x0))));
    }
    let xt_slice = xt.as_slice();
    dst.bytes
        .par_chunks_mut(dst.w * 3)
        .enumerate()
        .for_each(|(oy, row)| {
            let fy = clampf(win.y + (usize_to_f64(oy) + 0.5) * sy - 0.5, 0.0, maxy_f);
            let y0 = f64_to_index(fy, maxy);
            let y1 = (y0 + 1).min(maxy);
            let wy = weight256(fy - usize_to_f64(y0));
            let (Some(r0), Some(r1)) = (
                src.bytes.get(y0 * row_bytes..(y0 + 1) * row_bytes),
                src.bytes.get(y1 * row_bytes..(y1 + 1) * row_bytes),
            ) else {
                return; // unreachable: y0, y1 <= maxy
            };
            for (px, &(a, b, wx)) in row.as_chunks_mut::<3>().0.iter_mut().zip(xt_slice.iter()) {
                for (ch, o) in px.iter_mut().enumerate() {
                    let g = |r: &[u8], i: usize| u32::from(r.get(i + ch).copied().unwrap_or(0));
                    let top = g(r0, a) * (256 - wx) + g(r0, b) * wx;
                    let bot = g(r1, a) * (256 - wx) + g(r1, b) * wx;
                    *o = u8::try_from((top * (256 - wy) + bot * wy + 32768) >> 16)
                        .unwrap_or(u8::MAX);
                }
            }
        });
}

/// Bilinear crop+scale of an RGB24 source (`sw x sh`) into a new RGB24 buffer (`ow x oh`), rows in parallel.
/// Errors: any size 0 or `> MAX_SIDE`, buffer size overflow, `src.len() != sw*sh*3`, window not finite or
/// `w <= 0` / `h <= 0`.
pub fn warp(
    src: &[u8],
    sw: usize,
    sh: usize,
    win: Win,
    ow: usize,
    oh: usize,
) -> Result<Vec<u8>, String> {
    let need = rgb_len(sw, sh, "source")?;
    if src.len() != need {
        return Err(format!(
            "src has {} bytes, expected {need} ({sw}x{sh}x3)",
            src.len()
        ));
    }
    let out_len = rgb_len(ow, oh, "output")?;
    let win = win.check("window")?;
    let mut out = vec![0u8; out_len];
    let mut xt = Vec::with_capacity(ow);
    warp_into_inner(
        RgbSrc {
            bytes: src,
            w: sw,
            h: sh,
        },
        win,
        RgbDst {
            bytes: &mut out,
            w: ow,
            h: oh,
        },
        &mut xt,
    );
    Ok(out)
}

fn check_windows(wins: &[Win]) -> Result<Vec<Win>, String> {
    if wins.len() > MAX_WINDOWS {
        return Err(format!(
            "windows length {} exceeds MAX_WINDOWS ({MAX_WINDOWS})",
            wins.len()
        ));
    }
    wins.iter().map(|w| w.check("window")).collect()
}

fn read_frame<R: Read>(r: &mut R, buf: &mut [u8]) -> std::io::Result<bool> {
    let mut filled = 0;
    while filled < buf.len() {
        match r.read(&mut buf[filled..]) {
            Ok(0) => return Ok(false),
            Ok(n) => filled += n,
            Err(e) if e.kind() == ErrorKind::Interrupted => {}
            Err(e) => return Err(e),
        }
    }
    Ok(true)
}

/// Warps one RGB24 still `src` across all `wins` and writes every frame into `dst` with a single
/// reusable output buffer and x-tap buffer.
pub fn warp_still_to_writer<W: Write>(
    src: &[u8],
    sw: usize,
    sh: usize,
    wins: &[Win],
    ow: usize,
    oh: usize,
    mut dst: W,
) -> Result<(), WarpErr> {
    let need = rgb_len(sw, sh, "source")?;
    if src.len() != need {
        return Err(WarpErr::Value(format!(
            "src has {} bytes, expected {need} ({sw}x{sh}x3)",
            src.len()
        )));
    }
    let out_len = rgb_len(ow, oh, "output")?;
    let wins = check_windows(wins)?;
    let mut out = vec![0u8; out_len];
    let mut xt = Vec::with_capacity(ow);
    for win in wins {
        warp_into_inner(
            RgbSrc {
                bytes: src,
                w: sw,
                h: sh,
            },
            win,
            RgbDst {
                bytes: &mut out,
                w: ow,
                h: oh,
            },
            &mut xt,
        );
        dst.write_all(&out)?;
    }
    dst.flush()?;
    Ok(())
}

/// Reads RGB24 frames from `src_r`, warps each against `wins`, and writes to `dst`.
/// If `src_r` hits EOF before frame 0 completes, writes nothing and returns `Ok(0)`.
/// If `src_r` ends early after frame 0, holds the last complete frame for remaining windows.
pub fn warp_stream_rw<R: Read, W: Write>(
    mut src_r: R,
    mut dst: W,
    sw: usize,
    sh: usize,
    wins: &[Win],
    ow: usize,
    oh: usize,
) -> Result<usize, WarpErr> {
    let need = rgb_len(sw, sh, "source")?;
    let out_len = rgb_len(ow, oh, "output")?;
    let wins = check_windows(wins)?;
    if wins.is_empty() {
        return Ok(0);
    }
    let mut cur = vec![0u8; need];
    if !read_frame(&mut src_r, &mut cur)? {
        return Ok(0);
    }
    let mut next = vec![0u8; need];
    let mut out = vec![0u8; out_len];
    let mut xt = Vec::with_capacity(ow);
    let mut eof = false;
    for (i, &win) in wins.iter().enumerate() {
        if i > 0 && !eof {
            if read_frame(&mut src_r, &mut next)? {
                std::mem::swap(&mut cur, &mut next);
            } else {
                eof = true;
            }
        }
        warp_into_inner(
            RgbSrc {
                bytes: &cur,
                w: sw,
                h: sh,
            },
            win,
            RgbDst {
                bytes: &mut out,
                w: ow,
                h: oh,
            },
            &mut xt,
        );
        dst.write_all(&out)?;
    }
    dst.flush()?;
    Ok(wins.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn win(x: f64, y: f64, w: f64, h: f64) -> Win {
        Win { x, y, w, h }
    }

    #[test]
    fn zoom_in_moves_and_stays_inside() {
        let (s, e) = endpoints(
            "ken_burns_zoom_in",
            1.0,
            Some([200.0, 300.0, 800.0, 700.0]),
            4000.0,
            3000.0,
            1080.0,
            1920.0,
        )
        .unwrap();
        assert!(e.w < s.w);
        for w in windows(s, e, 240).unwrap() {
            assert!(
                w.x >= -1e-6
                    && w.y >= -1e-6
                    && w.x + w.w <= 4000.0 + 1e-6
                    && w.y + w.h <= 3000.0 + 1e-6
            );
        }
        assert!(motion_px(s, e, 1080.0, 1920.0) > 100.0);
    }

    #[test]
    fn static_has_no_motion() {
        let (s, e) = endpoints("static", 1.0, None, 1920.0, 1080.0, 1080.0, 1920.0).unwrap();
        assert!(motion_px(s, e, 1080.0, 1920.0).abs() < 1e-12);
    }

    #[test]
    fn intensity_scales_motion() {
        let m = |i| {
            let (s, e) = endpoints("pan_right", i, None, 1920.0, 1080.0, 1080.0, 1920.0).unwrap();
            motion_px(s, e, 1080.0, 1920.0)
        };
        assert!(m(2.0) > m(1.0));
    }

    #[test]
    fn warp_identity() {
        let src: Vec<u8> = (0..48u8).map(|v| v.wrapping_mul(5)).collect();
        let out = warp(&src, 4, 4, win(0.0, 0.0, 4.0, 4.0), 4, 4).unwrap();
        assert_eq!(src, out);
    }

    #[test]
    fn bad_preset() {
        assert!(endpoints("wobble", 1.0, None, 10.0, 10.0, 9.0, 16.0).is_err());
    }

    #[test]
    fn warp_rejects_degenerate_input_without_panic() {
        let w = win(0.0, 0.0, 1.0, 1.0);
        assert!(warp(&[], 0, 0, w, 4, 4).is_err()); // empty src / zero sizes (sw-1 underflow)
        assert!(warp(&[0; 3], 1, 1, w, 0, 4).is_err()); // chunk size 0
        assert!(warp(&[0; 3], 1, 1, w, 4, 0).is_err());
        assert!(warp(&[0; 2], 1, 1, w, 4, 4).is_err()); // length mismatch
        assert!(warp(&[0; 3], 1, 1, win(f64::NAN, 0.0, 1.0, 1.0), 4, 4).is_err());
        assert!(warp(&[0; 3], 1, 1, win(0.0, 0.0, 0.0, 1.0), 4, 4).is_err());
        assert!(warp(&[0; 3], 1, 1, w, usize::MAX, usize::MAX).is_err()); // w*h*3 overflow
        assert!(warp(&[0; 3], usize::MAX, 1, w, 4, 4).is_err());
        assert_eq!(warp(&[7; 3], 1, 1, w, 2, 2).unwrap(), vec![7; 12]); // 1x1 source
    }

    #[test]
    fn warp_window_outside_source_is_clamped() {
        let src = vec![9u8; 4 * 4 * 3];
        let out = warp(&src, 4, 4, win(1e9, -1e9, 1e9, 1e9), 3, 3).unwrap();
        assert!(out.iter().all(|&v| v == 9));
    }

    #[test]
    fn endpoints_reject_bad_sizes() {
        assert!(endpoints("pan_left", 1.0, None, 100.0, 100.0, 0.0, 1920.0).is_err());
        assert!(endpoints("pan_left", 1.0, None, 100.0, 100.0, 1080.0, 0.0).is_err());
        assert!(endpoints("pan_left", 1.0, None, f64::NAN, 100.0, 1080.0, 1920.0).is_err());
        assert!(endpoints("pan_up", 1.0, None, 100.0, f64::INFINITY, 1080.0, 1920.0).is_err());
        assert!(endpoints("pan_up", f64::NAN, None, 100.0, 100.0, 1080.0, 1920.0).is_err());
        assert!(endpoints("pan_up", -1.0, None, 100.0, 100.0, 1080.0, 1920.0).is_err());
    }

    #[test]
    fn pans_never_panic_on_extreme_intensity() {
        for p in ["pan_left", "pan_right", "pan_up", "pan_down"] {
            for i in [0.0, 1e-9, 3.3, 1e6, 1e300] {
                let (s, e) = endpoints(
                    p,
                    i,
                    Some([0.0, 0.0, 1.0, 1.0]),
                    1000.0,
                    1000.0,
                    1080.0,
                    1920.0,
                )
                .unwrap();
                assert!(s.x.is_finite() && e.y.is_finite());
            }
        }
    }

    #[test]
    fn focus_box_validation() {
        let ok = |b| endpoints("static", 1.0, Some(b), 100.0, 100.0, 9.0, 16.0).is_ok();
        assert!(ok([0.0, 0.0, 1000.0, 1000.0]));
        assert!(!ok([f64::NAN, 0.0, 10.0, 10.0]));
        assert!(!ok([0.0, f64::NAN, 10.0, 10.0]));
        assert!(!ok([0.0, 0.0, f64::INFINITY, 10.0]));
        assert!(!ok([0.0, -1.0, 10.0, 10.0])); // xmin outside the grid
        assert!(!ok([0.0, 0.0, 1001.0, 10.0])); // ymax outside the grid
        assert!(!ok([10.0, 0.0, 5.0, 10.0])); // unordered
        assert!(!ok([0.0, 10.0, 10.0, 10.0])); // empty
    }

    #[test]
    fn windows_validate() {
        let good = win(0.0, 0.0, 10.0, 10.0);
        assert!(windows(win(0.0, 0.0, 0.0, 10.0), good, 3).is_err());
        assert!(windows(good, win(0.0, 0.0, -1.0, 10.0), 3).is_err());
        assert!(windows(good, win(f64::NAN, 0.0, 1.0, 1.0), 3).is_err());
        assert!(windows(good, good, MAX_WINDOWS + 1).is_err());
        assert_eq!(windows(good, good, 0).unwrap().len(), 0);
        let one = windows(good, good, 1).unwrap();
        assert_eq!(one.len(), 1);
        assert!((one[0].w - 10.0).abs() < 1e-9 && one[0].x.abs() < 1e-9);
    }
}

#[cfg(test)]
mod pivot_tests {
    use super::{windows, Win};

    /// Screen position (fraction of the frame) of source point p in window w.
    fn screen(w: &Win, p: (f64, f64)) -> (f64, f64) {
        ((p.0 - w.x) / w.w, (p.1 - w.y) / w.h)
    }

    #[test]
    fn zoom_keeps_the_pivot_fixed_on_screen() {
        let s = Win {
            x: 0.0,
            y: 0.0,
            w: 1000.0,
            h: 1778.0,
        };
        let e = Win {
            x: 300.0,
            y: 500.0,
            w: 600.0,
            h: 1066.8,
        };
        // pivot p* = (ws*ce - we*cs) / (ws - we)
        let (cs, ce) = ((500.0, 889.0), (600.0, 1033.4));
        let p = (
            (s.w * ce.0 - e.w * cs.0) / (s.w - e.w),
            (s.w * ce.1 - e.w * cs.1) / (s.w - e.w),
        );
        let v = windows(s, e, 61).unwrap();
        let p0 = screen(&v[0], p);
        for w in &v {
            let q = screen(w, p);
            assert!(
                (q.0 - p0.0).abs() < 1e-9 && (q.1 - p0.1).abs() < 1e-9,
                "{q:?} vs {p0:?}"
            );
            assert!(
                w.x >= -1e-9
                    && w.y >= -1e-9
                    && w.x + w.w <= 1000.0 + 1e-9
                    && w.y + w.h <= 1778.0 + 1e-6
            );
        }
        let l = v.last().unwrap();
        assert!((l.x - e.x).abs() < 1e-9 && (l.w - e.w).abs() < 1e-9);
    }

    #[test]
    fn pure_pan_moves_the_centre_with_the_ease() {
        let s = Win {
            x: 0.0,
            y: 0.0,
            w: 500.0,
            h: 900.0,
        };
        let e = Win {
            x: 400.0,
            y: 0.0,
            w: 500.0,
            h: 900.0,
        };
        let v = windows(s, e, 31).unwrap();
        assert!((v[15].x - 200.0).abs() < 1e-9 && v.iter().all(|w| (w.w - 500.0).abs() < 1e-9));
    }

    #[test]
    fn still_writer_matches_single_warp() {
        let (sw, sh, ow, oh) = (8, 6, 4, 4);
        let src: Vec<u8> = (0..sw * sh * 3).map(|i| (i * 7 % 251) as u8).collect();
        let wins = windows(
            Win {
                x: 0.0,
                y: 0.0,
                w: 6.0,
                h: 6.0,
            },
            Win {
                x: 2.0,
                y: 1.0,
                w: 4.0,
                h: 4.0,
            },
            5,
        )
        .unwrap();
        let mut batched = Vec::new();
        super::warp_still_to_writer(&src, sw, sh, &wins, ow, oh, &mut batched).unwrap();
        let expected: Vec<u8> = wins
            .iter()
            .flat_map(|&w| super::warp(&src, sw, sh, w, ow, oh).unwrap())
            .collect();
        assert_eq!(batched, expected);
    }

    #[test]
    fn stream_rw_holds_short_tail_and_handles_empty_input() {
        let (sw, sh, ow, oh) = (6, 6, 4, 4);
        let fsz = sw * sh * 3;
        let f0: Vec<u8> = (0..fsz).map(|i| (i % 200) as u8).collect();
        let f1: Vec<u8> = (0..fsz).map(|i| ((i + 50) % 200) as u8).collect();
        let wins = [
            Win {
                x: 0.0,
                y: 0.0,
                w: 4.0,
                h: 4.0,
            },
            Win {
                x: 1.0,
                y: 0.0,
                w: 4.0,
                h: 4.0,
            },
            Win {
                x: 1.0,
                y: 1.0,
                w: 4.0,
                h: 4.0,
            },
        ];
        // Empty / truncated first frame -> 0 written
        let mut out_empty = Vec::new();
        assert_eq!(
            super::warp_stream_rw(&f0[..10], &mut out_empty, sw, sh, &wins, ow, oh).unwrap(),
            0
        );
        assert!(out_empty.is_empty());
        // 2 input frames for 3 windows -> frame 2 reuses f1
        let mut stream_in = Vec::new();
        stream_in.extend_from_slice(&f0);
        stream_in.extend_from_slice(&f1);
        let mut out = Vec::new();
        assert_eq!(
            super::warp_stream_rw(stream_in.as_slice(), &mut out, sw, sh, &wins, ow, oh).unwrap(),
            3
        );
        let mut expected = Vec::new();
        expected.extend_from_slice(&super::warp(&f0, sw, sh, wins[0], ow, oh).unwrap());
        expected.extend_from_slice(&super::warp(&f1, sw, sh, wins[1], ow, oh).unwrap());
        expected.extend_from_slice(&super::warp(&f1, sw, sh, wins[2], ow, oh).unwrap());
        assert_eq!(out, expected);
    }

    #[test]
    fn still_and_stream_validate_inputs_before_io() {
        let src = vec![0u8; 4 * 4 * 3];
        let bad = [Win {
            x: 0.0,
            y: 0.0,
            w: 0.0,
            h: 4.0,
        }];
        let mut out = Vec::new();
        assert!(matches!(
            super::warp_still_to_writer(&src, 4, 4, &bad, 4, 4, &mut out),
            Err(super::WarpErr::Value(_))
        ));
        assert!(out.is_empty());
        assert!(matches!(
            super::warp_stream_rw(src.as_slice(), &mut out, 4, 4, &bad, 4, 4),
            Err(super::WarpErr::Value(_))
        ));
    }
}
