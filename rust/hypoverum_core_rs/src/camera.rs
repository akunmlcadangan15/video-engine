//! Camera moves on stills/footage: per-frame view windows (sub-pixel, eased) and a bilinear crop-scale warp.
//! A window is (x, y, w, h) in source pixels with the output aspect ratio.
//! All public functions validate their inputs and return `Err(String)`; none panics on user data.

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

/// `n` per-frame windows: zoom interpolated in log space, centre linear, both with the glide ease.
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
            let c = (cs.0 + (ce.0 - cs.0) * t, cs.1 + (ce.1 - cs.1) * t);
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
    let sx = win.w / usize_to_f64(ow);
    let sy = win.h / usize_to_f64(oh);
    let (maxx, maxy) = (sw - 1, sh - 1); // sw, sh >= 1 (rgb_len)
    let (maxx_f, maxy_f) = (usize_to_f64(maxx), usize_to_f64(maxy));
    let row_bytes = sw * 3;
    // x taps: (byte offset x0, byte offset x1, weight of x1 in 0..=256)
    let xt: Vec<(usize, usize, u32)> = (0..ow)
        .map(|ox| {
            let fx = clampf(win.x + (usize_to_f64(ox) + 0.5) * sx - 0.5, 0.0, maxx_f);
            let x0 = f64_to_index(fx, maxx);
            let x1 = (x0 + 1).min(maxx);
            (x0 * 3, x1 * 3, weight256(fx - usize_to_f64(x0)))
        })
        .collect();
    out.par_chunks_mut(ow * 3)
        .enumerate()
        .for_each(|(oy, row)| {
            let fy = clampf(win.y + (usize_to_f64(oy) + 0.5) * sy - 0.5, 0.0, maxy_f);
            let y0 = f64_to_index(fy, maxy);
            let y1 = (y0 + 1).min(maxy);
            let wy = weight256(fy - usize_to_f64(y0));
            let (Some(r0), Some(r1)) = (
                src.get(y0 * row_bytes..(y0 + 1) * row_bytes),
                src.get(y1 * row_bytes..(y1 + 1) * row_bytes),
            ) else {
                return; // unreachable: y0, y1 <= maxy
            };
            for (px, &(a, b, wx)) in row.as_chunks_mut::<3>().0.iter_mut().zip(xt.iter()) {
                for (ch, o) in px.iter_mut().enumerate() {
                    let g = |r: &[u8], i: usize| u32::from(r.get(i + ch).copied().unwrap_or(0));
                    let top = g(r0, a) * (256 - wx) + g(r0, b) * wx;
                    let bot = g(r1, a) * (256 - wx) + g(r1, b) * wx;
                    *o = u8::try_from((top * (256 - wy) + bot * wy + 32768) >> 16)
                        .unwrap_or(u8::MAX);
                }
            }
        });
    Ok(out)
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
