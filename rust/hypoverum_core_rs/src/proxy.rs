//! ffmpeg orchestration from Rust: low-resolution, low-fps analysis proxies and mono PCM decode for
//! [`crate::silence`]. Every call spawns `ffmpeg` with an argument vector (no shell), writes the
//! proxy to `<out>.part` and renames it into place only after ffmpeg exits 0.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// Accepted proxy heights (px, even).
pub const HEIGHT_RANGE: std::ops::RangeInclusive<u32> = 64..=1080;
/// Accepted proxy frame rates.
pub const PROXY_FPS_RANGE: std::ops::RangeInclusive<u32> = 1..=30;
/// Largest decoded PCM accepted (samples): 6 h at 16 kHz.
pub const MAX_SAMPLES: usize = 6 * 3600 * 16_000;

/// Proxy request.
#[derive(Debug, Clone, PartialEq)]
pub struct ProxySpec {
    pub src: PathBuf,
    pub out: PathBuf,
    pub height: u32,
    pub fps: u32,
    pub start_s: f64,
    /// `None` = until the end of the source.
    pub seconds: Option<f64>,
    pub crf: u32,
    pub threads: u32,
}

/// ffmpeg argument vector for a proxy (without the program name). Errors on invalid spec.
pub fn proxy_args(s: &ProxySpec, part: &Path) -> Result<Vec<String>, String> {
    if !HEIGHT_RANGE.contains(&s.height) || !s.height.is_multiple_of(2) {
        return Err(format!(
            "proxy height {} must be even and within 64..=1080",
            s.height
        ));
    }
    if !PROXY_FPS_RANGE.contains(&s.fps) {
        return Err(format!("proxy fps {} must be within 1..=30", s.fps));
    }
    if !s.start_s.is_finite() || s.start_s < 0.0 {
        return Err(format!(
            "start_s must be finite and >= 0, got {}",
            s.start_s
        ));
    }
    if let Some(d) = s.seconds {
        if !d.is_finite() || d <= 0.0 {
            return Err(format!("seconds must be finite and > 0, got {d}"));
        }
    }
    if s.crf > 51 || !(1..=16).contains(&s.threads) {
        return Err(format!(
            "crf {} must be <= 51 and threads {} within 1..=16",
            s.crf, s.threads
        ));
    }
    let mut a: Vec<String> = ["-v", "error", "-nostdin", "-y"].map(String::from).to_vec();
    if s.start_s > 0.0 {
        a.extend(["-ss".into(), format!("{:.3}", s.start_s)]);
    }
    if let Some(d) = s.seconds {
        a.extend(["-t".into(), format!("{d:.3}")]);
    }
    a.extend(["-i".into(), s.src.display().to_string()]);
    a.extend([
        "-an".into(),
        "-vf".into(),
        format!(
            "fps={},scale=-2:{}:flags=bilinear,setsar=1",
            s.fps, s.height
        ),
        "-c:v".into(),
        "libx264".into(),
        "-preset".into(),
        "veryfast".into(),
        "-crf".into(),
        s.crf.to_string(),
        "-pix_fmt".into(),
        "yuv420p".into(),
        "-threads".into(),
        s.threads.to_string(),
        "-movflags".into(),
        "+faststart".into(),
        "-f".into(),
        "mp4".into(),
        part.display().to_string(),
    ]);
    Ok(a)
}

fn part_path(out: &Path) -> PathBuf {
    let mut p = out.as_os_str().to_owned();
    p.push(".part");
    PathBuf::from(p)
}

/// Render the proxy. Returns the output path. Errors: invalid spec, missing source, ffmpeg spawn
/// failure or non-zero exit (stderr tail included), empty output.
pub fn make_proxy(ffmpeg: &str, s: &ProxySpec) -> Result<PathBuf, String> {
    if !s.src.is_file() {
        return Err(format!("proxy source not found: {}", s.src.display()));
    }
    let part = part_path(&s.out);
    let args = proxy_args(s, &part)?;
    if let Some(d) = s.out.parent() {
        std::fs::create_dir_all(d).map_err(|e| format!("{}: {e}", d.display()))?;
    }
    let o = Command::new(ffmpeg)
        .args(&args)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("cannot run {ffmpeg}: {e}"))?;
    if !o.status.success() {
        let _ = std::fs::remove_file(&part);
        let err = String::from_utf8_lossy(&o.stderr);
        let tail: String = err
            .chars()
            .rev()
            .take(600)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        return Err(format!(
            "ffmpeg proxy failed ({}): {}",
            o.status,
            tail.trim()
        ));
    }
    let len = std::fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
    if len == 0 {
        let _ = std::fs::remove_file(&part);
        return Err("ffmpeg proxy produced an empty file".into());
    }
    std::fs::rename(&part, &s.out).map_err(|e| format!("rename {}: {e}", s.out.display()))?;
    Ok(s.out.clone())
}

/// Decode the first audio stream of `src` to mono s16le at `rate` Hz via ffmpeg (streamed through
/// a pipe). Errors: missing file, spawn failure, non-zero exit, more than [`MAX_SAMPLES`].
pub fn decode_pcm(ffmpeg: &str, src: &Path, rate: u32) -> Result<Vec<i16>, String> {
    if !src.is_file() {
        return Err(format!("audio source not found: {}", src.display()));
    }
    if !crate::silence::RATE_RANGE.contains(&rate) {
        return Err(format!("sample rate {rate} must be within 1000..=192000"));
    }
    let mut child = Command::new(ffmpeg)
        .args(["-v", "error", "-nostdin", "-i"])
        .arg(src)
        .args([
            "-vn",
            "-ac",
            "1",
            "-ar",
            &rate.to_string(),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("cannot run {ffmpeg}: {e}"))?;
    let mut bytes = Vec::new();
    let mut stdout = child.stdout.take().ok_or("no stdout pipe")?;
    let limit = (MAX_SAMPLES as u64) * 2 + 1;
    stdout
        .by_ref()
        .take(limit)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 >= limit {
        let _ = child.kill();
        let _ = child.wait();
        return Err(format!("decoded audio exceeds {MAX_SAMPLES} samples"));
    }
    drop(stdout);
    let o = child.wait_with_output().map_err(|e| e.to_string())?;
    if !o.status.success() {
        return Err(format!(
            "ffmpeg decode failed ({}): {}",
            o.status,
            String::from_utf8_lossy(&o.stderr).trim()
        ));
    }
    Ok(bytes
        .as_chunks::<2>()
        .0
        .iter()
        .map(|c| i16::from_le_bytes(*c))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(out: &Path) -> ProxySpec {
        ProxySpec {
            src: "in.mp4".into(),
            out: out.into(),
            height: 360,
            fps: 5,
            start_s: 1.5,
            seconds: Some(4.0),
            crf: 30,
            threads: 2,
        }
    }

    #[test]
    fn args_are_exact() {
        let a = proxy_args(&spec(Path::new("/x/p.mp4")), Path::new("/x/p.mp4.part")).unwrap();
        let j = a.join(" ");
        assert!(j.contains("-ss 1.500 -t 4.000 -i in.mp4"), "{j}");
        assert!(j.contains("fps=5,scale=-2:360:flags=bilinear,setsar=1"));
        assert!(j.ends_with("-f mp4 /x/p.mp4.part"));
    }

    #[test]
    fn args_reject_bad_spec() {
        let o = Path::new("/x/p.mp4");
        for f in [
            |s: &mut ProxySpec| s.height = 361,
            |s: &mut ProxySpec| s.height = 2160,
            |s: &mut ProxySpec| s.fps = 0,
            |s: &mut ProxySpec| s.fps = 60,
            |s: &mut ProxySpec| s.start_s = -1.0,
            |s: &mut ProxySpec| s.start_s = f64::NAN,
            |s: &mut ProxySpec| s.seconds = Some(0.0),
            |s: &mut ProxySpec| s.crf = 60,
            |s: &mut ProxySpec| s.threads = 0,
        ] {
            let mut s = spec(o);
            f(&mut s);
            assert!(proxy_args(&s, o).is_err(), "{s:?}");
        }
    }

    fn have_ffmpeg() -> bool {
        Command::new("ffmpeg")
            .arg("-version")
            .output()
            .is_ok_and(|o| o.status.success())
    }

    #[test]
    fn proxy_and_pcm_roundtrip() {
        if !have_ffmpeg() {
            eprintln!("ffmpeg not available: skipped");
            return;
        }
        let d = std::env::temp_dir().join(format!("hcr_proxy_{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let src = d.join("src.mp4");
        let ok = Command::new("ffmpeg")
            .args([
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=640x480:r=30:d=2",
                "-f",
                "lavfi",
                "-i",
                "sine=f=440:d=1,apad=pad_dur=1",
                "-shortest",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-c:a",
                "aac",
            ])
            .arg(&src)
            .status()
            .unwrap()
            .success();
        assert!(ok);
        let mut s = spec(&d.join("p.mp4"));
        s.src = src.clone();
        s.start_s = 0.0;
        s.seconds = None;
        let out = make_proxy("ffmpeg", &s).unwrap();
        assert!(out.is_file() && !part_path(&out).exists());
        let pcm = decode_pcm("ffmpeg", &src, 16_000).unwrap();
        assert!((31_000..=33_000).contains(&pcm.len()), "{}", pcm.len());
        let (p, _) = crate::silence::detect_pauses(
            &pcm,
            16_000,
            30,
            crate::silence::PauseParams {
                threshold_db: Some(-50.0),
                min_frames: 6,
            },
        )
        .unwrap();
        assert_eq!(p.len(), 1);
        assert!((29..=32).contains(&p[0].start), "{p:?}");
        // bad: missing source, failing ffmpeg leaves no .part
        s.src = d.join("missing.mp4");
        assert!(make_proxy("ffmpeg", &s).is_err());
        let bad = d.join("bad.mp4");
        std::fs::write(&bad, b"not a video").unwrap();
        s.src = bad.clone();
        s.out = d.join("p2.mp4");
        assert!(make_proxy("ffmpeg", &s)
            .unwrap_err()
            .contains("ffmpeg proxy failed"));
        assert!(!part_path(&s.out).exists() && !s.out.exists());
        assert!(decode_pcm("ffmpeg", &bad, 16_000).is_err());
        assert!(decode_pcm("/nonexistent/ffmpeg", &src, 16_000)
            .unwrap_err()
            .contains("cannot run"));
        std::fs::remove_dir_all(&d).unwrap();
    }
}
