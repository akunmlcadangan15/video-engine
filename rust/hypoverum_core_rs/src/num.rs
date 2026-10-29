//! Checked numeric conversions and input validation shared by the core modules.
//! Every function returns `Err(String)` instead of panicking; `lib.rs` maps errors to `ValueError`.

/// Largest accepted frame index / count (exclusive): 2^40.
pub const MAX_FRAME: i64 = 1 << 40;
/// Largest accepted image side (px) for warp / pixel input and output.
pub const MAX_SIDE: usize = 1 << 15;
/// Largest accepted RGB24 buffer (bytes): 2^31.
pub const MAX_BUF: usize = 1 << 31;

/// `v` as `i64` when finite and inside `[-2^53, 2^53]` (exactly representable range).
#[must_use]
pub fn f64_to_i64(v: f64) -> Option<i64> {
    const LIM: f64 = 9_007_199_254_740_992.0; // 2^53
    if v.is_finite() && (-LIM..=LIM).contains(&v) {
        #[allow(clippy::cast_possible_truncation)]
        Some(v as i64)
    } else {
        None
    }
}

/// `v` clamped to `0..=max` and truncated toward zero; NaN maps to 0.
#[must_use]
pub fn f64_to_index(v: f64, max: usize) -> usize {
    if v.is_nan() || v <= 0.0 {
        return 0;
    }
    let max_f = usize_to_f64(max);
    if v >= max_f {
        return max;
    }
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    let i = v as usize;
    i.min(max)
}

/// Bilinear weight of a fractional part in `0.0..1.0` as `0..=256`.
#[must_use]
pub fn weight256(frac: f64) -> u32 {
    let w = f64_to_index(frac * 256.0, 256);
    u32::try_from(w).unwrap_or(256)
}

/// `usize` as `f64` (exact below 2^53; all accepted sizes are far below).
#[must_use]
pub fn usize_to_f64(v: usize) -> f64 {
    #[allow(clippy::cast_precision_loss)]
    let f = v as f64;
    f
}

/// `NaN`-safe clamp: never panics (unlike `f64::clamp` with `lo > hi` or `NaN` bounds).
/// When `hi < lo` the result is `lo`. A `NaN` value maps to `lo`.
#[must_use]
pub fn clampf(v: f64, lo: f64, hi: f64) -> f64 {
    if v.is_nan() {
        return lo;
    }
    let hi = if hi < lo { lo } else { hi };
    if v < lo {
        lo
    } else if v > hi {
        hi
    } else {
        v
    }
}

/// `Ok(v)` when `v` is finite, else an error naming `what`.
pub fn finite(v: f64, what: &str) -> Result<f64, String> {
    if v.is_finite() {
        Ok(v)
    } else {
        Err(format!("{what} must be finite, got {v}"))
    }
}

/// `Ok(v)` when `v` is finite and `> 0`.
pub fn positive(v: f64, what: &str) -> Result<f64, String> {
    if v.is_finite() && v > 0.0 {
        Ok(v)
    } else {
        Err(format!("{what} must be finite and > 0, got {v}"))
    }
}

/// Byte length of an RGB24 image `w x h`: both sides in `1..=MAX_SIDE`, product checked, `<= MAX_BUF`.
pub fn rgb_len(w: usize, h: usize, what: &str) -> Result<usize, String> {
    if w == 0 || h == 0 || w > MAX_SIDE || h > MAX_SIDE {
        return Err(format!(
            "{what} size {w}x{h} must be within 1..={MAX_SIDE} per side"
        ));
    }
    w.checked_mul(h)
        .and_then(|p| p.checked_mul(3))
        .filter(|&n| n <= MAX_BUF)
        .ok_or_else(|| format!("{what} size {w}x{h} exceeds {MAX_BUF} bytes"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clampf_never_panics() {
        assert_eq!(clampf(5.0, 0.0, 10.0), 5.0);
        assert_eq!(clampf(f64::NAN, 1.0, 2.0), 1.0);
        assert_eq!(clampf(5.0, 3.0, 1.0), 3.0); // inverted bounds
        assert_eq!(clampf(5.0, 0.0, f64::NAN), 5.0);
    }

    #[test]
    fn conversions_are_checked() {
        assert_eq!(f64_to_i64(f64::NAN), None);
        assert_eq!(f64_to_i64(f64::INFINITY), None);
        assert_eq!(f64_to_i64(1e300), None);
        assert_eq!(f64_to_i64(-3.0), Some(-3));
        assert_eq!(f64_to_index(-1.0, 9), 0);
        assert_eq!(f64_to_index(f64::NAN, 9), 0);
        assert_eq!(f64_to_index(99.9, 9), 9);
        assert_eq!(f64_to_index(3.7, 9), 3);
        assert_eq!(weight256(0.5), 128);
        assert_eq!(weight256(1.5), 256);
    }

    #[test]
    fn rgb_len_rejects_zero_and_overflow() {
        assert_eq!(rgb_len(4, 4, "x"), Ok(48));
        assert!(rgb_len(0, 4, "x").is_err());
        assert!(rgb_len(4, 0, "x").is_err());
        assert!(rgb_len(MAX_SIDE + 1, 1, "x").is_err());
        assert!(rgb_len(MAX_SIDE, MAX_SIDE, "x").is_err()); // 3 GiB > MAX_BUF
    }
}
