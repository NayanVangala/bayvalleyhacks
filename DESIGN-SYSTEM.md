# OnRecord Design System

**Product:** OnRecord, a conversation-memory app on Meta Ray-Ban glasses.
**Tagline:** "Remember who said what, without taking notes."

This is the single merged spec from the two-part design research (Palette/Type/Imagery + Motion/Components/Screens). Part 1 owns every hex and typeface. Part 2 owns every duration, curve, component, and screen assembly. If a color or typeface question arises, it is answered in sections 1-3. If a timing question arises, it is answered in section 4.

**Nayan's hard rule, honored everywhere:** NO white/blue minimalism. No sterile white field, no corporate trust-blue, no timid color. If color is in the design it owns the whole section. Blue appears only as Focus Indigo `#4B4BD8`, a small supporting tone under 3 percent of any screen, never dominant, never as a field.

**The brand shape:** wild art plus strict UI. The 9 generated brand images are the "wild" half: deep violet-plum dark fields with magenta, tangerine, and teal ribbon work. The strict half is rationed color, tracked mono metadata, and hard-edged stickers. Richness comes from saturation plus area discipline, not from color count.

---

## Quick-start for developers

New to this system? Do these six things, in order.

**1. Import the Design module and think in chapters, not tabs.**
Each main tab owns a full-bleed field color. Navigation reads as the color change itself, never as a highlighted tab icon. Pick the chapter first, everything else follows.

```swift
var chapter: AppChapter = .people   // .timeline, .people, .ask, .capture

ZStack {
    chapter.field.ignoresSafeArea()
    // your content here
}
```

**2. Reach for the semantic colors, not raw hexes.**
The four trust states are fixed: `.verified` (lime), `.review` (tangerine), `.silence` (teal), `.unknown` (magenta). Raw hexes live in `Colors.swift` for one-off art direction; everyday UI uses the semantic aliases.

```swift
StatusChip(state: .verified)   // see Components.swift
```

**3. Use the type scale, in this order of loudness.**
Display for emotion, body for reading, mono for proof. Verbatim quotes are always the clean grotesk (`.orQuoteHero()`), never the display face.

```swift
Text("Every word, kept.")
    .font(.orDisplay())
    .orLeading(0.95, for: 56)
```

**4. Print metadata as bracketed mono receipts.**
One label per line, uppercase, tracked, bracketed. Grammar: `[WHO · WHAT · WHEN · WHERE]`, elements separated by middots.

```swift
Label("Maya · Verified Verbatim · 4:32 PM", icon: { EmptyView() })
    .labelStyle(.receipt)   // dark fields; use .receiptOnPaper on Warm Paper
```

**5. Ration accents like they cost money.**
One loud panel per view. Acid Lime marks action and proof (the record button, VERIFIED pills, marker sweeps). Heat (magenta/tangerine) never exceeds one sticker per card. If two accents compete for the eye in one screen, one of them is wrong.

**6. Move like the research says.**
Presses get `.spring` (fingers), reveals get the signature curve (stories). Never a plain opacity fade for narrative reveals; never a loader without an 8-second failsafe to a retry state. Always respect `\.accessibilityReduceMotion` (section 4.4).

```swift
.animation(.timingCurve(0.625, 0.05, 0, 1, duration: 0.9), value: shown)  // narrative
.animation(.spring(response: 0.25, dampingFraction: 0.7), value: pressed)   // fingers
```

**Files:** `Design/Colors.swift` (palette, semantic aliases, `AppChapter`), `Design/Typography.swift` (`.or*` font styles, `ReceiptLabelStyle`), `Design/Components.swift` (the eight named components, written by Implementer 2).

---

## 1. The palette

### 1.1 How color is rationed (the math)

Every loud reference site runs one dominant field (70 to 85 percent), one rationed accent (5 to 10 percent), and neutrals for type. One loud panel per view, never mixed inside a view. Accent marks interactivity or the single most important thing on screen, never decoration. The lead direction is Synthesis Direction C: deep plum field, acid lime rationed to verification and primary actions, magenta-to-tangerine heat for speakers and alerts, warm cream type.

### 1.2 Field colors (the 70-85 percent)

These own entire views. Never pure black, never white-dominant.

| Name | Hex | Usage rule |
|---|---|---|
| **Olive Night** | `#22251C` | The capture field. Warm black-olive. Primary type on it is Bone. Glasses-at-night energy. Capture overlays sit on this regardless of tab. |
| **Deep Plum** | `#2A1428` | The people field. Saturated dark plum, matches the brand art's violet ground. Primary type on it is Bone. |
| **Warm Paper** | `#F4F2ED` | The day field. Warm off-white, never sterile white. Primary type on it is Ink. Lets saturated color sit without vibrating. |
| **Espresso Night** | `#201616` | The recall field. Near-black brown. Primary type on it is Bone. The listening room, deep and quiet. |

Rule: a view gets exactly one field. Two loud fields never share one screen. Cards on dark fields use a raised tone (see 1.4); cards on Warm Paper use the paper itself with hard-edge photo rails, never gray cards on white.

### 1.3 Loud accents (the 5-10 percent, rationed)

Three loud accents plus one small supporting tone. Each accent has a ration and a job.

| Name | Hex | Ration | Usage rule | Pairs with |
|---|---|---|---|---|
| **Acid Lime** | `#BCF015` | 5-10% | The action and proof color. The record button, the VERIFIED sticker pill, the marker sweep behind verified key phrases, recognized-face rings, the satellite arrow circle in CTAs. Never decoration. | Bone type knocked out of it (dark type on lime), or lime type/lines on Olive Night. Never lime on Warm Paper (vibrates) except as a sticker with a dark outline. |
| **Hot Magenta** | `#E835C1` | 3-7% | The stranger and heat color. The UNKNOWN speaker sticker, unreviewed-capture badges, enrollment ceremony moments. | Bone on dark fields. On Warm Paper only as a sticker with a dark outline. |
| **Tangerine** | `#FF7A1A` | 3-7% | The attention color. The REVIEW sticker pill, needs-a-second-look quotes, heat one on speaker stickers, warning microcopy. | Ink type knocked out of it, or Bone on dark fields. |
| **Acid Teal** | `#3DD6C3` | 2-5% | The supporting cool. Waveform strokes, SILENCE-adjacent calm tones, the "listening live" pulse ring. The coldest accent, stays quiet. | Bone on dark fields. |

Hard ration rule: lime never decorates, heat never exceeds one sticker per card. If two accents compete for the eye in one screen, one of them is wrong.

### 1.4 Warm neutrals (text and metadata)

| Name | Hex | Usage rule |
|---|---|---|
| **Bone** | `#F6F1E7` | Primary type on all dark fields. Warmer than white, matches the art's glow. |
| **Ink** | `#1E1E1E` | Primary type on Warm Paper. Never pure black. |
| **Warm Gray** | `#8A8378` | Secondary type and mono micro-labels on dark fields. Muted, recedes. Darkened on paper. |
| **Smoked Plum** | `#45304B` | Raised card surface on Deep Plum. Cards lift from the field without going gray. |
| **Raised Olive** | `#30341F` | Raised card surface on Olive Night. Slightly lifted from the field. |
| **Paper Shadow** | `#E9E4D8` | Inset/pressed surface on Warm Paper for transcript rows and search field wells. |

### 1.5 The blue rule

Blue may appear only as a small supporting tone, never dominant, never as a field. One approved use: **Focus Indigo** `#4B4BD8` (under 3 percent of any screen), allowed only as the Ask input focus ring and the live-listen halo on the Ask tab. If it ever reads as "the app is blue," it has broken the rule.

### 1.6 Semantic colors (status language)

Status is a sticker pill plus a mono receipt line. The color carries the verdict.

| Status | Color | Hex | Usage rule |
|---|---|---|---|
| **VERIFIED** | Acid Lime | `#BCF015` | Verbatim quotes with exact-match proof. Sample: `[VERIFIED VERBATIM]` under a quote card. |
| **REVIEW** | Tangerine | `#FF7A1A` | Capture that needs attention: low confidence transcription, overlapping voices, a name that needs confirming. One per card max. Sample: `[REVIEW · 2 VOICES OVERLAPPED]`. |
| **SILENCE** | Acid Teal | `#3DD6C3` | Listening paused, dead air in a capture, the "nothing to report" state. Quiet by design. Sample: `[SILENCE · 6 MIN]`. The paused pill reads `[TAKING A NAP]`. |
| **UNKNOWN** | Hot Magenta | `#E835C1` | A speaker the app has not enrolled. The stranger color. Sample: `[UNKNOWN VOICE · ENROLL?]`. Enrollment converts magenta to lime: `[ENROLLED · VOICE + FACE LOCKED]`. |

### 1.7 Per-tab color chapters

Each main tab owns its own full-bleed field. The floating pill nav adapts its tint to the chapter it sits in.

| Tab | Field | Accent owner | Type |
|---|---|---|---|
| **Timeline** | Warm Paper `#F4F2ED` | Acid Lime for the record button and verified receipts; Tangerine for REVIEW flags on the day | Ink body, darkened Warm Gray mono labels, ink-on-lime record button |
| **People** | Deep Plum `#2A1428` | Hot Magenta for UNKNOWN speakers and enrollment; Tangerine for one heat sticker per speaker card | Bone body, Warm Gray mono labels |
| **Ask** | Espresso Night `#201616` | Acid Lime for answer receipts and the marker sweep; Focus Indigo `#4B4BD8` as the input focus ring and listen halo (the only blue, under 3%) | Bone body, Warm Gray mono labels |

Capture overlays (the record sheet) sit on Olive Night `#22251C` regardless of tab: capture is its own chapter.

---

## 2. Typography

### 2.1 The pairing (two faces, never three)

One characterful display face for emotion, one neutral grotesk for body and UI, nothing else. Mono is the third register, for metadata only. No pixel or bitmap type for verbatim quotes. Eyebrows, tags, and numbers may carry the character face.

| Role | Ideal | iOS fallback (shipped) |
|---|---|---|
| **Display** | One bundled condensed grotesk with attitude, or a sharp editorial serif. Pick one, never two. Bundling one expressive display face is the single highest-ROI font decision in this system. | SF Pro Black, rounded design (`.orDisplay()`). Serif option: New York ExtraBold. |
| **Body and UI** | A neutral grotesk, clean and legible | SF Pro throughout (`.orBody()`, etc.) |
| **Micro-labels** | Tracked uppercase mono | SF Mono, tracked +100 (`.orMicro()`) |

Display carries the emotion, body stays legible. Verbatim quotes are always the clean grotesk, never the display face.

### 2.2 Type scale (iOS points)

| Style | Size | Weight | Leading | Usage | Swift |
|---|---|---|---|---|---|
| Display | 44-64 | Black | 0.95x, tight | View headers ("Every word, kept."), hero statements. Cropped by screen edges is a feature. | `.font(.orDisplay(56))` |
| Headline | 28-34 | Bold | 1.0x | Section headers, speaker names, Ask answers' supporting lines | `.font(.orHeadline(30))` |
| Title | 20-22 | Semibold | 1.1x | Card titles, conversation titles ("Kitchen, Tuesday") | `.font(.orTitle(21))` |
| Body | 15-17 | Regular | 1.35x | Transcript text, settings prose, all readable copy. Quiet and small so the display type sings. | `.font(.orBody(16))` |
| Caption | 12-13 | Regular | 1.3x | Secondary metadata in sentence case, card footnotes | `.font(.orCaption(12.5))` |
| Micro-label | 10-11 | Medium, uppercase, tracked +100 | 1.0x | Bracketed mono. The metadata voice. Minimum 10 pt. | `.font(.orMicro(11))` |

Use `.orLeading(multiple, for: size)` to apply the proportional leading (e.g. `.orLeading(0.95, for: 56)` for display).

### 2.3 The oversized-quote treatment (the product's signature)

When OnRecord answers, the verbatim quote is the biggest thing on screen: 30 to 34 pt Bold (2 to 3x body), tight leading, in the clean grotesk, Bone on dark fields or Ink on paper. In Read mode it goes full viewport at 40+ pt (`.orQuoteRead()`). The key phrase gets the highlight-marker sweep behind it: a lime bar scaling in behind the exact words, like someone marked it live as it arrives.

Sample quote hero on Espresso Night:

"THE BUDGET IS CAPPED AT FORTY THOUSAND. NO EXCEPTIONS."

with the marker sweep under "forty thousand", then the receipt line:

[MAYA · VERIFIED VERBATIM · TUE 4:32 PM · KITCHEN]

### 2.4 The bracketed mono micro-label style

All memory metadata in uppercase tracked mono, always bracketed. This is the label voice and the proof language. The standard grammar: `[WHO · WHAT · WHEN · WHERE]`. Elements separated by middots. One label per line, never stacked as sentences.

Apply with `ReceiptLabelStyle`:

```swift
Label("Maya · Verified Verbatim · 4:32 PM", icon: { EmptyView() })
    .labelStyle(.receipt)          // dark fields
    .labelStyle(.receiptOnPaper)   // Warm Paper
```

Real samples:

- `[MAYA · VERIFIED VERBATIM · 4:32 PM]`
- `[3 CONVERSATIONS TODAY]`
- `[LISTENING PAUSED]`
- `[QUOTE 042 OF 117]`
- `[CAPTURED TUE 14 OCT 6:42 PM · KITCHEN · GLASSES MIC 2]`
- `[MAYA · ENROLLED 14 OCT · VOICE + FACE]`
- `[NO MEMORIES FOUND. YET.]`
- `[TAKING A NAP]`
- `[UNKNOWN VOICE · ENROLL?]`
- `[CRITERION FOR ENTRY: OWN A PAIR OF GLASSES]`

Labels print in Warm Gray on dark fields, in a darkened warm gray on paper. Status pills are NOT mono labels: pills are sticker components in the semantic colors; the mono line is the receipt beneath them.

---

## 3. Imagery

### 3.1 The rule: art-directed, never stock

Real photography treated hard, or illustration with a point of view. Never a smiling-headset stock photo. OnRecord's visuals are the captured words themselves (the quote is the image) plus the generated brand art and graded POV stills, never generic.

### 3.2 Treatment rules

- **Masks and crops:** rectangular crops, hard edges, thin gutters. No rounded-corner-everything sameness. Where corners are needed (tiles), one consistent 12 pt radius, always with a 2 pt dark outline so art reads as a sticker, not a photo.
- **Grain:** a fine film grain may sit on the generated art only, baked per asset at low opacity. Never a full-screen grain or scanline overlay over app UI: it works on a marketing site and is unreadable over dense transcript text.
- **Type over art:** Bone display type knocks out directly over the dark art. Where art is busy behind type, a 40 percent Espresso Night scrim rises from the bottom. Never a full-bleed white scrim, never centered text floating unanchored.
- **Inline-in-type:** art can appear as a word inside giant headlines. A small rounded crop of the rings art sits between words in a view header ("EVERY [art] WORD, KEPT."). On the Ask tab, a tiny waveform tile sits inline inside the answer headline where the speaker's name appears.
- **Stickers on art:** thick-outline stickers (quote marks, waveform glyphs, tiny glasses icon) paste overlapping art corners, tilted 2 to 4 degrees.

### 3.3 Speaker photos vs abstract art

- **Speaker cards:** rectangular photos, hard edges, thin gutters, no circular avatars in gray rings. One warm grade applied to all captures so the world feels continuous (grade everything into one film). A chunky speaker sticker with the name in Hot Magenta or Tangerine, dark outline, pastes overlapping one corner, tilted about 3 degrees. Mono caption under the photo: `[MAYA · ENROLLED 14 OCT · VOICE + FACE]`.
- **Abstract art:** the generated set is the realm of memory, not people. It backs empty states, recaps, stats, and chapter headers. It never substitutes for a speaker's face. When a speaker is UNKNOWN, the card shows magenta art from the set behind a `[UNKNOWN VOICE · ENROLL?]` label, never a gray silhouette.
- **Glasses-POV stills:** when a capture includes a POV still, it gets the same warm grade as speaker photos and sits inline in the timeline as a rectangular frame, never full-bleed behind text.

### 3.4 Sticker and badge overlay language

Stickers are the brand's playful register.

- **Speaker stickers:** name in display face on a loud fill (magenta for unknown, tangerine for heat, lime for verified-enrolled), dark outline, slight tilt, pasted overlapping the photo's corner. Sample: "MAYA" tilted -2 degrees over her card's bottom-left corner.
- **Status pills:** VERIFIED (lime), REVIEW (tangerine), SILENCE (teal) as sticker pills pinned to the top edge of quote cards.
- **Ticker starbursts:** the marquee ticker uses ✦ separators on a lime strip; starburst badges punctuate onboarding steps ("STEP 2 ✦ TAG A VOICE").
- **Corner rule:** corners never sit empty. Microcopy, mono labels, and small stickers live in margins. One centered card on a blank screen is barren, not minimal.

### 3.5 Brand-asset inventory (the 9 generated images)

All in `~/workspace/bay-valley-hacks/bayvalleyhacks/design-assets/`. Deep violet-plum dark fields with magenta, tangerine, and teal ribbon work. The set is the anchor for the whole palette: it is why the lead direction is deep plum plus acid lime plus magenta-to-tangerine heat.

| File | Content | Placement |
|---|---|---|
| `onrecord-hero-01.png` | Dark plum ground, magenta/tangerine/teal smoke ribbons rising | Marketing hero and the Ask tab backdrop. Giant display type knocks out over it in Bone. |
| `onrecord-hero-02.png` | Companion hero variant | Onboarding finale, and the review/loading moment behind the quote reveal |
| `onrecord-empty-state-03.png` | Empty state variant | The "nothing captured yet" state. Paired with the joke label `[NO MEMORIES FOUND. YET.]`. Empty states get art, not gray illustrations. |
| `onrecord-onboarding-04.png` | Violet ground, concentric translucent rings in magenta/teal/amber around a glowing core | The enrollment ceremony backdrop. The glowing core sits behind the captured face still during "say her name to tag her." |
| `onrecord-card-memory-vortex-05.png` | Card art variant | Memory vortex cards on Timeline: the "one week ago" recap card art |
| `onrecord-card-portrait-fragments-06.png` | Card art variant | People tab section art behind the speaker rail |
| `onrecord-dashboard-3d-07.png` | Dashboard art | The stats/weekly-recap chapter art: "You captured 41 conversations this week" |
| `onrecord-tile-waveform-08.png` | Waveform tile art | The playback screen header and waveform hero tiles. Also the tiny waveform tile inline inside Ask answer headlines. |
| `onrecord-tile-glass-09.png` | Tile art | Small tile crops inside transcript rows and the People grid |

---

## 4. Motion

### 4.1 The timing budget (measured, not vibes)

Durations below come from the reference sites' own animation code, not from eyeballing video.

**Pointer feedback is instant: 0.1 to 0.3 seconds.** Presses, taps, toggles, and hovers never exceed 0.3s. A 0.6s button ease reads as lag.

**Narrative reveals are slow: 0.6 to 1.2 seconds.** Quotes, headers, and view transitions take their time. They are always masked, split, swept, or wiped. Never a plain opacity fade. A bare `.opacity` reveal reads as unfinished.

**The signature ease is a fast attack with a long luxurious settle**, `cubic-bezier(0.625, 0.05, 0, 1)`:

```swift
.animation(.timingCurve(0.625, 0.05, 0, 1, duration: 0.9), value: shown)
```

**The expo-style rise for masked type** maps to:

```swift
.animation(.timingCurve(0.16, 1, 0.3, 1, duration: 0.9), value: shown)
```

Rule of thumb: springs are for fingers (interactive), curves are for stories (narrative). Presses get `.spring`; reveals get the signature curve.

### 4.2 The six motion moves

| Move | What | Where |
|---|---|---|
| **M1. Split-text masked rise** | Heading lines rise from below a line mask (lines 1.0s, 0.09 stagger; words 0.6s, 0.06; chars 0.4s, 0.01), expo ease | View headers, quote reveals, enrollment name resolving |
| **M2. Highlight-marker sweep** | A lime bar scales in behind the key phrase, 0.9s, power3-ish in-out, 0.1s stagger | The single most OnRecord move: exact-match retrieval as a visual signature |
| **M3. Pixel-wipe transitions** | 12-column grid of accent pixels staggering in then out (1.2s total) for navigation; cheap variant: single accent block sweeps scaleX across a card (0.5s in, 0.5s out) while the photo appears underneath | Tab/chapter switches (full wipe); speaker photo reveals (block sweep); enrollment step capture sweep |
| **M4. Scrubbed word-by-word reveal** | Transcript words shift from dim (28% opacity) to full as playback or scroll crosses them, expo ease 0.35s | Playback screen: the transcript develops like a photo as the audio plays |
| **M5. Magnetic press + snap-back** | Tiles lean up to 6 pt toward the touch point while pressed (0.22s), snap home on release (0.28s spring); draggable stickers get up to 14 degrees tilt and 1.05 scale on grab | Speaker stickers, status chips, record button, digest cards |
| **M6. Scramble-in on retrieval** | Key label (name, amount, date) cycles glyphs for ~1s before resolving left to right, like decoding a transmission | Names, dates, amounts on answer surfaces |

Full SwiftUI implementations for all six live in `Design/Components.swift` (Implementer 2), including the exact timing curves and the `ScrambleText` view.

### 4.3 OnRecord's signature choreography

- **The record pulse.** While listening, two rings expand from the RecordButton and fade at the edge on a 1.6s loop, offset 0.8s. Never a static red dot. The pulse stops the instant recording stops: no orphaned animation.
- **The sticker slap.** When a speaker is enrolled or a status resolves, the sticker pops in: scale 1.4 to 1 with a loose spring, rotation settling from +8 degrees to its resting tilt, landing 0.1s after the mono receipt line prints. No opacity fade on the pop. Two stickers landing at once stagger 0.12s.
- **The color-chapter cut.** Tab switches do not slide or fade. The field color cuts hard (instant, no animation on the background itself: the cut IS the transition), then the new screen's header rises masked (M1, 0.6s) and its cards sweep in with a 0.06 stagger.
- **The captured ticker.** A looping ✦-separated marquee on the home screen at a constant 75 px/s regardless of text length, mono caps on a lime strip, paused when offscreen.

### 4.4 Reduced motion fallbacks (non-negotiable)

Respect `\.accessibilityReduceMotion`. When it is on:

- Masked rises become plain crossfades at 0.2s (the one place plain fades are allowed).
- Highlight sweeps render instantly at full width, no animation.
- Pixel wipes become instant hard cuts.
- Marquees render static, first frame only.
- Scramble shows the resolved text immediately.
- The record pulse stops; recording state is shown by a static ring plus the `[LISTENING]` mono label.
- Magnetic press keeps a plain 0.92 scale on tap (feedback without travel).

Also: never trap the user behind a loader without a timeout. Any capture or sync spinner gets an 8-second failsafe that resolves to a retry state with copy, never a dead screen.

---

## 5. The eight named components

Each component ships in `Design/Components.swift` with name, anatomy, states, and real sample copy. No gray chips anywhere. No lorem ipsum anywhere. Summary here; code and exact SwiftUI live with Implementer 2.

### C1. RecordButton (the hero control)

The single most-touched control. A 76 pt circle, Acid Lime fill, 3 pt Ink outline, bold mic glyph in Ink when idle and a stop square when recording. Two pulse rings run while listening. Mono eyebrow above prints state (`[TAP TO REMEMBER]`, `[LISTENING · 02:14 · KITCHEN]`, `[TAKING A NAP]`); `[3 CONVERSATIONS TODAY]` anchors a corner. Pressed: scales to 0.92 with a spring plus the magnetic pull. Stopping fires a spring-in toast: "Memory saved. Maya, 14 seconds."

### C2. SpeakerSticker (chunky badge, never a gray chip)

The identity unit. A name on screen is always a sticker, never plain text and never a gray chip. Heavy grotesk caps, 15-19 pt, on a loud fill (heat for the default speaker color, lime reserved for the current user), 2.5 pt Ink outline, 10 pt radius, resting rotation -3 to +3 degrees kept stable per speaker. Pastes overlapping the photo card's bottom-left corner. Unknown: dashed 2 pt Warm Gray outline, transparent fill, mono `UNKNOWN`; tapping opens enrollment ("Who is this? Tap to enroll."). Resolving identity runs the scramble (M6), then the sticker slaps in. Receipt beneath: `[ENROLLED 14 OCT · VOICE + FACE]`.

### C3. QuoteCard (the verbatim quote as hero)

The proof object. Full-bleed-width card, 20 pt padding. Top row: SpeakerSticker overlapping top-left, StatusChip pinned top-right. The quote in clean grotesk at 30-34 pt, Ink or Bone depending on chapter, tight leading. The key phrase gets the marker sweep (M2) in lime. Beneath: one bracketed mono receipt line, 11 pt: speaker, verification, date, time, place. Bottom row: the button + satellite pair ("Play the moment" pill + lime circle carrying ↘). Review-flagged: 2 pt Tangerine inner border, chip reads REVIEW, copy adds `[TAP TO CONFIRM THE WORDS]`.

Sample: "The budget is capped at forty thousand. No exceptions." Sweep under "forty thousand". Receipt: `[MAYA · VERIFIED VERBATIM · TUE 4:32 PM · KITCHEN]`.

### C4. TranscriptLine (with inline tappable waveform "words")

The transcript is a playable document. One row: mono timestamp `[04:12]` in Warm Gray, a small SpeakerSticker, grotesk 16 pt body. Where a quoted moment lives, a tiny waveform glyph sits inline between the words like a word of type (5-9 bars, 18 pt tall, lime when unplayed). Tapping the waveform plays exactly that span; tapping the timestamp seeks the playback head. Playing: bars animate on a 0.12s loop, spoken words brighten via the scrub reveal (M4), the timestamp flips to lime. Silence spans flatten to a 2 pt rule with `[SILENCE · 6S]`, skippable with one tap.

### C5. StatusChip (VERIFIED / REVIEW / SILENCE / UNKNOWN)

The trust readout. Mono caps, 11 pt, tracked, 2 pt Ink outline: VERIFIED on lime; REVIEW on tangerine (slow 2.4s pulse until a human confirms, static heat fill plus `[NEEDS YOUR EYES]` under reduced motion); SILENCE on Warm Gray with Bone text; UNKNOWN as a dashed Warm Gray outline with transparent fill. Always paired with a bracketed receipt line so the state is never unexplained. Tapping VERIFIED shows the proof sheet; REVIEW opens confirm/correct; SILENCE offers "Skip silences" in playback. While verifying, the chip text scrambles (M6) ~0.8s, then slaps in.

### C6. SearchBar (memory search)

The front door to recall. A floating frosted card, full width minus 32 pt margins, 18 pt radius, 1 pt Ink hairline. Mono prompt `[ASK YOUR MEMORY]` above an 18 pt grotesk input, lime caret, 40 pt lime satellite circle carrying → as its own button. Empty: three outline suggestion stickers ("What did Maya say about the budget", "Roof deck quotes", "Friday deadlines"). Focused: 2 pt lime outline snaps in (0.15s). Loading: `[SEARCHING 117 MEMORIES]` scrambles, resolves to `[3 MEMORIES FOUND]`. No results: the branded empty state, never a blank screen (display type "Nothing yet.", `[NO MEMORIES FOUND. YET.]`, sticker button "Keep listening").

### C7. EnrollmentCard (face + voice capture steps)

Enrolling a speaker is a collectible ceremony, not a settings form. Rectangular face still (hard edges, one warm grade), name area starts as a dashed outline box. Steps print as numbered mono lines: `[1 HOLD STILL · 3S]` `[2 SAY YOUR NAME]` `[3 CONFIRM THE MATCH]`, completed steps get a lime ▪ bullet, corner holds a `[+]` window button. Capturing: the current step gets the block sweep (M3 cheap variant). Success: the name scrambles in (M6), the SpeakerSticker slaps onto the photo corner, the receipt prints `[ENROLLED 14 OCT · VOICE + FACE LOCKED]`, then "Say her name to tag her." Failed: 2 pt Tangerine outline, `[TRY AGAIN · MOVE CLOSER]`, one-tap retry, no dead ends. Microcopy: "OnRecord tags her voice automatically from here on."

### C8. DigestCard (the Slack-style summary)

The day compressed into a scannable card that fires to Slack with one tap. Title bar: left `[DAILY DIGEST · TUE 14 OCT]`, right `[+]` button. Body: 3-4 grotesk 16 pt digest lines with inline small SpeakerStickers and mono time tags. Footer hairline: `[3 QUOTES · 2 PEOPLE · 1 OPEN QUESTION]` left, "Send to Slack" pill + ↘ satellite right. Sent: pill flips to lime fill with Ink text "Sent", mono prints `[SENT TO #STANDUP · 9:41 AM]`, toast "Digest sent to #standup" (2s). Loading: the satellite arrow becomes a 0.6s orbit spinner with an 8-second failsafe to `[SEND FAILED · TAP TO RETRY]`.

---

## 6. Screen compositions (the four chapters)

Each main screen owns a full-bleed field. The floating pill nav ("◉ ONRECORD" + `=`) rides above all four chapters and morphs into a frosted card menu via `matchedGeometryEffect`, dimming the chapter behind it; the card lists `[LIVE] [PEOPLE] [MEMORY] [READ]` in big display type plus a live status tile `[LISTENING · 2 VOICES NEARBY]`. Corners never sit empty: mono microcopy and sticker pills live in the margins of every screen.

**Live / Capture. Chapter: Olive Night (the listening room).** Giant masked-rise header "Every word, kept." fills the top third in display type, cropped slightly by the screen edge as a feature. Center stage: the RecordButton (C1) at 76 pt with pulse rings running while listening, eyebrow `[LISTENING · 02:14 · KITCHEN]` above, `[3 CONVERSATIONS TODAY]` pinned bottom-left. Beneath the button, the captured ticker loops on a lime strip: "MAYA SAID THE BUDGET IS $300 ✦ PRANAV MENTIONED THE ROOF DECK ✦ QUOTE 042 VERIFIED ✦". The lower half is a live transcript stack of TranscriptLines (C4) developing word by word (M4) as audio lands. A small StatusChip (C5) floats top-right; when the room goes quiet it flips to SILENCE and the eyebrow winks `[TAKING A NAP]`.

**People. Chapter: Deep Plum (the sticker party).** A dense, playful grid of speaker cards, two across with thin gutters. Each card: rectangular graded photo with hard edges, SpeakerSticker (C2) pasted overlapping the bottom-left corner at its permanent tilt, mono caption beneath: `[MAYA · ENROLLED 14 OCT · VOICE + FACE]`. Photos reveal with the block sweep (M3 cheap variant) as the grid loads. One card is always the UNKNOWN: dashed outline, no photo, mono "Who is this? Tap to enroll." with its `[+]` window button. Header: oversized display type "Your people." rising masked (M1), corner count `[6 ENROLLED · 1 UNKNOWN]`. Bottom: the button + satellite pair ("Enroll someone" pill + ↘ lime circle) opens a fresh EnrollmentCard (C7) in the morphing overlay, never a pushed settings screen. Tapping any sticker cuts hard to that speaker's memory page in the Memory chapter.

**Search / Memory. Chapter: Warm Paper (the day archive).** The reading-room screen. The SearchBar (C6) sits hero at top with its satellite → circle, outline suggestion stickers beneath when empty. Results arrive as a stack of QuoteCards (C3): 30 pt grotesk quotes with highlight sweeps under key phrases, bracketed mono receipts, VERIFIED chips slapping in with 0.12s stagger. Tapping a card's "Play the moment" satellite expands the card in place (matched geometry, not a push) into TranscriptLines (C4) with scrubbed word-by-word playback (M4) and the full-width waveform. Empty state is branded: display type "Nothing yet.", `[NO MEMORIES FOUND. YET.]`, sticker button "Keep listening", corner joke `[THE ARCHIVE IS HUNGRY]`. A DigestCard (C8) pins the bottom of the scroll on days with captures.

**Read mode. Chapter: warm brown-black (the developing room, dark like film).** One memory at a time, full screen, no chrome except the floating pill. The quote is the entire viewport: 40+ pt grotesk in Bone, key phrase under a slow lime highlight sweep, receipt below: `[MAYA · VERIFIED VERBATIM · TUE 4:32 PM · KITCHEN · GLASSES MIC 2]`. Playback drives the M4 scrub: words brighten as the voice speaks them, the inline waveform glyph pulsing at the exact span. Swipe left for the next memory with the pixel-wipe (M3); the wipe's accent is the chapter's lime. Corners carry the archive voice: top-left `[QUOTE 042 OF 117]`, bottom-right a mini StatusChip, bottom-left the "Copy quote ↘" trigger firing the 2s "Quote copied" toast. This is the demo's money screen: the verbatim quote as hero, the highlight sweep as signature, the receipt as proof.

---

## 7. Do / Don't

**Do**
- Give every view exactly one field color, and let navigation read as the color changing.
- Ration Acid Lime to action and proof: the record button, VERIFIED pills, marker sweeps, the satellite circles in CTAs.
- Set verbatim quotes in the clean grotesk at 2 to 3x body size, the biggest thing on screen.
- Print all metadata as bracketed mono receipts: `[WHO · WHAT · WHEN · WHERE]`.
- Put mono microcopy and small stickers in the corners. Corners never sit empty.
- Tilt stickers 2 to 4 degrees, paste them overlapping photo and art corners, give them dark outlines.
- Grade every photo (speaker cards, POV stills) into one warm film so the world feels continuous.
- Use real voice copy everywhere. No lorem ipsum anywhere. Every screen ships with real words.
- Respect reduced motion and give every spinner an 8-second failsafe to a retry state.

**Don't**
- No white/blue minimalism. No sterile white field, no corporate trust-blue, no timid color. Blue appears only as the Focus Indigo focus ring and listen halo, under 3 percent.
- Never mix two loud fields in one screen, and never put two accents competing for the eye in one screen.
- Never use lime on Warm Paper except as a sticker with a dark outline (it vibrates).
- Never set verbatim quotes in the display face, in pixel type, or in gray chips. Quotes are grotesk, loud, and on the field.
- Never use plain gray chips for status. Status is a sticker pill in a semantic color plus a mono receipt line.
- Never use plain opacity fades for narrative reveals. Reveals are masked, split, swept, or wiped.
- Never use gray silhouettes for unknown speakers. UNKNOWN cards get magenta art plus the `[UNKNOWN VOICE · ENROLL?]` label.
- Never round every corner. Rectangular crops with hard edges and thin gutters; one consistent 12 pt radius only where tiles need it, always with a 2 pt dark outline.
- Never trap the user behind a loader, and never show a blank empty state. Empty states get brand art and a joke label: `[NO MEMORIES FOUND. YET.]`.
- Never introduce a new hue, a third typeface, or a generic modal. If a color or type question arises it is answered in sections 1-3 of this document.

---

## 8. Sample copy bank (real, in the voice)

The voice: playful, honest, belonging-first. Identity before features.

- Hero eyebrow: `[CRITERION FOR ENTRY: OWN A PAIR OF GLASSES]`
- Hero: "The least forgetful app you'll ever wear."
- Subline: "A LIVING ARCHIVE OF YOUR CONVERSATIONS"
- Identity line: "Rememberers, welcome home."
- Quote card: "Maya said the budget is forty-two thousand, not thirty." under it `[MAYA · VERIFIED VERBATIM · 4:32 PM]`
- Ticker: `MAYA SAID THE BUDGET IS $300 ✦ PRANAV MENTIONED THE ROOF DECK ✦ QUOTE 042 VERIFIED ✦ ...`
- Empty search: `[NO MEMORIES FOUND. YET.]`
- Paused pill: `[TAKING A NAP]`
- Copy toast: "Quote copied" (2 seconds)
- Toast after stopping: "Memory saved. Maya, 14 seconds."
- CTA pair: pill "Ask about Maya" plus lime satellite circle with the arrow. On a quote card: "Play the moment" with the satellite carrying ↘.
- Enrollment: `[ENROLLED 14 OCT · SAY HER NAME TO TAG HER]`
- Ask placeholder: "Ask what anyone said. We kept the receipts."
- Weekly recap: "You captured 41 conversations this week. Here is what mattered."
- Dead end: "Nothing here. Your glasses were off, or the room was boring."
- Unknown speaker: "Who is this? Tap to enroll."
- After enrollment: "Say her name to tag her."
- Digest lines: "MAYA locked the budget at $40K, no exceptions. [4:32 PM]" / "PRANAV got the roof deck quote: $1,200. Needs permit. [4:19 PM]" / "Open: who owns the Friday deck deadline? [UNRESOLVED]"

---

## File map

| File | Contents |
|---|---|
| `bridge-ios/OnRecord/Design/Colors.swift` | `Color(hex:)` init helper, the full named palette, semantic status aliases (`.verified` `.review` `.silence` `.unknown`), and the `AppChapter` per-tab chapter mapping |
| `bridge-ios/OnRecord/Design/Typography.swift` | `.or*` font styles matching the type scale (display through 40+ pt read-mode quotes), proportional leading helper, `ReceiptLabelStyle` for bracketed mono labels (`.receipt` / `.receiptOnPaper`) |
| `bridge-ios/OnRecord/Design/Components.swift` | The eight named components with full SwiftUI implementations: C1 RecordButton, C2 SpeakerSticker, C3 QuoteCard, C4 TranscriptLine, C5 StatusChip, C6 SearchBar, C7 EnrollmentCard, C8 DigestCard (written by Implementer 2) |
| `design-assets/` | The 9 generated brand images and their placements (see 3.5) |
| This document | The merged spec both parts agreed on |

Research sources: `design-research/design-system-part1.md` (palette, typography, imagery), `design-research/design-system-part2.md` (motion, components, screen compositions), `design-research/synthesis.md`, and the awwwards/society research notes.
