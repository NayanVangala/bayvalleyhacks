import SwiftUI

// MARK: - OnRecord type scale
//
// The pairing: one characterful display face for emotion, one neutral
// grotesk for body and UI, nothing else. Mono is the third register, for
// metadata only. Two faces, never three. No pixel or bitmap type for
// verbatim quotes: it would wreck quote legibility, and the quote is the
// product.
//
// iOS fallbacks (bundling one expressive display face is the single
// highest-ROI font decision in this system; until then these ship):
//   Display  - SF Pro Black with rounded design. Editorial serif option:
//              New York ExtraBold (the Reckless/Belgrade end). Pick one
//              display direction, never two.
//   Body/UI  - SF Pro throughout.
//   Micro    - SF Mono, tracked uppercase (the label voice).
//
// The loud/quiet ratio is the point: display type is enormous, body stays
// small and quiet.

extension Font {

    /// Display, 44 to 64 pt, Black, tight leading (0.95x).
    /// View headers ("Every word, kept."), hero statements, marketing
    /// headlines. Cropped by screen edges is a feature.
    /// Fallback: SF Pro Black, rounded. Swap in the bundled display face
    /// here when it lands; every call site follows automatically.
    static func orDisplay(_ size: CGFloat = 56) -> Font {
        .system(size: size, weight: .black, design: .rounded)
    }

    /// Headline, 28 to 34 pt, Bold, 1.0x leading.
    /// Section headers, speaker names, Ask answers' supporting lines.
    static func orHeadline(_ size: CGFloat = 30) -> Font {
        .system(size: size, weight: .bold, design: .rounded)
    }

    /// Title, 20 to 22 pt, Semibold, 1.1x leading.
    /// Card titles, conversation titles ("Kitchen, Tuesday").
    static func orTitle(_ size: CGFloat = 21) -> Font {
        .system(size: size, weight: .semibold, design: .default)
    }

    /// Body, 15 to 17 pt, Regular, 1.35x leading.
    /// Transcript text, settings prose, all readable copy. Quiet and
    /// small so the display type sings.
    static func orBody(_ size: CGFloat = 16) -> Font {
        .system(size: size, weight: .regular, design: .default)
    }

    /// Caption, 12 to 13 pt, Regular/Medium, 1.3x leading.
    /// Secondary metadata in sentence case, card footnotes.
    static func orCaption(_ size: CGFloat = 12.5) -> Font {
        .system(size: size, weight: .regular, design: .default)
    }

    /// Micro-label, 10 to 11 pt, Medium, uppercase, tracked +100.
    /// The bracketed mono metadata voice. Minimum 10 pt: the dense 10px
    /// microcopy on the reference sites is too small for a memory app
    /// used on the go.
    /// Fallback: SF Mono.
    static func orMicro(_ size: CGFloat = 11) -> Font {
        .system(size: size, weight: .medium, design: .monospaced)
            .tracking(1.0)
    }

    /// Quote hero, 30 to 34 pt, Bold, tight leading.
    /// The verbatim quote is the biggest thing on screen: 2 to 3x body
    /// size, set in the clean grotesk (never the display face), in Bone on
    /// dark fields or Ink on paper.
    static func orQuoteHero(_ size: CGFloat = 32) -> Font {
        .system(size: size, weight: .bold, design: .default)
    }

    /// Read mode quote, 40+ pt, Bold, tight leading.
    /// One memory at a time, full screen: the quote is the entire
    /// viewport. Same clean grotesk rule as the hero.
    static func orQuoteRead(_ size: CGFloat = 40) -> Font {
        .system(size: size, weight: .bold, design: .default)
    }
}

// MARK: - Proportional line heights

extension View {

    /// Applies proportional leading: 0.95x display, 1.0x headline,
    /// 1.1x title, 1.35x body, 1.3x caption, 1.0x mono.
    func orLeading(_ multiple: CGFloat, for size: CGFloat) -> some View {
        lineSpacing(max(0, (multiple - 1.0) * size))
    }
}

// MARK: - Bracketed mono label style
//
// All memory metadata in uppercase tracked mono, always bracketed. This
// is the label voice and the proof language. The grammar is
// [WHO · WHAT · WHEN · WHERE], elements separated by middots, one label
// per line, never stacked as sentences.
//
// Status pills (VERIFIED / REVIEW / SILENCE / UNKNOWN) are NOT mono
// labels: pills are sticker components in the semantic colors; the mono
// line is the receipt beneath them.
//
// Usage:
//   Label("Maya · Verified Verbatim · 4:32 PM", icon: { EmptyView() })
//       .labelStyle(.receipt)
//   Label("Taking a nap", icon: { EmptyView() })
//       .labelStyle(.receiptOnPaper)

struct ReceiptLabelStyle: LabelStyle {
    /// Label size, 10 to 11 pt per the spec.
    var size: CGFloat = 11
    /// Label color. Warm Gray on dark fields; a darkened warm gray on
    /// paper (`.orOnPaper` presets both).
    var color: Color = .warmGray

    func makeBody(configuration: Configuration) -> some View {
        Text("[\((configuration.title))]")
            .font(.orMicro(size))
            .textCase(.uppercase)
            .foregroundStyle(color)
    }
}

extension LabelStyle where Self == ReceiptLabelStyle {

    /// Bracketed mono receipt label for dark fields.
    /// Example: [MAYA · VERIFIED VERBATIM · 4:32 PM]
    static var receipt: ReceiptLabelStyle {
        ReceiptLabelStyle(color: .warmGray)
    }

    /// Bracketed mono receipt label for Warm Paper.
    /// Example: [3 CONVERSATIONS TODAY]
    static var receiptOnPaper: ReceiptLabelStyle {
        ReceiptLabelStyle(color: .warmGray.opacity(0.85))
    }
}
