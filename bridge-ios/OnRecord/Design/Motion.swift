import SwiftUI

// ============================================================================
// Motion.swift
// OnRecord / Design
//
// The motion grammar from Part 2 of the design system (section 1): the timing
// budget, the six motion moves as SwiftUI, the signature choreography, and
// the reduced-motion fallbacks. Also the shared atoms every component needs:
// FlowLayout (word-wrapping inline elements), ScrambleText, WaveformGlyph,
// SatellitePair, OnrToast, and the press/highlight/slap/rise modifiers.
//
// Sibling-owned (Implementer 1): Colors.swift, Typography.swift,
// DESIGN-SYSTEM.md. This file references those directly:
//   Color.acidLime / .hotMagenta / .tangerine / .acidTeal / .oliveNight /
//   .deepPlum / .warmPaper / .espressoNight / .bone / .ink / .warmGray /
//   .smokedPlum / .raisedOlive / .paperShadow / .focusIndigo, plus the
//   semantic .verified / .review / .silence / .unknown.
//   Font.orDisplay / .orHeadline / .orTitle / .orBody / .orCaption /
//   .orMicro / .orQuoteHero / .orQuoteRead.
//   Label("...", icon: { EmptyView() }).labelStyle(.receipt / .receiptOnPaper)
//   or LabelStyle(ReceiptLabelStyle(color:)) for tinted receipt lines.
//
// Judgment calls (flagged for the design leads):
// - SILENCE chip uses the semantic Color.silence (Acid Teal) from
//   Colors.swift with ink text. Part 2 section C5 describes a mute fill
//   with bone text; Part 1 (which owns color) maps SILENCE to Acid Teal.
//   Colors.swift wins here.
// - The search focus ring uses Focus Indigo per Part 1's blue rule
//   (the Ask input focus ring is indigo's one approved job), even though
//   Part 2 C6 says "accent-loud outline".
//
// Reduced motion: every animated helper below reads
// \.accessibilityReduceMotion and degrades per the Part 2 spec.
// ============================================================================

// MARK: - 1.1 The signature easings

extension Animation {

    /// The signature ease: fast attack, long luxurious settle.
    /// CSS cubic-bezier(0.625, 0.05, 0, 1). Reveals get this curve.
    static func onrSignature(duration: Double = 0.9) -> Animation {
        .timingCurve(0.625, 0.05, 0, 1, duration: duration)
    }

    /// Expo-style rise for masked type. Maps Society's expo.out.
    /// Headings, quotes, and enrollment names rise with this.
    static func onrExpo(duration: Double = 0.9) -> Animation {
        .timingCurve(0.16, 1, 0.3, 1, duration: duration)
    }

    /// Power3-ish inout for marker sweeps and block wipes.
    static func onrMarker(duration: Double = 0.9) -> Animation {
        .timingCurve(0.45, 0, 0.55, 1, duration: duration)
    }

    /// Interactive spring. Rule of thumb: springs are for fingers
    /// (presses, taps), curves are for stories (reveals).
    static func onrSpring(response: Double = 0.25, damping: Double = 0.7) -> Animation {
        .spring(response: response, dampingFraction: damping)
    }
}

// MARK: - M2 Highlight-marker sweep

/// A bar scales in behind the key phrase like a highlighter swipe.
/// Apply to a Text view; the bar sits behind the text plus a little padding.
struct HighlightMarkerSweep: ViewModifier {
    var revealed: Bool
    var delay: Double = 0.15
    var color: Color = .acidLime
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        content
            .padding(.horizontal, 3)
            .padding(.vertical, 1)
            .background(
                RoundedRectangle(cornerRadius: 4)
                    .fill(color)
                    .scaleEffect(x: (revealed || reduceMotion) ? 1 : 0, anchor: .leading)
                    .animation(.onrMarker(duration: 0.9).delay(delay), value: revealed)
            )
    }
}

// MARK: - M5 Magnetic press + snap-back

/// Interactive tiles lean up to 6pt toward the touch point while pressed,
/// then snap home on release. Taps (under 12pt of travel) call onTap.
/// Note: the zero-distance drag claims the touch, so avoid putting this on
/// views inside a ScrollView where the touch should start a scroll.
struct MagneticPress: ViewModifier {
    var onTap: () -> Void = {}
    @State private var offset: CGSize = .zero
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        content
            .offset(offset)
            .animation(
                offset == .zero
                    ? .spring(response: 0.28, dampingFraction: 0.65)
                    : .spring(response: 0.22, dampingFraction: 0.7),
                value: offset
            )
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        guard !reduceMotion else { return }
                        offset = CGSize(
                            width: min(max(value.translation.width, -6), 6),
                            height: min(max(value.translation.height, -6), 6)
                        )
                    }
                    .onEnded { value in
                        offset = .zero
                        let isTap = abs(value.translation.width) < 12
                            && abs(value.translation.height) < 12
                        if isTap { onTap() }
                    }
            )
    }
}

// MARK: - M1 Masked rise

/// Lines climb out of their own mask with the expo ease, never a plain fade.
/// Under reduced motion this degrades to the one allowed plain crossfade.
struct MaskedRise: ViewModifier {
    var shown: Bool
    var delay: Double = 0
    var distance: CGFloat = 28
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        Group {
            if reduceMotion {
                content
                    .opacity(shown ? 1 : 0)
                    .animation(.easeInOut(duration: 0.2), value: shown)
            } else {
                content
                    .offset(y: shown ? 0 : distance)
                    .animation(.onrExpo(duration: 0.9).delay(delay), value: shown)
                    .mask(Rectangle())
            }
        }
    }
}

// MARK: - Sticker slap (signature choreography)

/// The enrollment pop: scale from 1.4 to 1 on a loose spring, rotation
/// settling from +8 degrees to the resting tilt. No opacity fade.
struct StickerSlap: ViewModifier {
    var restingTilt: Double
    var slapped: Bool

    func body(content: Content) -> some View {
        content
            .scaleEffect(slapped ? 1 : 1.4)
            .rotationEffect(.degrees(slapped ? restingTilt : restingTilt + 8))
            .animation(.spring(response: 0.38, dampingFraction: 0.55), value: slapped)
    }
}

// MARK: - Card press (jointed-pair feel)

/// Whole-card press feedback: scales to 0.98 with the signature curve.
/// Action-less on purpose; children keep their own tap targets.
struct CardPress: ViewModifier {
    var scale: CGFloat = 0.98
    @State private var pressed = false

    func body(content: Content) -> some View {
        content
            .scaleEffect(pressed ? scale : 1)
            .animation(.onrSignature(duration: 0.25), value: pressed)
            .simultaneousGesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in pressed = true }
                    .onEnded { _ in pressed = false }
            )
    }
}

// MARK: - M3 cheap variant: block sweep

/// An accent block that sweeps across its container on a loop while a
/// capture runs (enrollment steps, photo reveals). Approximates the M3
/// in/out wipe as a repeating in/out pulse from the leading edge.
struct SweepBlock: View {
    var color: Color = .acidLime
    @State private var on = false

    var body: some View {
        color
            .scaleEffect(x: on ? 1 : 0.04, anchor: .leading)
            .animation(.onrMarker(duration: 0.5).repeatForever(autoreverses: true), value: on)
            .onAppear { on = true }
    }
}

// MARK: - View sugar

extension View {
    /// M2: highlight-marker sweep behind a phrase.
    func onrHighlightSweep(revealed: Bool, delay: Double = 0.15, color: Color = .acidLime) -> some View {
        modifier(HighlightMarkerSweep(revealed: revealed, delay: delay, color: color))
    }

    /// M5: magnetic press with tap callback.
    func onrMagneticPress(onTap: @escaping () -> Void = {}) -> some View {
        modifier(MagneticPress(onTap: onTap))
    }

    /// M1: masked rise reveal.
    func onrMaskedRise(shown: Bool, delay: Double = 0, distance: CGFloat = 28) -> some View {
        modifier(MaskedRise(shown: shown, delay: delay, distance: distance))
    }

    /// Sticker slap entrance.
    func onrStickerSlap(restingTilt: Double, slapped: Bool) -> some View {
        modifier(StickerSlap(restingTilt: restingTilt, slapped: slapped))
    }

    /// Whole-card press feedback.
    func onrCardPress(scale: CGFloat = 0.98) -> some View {
        modifier(CardPress(scale: scale))
    }
}

// MARK: - Press button style

/// Springs are for fingers: pressed scale with the interactive spring.
/// Under reduced motion every press keeps a plain 0.92 scale (no travel).
struct OnrPressStyle: ButtonStyle {
    var pressedScale: CGFloat = 0.94
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? (reduceMotion ? 0.92 : pressedScale) : 1.0)
            .animation(.onrSpring(response: 0.25, damping: 0.7), value: configuration.isPressed)
    }
}

// MARK: - FlowLayout

/// A simple word-wrapping flow layout so inline elements (highlight sweeps,
/// waveform glyphs, stickers) can sit inside running text and still wrap.
/// Items are top-aligned per row.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > maxWidth, x > 0 {
                x = 0
                y += rowHeight + lineSpacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxWidth.isInfinite ? x : maxWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX
                y += rowHeight + lineSpacing
                rowHeight = 0
            }
            subview.place(at: CGPoint(x: x, y: y), proposal: .unspecified)
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

// MARK: - M6 Scramble-in

/// When OnRecord surfaces an answer, the key label cycles glyphs for about
/// a second before resolving left to right, like decoding a transmission.
/// Use sparingly: names, dates, amounts. Caller applies font and color.
struct ScrambleText: View {
    var target: String
    @State private var display: String
    @State private var step = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private let glyphs = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ&@#0123456789")
    private let chars: [Character]

    init(target: String) {
        self.target = target
        self.chars = Array(target)
        self._display = State(initialValue: String(target.map { $0 == " " ? " " : "·" }))
    }

    var body: some View {
        Text(display)
            .onAppear {
                if reduceMotion {
                    display = target
                    return
                }
                Timer.scheduledTimer(withTimeInterval: 0.04, repeats: true) { timer in
                    step += 1
                    if step >= chars.count * 2 {
                        timer.invalidate()
                        display = target
                        return
                    }
                    let locked = min(step / 2, chars.count)
                    var out = ""
                    for (index, ch) in chars.enumerated() {
                        if index < locked || ch == " " {
                            out.append(ch)
                        } else {
                            out.append(glyphs.randomElement()!)
                        }
                    }
                    display = out
                }
            }
    }
}

// MARK: - WaveformGlyph

/// The inline waveform "word": 5 to 9 bars, 18pt tall, treated like a word
/// of type inside transcript lines. Tapping plays exactly that span.
struct WaveformGlyph: View {
    var played: Bool = false
    var active: Bool = false
    var barCount: Int = 7
    @State private var tick = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let seed: [CGFloat] = [0.55, 0.9, 0.4, 1.0, 0.65, 0.85, 0.45, 0.75, 0.5]

    var body: some View {
        HStack(alignment: .center, spacing: 2.5) {
            ForEach(0..<barCount, id: \.self) { index in
                let height = 18 * seed[index % seed.count] * (active && tick ? 0.55 : 1)
                RoundedRectangle(cornerRadius: 1.25)
                    .fill(played ? Color.bone : Color.acidLime)
                    .frame(width: 3, height: max(height, 3))
                    .animation(
                        active && !reduceMotion
                            ? .easeInOut(duration: 0.12).repeatForever(autoreverses: true)
                            : .default,
                        value: tick
                    )
            }
        }
        .frame(height: 18)
        .onAppear {
            if active && !reduceMotion { tick.toggle() }
        }
        .accessibilityLabel(played ? "Played audio span" : "Unplayed audio span")
    }
}

// MARK: - SatellitePair (button + satellite CTA)

/// The button + satellite pairing from the research: a pill label plus an
/// accent-loud circle carrying an arrow glyph. The pair feels jointed: on
/// press the pill scales to 0.97 while the satellite scales to 0.88 a beat
/// faster, and the arrow kicks 45 degrees.
struct SatellitePair: View {
    enum Style { case normal, sent }

    var label: String
    var glyph: String = "↘"
    var style: Style = .normal
    var spinning: Bool = false
    var dimmed: Bool = false
    var foreground: Color = .bone
    var outline: Color = .bone
    var onTap: () -> Void = {}

    @State private var pressed = false
    @State private var spin = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 10) {
            Text(label)
                .font(.orTitle(14))
                .foregroundColor(style == .sent ? .ink : foreground)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(
                    Capsule()
                        .fill(style == .sent ? Color.acidLime : Color.clear)
                        .overlay(
                            Capsule().strokeBorder(
                                style == .sent ? Color.ink : outline,
                                lineWidth: style == .sent ? 2 : 1.5
                            )
                        )
                )
                .scaleEffect(pressed ? 0.97 : 1)
            if style == .normal {
                ZStack {
                    Circle()
                        .fill(Color.acidLime)
                        .opacity(dimmed ? 0.4 : 1)
                    Text(glyph)
                        .font(.orTitle(16))
                        .foregroundColor(.ink)
                        .rotationEffect(.degrees(rotationDegrees))
                        .animation(rotationAnimation, value: rotationValue)
                }
                .frame(width: 38, height: 38)
                .scaleEffect(pressed ? 0.88 : 1)
                .onAppear { if spinning && !reduceMotion { spin = true } }
            }
        }
        .animation(.onrSpring(response: 0.2, damping: 0.7), value: pressed)
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in pressed = true }
                .onEnded { value in
                    pressed = false
                    let isTap = abs(value.translation.width) < 12
                        && abs(value.translation.height) < 12
                    if isTap { onTap() }
                }
        )
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(label)
    }

    private var rotationDegrees: Double {
        if spinning && !reduceMotion { return spin ? 360 : 0 }
        return pressed ? 45 : 0
    }

    private var rotationValue: Bool {
        spinning && !reduceMotion ? spin : pressed
    }

    private var rotationAnimation: Animation {
        if spinning && !reduceMotion {
            return .linear(duration: 0.6).repeatForever(autoreverses: false)
        }
        return .onrSpring(response: 0.2, damping: 0.7)
    }
}

// MARK: - OnrToast

/// The 2s copy-toast ("Memory saved", "Digest sent to #standup").
/// The parent owns the 2-second dismissal; this view just shows and hides.
struct OnrToast: View {
    var text: String
    @Binding var shown: Bool

    var body: some View {
        Text(text)
            .font(.orTitle(14))
            .foregroundColor(.ink)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(
                Capsule()
                    .fill(Color.acidLime)
                    .overlay(Capsule().strokeBorder(Color.ink, lineWidth: 2))
            )
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : 8)
            .animation(.onrSpring(), value: shown)
            .accessibilityAddTraits(.isStaticText)
    }
}
