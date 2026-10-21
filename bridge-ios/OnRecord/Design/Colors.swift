import SwiftUI

// MARK: - Hex init helper

extension Color {
    /// Builds a color from a hex string such as "#BCF015" or "BCF015".
    /// Accepts an optional leading "#" and ignores surrounding whitespace.
    init(hex: String) {
        var cleaned = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if cleaned.hasPrefix("#") { cleaned.removeFirst() }
        var rgb: UInt64 = 0
        Scanner(string: cleaned).scanHexInt64(&rgb)
        let red = Double((rgb >> 16) & 0xFF) / 255.0
        let green = Double((rgb >> 8) & 0xFF) / 255.0
        let blue = Double(rgb & 0xFF) / 255.0
        self.init(red: red, green: green, blue: blue)
    }
}

// MARK: - OnRecord palette

extension Color {

    // The field colors. Each owns entire views (70 to 85 percent of the
    // screen). Never pure black, never white-dominant. One field per view,
    // two loud fields never share one screen.

    /// The capture field. Warm black-olive, glasses-at-night energy.
    /// Primary type on it is Bone.
    static let oliveNight = Color(hex: "22251C")

    /// The people field. Saturated dark plum, matches the brand art's
    /// violet ground. Primary type on it is Bone.
    static let deepPlum = Color(hex: "2A1428")

    /// The day field. Warm off-white, never sterile white. Primary type
    /// on it is Ink. Lets saturated color sit without vibrating.
    static let warmPaper = Color(hex: "F4F2ED")

    /// The recall field. Near-black brown, the listening room, deep and
    /// quiet. Primary type on it is Bone.
    static let espressoNight = Color(hex: "201616")

    // The loud accents. Rationed 5 to 10 percent of a screen, never
    // decoration. Accent marks interactivity or the single most important
    // thing on screen.

    /// The action and proof color. The record button, the VERIFIED sticker
    /// pill, the marker sweep behind verified key phrases, recognized-face
    /// rings. Dark type (Ink) knocked out of it; on dark fields use it for
    /// type and lines. Never on Warm Paper except as a sticker with a dark
    /// outline (it vibrates on paper).
    static let acidLime = Color(hex: "BCF015")

    /// The stranger and heat color. The UNKNOWN speaker sticker,
    /// unreviewed-capture badges, enrollment ceremony moments.
    static let hotMagenta = Color(hex: "E835C1")

    /// The attention color. The REVIEW sticker pill, needs-a-second-look
    /// quotes, warning microcopy. Ink type knocked out of it.
    static let tangerine = Color(hex: "FF7A1A")

    /// The supporting cool. Waveform strokes, SILENCE-adjacent calm tones,
    /// the "listening live" pulse ring. The coldest accent, stays quiet.
    static let acidTeal = Color(hex: "3DD6C3")

    // Warm neutrals for text and metadata.

    /// Primary type on all dark fields. Warmer than white.
    static let bone = Color(hex: "F6F1E7")

    /// Primary type on Warm Paper. Never pure black.
    static let ink = Color(hex: "1E1E1E")

    /// Secondary type and mono micro-labels on dark fields. Muted, recedes.
    static let warmGray = Color(hex: "8A8378")

    /// Raised card surface on Deep Plum. Cards lift from the field without
    /// going gray.
    static let smokedPlum = Color(hex: "45304B")

    /// Raised card surface on Olive Night. Slightly lifted from the field.
    static let raisedOlive = Color(hex: "30341F")

    /// Inset and pressed surface on Warm Paper for transcript rows and
    /// search field wells.
    static let paperShadow = Color(hex: "E9E4D8")

    // The blue rule: blue may appear only as a small supporting tone, never
    // dominant, never as a field. Under 3 percent of any screen.

    /// The only blue. Allowed only as the Ask input focus ring and the
    /// live-listen halo on the Ask tab. If the app ever reads as blue, this
    /// has broken the rule.
    static let focusIndigo = Color(hex: "4B4BD8")
}

// MARK: - Semantic status colors

extension Color {

    /// VERIFIED. Verbatim quotes with exact-match proof. The lime pill
    /// says the words are trusted.
    static let verified = Color.acidLime

    /// REVIEW. Capture that needs attention: low confidence transcription,
    /// overlapping voices, a name that needs confirming. One per card max.
    static let review = Color.tangerine

    /// SILENCE. Listening paused, dead air in a capture, the "nothing to
    /// report" state. Quiet by design.
    static let silence = Color.acidTeal

    /// UNKNOWN. A speaker the app has not enrolled. The stranger color.
    /// Enrollment converts magenta to lime.
    static let unknown = Color.hotMagenta
}

// MARK: - Per-tab color chapters

/// Each main tab owns its own full-bleed field. Navigation reads as color
/// change, not tab-bar highlight. The floating pill nav adapts its tint to
/// the chapter it sits in. Capture overlays always sit on Olive Night,
/// regardless of tab: capture is its own chapter.
enum AppChapter: String, CaseIterable, Identifiable {
    /// Warm Paper day field. Lime owns the record button and verified
    /// receipts; Tangerine owns REVIEW flags.
    case timeline
    /// Deep Plum sticker party. Magenta owns UNKNOWN speakers and
    /// enrollment; Tangerine owns one heat sticker per speaker card.
    case people
    /// Espresso Night listening room. Lime owns answer receipts and the
    /// marker sweep; Focus Indigo owns the input focus ring and listen
    /// halo (the only blue, rationed under 3 percent).
    case ask
    /// Olive Night capture field. The record sheet sits on this chapter
    /// no matter which tab opened it.
    case capture

    var id: String { rawValue }

    /// The full-bleed background this chapter owns.
    var field: Color {
        switch self {
        case .timeline: .warmPaper
        case .people: .deepPlum
        case .ask: .espressoNight
        case .capture: .oliveNight
        }
    }

    /// Body type color on this chapter's field.
    var primaryText: Color {
        switch self {
        case .timeline: .ink
        case .people, .ask, .capture: .bone
        }
    }

    /// Mono micro-label and secondary type color on this chapter's field.
    /// On paper the spec calls for a darkened warm gray, achieved here by
    /// deepening Warm Gray rather than inventing a new hue.
    var secondaryText: Color {
        switch self {
        case .timeline: .warmGray.opacity(0.85)
        case .people, .ask, .capture: .warmGray
        }
    }

    /// The raised card surface for this chapter's field. Cards on Warm
    /// Paper use the paper itself with hard-edge photo rails, so the
    /// timeline chapter returns nil: use paperShadow for inset wells only.
    var raisedCardSurface: Color? {
        switch self {
        case .timeline: nil
        case .people: .smokedPlum
        case .ask: .smokedPlum
        case .capture: .raisedOlive
        }
    }
}
