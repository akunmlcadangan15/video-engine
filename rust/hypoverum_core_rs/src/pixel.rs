//! Fast pixel gates on sampled frames held in memory (RGB24): R38 empty thirds, black frames, motion.

use rayon::prelude::*;

use crate::num::{rgb_len, usize_to_f64};

/// Smallest accepted frame side (px): the luma plane is sampled every 4 px.
pub const MIN_SIDE: usize = 4;

/// Rules: `w >= 4`, `h >= 4`, sizes within `num::MAX_SIDE`, every frame exactly `w*h*3` bytes.
pub fn check_frames(frames: &[&[u8]], w: usize, h: usize) -> Result<(), String> {
    if w < MIN_SIDE || h < MIN_SIDE {
        return Err(format!(
            "frame size {w}x{h} must be at least {MIN_SIDE}x{MIN_SIDE}"
        ));
    }
    let need = rgb_len(w, h, "frame")?;
    for (i, f) in frames.iter().enumerate() {
        if f.len() != need {
            return Err(format!(
                "frame {i} has {} bytes, expected {need} ({w}x{h}x3)",
                f.len()
            ));
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Default)]
pub struct FrameStats {
    pub mean_luma: f64,
    pub black_frac: f64,
    /// luma standard deviation of the top / middle / bottom third (portrait) or left / centre / right (landscape)
    pub third_std: [f64; 3],
    /// mean absolute horizontal+vertical gradient of each third (detail / edge energy)
    pub third_detail: [f64; 3],
}

#[inline]
fn luma(p: &[u8]) -> f64 {
    match p {
        [r, g, b, ..] => 0.2126 * f64::from(*r) + 0.7152 * f64::from(*g) + 0.0722 * f64::from(*b),
        _ => 0.0,
    }
}

/// downsampled luma plane (step px)
pub fn luma_plane(rgb: &[u8], w: usize, h: usize, step: usize) -> (Vec<f64>, usize, usize) {
    let (lw, lh) = (w / step, h / step);
    let mut v = Vec::with_capacity(lw * lh);
    for y in 0..lh {
        for x in 0..lw {
            let i = ((y * step) * w + x * step) * 3;
            v.push(luma(rgb.get(i..i + 3).unwrap_or(&[])));
        }
    }
    (v, lw, lh)
}

/// Stats of one frame. Precondition: [`check_frames`] passed (callers: [`stats_many`]).
#[must_use]
pub fn stats(rgb: &[u8], w: usize, h: usize) -> FrameStats {
    let (l, lw, lh) = luma_plane(rgb, w, h, 4);
    let n = usize_to_f64(l.len()).max(1.0);
    let mean = l.iter().sum::<f64>() / n;
    let black = usize_to_f64(l.iter().filter(|&&v| v < 20.0).count()) / n;
    let portrait = h >= w;
    let mut st = [0.0; 3];
    let mut det = [0.0; 3];
    for k in 0..3 {
        let (mut s, mut s2, mut d, mut c, mut cd) = (0.0f64, 0.0f64, 0.0f64, 0.0f64, 0.0f64);
        for y in 0..lh {
            for x in 0..lw {
                let band = if portrait { y * 3 / lh } else { x * 3 / lw };
                if band != k {
                    continue;
                }
                let v = l[y * lw + x];
                s += v;
                s2 += v * v;
                c += 1.0;
                if x + 1 < lw && y + 1 < lh {
                    d += (l[y * lw + x + 1] - v).abs() + (l[(y + 1) * lw + x] - v).abs();
                    cd += 1.0;
                }
            }
        }
        let m = s / c.max(1.0);
        st[k] = (s2 / c.max(1.0) - m * m).max(0.0).sqrt();
        det[k] = d / cd.max(1.0);
    }
    FrameStats {
        mean_luma: mean,
        black_frac: black,
        third_std: st,
        third_detail: det,
    }
}

/// Stats of many frames in parallel. Errors: see [`check_frames`].
pub fn stats_many(frames: &[&[u8]], w: usize, h: usize) -> Result<Vec<FrameStats>, String> {
    check_frames(frames, w, h)?;
    Ok(frames.par_iter().map(|f| stats(f, w, h)).collect())
}

/// mean absolute luma difference between consecutive frames (downsampled), one value per pair
/// Errors: see [`check_frames`].
pub fn motion(frames: &[&[u8]], w: usize, h: usize) -> Result<Vec<f64>, String> {
    check_frames(frames, w, h)?;
    let planes: Vec<Vec<f64>> = frames
        .par_iter()
        .map(|f| luma_plane(f, w, h, 4).0)
        .collect();
    Ok(planes
        .par_windows(2)
        .map(|p| match p {
            [a, b] => {
                a.iter().zip(b).map(|(x, y)| (x - y).abs()).sum::<f64>()
                    / usize_to_f64(a.len()).max(1.0)
            }
            _ => 0.0,
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn black_and_empty() {
        let (w, h) = (90, 160);
        let black = vec![0u8; w * h * 3];
        let s = stats(&black, w, h);
        assert!(s.black_frac > 0.99 && s.third_std.iter().all(|&v| v < 1.0));
        let noise: Vec<u8> = (0..w * h * 3)
            .map(|i| u8::try_from((i * 7919) % 251).unwrap())
            .collect();
        let s = stats(&noise, w, h);
        assert!(s.third_std.iter().all(|&v| v > 20.0));
        let m = motion(&[&black, &noise], w, h).unwrap();
        assert!(m[0] > 50.0);
    }

    #[test]
    fn tiny_or_mismatched_frames_are_rejected() {
        for (w, h) in [(0, 0), (3, 4), (4, 3), (1, 100)] {
            let f = vec![0u8; w * h * 3];
            assert!(stats_many(&[&f], w, h).is_err(), "{w}x{h}");
            assert!(motion(&[&f, &f], w, h).is_err(), "{w}x{h}");
        }
        let f = vec![0u8; 4 * 4 * 3];
        assert_eq!(stats_many(&[&f], 4, 4).unwrap().len(), 1);
        assert!(stats_many(&[&f[..47]], 4, 4).is_err());
        assert!(motion(&[], 4, 4).unwrap().is_empty());
        assert!(stats_many(&[&f], usize::MAX, 4).is_err());
    }
}
