//! Integer frame math. The only seconds->frames conversion of the engine
//! (`video_engine.timeline.sec_to_frame` calls into this crate).

use crate::num::{f64_to_i64, MAX_FRAME};

/// Accepted frame rates: integer `1..=99` (`frame_to_cs` is frame exact only below 100 fps).
pub const FPS_RANGE: std::ops::RangeInclusive<u32> = 1..=99;

/// `Ok(())` when `fps` is in [`FPS_RANGE`].
pub fn check_fps(fps: u32) -> Result<(), String> {
    if FPS_RANGE.contains(&fps) {
        Ok(())
    } else {
        Err(format!("fps {fps} must be within 1..=99"))
    }
}

/// Frame index of a time in seconds: round half away from zero, clamped to `>= 0`.
/// Errors: non-finite `seconds`, fps outside `1..=99`, result `>= 2^40`.
pub fn sec_to_frame(seconds: f64, fps: u32) -> Result<i64, String> {
    check_fps(fps)?;
    if !seconds.is_finite() {
        return Err(format!("seconds must be finite, got {seconds}"));
    }
    let f = f64_to_i64((seconds * f64::from(fps)).round())
        .filter(|&f| f < MAX_FRAME)
        .ok_or_else(|| format!("{seconds} s at {fps} fps exceeds the frame limit 2^40"))?;
    Ok(f.max(0))
}

/// .ass timestamp in centiseconds that shows an event exactly from frame `frame` on:
/// `floor(frame * 100 / fps)` in integer arithmetic. Exact for fps < 100: frame n is presented at
/// `n/fps >= cs/100` and frame n-1 at `(n-1)/fps < cs/100` because `1/fps > 0.01 s`.
/// Errors: `frame < 0`, overflow, fps outside `1..=99`.
pub fn frame_to_cs(frame: i64, fps: u32) -> Result<i64, String> {
    check_fps(fps)?;
    if frame < 0 {
        return Err(format!("frame {frame} must be >= 0"));
    }
    frame
        .checked_mul(100)
        .map(|v| v / i64::from(fps))
        .ok_or_else(|| format!("frame {frame} overflows centiseconds"))
}

/// Centiseconds -> `h:mm:ss.cc` (negative input clamps to 0).
#[must_use]
pub fn cs_to_ass(cs: i64) -> String {
    let cs = cs.max(0);
    let h = cs / 360_000;
    let m = (cs / 6000) % 60;
    let s = (cs / 100) % 60;
    let c = cs % 100;
    format!("{h}:{m:02}:{s:02}.{c:02}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn eight_seconds_is_240() {
        assert_eq!(sec_to_frame(8.0, 30), Ok(240));
        assert_eq!(sec_to_frame(0.0166, 30), Ok(0));
        assert_eq!(sec_to_frame(0.0167, 30), Ok(1));
        assert_eq!(sec_to_frame(2.5, 1), Ok(3)); // half away from zero
        assert_eq!(sec_to_frame(-4.0, 30), Ok(0));
    }

    #[test]
    fn sec_to_frame_rejects_bad_input() {
        assert!(sec_to_frame(f64::NAN, 30).is_err());
        assert!(sec_to_frame(f64::INFINITY, 30).is_err());
        assert!(sec_to_frame(1.0, 0).is_err());
        assert!(sec_to_frame(1.0, 100).is_err());
        assert!(sec_to_frame(1e12, 99).is_err()); // > 2^40 frames
        assert_eq!(sec_to_frame(1.0, 99), Ok(99));
    }

    #[test]
    fn ass_floor_is_frame_exact() {
        for fps in [1u32, 24, 25, 30, 50, 60, 99] {
            for n in 0..5_000i64 {
                let cs = frame_to_cs(n, fps).unwrap();
                assert!(n * 100 >= cs * i64::from(fps));
                if n > 0 {
                    assert!((n - 1) * 100 < cs * i64::from(fps));
                }
            }
        }
        assert_eq!(cs_to_ass(frame_to_cs(240, 30).unwrap()), "0:00:08.00");
    }

    #[test]
    fn frame_to_cs_is_checked() {
        assert!(frame_to_cs(-1, 30).is_err());
        assert!(frame_to_cs(i64::MAX, 30).is_err());
        assert!(frame_to_cs(10, 0).is_err());
        assert!(frame_to_cs(10, 100).is_err());
        assert_eq!(frame_to_cs((1 << 40) - 1, 99).map(|v| v > 0), Ok(true));
    }
}
