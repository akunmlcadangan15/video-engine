//! .ass caption compiler: word timestamps (integer frames) -> 1-2 word captions with a configurable style.
//! Default style: no backing box; white bold text, dark outline, soft shadow.
//! Safe margins hold by construction: every caption is one line, scaled down when its estimated width
//! exceeds width - 2*margin_x.

use crate::frames::{check_fps, cs_to_ass, frame_to_cs};
use crate::num::MAX_FRAME;

#[derive(Clone, Debug)]
pub struct Word {
    pub text: String,
    pub start: i64,
    pub end: i64,
    pub highlight: bool,
}

#[derive(Clone, Debug)]
pub struct Style {
    pub font: String,
    pub size: f64,
    pub bold: bool,
    pub uppercase: bool,
    pub primary: String, // RRGGBB
    pub outline_colour: String,
    pub outline: f64,
    pub shadow: f64,
    pub soft_shadow: bool,
    pub shadow_blur: f64,
    pub shadow_alpha: u8, // 0 opaque .. 255 invisible
    pub shadow_dx: Option<f64>, // soft shadow offset x (px); None = `shadow`
    pub shadow_dy: Option<f64>, // soft shadow offset y (px); None = 1.5 x `shadow`
    pub box_bg: bool,     // BorderStyle 3 (opaque box). Default false.
    pub box_colour: String,
    pub slot: String,  // lower | mid | upper
    pub margin_x: f64, // safe side margin (px)
    pub max_words: usize,
    pub max_chars: usize,
    pub gap_break_frames: i64,
    pub hold_frames: i64,
    pub active_word_highlight: bool,
    pub highlight_colour: String,
    pub emphasis_scale: f64, // per-word highlight=true: size factor (colour stays primary unless emphasis_colour set)
    pub emphasis_colour: String, // "" = primary
    pub pop: bool,
}

impl Default for Style {
    fn default() -> Self {
        Style {
            font: "Inter".into(),
            size: 84.0,
            bold: true,
            uppercase: false,
            primary: "FFFFFF".into(),
            outline_colour: "000000".into(),
            outline: 5.0,
            shadow: 3.0,
            soft_shadow: true,
            shadow_blur: 6.0,
            shadow_alpha: 0x60,
            shadow_dx: None,
            shadow_dy: None,
            box_bg: false,
            box_colour: "000000".into(),
            slot: "lower".into(),
            margin_x: 90.0,
            max_words: 2,
            max_chars: 18,
            gap_break_frames: 8,
            hold_frames: 6,
            active_word_highlight: false,
            highlight_colour: "FFD84A".into(),
            emphasis_scale: 1.12,
            emphasis_colour: "".into(),
            pop: true,
        }
    }
}

impl Style {
    /// Rules: `size > 0`, `emphasis_scale > 0`, `outline >= 0`, `shadow_blur >= 0`, `shadow` finite,
    /// `0 <= margin_x < width/2`, `max_words >= 1`, `max_chars >= 1`, `gap_break_frames >= 0`,
    /// `hold_frames >= 0`, `font` non-empty without `,` or control characters, known slot, RRGGBB colours.
    pub fn validate(&self, width: u32) -> Result<(), String> {
        let pos = |v: f64, k: &str| {
            if v.is_finite() && v > 0.0 {
                Ok(())
            } else {
                Err(format!(
                    "caption style '{k}' must be finite and > 0, got {v}"
                ))
            }
        };
        let nonneg = |v: f64, k: &str| {
            if v.is_finite() && v >= 0.0 {
                Ok(())
            } else {
                Err(format!(
                    "caption style '{k}' must be finite and >= 0, got {v}"
                ))
            }
        };
        pos(self.size, "size")?;
        pos(self.emphasis_scale, "emphasis_scale")?;
        nonneg(self.outline, "outline")?;
        nonneg(self.shadow_blur, "shadow_blur")?;
        for (v, k) in [(self.shadow_dx, "shadow_dx"), (self.shadow_dy, "shadow_dy")] {
            if let Some(v) = v {
                if !v.is_finite() {
                    return Err(format!("caption style '{k}' must be finite, got {v}"));
                }
            }
        }
        if !self.shadow.is_finite() {
            return Err(format!(
                "caption style 'shadow' must be finite, got {}",
                self.shadow
            ));
        }
        let half = f64::from(width) / 2.0;
        if !(self.margin_x.is_finite() && self.margin_x >= 0.0 && self.margin_x < half) {
            return Err(format!(
                "caption style 'margin_x' {} must be within 0 <= margin_x < width/2 ({half})",
                self.margin_x
            ));
        }
        if self.max_words == 0 || self.max_chars == 0 {
            return Err("caption style 'max_words' and 'max_chars' must be >= 1".into());
        }
        if self.gap_break_frames < 0 || self.hold_frames < 0 {
            return Err("caption style 'gap_break_frames' and 'hold_frames' must be >= 0".into());
        }
        if self.font.trim().is_empty()
            || self.font.contains(',')
            || self.font.chars().any(char::is_control)
        {
            return Err(format!(
                "caption style 'font' {:?} must be non-empty without ',' or control characters",
                self.font
            ));
        }
        slot_y(&self.slot)
            .ok_or_else(|| format!("caption slot '{}' invalid (lower|mid|upper)", self.slot))?;
        for c in [
            &self.primary,
            &self.outline_colour,
            &self.box_colour,
            &self.highlight_colour,
        ] {
            ass_colour(c, 0)?;
        }
        if !self.emphasis_colour.is_empty() {
            ass_colour(&self.emphasis_colour, 0)?;
        }
        Ok(())
    }
}

/// Word rules: `0 <= start < end < 2^40`, sorted by `start` (non-decreasing).
pub fn check_words(words: &[Word]) -> Result<(), String> {
    let mut prev = 0i64;
    for (i, w) in words.iter().enumerate() {
        if !(0 <= w.start && w.start < w.end && w.end < MAX_FRAME) {
            return Err(format!(
                "word {i} {:?}: frames must satisfy 0 <= start < end < 2^40, got [{}, {})",
                w.text, w.start, w.end
            ));
        }
        if w.start < prev {
            return Err(format!("word {i} {:?}: start {} before previous start {prev}; words must be sorted by start", w.text, w.start));
        }
        prev = w.start;
    }
    Ok(())
}

pub const SLOTS: &[(&str, f64)] = &[("lower", 0.70), ("mid", 0.50), ("upper", 0.24)];

pub fn slot_y(slot: &str) -> Option<f64> {
    SLOTS.iter().find(|(n, _)| *n == slot).map(|(_, y)| *y)
}

/// "RRGGBB" -> ASS "&HAABBGGRR"
fn ass_colour(rgb: &str, alpha: u8) -> Result<String, String> {
    let h = rgb.trim_start_matches('#');
    if h.len() != 6 || !h.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(format!("colour '{}' must be RRGGBB", rgb));
    }
    Ok(format!("&H{:02X}{}{}{}", alpha, &h[4..6], &h[2..4], &h[0..2]).to_uppercase())
}

/// "RRGGBB" -> override-tag colour "&HBBGGRR&"
fn tag_colour(rgb: &str) -> Result<String, String> {
    let c = ass_colour(rgb, 0)?;
    Ok(format!("&H{}&", &c[4..]))
}

fn ends_phrase(t: &str) -> bool {
    t.trim_end().ends_with(|c: char| ".,!?;:…".contains(c))
}

/// Conservative width estimate of Inter Bold text at `size` px (em fractions per glyph class).
pub fn est_width(text: &str, size: f64) -> f64 {
    text.chars()
        .map(|c| {
            if c == ' ' {
                0.28
            } else if "iIl.,:;!|'".contains(c) {
                0.32
            } else if "mwMW".contains(c) {
                0.95
            } else if c.is_uppercase() || c.is_ascii_digit() {
                0.74
            } else {
                0.62
            }
        })
        .sum::<f64>()
        * size
}

#[derive(Clone, Debug)]
pub struct Caption {
    pub start: i64,
    pub end: i64,
    pub words: Vec<Word>,
}

fn push_caption(caps: &mut Vec<Caption>, words: Vec<Word>) {
    if let (Some(first), Some(last)) = (words.first(), words.last()) {
        let (start, end) = (first.start, last.end);
        caps.push(Caption { start, end, words });
    }
}

pub fn group(words: &[Word], st: &Style) -> Vec<Caption> {
    let ws: Vec<Word> = words
        .iter()
        .filter(|w| !w.text.trim().is_empty() && w.end > w.start)
        .map(|w| Word {
            text: w.text.trim().to_string(),
            ..w.clone()
        })
        .collect();
    let mut caps: Vec<Caption> = Vec::new();
    let mut cur: Vec<Word> = Vec::new();
    for w in ws {
        if let Some(prev) = cur.last() {
            let chars: usize = cur
                .iter()
                .map(|x| x.text.chars().count() + 1)
                .sum::<usize>()
                + w.text.chars().count();
            let split = cur.len() >= st.max_words.max(1)
                || chars > st.max_chars
                || w.start.saturating_sub(prev.end) > st.gap_break_frames
                || ends_phrase(&prev.text);
            if split {
                push_caption(&mut caps, std::mem::take(&mut cur));
            }
        }
        cur.push(w);
    }
    push_caption(&mut caps, cur);
    // hold each caption a little, but never into the next one and never past 2^40
    let n = caps.len();
    for i in 0..n {
        let next = if i + 1 < n {
            caps[i + 1].start
        } else {
            i64::MAX
        };
        let held = caps[i].end.saturating_add(st.hold_frames);
        caps[i].end = held
            .min(next)
            .min(MAX_FRAME)
            .max(caps[i].start.saturating_add(1));
    }
    caps
}

#[derive(Clone, Debug)]
pub struct Event {
    pub start: i64,
    pub end: i64,
    pub text: String, // plain text shown
    pub font_size: f64,
    pub est_width: f64,
    pub x: f64,
    pub y: f64,
}

/// Caption text escaping for libass: `{`/`}` -> `(`/`)`; `\` -> U+FF3C FULLWIDTH REVERSE SOLIDUS (libass has
/// no `\\` escape, so a literal backslash could form `\N`, `\n`, `\h`); every control character
/// (CR, LF, tab, ...) -> space.
#[must_use]
pub fn esc(t: &str) -> String {
    t.chars()
        .map(|c| match c {
            '{' => '(',
            '}' => ')',
            '\\' => '\u{FF3C}',
            c if c.is_control() => ' ',
            c => c,
        })
        .collect()
}

/// `y_overrides`: (start_frame, end_frame, y_px). A caption whose start frame lies in [start, end) is centred at y_px
/// instead of the slot y (automatic placement away from on-screen elements).
/// Errors: fps outside 1..=99, width/height 0, invalid style (see [`Style::validate`]), invalid words
/// (see [`check_words`]), a `y_overrides` entry with non-finite `y` or `start > end`.
pub fn compile(
    words: &[Word],
    st: &Style,
    w: u32,
    h: u32,
    fps: u32,
    y_overrides: &[(i64, i64, f64)],
) -> Result<(String, Vec<Event>), String> {
    check_fps(fps)?;
    if w == 0 || h == 0 {
        return Err(format!("video size {w}x{h} must be > 0"));
    }
    st.validate(w)?;
    check_words(words)?;
    for (a, b, y) in y_overrides {
        if !y.is_finite() || a > b {
            return Err(format!(
                "y_override ({a}, {b}, {y}): y must be finite and start <= end"
            ));
        }
    }
    let yfrac = slot_y(&st.slot)
        .ok_or_else(|| format!("caption slot '{}' invalid (lower|mid|upper)", st.slot))?;
    let prim = ass_colour(&st.primary, 0)?;
    // libass BorderStyle=3 draws the opaque box in OutlineColour and its shadow in BackColour.
    let (outl, back) = if st.box_bg {
        (
            ass_colour(&st.box_colour, 0x50)?,
            ass_colour(&st.outline_colour, st.shadow_alpha)?,
        )
    } else {
        (
            ass_colour(&st.outline_colour, 0x20)?,
            ass_colour(&st.outline_colour, st.shadow_alpha)?,
        )
    };
    let hl = tag_colour(&st.highlight_colour)?;
    let primt = tag_colour(&st.primary)?;
    let emc = if st.emphasis_colour.is_empty() {
        primt.clone()
    } else {
        tag_colour(&st.emphasis_colour)?
    };
    let (cx, slot_cy) = (f64::from(w) / 2.0, (f64::from(h) * yfrac).round());
    let safe_w = f64::from(w) - 2.0 * st.margin_x;
    let mut out = String::new();
    out += "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n";
    out += &format!("PlayResX: {}\nPlayResY: {}\n\n", w, h);
    out += "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n";
    out += &format!(
        "Style: Cap,{},{},{},{},{},{},{},0,0,0,100,100,0,0,{},{},{},5,{},{},0,1\n",
        st.font,
        st.size.round(),
        prim,
        prim,
        outl,
        back,
        if st.bold { -1 } else { 0 },
        if st.box_bg { 3 } else { 1 },
        st.outline,
        if st.soft_shadow && !st.box_bg {
            0.0
        } else {
            st.shadow
        },
        st.margin_x.round(),
        st.margin_x.round()
    );
    out += "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n";
    let mut evs = Vec::new();
    for cap in group(words, st) {
        let txt: Vec<String> = cap
            .words
            .iter()
            .map(|x| {
                if st.uppercase {
                    x.text.to_uppercase()
                } else {
                    x.text.clone()
                }
            })
            .collect();
        let plain = txt.join(" ");
        // emphasised words are wider; estimate with the factor applied to them
        let mut est = 0.0;
        for (i, t) in txt.iter().enumerate() {
            let f = if cap.words[i].highlight {
                st.emphasis_scale
            } else {
                1.0
            };
            est += est_width(t, st.size * f) + if i > 0 { est_width(" ", st.size) } else { 0.0 };
        }
        est += 2.0 * st.outline;
        let fs = if est > safe_w {
            (st.size * safe_w / est).floor()
        } else {
            st.size
        };
        let est_fit = est * fs / st.size;
        let cy = y_overrides
            .iter()
            .find(|(a, b, _)| cap.start >= *a && cap.start < *b)
            .map(|o| o.2.round())
            .unwrap_or(slot_cy);
        // one Dialogue per active word when highlighting, else one for the caption
        let spans: Vec<(i64, i64, Option<usize>)> =
            if st.active_word_highlight && cap.words.len() > 1 {
                let n = cap.words.len();
                (0..n)
                    .map(|i| {
                        let s = if i == 0 {
                            cap.start
                        } else {
                            cap.words[i].start.max(cap.start)
                        };
                        let e = if i + 1 < n {
                            cap.words[i + 1].start.min(cap.end)
                        } else {
                            cap.end
                        };
                        (s, e, Some(i))
                    })
                    .filter(|(s, e, _)| e > s)
                    .collect()
            } else if st.active_word_highlight {
                vec![(cap.start, cap.end, Some(0))]
            } else {
                vec![(cap.start, cap.end, None)]
            };
        for (k, (s, e, active)) in spans.iter().enumerate() {
            let mut body = String::new();
            for (i, t) in txt.iter().enumerate() {
                if i > 0 {
                    body.push(' ');
                }
                let mut tags = String::new();
                if cap.words[i].highlight {
                    tags += &format!(
                        "\\fscx{0}\\fscy{0}\\1c{1}",
                        (st.emphasis_scale * 100.0).round(),
                        emc
                    );
                }
                if *active == Some(i) {
                    tags += &format!("\\1c{}", hl);
                }
                if tags.is_empty() {
                    body += &esc(t);
                } else {
                    body += &format!("{{{}}}{}{{\\fscx100\\fscy100\\1c{}}}", tags, esc(t), primt);
                }
            }
            let pop = if st.pop && k == 0 { "\\fad(70,0)" } else { "" };
            let (ts, te) = (
                cs_to_ass(frame_to_cs(*s, fps)?),
                cs_to_ass(frame_to_cs(*e, fps)?),
            );
            let pos = format!("\\an5\\fs{}", fs);
            if st.soft_shadow && !st.box_bg {
                // layer 0: blurred, offset, translucent shadow only (no fill)
                out += &format!(
                    "Dialogue: 0,{},{},Cap,,0,0,0,,{{{}{}\\1a&HFF&\\3c{}\\3a&H{:02X}&\\bord{}\\blur{}\\shad0\\pos({},{})}}{}\n",
                    ts, te, pos, pop, tag_colour(&st.outline_colour)?, st.shadow_alpha, st.outline + 2.0, st.shadow_blur,
                    cx + st.shadow_dx.unwrap_or(st.shadow), cy + st.shadow_dy.unwrap_or(st.shadow * 1.5), body
                );
            }
            out += &format!(
                "Dialogue: 1,{},{},Cap,,0,0,0,,{{{}\\pos({},{}){}\\blur0.8}}{}\n",
                ts, te, pos, cx, cy, pop, body
            );
            evs.push(Event {
                start: *s,
                end: *e,
                text: plain.clone(),
                font_size: fs,
                est_width: est_fit,
                x: cx,
                y: cy,
            });
        }
    }
    Ok((out, evs))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn w(t: &str, s: i64, e: i64) -> Word {
        Word {
            text: t.into(),
            start: s,
            end: e,
            highlight: false,
        }
    }
    #[test]
    fn groups_one_or_two_words() {
        let ws = vec![
            w("Pagi", 0, 10),
            w("10", 10, 20),
            w("November", 20, 35),
            w("1945,", 35, 50),
            w("batas", 52, 60),
        ];
        let c = group(&ws, &Style::default());
        assert!(c.iter().all(|c| !c.words.is_empty() && c.words.len() <= 2));
        assert_eq!(c[0].words.len(), 2);
        // phrase end "1945," closes a caption
        assert!(c.iter().any(|c| c.words.last().unwrap().text == "1945,"));
        for p in c.windows(2) {
            assert!(p[0].end <= p[1].start);
        }
    }
    #[test]
    fn default_has_no_box() {
        let (a, _) = compile(&[w("Halo", 0, 30)], &Style::default(), 1080, 1920, 30, &[]).unwrap();
        let st = a.lines().find(|l| l.starts_with("Style:")).unwrap();
        assert_eq!(st.split(',').nth(15).unwrap(), "1");
    }
    fn shadow_pos(st: &Style) -> String {
        let (a, _) = compile(&[w("Halo", 0, 30)], st, 1080, 1920, 30, &[(0, 30, 1190.0)]).unwrap();
        let l = a.lines().find(|l| l.starts_with("Dialogue: 0,")).unwrap();
        l.split("\\pos(").nth(1).unwrap().split(')').next().unwrap().to_string()
    }
    #[test]
    fn good_shadow_dx_dy_offset_the_soft_shadow_straight_down() {
        let st = Style {
            shadow_dx: Some(0.0),
            shadow_dy: Some(3.0),
            ..Style::default()
        };
        assert_eq!(shadow_pos(&st), "540,1193");
    }
    #[test]
    fn good_default_soft_shadow_offset_unchanged() {
        assert_eq!(shadow_pos(&Style::default()), "543,1194.5");
    }
    #[test]
    fn bad_non_finite_shadow_dy_rejected() {
        let st = Style {
            shadow_dy: Some(f64::NAN),
            ..Style::default()
        };
        let e = compile(&[w("Halo", 0, 30)], &st, 1080, 1920, 30, &[]).unwrap_err();
        assert!(e.contains("shadow_dy"), "{e}");
    }
    #[test]
    fn long_word_scaled_into_safe_area() {
        let (_, ev) = compile(
            &[w("Pemberontakan-Kemerdekaan", 0, 30)],
            &Style::default(),
            1080,
            1920,
            30,
            &[],
        )
        .unwrap();
        assert!(ev[0].est_width <= 1080.0 - 180.0 + 0.5);
        assert!(ev[0].font_size < 84.0);
    }
    #[test]
    fn active_word_events() {
        let st = Style {
            active_word_highlight: true,
            ..Style::default()
        };
        let (_, ev) = compile(
            &[w("Surabaya", 0, 10), w("berkembang", 10, 20)],
            &st,
            1080,
            1920,
            30,
            &[],
        )
        .unwrap();
        assert_eq!(ev.len(), 2);
        assert_eq!((ev[0].start, ev[0].end, ev[1].start), (0, 10, 10));
    }
    #[test]
    fn bad_slot() {
        let st = Style {
            slot: "side".into(),
            ..Style::default()
        };
        assert!(compile(&[w("x", 0, 1)], &st, 1080, 1920, 30, &[]).is_err());
    }

    #[test]
    fn y_override_moves_only_matching_captions() {
        let ws = [w("satu", 0, 10), w("dua", 40, 50)];
        let (_, ev) = compile(
            &ws,
            &Style {
                max_words: 1,
                ..Style::default()
            },
            1080,
            1920,
            30,
            &[(30, 60, 1400.0)],
        )
        .unwrap();
        assert_eq!(ev[0].y, (1920.0f64 * 0.70).round());
        assert_eq!(ev[1].y, 1400.0);
    }

    fn dialogue_lines(a: &str) -> Vec<&str> {
        a.lines().filter(|l| l.starts_with("Dialogue:")).collect()
    }

    #[test]
    fn newline_injection_is_neutralised() {
        let evil = "a\nDialogue: 9,0:00:00.00,9:00:00.00,Cap,,0,0,0,,PWNED\r\tb\u{0}c";
        let (a, ev) = compile(&[w(evil, 0, 30)], &Style::default(), 1080, 1920, 30, &[]).unwrap();
        // one shadow + one text line, nothing injected
        assert_eq!(dialogue_lines(&a).len(), 2);
        assert!(!a.lines().any(|l| l.starts_with("Dialogue: 9")));
        assert!(!a.lines().any(|l| l.starts_with("PWNED") || l == "b"));
        assert_eq!(ev.len(), 1);
    }

    #[test]
    fn backslash_cannot_form_override_codes() {
        let e = esc(r"x\N y\h z\\n {\b1}");
        assert!(!e.contains('\\'));
        assert!(!e.contains('{') && !e.contains('}'));
        assert_eq!(e, "x\u{FF3C}N y\u{FF3C}h z\u{FF3C}\u{FF3C}n (\u{FF3C}b1)");
        let (a, _) = compile(&[w(r"a\Nb", 0, 30)], &Style::default(), 1080, 1920, 30, &[]).unwrap();
        for l in dialogue_lines(&a) {
            let text = l.rsplit('}').next().unwrap();
            assert!(!text.contains('\\'), "{l}");
        }
    }

    #[test]
    fn word_frames_are_validated() {
        let st = Style::default();
        let c = |ws: &[Word]| compile(ws, &st, 1080, 1920, 30, &[]);
        assert!(c(&[w("a", -1, 5)]).is_err());
        assert!(c(&[w("a", 5, 5)]).is_err());
        assert!(c(&[w("a", 6, 5)]).is_err());
        assert!(c(&[w("a", 0, 1 << 40)]).is_err());
        assert!(c(&[w("a", 0, i64::MAX)]).is_err());
        assert!(c(&[w("b", 10, 20), w("a", 0, 5)]).is_err()); // unsorted
        assert!(c(&[w("a", 0, 5), w("b", 0, 9)]).is_ok()); // equal starts are sorted
        assert!(c(&[w("a", 0, (1 << 40) - 1)]).is_ok());
    }

    #[test]
    fn hold_saturates() {
        let st = Style {
            hold_frames: i64::MAX,
            ..Style::default()
        };
        let (_, ev) = compile(
            &[w("a", (1 << 40) - 10, (1 << 40) - 1)],
            &st,
            1080,
            1920,
            30,
            &[],
        )
        .unwrap();
        assert_eq!(ev[0].end, 1 << 40);
        let g = group(
            &[w("a", 0, 5), w("b", 1 << 39, (1 << 39) + 5)],
            &Style {
                gap_break_frames: 0,
                ..Style::default()
            },
        );
        assert_eq!(g.len(), 2);
    }

    #[test]
    fn fps_range() {
        let ws = [w("a", 0, 30)];
        for fps in [0u32, 100, 1000] {
            assert!(compile(&ws, &Style::default(), 1080, 1920, fps, &[]).is_err());
        }
        for fps in [1u32, 24, 99] {
            assert!(compile(&ws, &Style::default(), 1080, 1920, fps, &[]).is_ok());
        }
        assert!(compile(&ws, &Style::default(), 0, 1920, 30, &[]).is_err());
        assert!(compile(&ws, &Style::default(), 1080, 0, 30, &[]).is_err());
    }

    #[test]
    fn style_validation() {
        let bad = [
            Style {
                size: 0.0,
                ..Style::default()
            },
            Style {
                size: f64::NAN,
                ..Style::default()
            },
            Style {
                emphasis_scale: 0.0,
                ..Style::default()
            },
            Style {
                margin_x: -1.0,
                ..Style::default()
            },
            Style {
                margin_x: 540.0,
                ..Style::default()
            },
            Style {
                outline: -1.0,
                ..Style::default()
            },
            Style {
                font: "Inter,Bold".into(),
                ..Style::default()
            },
            Style {
                font: "Inter\nStyle: X".into(),
                ..Style::default()
            },
            Style {
                font: String::new(),
                ..Style::default()
            },
            Style {
                max_words: 0,
                ..Style::default()
            },
            Style {
                hold_frames: -1,
                ..Style::default()
            },
            Style {
                primary: "white".into(),
                ..Style::default()
            },
        ];
        for st in &bad {
            assert!(
                compile(&[w("a", 0, 30)], st, 1080, 1920, 30, &[]).is_err(),
                "{st:?}"
            );
        }
        assert!(compile(
            &[w("a", 0, 30)],
            &Style {
                margin_x: 539.0,
                ..Style::default()
            },
            1080,
            1920,
            30,
            &[]
        )
        .is_ok());
    }

    #[test]
    fn box_colour_goes_to_outline_colour() {
        let st = Style {
            box_bg: true,
            box_colour: "112233".into(),
            outline_colour: "000000".into(),
            ..Style::default()
        };
        let (a, _) = compile(&[w("a", 0, 30)], &st, 1080, 1920, 30, &[]).unwrap();
        let f: Vec<&str> = a
            .lines()
            .find(|l| l.starts_with("Style:"))
            .unwrap()
            .split(',')
            .collect();
        assert_eq!(f[15], "3"); // BorderStyle 3 = opaque box
        assert_eq!(f[5], "&H50332211"); // OutlineColour = box colour
        assert_eq!(f[6], "&H60000000"); // BackColour = shadow
                                        // default (no box): OutlineColour is the outline colour
        let (a, _) = compile(&[w("a", 0, 30)], &Style::default(), 1080, 1920, 30, &[]).unwrap();
        let f: Vec<&str> = a
            .lines()
            .find(|l| l.starts_with("Style:"))
            .unwrap()
            .split(',')
            .collect();
        assert_eq!((f[2], f[5], f[15], f[16]), ("84", "&H20000000", "1", "5"));
    }

    #[test]
    fn y_override_validation() {
        let ws = [w("a", 0, 30)];
        assert!(compile(&ws, &Style::default(), 1080, 1920, 30, &[(0, 10, f64::NAN)]).is_err());
        assert!(compile(&ws, &Style::default(), 1080, 1920, 30, &[(10, 0, 900.0)]).is_err());
    }
}
