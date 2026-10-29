//! Energy-based pause detection on narration PCM and pause-driven segment boundary suggestions.
//! Input: mono `i16` samples at `rate` Hz. Output: half-open integer frame ranges `[start, end)` at
//! the timeline `fps`. Frame `f` covers samples `[f*rate/fps, (f+1)*rate/fps)` (integer math).

use crate::frames::check_fps;
use crate::num::{f64_to_i64, usize_to_f64, MAX_FRAME};

/// Accepted PCM sample rates (Hz).
pub const RATE_RANGE: std::ops::RangeInclusive<u32> = 1000..=192_000;
/// Level of an all-zero frame (dBFS); keeps the output finite.
pub const FLOOR_DB: f64 = -120.0;
/// Adaptive threshold: speech level (90th percentile of frame levels) minus this margin (dB).
pub const ADAPTIVE_MARGIN_DB: f64 = 24.0;
/// Adaptive threshold is never above this (dBFS), so a quiet recording still finds pauses.
pub const ADAPTIVE_CEIL_DB: f64 = -30.0;

/// Parameters of [`detect_pauses`].
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PauseParams {
    /// Frames below this level (dBFS) are silent; `None` = adaptive (see [`ADAPTIVE_MARGIN_DB`]).
    pub threshold_db: Option<f64>,
    /// Shortest pause reported, in frames (>= 1).
    pub min_frames: i64,
}

/// One detected pause: frames `[start, end)` and its mean level in dBFS.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pause {
    pub start: i64,
    pub end: i64,
    pub mean_db: f64,
}

fn check_rate(rate: u32) -> Result<(), String> {
    if RATE_RANGE.contains(&rate) {
        Ok(())
    } else {
        Err(format!("sample rate {rate} must be within 1000..=192000"))
    }
}

/// RMS level (dBFS, full scale = 32768) of every video frame. A trailing partial frame counts.
/// Errors: rate or fps out of range, frame count >= 2^40.
pub fn frame_levels(samples: &[i16], rate: u32, fps: u32) -> Result<Vec<f64>, String> {
    check_fps(fps)?;
    check_rate(rate)?;
    let n = samples.len() as u128;
    let (r, f) = (u128::from(rate), u128::from(fps));
    // number of frames = ceil(n * fps / rate)
    let frames = (n * f).div_ceil(r);
    if frames >= MAX_FRAME as u128 {
        return Err(format!("{frames} frames exceeds the frame limit 2^40"));
    }
    let frames = usize::try_from(frames).map_err(|_| "frame count overflows usize".to_string())?;
    let mut out = Vec::with_capacity(frames);
    for i in 0..frames as u128 {
        let a = usize::try_from(i * r / f)
            .unwrap_or(usize::MAX)
            .min(samples.len());
        let b = usize::try_from((i + 1) * r / f)
            .unwrap_or(usize::MAX)
            .min(samples.len());
        let w = &samples[a..b];
        if w.is_empty() {
            out.push(FLOOR_DB);
            continue;
        }
        let sum: f64 = w.iter().map(|&s| f64::from(s) * f64::from(s)).sum();
        let rms = (sum / usize_to_f64(w.len())).sqrt() / 32768.0;
        out.push(if rms > 0.0 {
            (20.0 * rms.log10()).max(FLOOR_DB)
        } else {
            FLOOR_DB
        });
    }
    Ok(out)
}

/// Threshold used when `threshold_db` is `None`: `clamp(p90 - margin, FLOOR_DB + 1, ceil)`, so an
/// all-silent input is one pause.
#[must_use]
pub fn adaptive_threshold(levels: &[f64]) -> f64 {
    if levels.is_empty() {
        return ADAPTIVE_CEIL_DB;
    }
    let mut v = levels.to_vec();
    v.sort_by(f64::total_cmp);
    let idx = (v.len() - 1) * 9 / 10;
    (v[idx] - ADAPTIVE_MARGIN_DB).clamp(FLOOR_DB + 1.0, ADAPTIVE_CEIL_DB)
}

/// Pauses in per-frame levels: maximal runs of frames below the threshold, at least `min_frames`
/// long. Returns `(pauses, threshold_used)`. Errors: non-finite threshold, `min_frames < 1`.
pub fn pauses_from_levels(levels: &[f64], p: PauseParams) -> Result<(Vec<Pause>, f64), String> {
    if p.min_frames < 1 || p.min_frames >= MAX_FRAME {
        return Err(format!(
            "min_frames {} must be within 1..2^40",
            p.min_frames
        ));
    }
    let thr = match p.threshold_db {
        Some(t) if t.is_finite() => t,
        Some(t) => return Err(format!("threshold_db must be finite, got {t}")),
        None => adaptive_threshold(levels),
    };
    let mut out = Vec::new();
    let mut run_start: Option<usize> = None;
    let close = |s: usize, e: usize, out: &mut Vec<Pause>| {
        let (si, ei) = (s as i64, e as i64);
        if ei - si >= p.min_frames {
            let mean = levels[s..e].iter().sum::<f64>() / usize_to_f64(e - s);
            out.push(Pause {
                start: si,
                end: ei,
                mean_db: mean,
            });
        }
    };
    for (i, &l) in levels.iter().enumerate() {
        match (l < thr, run_start) {
            (true, None) => run_start = Some(i),
            (false, Some(s)) => {
                close(s, i, &mut out);
                run_start = None;
            }
            _ => {}
        }
    }
    if let Some(s) = run_start {
        close(s, levels.len(), &mut out);
    }
    Ok((out, thr))
}

/// [`frame_levels`] + [`pauses_from_levels`].
pub fn detect_pauses(
    samples: &[i16],
    rate: u32,
    fps: u32,
    p: PauseParams,
) -> Result<(Vec<Pause>, f64), String> {
    pauses_from_levels(&frame_levels(samples, rate, fps)?, p)
}

/// Interior cut frames for a timeline of `total` frames, each segment within `[min_len, max_len]`
/// frames where the pauses allow it. Cuts sit at pause midpoints; among the candidates reachable
/// from the previous cut the longest pause wins (earliest on ties). With no candidate in range a
/// forced cut lands at `prev + max_len`. A final remainder shorter than `min_len` merges into the
/// previous segment. Leading/trailing pauses (touching frame 0 or `total`) are never cut points.
/// Errors: `total < 1`, `min_len < 1`, `max_len < min_len`, values >= 2^40.
pub fn suggest_boundaries(
    pauses: &[Pause],
    total: i64,
    min_len: i64,
    max_len: i64,
) -> Result<Vec<i64>, String> {
    if !(1..MAX_FRAME).contains(&total) {
        return Err(format!("total {total} must be within 1..2^40"));
    }
    if min_len < 1 || max_len < min_len || max_len >= MAX_FRAME {
        return Err(format!(
            "need 1 <= min_len ({min_len}) <= max_len ({max_len}) < 2^40"
        ));
    }
    let cands: Vec<(i64, i64)> = pauses
        .iter()
        .filter(|p| p.start > 0 && p.end < total && p.end > p.start)
        .map(|p| (p.start + (p.end - p.start) / 2, p.end - p.start))
        .collect();
    let mut cuts = Vec::new();
    let mut prev = 0i64;
    while total - prev > max_len {
        let lo = prev + min_len;
        let hi = prev + max_len;
        let best = cands
            .iter()
            .filter(|&&(m, _)| m >= lo && m <= hi && total - m >= min_len)
            .fold(None::<(i64, i64)>, |acc, &(m, len)| match acc {
                Some((_, bl)) if bl >= len => acc,
                _ => Some((m, len)),
            });
        let cut = best.map_or(hi, |(m, _)| m);
        cuts.push(cut);
        prev = cut;
    }
    if let Some(&last) = cuts.last() {
        if total - last < min_len {
            cuts.pop();
        }
    }
    Ok(cuts)
}

/// Seconds -> sample count guard used by the decoder (rejects absurd durations).
pub fn max_samples(seconds: f64, rate: u32) -> Result<usize, String> {
    let n = f64_to_i64((seconds * f64::from(rate)).ceil())
        .filter(|&n| n >= 0)
        .ok_or("bad duration")?;
    usize::try_from(n).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(n: usize, amp: f64) -> Vec<i16> {
        (0..n)
            .map(|i| {
                let v = amp * 32767.0 * (usize_to_f64(i) * 0.3).sin();
                #[allow(clippy::cast_possible_truncation)]
                let s = v as i16;
                s
            })
            .collect()
    }

    /// 1 s speech, 0.5 s silence, 1 s speech, 0.1 s silence, 1 s speech at 16 kHz.
    fn narration() -> Vec<i16> {
        let mut v = tone(16_000, 0.3);
        v.extend(vec![0i16; 8_000]);
        v.extend(tone(16_000, 0.3));
        v.extend(vec![3i16; 1_600]);
        v.extend(tone(16_000, 0.3));
        v
    }

    #[test]
    fn levels_cover_partial_frame() {
        let l = frame_levels(&[0i16; 1001], 1000, 30).unwrap();
        assert_eq!(l.len(), 31); // ceil(1001*30/1000)
        assert!(l.iter().all(|&d| d == FLOOR_DB));
        let full = frame_levels(&[i16::MAX; 3200], 16_000, 10).unwrap();
        assert!(full.iter().all(|&d| d.abs() < 0.01));
        assert!(frame_levels(&[], 16_000, 30).unwrap().is_empty());
    }

    #[test]
    fn finds_the_long_pause_only() {
        let (p, thr) = detect_pauses(
            &narration(),
            16_000,
            30,
            PauseParams {
                threshold_db: None,
                min_frames: 6,
            },
        )
        .unwrap();
        assert!(thr <= ADAPTIVE_CEIL_DB);
        assert_eq!(p.len(), 1, "{p:?}");
        assert_eq!((p[0].start, p[0].end), (30, 45));
        // a 3-frame minimum also finds the 0.1 s gap (3 frames)
        let (p3, _) = detect_pauses(
            &narration(),
            16_000,
            30,
            PauseParams {
                threshold_db: Some(-50.0),
                min_frames: 3,
            },
        )
        .unwrap();
        assert_eq!(
            p3.iter().map(|p| (p.start, p.end)).collect::<Vec<_>>(),
            vec![(30, 45), (75, 78)]
        );
    }

    #[test]
    fn all_silent_input_is_one_pause() {
        let (p, thr) = detect_pauses(
            &[0i16; 16_000],
            16_000,
            25,
            PauseParams {
                threshold_db: None,
                min_frames: 5,
            },
        )
        .unwrap();
        assert_eq!(thr, FLOOR_DB + 1.0);
        assert_eq!((p.len(), p[0].start, p[0].end), (1, 0, 25));
    }

    #[test]
    fn trailing_silence_is_closed() {
        let mut v = tone(16_000, 0.3);
        v.extend(vec![0i16; 16_000]);
        let (p, _) = detect_pauses(
            &v,
            16_000,
            25,
            PauseParams {
                threshold_db: Some(-40.0),
                min_frames: 5,
            },
        )
        .unwrap();
        assert_eq!((p[0].start, p[0].end), (25, 50));
    }

    #[test]
    fn rejects_bad_params() {
        let s = [0i16; 10];
        assert!(detect_pauses(
            &s,
            16_000,
            0,
            PauseParams {
                threshold_db: None,
                min_frames: 1
            }
        )
        .is_err());
        assert!(detect_pauses(
            &s,
            999,
            30,
            PauseParams {
                threshold_db: None,
                min_frames: 1
            }
        )
        .is_err());
        assert!(detect_pauses(
            &s,
            16_000,
            30,
            PauseParams {
                threshold_db: None,
                min_frames: 0
            }
        )
        .is_err());
        assert!(detect_pauses(
            &s,
            16_000,
            30,
            PauseParams {
                threshold_db: Some(f64::NAN),
                min_frames: 1
            }
        )
        .is_err());
    }

    fn pz(s: i64, e: i64) -> Pause {
        Pause {
            start: s,
            end: e,
            mean_db: -60.0,
        }
    }

    #[test]
    fn boundaries_prefer_longest_pause_in_range() {
        let ps = [pz(100, 104), pz(140, 160), pz(200, 202)];
        // segments 60..200 frames: from 0, candidates 102 (4), 150 (20) -> 150; remainder 150 <= 200
        assert_eq!(suggest_boundaries(&ps, 300, 60, 200).unwrap(), vec![150]);
    }

    #[test]
    fn boundaries_force_cut_without_pause_and_merge_short_tail() {
        assert_eq!(
            suggest_boundaries(&[], 500, 60, 200).unwrap(),
            vec![200, 400]
        );
        // remainder 10 < min_len -> forced cut dropped; last segment = 210 (> max, accepted: no pause)
        assert_eq!(
            suggest_boundaries(&[], 210, 60, 200).unwrap(),
            Vec::<i64>::new()
        );
        // leading / trailing pauses never become cuts
        assert_eq!(
            suggest_boundaries(&[pz(0, 20), pz(280, 300)], 300, 60, 400).unwrap(),
            Vec::<i64>::new()
        );
        assert_eq!(
            suggest_boundaries(&[], 50, 60, 200).unwrap(),
            Vec::<i64>::new()
        );
    }

    #[test]
    fn boundaries_reject_bad_params() {
        assert!(suggest_boundaries(&[], 0, 1, 2).is_err());
        assert!(suggest_boundaries(&[], 10, 0, 2).is_err());
        assert!(suggest_boundaries(&[], 10, 5, 4).is_err());
        assert!(suggest_boundaries(&[], 10, 1, MAX_FRAME).is_err());
    }
}
