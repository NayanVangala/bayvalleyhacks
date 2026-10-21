import SwiftUI

// ============================================================================
// Components.swift
// OnRecord / Design
//
// The eight named components from Part 2 of the design system (section 2).
// Motion helpers live in Motion.swift in this folder. Sibling-owned
// (Implementer 1): Colors.swift, Typography.swift, DESIGN-SYSTEM.md.
//
// This file references the sibling's API directly:
//   Color.acidLime / .hotMagenta / .tangerine / .acidTeal / .oliveNight /
//   .deepPlum / .warmPaper / .espressoNight / .bone / .ink / .warmGray /
//   .smokedPlum / .raisedOlive / .paperShadow / .focusIndigo,
//   semantic Color.verified / .review / .silence / .unknown,
//   Font.orDisplay / .orHeadline / .orTitle / .orBody / .orCaption /
//   .orMicro / .orQuoteHero / .orQuoteRead,
//   Label("X", icon: { EmptyView() }).labelStyle(.receipt / .receiptOnPaper)
//   or .labelStyle(ReceiptLabelStyle(color:)) for tinted receipt lines.
//
// Every component ships with a PreviewProvider using real sample copy from
// the Part 2 spec. No lorem ipsum anywhere. No gray chips anywhere.
// ============================================================================

// MARK: - C1 RecordButton (the hero control)
//
// Purpose: the single most-touched control in the app. Starts and stops
// capture. Reads as a physical object begging to be pressed, not an icon.
// Anatomy: 76pt circle, accent-loud fill, 3pt ink outline, mic glyph idle /
// stop glyph recording, two pulse rings while listening, mono eyebrow above,
// day-count mono line below.
// States: idle, recording, paused, loading (orbiting bone arc), disabled.

struct RecordButton: View {
    enum Phase { case idle, recording, paused, loading, disabled }

    var phase: Phase = .idle
    var elapsed: TimeInterval = 134
    var locationTag: String = "KITCHEN"
    var conversationsToday: Int = 3
    var onTap: () -> Void = {}

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulsing = false
    @State private var orbiting = false

    private static func clock(_ interval: TimeInterval) -> String {
        String(format: "%02d:%02d", Int(interval) / 60, Int(interval) % 60)
    }

    private var eyebrow: String {
        switch phase {
        case .idle: return "TAP TO REMEMBER"
        case .recording: return "LISTENING · \(Self.clock(elapsed)) · \(locationTag)"
        case .paused: return "TAKING A NAP"
        case .loading: return "SAVING · HOLD ON"
        case .disabled: return "MIC OFF IN SETTINGS"
        }
    }

    private var dayCount: String {
        "\(conversationsToday) CONVERSATION\(conversationsToday == 1 ? "" : "S") TODAY"
    }

    var body: some View {
        VStack(spacing: 16) {
            Label(eyebrow, icon: { EmptyView() })
                .labelStyle(.receipt)
            Button(action: onTap) {
                ZStack {
                    if phase == .recording {
                        if reduceMotion {
                            // Reduced motion: static ring plus the
                            // [LISTENING] mono label, no pulse.
                            Circle()
                                .strokeBorder(Color.acidLime, lineWidth: 3)
                                .frame(width: 96, height: 96)
                        } else {
                            pulseRing(delay: 0)
                            pulseRing(delay: 0.8)
                        }
                    }
                    Circle()
                        .fill(phase == .disabled ? Color.warmGray.opacity(0.4) : Color.acidLime)
                        .frame(width: 76, height: 76)
                        .overlay(
                            Circle().strokeBorder(
                                phase == .disabled ? Color.warmGray.opacity(0.4) : Color.ink,
                                lineWidth: phase == .disabled ? 1.5 : 3
                            )
                        )
                    Image(systemName: phase == .recording ? "stop.fill" : "mic.fill")
                        .font(.system(size: 28, weight: .bold))
                        .foregroundColor(phase == .disabled ? .warmGray : .ink)
                    if phase == .loading {
                        Circle()
                            .trim(from: 0, to: 0.28)
                            .stroke(Color.bone, lineWidth: 2)
                            .frame(width: 60, height: 60)
                            .rotationEffect(.degrees(orbiting ? 360 : 0))
                            .animation(
                                .linear(duration: 0.9).repeatForever(autoreverses: false),
                                value: orbiting
                            )
                            .onAppear { orbiting = true }
                    }
                }
                .frame(width: 110, height: 110)
            }
            .buttonStyle(OnrPressStyle(pressedScale: 0.92))
            .disabled(phase == .disabled || phase == .loading)
            .accessibilityLabel(phase == .recording ? "Stop recording" : "Start recording")
            Label(dayCount, icon: { EmptyView() })
                .labelStyle(.receipt)
        }
    }

    /// The record pulse: a ring expands from the button and fades at the
    /// edge on a 1.6s loop. Two rings offset by 0.8s read richer than one.
    /// The pulse stops the instant recording stops (the ring views are
    /// conditional on phase == .recording, so no orphaned animation).
    private func pulseRing(delay: Double) -> some View {
        Circle()
            .strokeBorder(Color.acidLime, lineWidth: 3)
            .frame(width: 76, height: 76)
            .scaleEffect(pulsing ? 1.45 : 1.0)
            .opacity(pulsing ? 0 : 0.9)
            .animation(
                .easeOut(duration: 1.6).delay(delay).repeatForever(autoreverses: false),
                value: pulsing
            )
            .onAppear { pulsing = true }
    }
}

// MARK: - C2 SpeakerSticker (chunky badge, never a gray chip)
//
// Purpose: the identity unit of the app. People, and only people, get
// stickers. A name on screen is always a sticker, never plain text.
// Anatomy: heavy grotesk caps 15-19pt, loud fill, 2.5pt ink outline, 10pt
// radius, 12/7 padding, resting tilt -3 to +3 degrees kept stable per
// speaker. Magnetic press enabled.

struct SpeakerSticker: View {
    enum Kind {
        /// Default speaker color: heat (tangerine) fill.
        case person
        /// Accent-loud fill, reserved for the current user.
        case me
        /// Stranger color: hot magenta fill, unreviewed captures.
        case stranger
        /// Dashed mute outline, transparent fill. Tapping opens enrollment.
        case unknown
        /// Outline-only sticker for tappable suggestions.
        case outline
        /// Identity resolving: the scramble-in runs on the name.
        case resolving
    }

    var name: String
    var tilt: Double = -3
    var kind: Kind = .person
    var fontSize: CGFloat = 17
    var onTap: () -> Void = {}

    var body: some View {
        stickerText
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(fillColor)
                    .overlay(border)
            )
            .rotationEffect(.degrees(tilt))
            .onrMagneticPress(onTap: onTap)
            .accessibilityAddTraits(.isButton)
            .accessibilityLabel("Speaker \(name)")
    }

    @ViewBuilder
    private var stickerText: some View {
        if kind == .resolving {
            ScrambleText(target: name.uppercased())
                .font(.orDisplay(fontSize))
                .foregroundColor(.bone)
        } else {
            Text(name.uppercased())
                .font(kind == .unknown ? .orMicro(13) : .orDisplay(fontSize))
                .foregroundColor(textColor)
        }
    }

    private var fillColor: Color {
        switch kind {
        case .person: return .tangerine
        case .me: return .acidLime
        case .stranger: return .hotMagenta
        case .unknown, .outline, .resolving: return .clear
        }
    }

    private var textColor: Color {
        switch kind {
        case .person, .me, .stranger: return .ink
        case .unknown: return .warmGray
        case .outline, .resolving: return .bone
        }
    }

    @ViewBuilder
    private var border: some View {
        switch kind {
        case .unknown:
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(Color.warmGray, style: StrokeStyle(lineWidth: 2, dash: [6, 4]))
        case .outline:
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(Color.bone.opacity(0.7), lineWidth: 2)
        case .resolving:
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(Color.bone, lineWidth: 2.5)
        default:
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(Color.ink, lineWidth: 2.5)
        }
    }
}

// MARK: - C3 QuoteCard (the verbatim quote as hero)
//
// Purpose: the proof object. The exact words are the biggest thing on
// screen, provenance printed underneath like file metadata.
// Anatomy: sticker overlapping top-left, StatusChip top-right, quote in
// clean grotesk 30-34pt with the highlight-marker sweep under the key
// phrase, bracketed mono receipt line, button + satellite pair.
// The quote rises as one masked block here; per-line stagger is a
// screen-level refinement (see Part 2, M1).

struct HighlightedQuote: View {
    var quote: String
    var highlight: String
    var revealed: Bool
    var font: Font = .orQuoteHero(32)
    var color: Color = .ink
    var lineSpacing: CGFloat = 4

    private struct Token: Hashable {
        let text: String
        let lit: Bool
    }

    /// Splits the quote into words and marks the words belonging to the
    /// highlight phrase (matched case-insensitively, ignoring trailing
    /// punctuation so "forty thousand" matches "forty thousand.").
    private var tokens: [Token] {
        let words = quote.split(separator: " ").map(String.init)
        let highlightWords = highlight.split(separator: " ").map(String.init)
        var litRange: Range<Int>?
        if !highlightWords.isEmpty, highlightWords.count <= words.count {
            let normWords = words.map { $0.lowercased().trimmingCharacters(in: .punctuationCharacters) }
            let normHi = highlightWords.map { $0.lowercased().trimmingCharacters(in: .punctuationCharacters) }
            for start in 0...(words.count - highlightWords.count) {
                if Array(normWords[start..<(start + highlightWords.count)]) == normHi {
                    litRange = start..<(start + highlightWords.count)
                    break
                }
            }
        }
        return words.enumerated().map { index, word in
            Token(text: word, lit: litRange?.contains(index) ?? false)
        }
    }

    var body: some View {
        FlowLayout(spacing: 7, lineSpacing: lineSpacing) {
            ForEach(Array(tokens.enumerated()), id: \.offset) { index, token in
                if token.lit {
                    Text(token.text)
                        .onrHighlightSweep(revealed: revealed, delay: 0.15 + Double(index) * 0.1)
                } else {
                    Text(token.text)
                }
            }
        }
        .font(font)
        .foregroundColor(color)
    }
}

struct QuoteCard: View {
    var quote: String = "The budget is capped at forty thousand. No exceptions."
    var highlightPhrase: String = "forty thousand"
    var speakerName: String = "MAYA"
    var speakerTilt: Double = -3
    var status: StatusChip.Status = .verified
    var receipt: String = "MAYA · VERIFIED VERBATIM · TUE 4:32 PM · KITCHEN"
    var needsReview: Bool = false
    var reviewNote: String = "TAP TO CONFIRM THE WORDS"
    var onPlay: () -> Void = {}
    var onOpenProof: () -> Void = {}

    @State private var appeared = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                SpeakerSticker(name: speakerName, tilt: speakerTilt, kind: .person, fontSize: 13)
                Spacer()
                StatusChip(status: status, onTap: onOpenProof)
            }
            HighlightedQuote(
                quote: quote,
                highlight: highlightPhrase,
                revealed: appeared,
                font: .orQuoteHero(32),
                color: .ink
            )
            .onrMaskedRise(shown: appeared)
            VStack(alignment: .leading, spacing: 6) {
                Label(receipt, icon: { EmptyView() })
                    .labelStyle(.receiptOnPaper)
                if needsReview {
                    Label(reviewNote, icon: { EmptyView() })
                        .labelStyle(ReceiptLabelStyle(color: .tangerine))
                }
            }
            SatellitePair(label: "Play the moment", foreground: .ink, outline: .ink, onTap: onPlay)
        }
        .padding(20)
        .background(
            RoundedRectangle(cornerRadius: 18)
                .fill(Color.warmPaper)
                .overlay(
                    RoundedRectangle(cornerRadius: 18)
                        .strokeBorder(Color.ink.opacity(0.18), lineWidth: 1)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .strokeBorder(Color.tangerine, lineWidth: needsReview ? 2 : 0)
                        .padding(2)
                )
        )
        .onrCardPress(scale: 0.98)
        .onAppear { appeared = true }
    }
}

// MARK: - C4 TranscriptLine (playable document)
//
// Purpose: the transcript is not a wall of text; each line carries its
// audio inside it as a tappable waveform glyph treated like a word of type.
// Anatomy: mono timestamp, small SpeakerSticker, grotesk 16pt body, inline
// waveform glyphs. Words brighten via the M4 scrub reveal as playback
// crosses them. Silence spans flatten to a 2pt rule with a mono tag.

struct TranscriptLine: View {
    enum Token: Hashable {
        case word(String)
        case wave(id: String)
    }

    var timestamp: String = "04:12"
    var speakerName: String = "MAYA"
    var speakerTilt: Double = -3
    var tokens: [Token] = Self.sampleMaya
    /// How many word-tokens have been spoken. Words before this index render
    /// full-bright; the rest sit dimmed (M4 scrub reveal).
    var playedWords: Int = 0
    var isPlaying: Bool = false
    var isSilence: Bool = false
    var silenceSeconds: Int = 6
    var onDark: Bool = true
    var onSeek: () -> Void = {}
    var onPlaySpan: (String) -> Void = { _ in }

    static var sampleMaya: [Token] = [
        .word("We"), .word("need"), .word("the"), .word("deck"), .word("by"), .word("Friday,"),
        .wave(id: "maya-0412-a"),
        .word("and"), .word("the"), .word("permit"), .word("is"), .word("still"), .word("pending.")
    ]

    static var samplePranav: [Token] = [
        .word("The"), .word("roof"), .word("deck"), .word("quote"),
        .word("came"), .word("in"), .word("at"), .word("twelve"), .word("hundred.")
    ]

    private var fg: Color { onDark ? .bone : .ink }

    /// Each token annotated with its word ordinal (waveforms take the
    /// ordinal of the words spoken before them) for the scrub reveal.
    private var annotated: [(token: Token, order: Int)] {
        var order = 0
        return tokens.map { token in
            let current = order
            if case .word = token { order += 1 }
            return (token, current)
        }
    }

    var body: some View {
        if isSilence {
            silenceRow
        } else {
            HStack(alignment: .top, spacing: 10) {
                Button(action: onSeek) {
                    Label(timestamp, icon: { EmptyView() })
                        .labelStyle(ReceiptLabelStyle(color: isPlaying ? .acidLime : .warmGray))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Seek to \(timestamp)")
                SpeakerSticker(name: speakerName, tilt: speakerTilt, kind: .person, fontSize: 12)
                FlowLayout(spacing: 5, lineSpacing: 5) {
                    ForEach(Array(annotated.enumerated()), id: \.offset) { _, item in
                        tokenView(item.token, order: item.order)
                    }
                }
                .font(.orBody(16))
                .animation(.onrExpo(duration: 0.35), value: playedWords)
            }
        }
    }

    @ViewBuilder
    private func tokenView(_ token: Token, order: Int) -> some View {
        switch token {
        case .word(let text):
            Text(text)
                .foregroundColor(order < playedWords ? fg : fg.opacity(0.3))
        case .wave(let id):
            Button(action: { onPlaySpan(id) }) {
                WaveformGlyph(
                    played: order < playedWords,
                    active: isPlaying && order == playedWords
                )
            }
            .buttonStyle(OnrPressStyle(pressedScale: 1.15))
            .accessibilityLabel("Play this moment")
        }
    }

    private var silenceRow: some View {
        HStack(spacing: 10) {
            Label(timestamp, icon: { EmptyView() })
                .labelStyle(.receipt)
            Rectangle()
                .fill(Color.warmGray)
                .frame(height: 2)
            Label("SILENCE · \(silenceSeconds)S", icon: { EmptyView() })
                .labelStyle(.receipt)
            Spacer()
        }
        .contentShape(Rectangle())
        .onTapGesture(perform: onSeek)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel("Silence, \(silenceSeconds) seconds. Tap to skip.")
    }
}

// MARK: - C5 StatusChip (VERIFIED / REVIEW / SILENCE / UNKNOWN)
//
// Purpose: the trust readout. Every captured fragment carries one of four
// states, always a sticker pill, never a gray chip or a colored dot.
// Anatomy: mono caps 11pt tracked, 2pt ink outline. VERIFIED on accent-loud,
// REVIEW on heat (slow 2.4s pulse until a human confirms), SILENCE on the
// semantic silence color, UNKNOWN as a dashed mute outline.
// Always paired with a bracketed receipt line nearby so the state is never
// unexplained. Magnetic press enabled.

struct StatusChip: View {
    enum Status: String, CaseIterable {
        case verified = "VERIFIED"
        case review = "REVIEW"
        case silence = "SILENCE"
        case unknown = "UNKNOWN"
    }

    var status: Status
    var loading: Bool = false
    var onTap: () -> Void = {}

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var reviewPulse = false

    private var fill: Color {
        switch status {
        case .verified: return .verified
        case .review: return .review
        case .silence: return .silence
        case .unknown: return .clear
        }
    }

    private var textColor: Color {
        switch status {
        case .verified, .review, .silence: return .ink
        case .unknown: return .warmGray
        }
    }

    var body: some View {
        Group {
            if loading {
                ScrambleText(target: status.rawValue)
            } else {
                Text(status.rawValue)
            }
        }
        .font(.orMicro(11))
        .textCase(.uppercase)
        .foregroundColor(textColor)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(
            Capsule()
                .fill(fill)
                // The REVIEW pulse lives on the fill only, so the ink
                // outline stays put. Under reduced motion the chip is
                // static and the screen adds a [NEEDS YOUR EYES] line.
                .opacity(status == .review && !reduceMotion ? (reviewPulse ? 0.75 : 1) : 1)
                .animation(
                    status == .review
                        ? .easeInOut(duration: 2.4).repeatForever(autoreverses: true)
                        : .default,
                    value: reviewPulse
                )
                .overlay(chipBorder)
        )
        .onAppear { if status == .review && !reduceMotion { reviewPulse = true } }
        .onrMagneticPress(onTap: onTap)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel("Status \(status.rawValue.lowercased())")
    }

    @ViewBuilder
    private var chipBorder: some View {
        if status == .unknown {
            Capsule().strokeBorder(Color.warmGray, style: StrokeStyle(lineWidth: 2, dash: [5, 4]))
        } else {
            Capsule().strokeBorder(Color.ink, lineWidth: 2)
        }
    }
}

// MARK: - C6 SearchBar (memory search)
//
// Purpose: the front door to recall. "Ask your memory" is a hero element,
// not a tucked-away field.
// Anatomy: floating frosted card, 18pt radius, 1pt ink hairline. Mono prompt
// above the grotesk 18pt input, accent-loud caret, satellite circle (40pt)
// with an arrow as its own button. Outline suggestion stickers beneath when
// empty. Focus ring is Focus Indigo per Part 1's blue rule.

struct SearchBar: View {
    @Binding var text: String
    var searching: Bool = false
    var searchingLabel: String = "SEARCHING 117 MEMORIES"
    var foundCount: Int? = nil
    var suggestions: [String] = [
        "What did Maya say about the budget",
        "Roof deck quotes",
        "Friday deadlines"
    ]
    var onSubmit: () -> Void = {}
    var onPickSuggestion: (String) -> Void = { _ in }

    @FocusState private var focused: Bool
    @State private var appeared = false

    private var prompt: String {
        if searching { return searchingLabel }
        if let count = foundCount { return "\(count) MEMORIES FOUND" }
        return "ASK YOUR MEMORY"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 8) {
                Group {
                    if searching {
                        ScrambleText(target: prompt)
                    } else {
                        Text(prompt)
                    }
                }
                .font(.orMicro(11))
                .textCase(.uppercase)
                .foregroundColor(.warmGray)
                HStack(spacing: 12) {
                    TextField("", text: $text)
                        .font(.orTitle(18))
                        .foregroundColor(.bone)
                        .tint(.acidLime)
                        .focused($focused)
                        .submitLabel(.search)
                        .onSubmit(onSubmit)
                    Button(action: onSubmit) {
                        ZStack {
                            Circle()
                                .fill(Color.acidLime)
                                .opacity(text.isEmpty ? 0.4 : 1)
                            Text("→")
                                .font(.orTitle(18))
                                .foregroundColor(.ink)
                        }
                        .frame(width: 40, height: 40)
                    }
                    .buttonStyle(OnrPressStyle(pressedScale: 0.88))
                    .accessibilityLabel("Search memory")
                }
            }
            .padding(16)
            .background(
                RoundedRectangle(cornerRadius: 18)
                    .fill(.ultraThinMaterial)
                    .overlay(
                        RoundedRectangle(cornerRadius: 18)
                            .strokeBorder(Color.ink, lineWidth: 1)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 18)
                            .strokeBorder(Color.focusIndigo, lineWidth: 2)
                            .opacity(focused ? 1 : 0)
                            .animation(.easeInOut(duration: 0.15), value: focused)
                    )
            )
            if text.isEmpty && !searching {
                FlowLayout(spacing: 8, lineSpacing: 8) {
                    ForEach(suggestions, id: \.self) { suggestion in
                        SpeakerSticker(
                            name: suggestion,
                            tilt: 0,
                            kind: .outline,
                            fontSize: 13,
                            onTap: { onPickSuggestion(suggestion) }
                        )
                    }
                }
                .onrMaskedRise(shown: appeared)
            }
        }
        .onAppear { appeared = true }
    }
}

/// The branded empty state, never a blank screen.
struct SearchEmptyState: View {
    var onKeepListening: () -> Void = {}

    var body: some View {
        VStack(spacing: 14) {
            Text("Nothing yet.")
                .font(.orDisplay(44))
                .foregroundColor(.bone)
            Label("NO MEMORIES FOUND. YET.", icon: { EmptyView() })
                .labelStyle(.receipt)
            SpeakerSticker(name: "Keep listening", tilt: -2, kind: .outline, fontSize: 14, onTap: onKeepListening)
        }
        .padding(.vertical, 32)
    }
}

// MARK: - C7 EnrollmentCard (face + voice capture steps)
//
// Purpose: enrolling a speaker is a collectible ceremony, not a settings
// form. The card turns "add a person" into a sticker being earned.
// Anatomy: rectangular face still with hard edges and one warm grade, the
// name area as a dashed outline box until it resolves, numbered mono steps,
// accent-loud square bullets for completed steps, [+] window button corner.
// Success: the name scrambles in (M6), the sticker slaps onto the photo
// corner 0.1s after the receipt prints, then "Say her name to tag her."

struct EnrollmentCard: View {
    enum Phase: Equatable {
        case ready
        case capturing(step: Int)
        case success
        case failed(step: Int)
        case disabled
    }

    var name: String = "MAYA"
    var nameTilt: Double = -3
    var phase: Phase = .ready
    var steps: [String] = ["HOLD STILL · 3S", "SAY YOUR NAME", "CONFIRM THE MATCH"]
    var receipt: String = "ENROLLED 14 OCT · VOICE + FACE LOCKED"
    var onRetry: () -> Void = {}
    var onOpenSettings: () -> Void = {}

    @State private var slapped = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var completedSteps: Int {
        switch phase {
        case .ready: return 0
        case .capturing(let step): return step
        case .success: return steps.count
        case .failed(let step): return step
        case .disabled: return 0
        }
    }

    private var capturingStep: Int? {
        if case .capturing(let step) = phase { return step }
        return nil
    }

    private var borderColor: Color {
        if case .failed = phase { return .tangerine }
        return .clear
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            faceStill
            if phase != .success {
                dashedNameBox
            }
            stepsList
            statusBlock
            Text("OnRecord tags her voice automatically from here on.")
                .font(.orCaption(13))
                .foregroundColor(.warmGray)
        }
        .padding(18)
        .background(
            RoundedRectangle(cornerRadius: 18)
                .fill(Color.deepPlum)
                .overlay(
                    RoundedRectangle(cornerRadius: 18)
                        .strokeBorder(borderColor, lineWidth: borderColor == .clear ? 0 : 2)
                )
        )
        .opacity(phase == .disabled ? 0.7 : 1)
        .onAppear {
            guard phase == .success else { return }
            if reduceMotion {
                slapped = true
            } else {
                // The sticker lands 0.1s after the mono receipt line prints
                // (the scramble runs about a second).
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.1) { slapped = true }
            }
        }
    }

    private var faceStill: some View {
        Rectangle()
            .fill(
                LinearGradient(
                    colors: [.smokedPlum, .deepPlum],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .frame(height: 190)
            .overlay(alignment: .topTrailing) {
                Text("[+]")
                    .font(.orMicro(12))
                    .foregroundColor(.bone.opacity(0.8))
                    .padding(10)
            }
            .overlay(alignment: .bottomLeading) {
                if phase != .success {
                    Label(
                        phase == .capturing ? "CAPTURING · HOLD STILL" : "CAMERA PREVIEW",
                        icon: { EmptyView() }
                    )
                    .labelStyle(ReceiptLabelStyle(color: .bone.opacity(0.7)))
                    .padding(10)
                }
            }
            .overlay(alignment: .bottomLeading) {
                if phase == .success {
                    SpeakerSticker(name: name, tilt: nameTilt, kind: .me, fontSize: 15)
                        .onrStickerSlap(restingTilt: nameTilt, slapped: slapped)
                        .offset(x: 12, y: 16)
                }
            }
    }

    private var dashedNameBox: some View {
        RoundedRectangle(cornerRadius: 10)
            .strokeBorder(Color.warmGray, style: StrokeStyle(lineWidth: 2, dash: [6, 4]))
            .frame(height: 46)
            .overlay(
                Label("NAME APPEARS HERE", icon: { EmptyView() })
                    .labelStyle(.receipt)
            )
    }

    private var stepsList: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                let done = index < completedSteps
                let current = capturingStep == index
                HStack(spacing: 10) {
                    Text(done ? "▪" : "▫")
                        .font(.orTitle(14))
                        .foregroundColor(done ? .acidLime : .warmGray)
                    Label("\(index + 1) \(step)", icon: { EmptyView() })
                        .labelStyle(ReceiptLabelStyle(color: current ? .ink : .bone))
                    Spacer()
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(Group { if current { SweepBlock() } })
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    @ViewBuilder
    private var statusBlock: some View {
        switch phase {
        case .success:
            ScrambleText(target: name.uppercased())
                .font(.orDisplay(30))
                .foregroundColor(.bone)
            Label(receipt, icon: { EmptyView() })
                .labelStyle(ReceiptLabelStyle(color: .acidLime))
            Text("Say her name to tag her.")
                .font(.orTitle(15))
                .foregroundColor(.bone)
        case .failed:
            Label("TRY AGAIN · MOVE CLOSER", icon: { EmptyView() })
                .labelStyle(ReceiptLabelStyle(color: .tangerine))
            SatellitePair(label: "Try again", foreground: .bone, outline: .bone, onTap: onRetry)
        case .disabled:
            Label("CAMERA + MIC NEEDED", icon: { EmptyView() })
                .labelStyle(.receipt)
            SatellitePair(label: "Open Settings", foreground: .bone, outline: .bone, onTap: onOpenSettings)
        case .capturing:
            Label("LISTENING · STAY CLOSE", icon: { EmptyView() })
                .labelStyle(ReceiptLabelStyle(color: .acidLime))
        case .ready:
            EmptyView()
        }
    }
}

// MARK: - C8 DigestCard (the Slack-style summary)
//
// Purpose: the day's conversations compressed into a scannable card that
// can be fired to Slack with one tap. Designed like a prize.
// Anatomy: OS-window title bar ([DAILY DIGEST · TUE 14 OCT] + [+]), 3-4
// digest lines with inline SpeakerStickers and mono time tags, hairline,
// footer with the stats receipt left and the button + satellite pair right.
// Lines rise masked with a 0.06 stagger when the card appears.

struct DigestLineItem: Hashable {
    var speaker: String?
    var speakerKind: SpeakerSticker.Kind = .person
    var tilt: Double = -3
    var sentence: String
    var timeTag: String
}

struct DigestCard: View {
    enum Phase { case idle, sending, sent, failed }

    var phase: Phase = .idle
    var titleTag: String = "DAILY DIGEST · TUE 14 OCT"
    var lines: [DigestLineItem] = [
        DigestLineItem(speaker: "MAYA", tilt: -3, sentence: "locked the budget at $40K, no exceptions.", timeTag: "4:32 PM"),
        DigestLineItem(speaker: "PRANAV", tilt: -2, sentence: "got the roof deck quote: $1,200. Needs permit.", timeTag: "4:19 PM"),
        DigestLineItem(speaker: nil, sentence: "Open: who owns the Friday deck deadline?", timeTag: "UNRESOLVED")
    ]
    var footerStats: String = "3 QUOTES · 2 PEOPLE · 1 OPEN QUESTION"
    var sentTag: String = "SENT TO #STANDUP · 9:41 AM"
    var failTag: String = "SEND FAILED · TAP TO RETRY"
    var onSend: () -> Void = {}

    @State private var appeared = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label(titleTag, icon: { EmptyView() })
                    .labelStyle(.receiptOnPaper)
                Spacer()
                Text("[+]")
                    .font(.orMicro(12))
                    .foregroundColor(.warmGray)
            }
            .padding(16)
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                    digestLine(line)
                        .onrMaskedRise(shown: appeared, delay: Double(index) * 0.06, distance: 16)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 16)
            Rectangle()
                .fill(Color.ink.opacity(0.15))
                .frame(height: 1)
            HStack {
                footerLeft
                Spacer()
                footerRight
            }
            .padding(16)
        }
        .background(
            RoundedRectangle(cornerRadius: 18)
                .fill(Color.warmPaper)
                .overlay(
                    RoundedRectangle(cornerRadius: 18)
                        .strokeBorder(Color.ink.opacity(0.18), lineWidth: 1)
                )
        )
        .onrCardPress(scale: 0.98)
        .onAppear { appeared = true }
    }

    private func digestLine(_ line: DigestLineItem) -> some View {
        HStack(alignment: .top, spacing: 8) {
            if let speaker = line.speaker {
                SpeakerSticker(name: speaker, tilt: line.tilt, kind: line.speakerKind, fontSize: 12)
            }
            (
                Text(line.sentence + " ").font(.orBody(16)).foregroundColor(.ink)
                + Text("[\(line.timeTag)]").font(.orMicro(11)).foregroundColor(.warmGray)
            )
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var footerLeft: some View {
        switch phase {
        case .sent:
            Label(sentTag, icon: { EmptyView() })
                .labelStyle(.receiptOnPaper)
        case .failed:
            Label(failTag, icon: { EmptyView() })
                .labelStyle(ReceiptLabelStyle(color: .tangerine))
        default:
            Label(footerStats, icon: { EmptyView() })
                .labelStyle(.receiptOnPaper)
        }
    }

    @ViewBuilder
    private var footerRight: some View {
        switch phase {
        case .idle:
            SatellitePair(label: "Send to Slack", foreground: .ink, outline: .ink, onTap: onSend)
        case .sending:
            SatellitePair(label: "Sending", foreground: .ink, outline: .ink, spinning: true, onTap: {})
        case .sent:
            SatellitePair(label: "Sent", style: .sent, onTap: {})
        case .failed:
            SatellitePair(label: "Retry send", foreground: .ink, outline: .ink, onTap: onSend)
        }
    }
}

// MARK: - Previews

#if DEBUG

struct RecordButton_Previews: PreviewProvider {
    struct Demo: View {
        @State private var phase: RecordButton.Phase = .idle
        @State private var toastShown = false

        var body: some View {
            ZStack {
                Color.oliveNight.ignoresSafeArea()
                VStack(spacing: 36) {
                    // Tap to toggle idle/recording and fire the toast.
                    RecordButton(phase: phase, elapsed: 134, onTap: {
                        switch phase {
                        case .idle: phase = .recording
                        case .recording: phase = .idle; toastShown = true
                        default: break
                        }
                    })
                    HStack(spacing: 20) {
                        RecordButton(phase: .paused, conversationsToday: 3)
                        RecordButton(phase: .loading, conversationsToday: 3)
                        RecordButton(phase: .disabled, conversationsToday: 3)
                    }
                }
                .padding()
                VStack {
                    Spacer()
                    OnrToast(text: "Memory saved. Maya, 14 seconds.", shown: $toastShown)
                        .padding(.bottom, 48)
                }
                .task(id: toastShown) {
                    if toastShown {
                        try? await Task.sleep(nanoseconds: 2_000_000_000)
                        toastShown = false
                    }
                }
            }
        }
    }

    static var previews: some View {
        Demo().previewDisplayName("C1 RecordButton")
    }
}

struct SpeakerSticker_Previews: PreviewProvider {
    static var previews: some View {
        VStack(alignment: .leading, spacing: 18) {
            SpeakerSticker(name: "MAYA", tilt: -3, kind: .person)
            Label("ENROLLED 14 OCT · VOICE + FACE", icon: { EmptyView() })
                .labelStyle(.receipt)
            SpeakerSticker(name: "PRANAV", tilt: -2, kind: .person)
            SpeakerSticker(name: "YOU", tilt: 2, kind: .me)
            SpeakerSticker(name: "NOVA", tilt: 1, kind: .stranger)
            SpeakerSticker(name: "UNKNOWN", tilt: 0, kind: .unknown)
            SpeakerSticker(name: "???", tilt: 0, kind: .resolving, fontSize: 15)
            SpeakerSticker(
                name: "What did Maya say about the budget",
                tilt: 0, kind: .outline, fontSize: 13
            )
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.deepPlum)
        .previewDisplayName("C2 SpeakerSticker")
    }
}

struct QuoteCard_Previews: PreviewProvider {
    static var previews: some View {
        VStack(spacing: 20) {
            QuoteCard()
            QuoteCard(
                quote: "He said the roof would hold, then it rained for a week.",
                highlightPhrase: "rained for a week",
                speakerName: "PRANAV",
                speakerTilt: -2,
                status: .review,
                receipt: "PRANAV · REVIEW · 2 VOICES OVERLAPPED · TUE 4:19 PM",
                needsReview: true
            )
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.warmPaper)
        .previewDisplayName("C3 QuoteCard")
    }
}

struct TranscriptLine_Previews: PreviewProvider {
    static var previews: some View {
        VStack(alignment: .leading, spacing: 18) {
            TranscriptLine(
                timestamp: "04:12",
                speakerName: "MAYA",
                speakerTilt: -3,
                tokens: TranscriptLine.sampleMaya,
                playedWords: 6,
                isPlaying: true
            )
            TranscriptLine(
                timestamp: "04:19",
                speakerName: "PRANAV",
                speakerTilt: -2,
                tokens: TranscriptLine.samplePranav,
                playedWords: 9
            )
            TranscriptLine(timestamp: "04:26", isSilence: true, silenceSeconds: 6)
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.oliveNight)
        .previewDisplayName("C4 TranscriptLine")
    }
}

struct StatusChip_Previews: PreviewProvider {
    static var previews: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                StatusChip(status: .verified)
                StatusChip(status: .review)
            }
            HStack(spacing: 12) {
                StatusChip(status: .silence)
                StatusChip(status: .unknown)
            }
            StatusChip(status: .verified, loading: true)
            Label("TAP TO CONFIRM THE WORDS", icon: { EmptyView() })
                .labelStyle(ReceiptLabelStyle(color: .tangerine))
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.warmPaper)
        .previewDisplayName("C5 StatusChip")
    }
}

struct SearchBar_Previews: PreviewProvider {
    struct Demo: View {
        @State private var empty = ""
        @State private var typing = "what did Maya say about the budget"

        var body: some View {
            ScrollView {
                VStack(spacing: 28) {
                    SearchBar(text: $empty, onPickSuggestion: { empty = $0 })
                    SearchBar(text: $typing)
                    SearchBar(text: .constant("budget"), searching: true)
                    SearchBar(text: $typing, foundCount: 3)
                    SearchEmptyState()
                }
                .padding(20)
            }
        }
    }

    static var previews: some View {
        Demo()
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(Color.espressoNight)
            .previewDisplayName("C6 SearchBar")
    }
}

struct EnrollmentCard_Previews: PreviewProvider {
    static var previews: some View {
        ScrollView {
            VStack(spacing: 20) {
                EnrollmentCard(phase: .ready)
                EnrollmentCard(phase: .capturing(step: 1))
                EnrollmentCard(phase: .success)
                EnrollmentCard(phase: .failed(step: 1))
                EnrollmentCard(phase: .disabled)
            }
            .padding(20)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.deepPlum)
        .previewDisplayName("C7 EnrollmentCard")
    }
}

struct DigestCard_Previews: PreviewProvider {
    struct Demo: View {
        @State private var toastShown = true

        var body: some View {
            ZStack(alignment: .bottom) {
                ScrollView {
                    VStack(spacing: 20) {
                        DigestCard(phase: .idle)
                        DigestCard(phase: .sending)
                        DigestCard(phase: .sent)
                        DigestCard(phase: .failed)
                    }
                    .padding(20)
                }
                OnrToast(text: "Digest sent to #standup", shown: $toastShown)
                    .padding(.bottom, 32)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(Color.warmPaper)
            .task(id: toastShown) {
                if toastShown {
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                    toastShown = false
                }
            }
        }
    }

    static var previews: some View {
        Demo().previewDisplayName("C8 DigestCard")
    }
}

#endif
