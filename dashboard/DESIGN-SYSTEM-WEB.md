# OnRecord Design System, Web: dashboard theme spec

Web translation of `~/workspace/bay-valley-hacks/design-research/design-system-part1.md` for the Next.js dashboard at `dashboard/`. Theme foundation only. Not a restyle: nothing here changes existing markup until `.onr-*` classes or chapter scopes are applied.

Nayan's hard rule stands on web too: no white/blue minimalism. No sterile white field, no corporate trust-blue, no timid color. Blue appears only as a small supporting tone, never dominant.

## 1. Palette as CSS custom properties

Same names, same hexes as the iOS system. All live in `theme.css` under `:root`. Copy the hex, never eyeball it.

### Field colors (the 70 to 85 percent, one per view)

| iOS name | Hex | CSS variable | Usage on web |
|---|---|---|---|
| Olive Night | `#22251C` | `--onr-olive-night` | The capture chapter. Live route background. |
| Deep Plum | `#2A1428` | `--onr-deep-plum` | The people chapter. People route background. |
| Warm Paper | `#F4F2ED` | `--onr-warm-paper` | The day chapter. Status route background. Never sterile white. |
| Espresso Night | `#201616` | `--onr-espresso-night` | The recall chapter. Search route background. The listening room. |

Rule: a view gets exactly one field. Two loud fields never share one screen. Cards on dark fields use a raised tone (`--onr-smoked-plum` on plum, `--onr-raised-olive` on olive); cards on Warm Paper use the paper itself with hard-edge photo rails and `--onr-paper-shadow` wells for transcript rows and search fields.

### Loud accents (the 5 to 10 percent, rationed)

| iOS name | Hex | CSS variable | Ration | Job on web |
|---|---|---|---|---|
| Acid Lime | `#BCF015` | `--onr-acid-lime` | 5 to 10% | The record button, VERIFIED pills, marker sweep behind verified key phrases, the focus ring everywhere except Ask. Dark type knocked out of it. Never lime on Warm Paper except as a sticker with a dark outline. |
| Hot Magenta | `#E835C1` | `--onr-hot-magenta` | 3 to 7% | UNKNOWN speaker stickers, unreviewed-capture badges, enrollment moments. |
| Tangerine | `#FF7A1A` | `--onr-tangerine` | 3 to 7% | REVIEW pills, needs-a-second-look quotes, heat stickers, warning microcopy. |
| Acid Teal | `#3DD6C3` | `--onr-acid-teal` | 2 to 5% | Waveform strokes, SILENCE pills, the listening-live pulse ring. The coldest accent, stays quiet. |

Hard ration rule: lime never decorates, heat never exceeds one sticker per card. If two accents compete for the eye in one screen, one of them is wrong.

### Warm neutrals

| iOS name | Hex | CSS variable | Usage on web |
|---|---|---|---|
| Bone | `#F6F1E7` | `--onr-bone` | Primary type on all dark fields. Warmer than white. |
| Ink | `#1E1E1E` | `--onr-ink` | Primary type on Warm Paper. Never pure black. Also the sticker outline color. |
| Warm Gray | `#8A8378` | `--onr-warm-gray` | Secondary type and mono micro-labels on dark fields. |
| Warm Gray Deep | (derived) | `--onr-warm-gray-deep` | Secondary type on Warm Paper. Web-only derivation of Warm Gray darkened for contrast. |
| Smoked Plum | `#45304B` | `--onr-smoked-plum` | Raised card surface on Deep Plum. |
| Raised Olive | `#30341F` | `--onr-raised-olive` | Raised card surface on Olive Night. |
| Paper Shadow | `#E9E4D8` | `--onr-paper-shadow` | Inset surface on Warm Paper for transcript rows and search field wells. |

### The blue rule

Focus Indigo `#4B4BD8` (`--onr-focus-indigo`), under 3 percent of any screen, allowed only as the Ask/search input focus ring and the live-listen halo. In `theme.css` this is scoped: `:focus-visible` is lime everywhere except inside `.onr-ask`, where it becomes indigo. If the dashboard ever reads as blue, the rule is broken.

### Semantic status colors

Status is a sticker pill plus a mono receipt line. The color carries the verdict.

| Status | Color | Hex | CSS class | Sample |
|---|---|---|---|---|
| VERIFIED | Acid Lime | `#BCF015` | `.onr-status-pill.is-verified` | `[MAYA · VERIFIED VERBATIM · 4:32 PM]` |
| REVIEW | Tangerine | `#FF7A1A` | `.onr-status-pill.is-review` | `[REVIEW · 2 VOICES OVERLAPPED]` |
| SILENCE | Acid Teal | `#3DD6C3` | `.onr-status-pill.is-silence` | `[SILENCE · 6 MIN]`, paused reads `[TAKING A NAP]` |
| UNKNOWN | Hot Magenta | `#E835C1` | `.onr-status-pill.is-unknown` | `[UNKNOWN VOICE · ENROLL?]` |

Enrollment converts magenta to lime: `[ENROLLED · VOICE + FACE LOCKED]`.

## 2. Type scale, mapped to web

iOS points map 1:1 to px on web. Two faces, never three. Display is the character face, body is the neutral grotesk, mono is the metadata register only. No pixel or bitmap type for verbatim quotes.

Font stacks (all in `theme.css` as variables):

- Display: `--onr-font-display`. First token is `"OnRecordDisplay"`, a placeholder for the one bundled display face (condensed grotesk or editorial serif, still to be chosen). Until it is bundled, the stack falls through: `"Arial Narrow"`, `"HelveticaNeue-CondensedBold"`, `-apple-system`, `"SF Pro"`, `"New York"`, `"Segoe UI"`, Roboto, sans-serif.
- Body: `--onr-font-body`. `-apple-system`, `"SF Pro"`, `"Segoe UI"`, Roboto, `"Helvetica Neue"`, Arial, sans-serif.
- Micro: `--onr-font-mono`. `ui-monospace`, `"SF Mono"`, `"Cascadia Mono"`, Menlo, Consolas, monospace.

| Style | iOS size | Web size | Class | Usage |
|---|---|---|---|---|
| Display | 44 to 64 pt | `clamp(2.75rem, 6vw, 4rem)` | `.onr-display` | Route headers ("Every word, kept."), hero statements. Black weight, 0.95 line height, uppercase. Cropped by screen edges is a feature. |
| Headline | 28 to 34 pt | `clamp(1.75rem, 3.4vw, 2.125rem)` | `.onr-headline` | Section headers, speaker names, supporting answer lines. |
| Title | 20 to 22 pt | `1.3125rem` | `.onr-title` | Card titles, conversation titles ("Kitchen, Tuesday"). Semibold, 1.1 line height. |
| Body | 15 to 17 pt | `1rem` | `.onr-body` | Transcript text, all readable copy. 1.35 line height. Quiet and small so the display type sings. |
| Caption | 12 to 13 pt | `0.8125rem` | `.onr-caption` | Secondary metadata in sentence case, card footnotes. |
| Micro-label | 10 to 11 pt | `0.6875rem` | `.onr-micro` | Bracketed uppercase tracked mono (0.09em). The metadata voice. |

The loud/quiet ratio is the point: display type is enormous, body stays small and quiet.

### The bracketed mono micro-label style

All memory metadata in uppercase tracked mono, always bracketed. The label grammar: `[WHO · WHAT · WHEN · WHERE]`. One label per line, never stacked as sentences. On dark fields the label prints in Warm Gray; on Warm Paper it prints in Warm Gray Deep.

Real samples (use these, no lorem ipsum):

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

### The oversized-quote treatment (the product's signature)

When OnRecord answers, the verbatim quote is the biggest thing on screen: `.onr-quote-hero`, 28 to 40 px bold with tight leading, in the clean grotesk (never the display face). The key phrase gets `.onr-sweep`: a lime bar scaling in behind the exact words, like someone marked it live as it arrives. Bone on dark fields, Ink on paper.

Sample quote hero on Espresso Night:

"THE BUDGET IS CAPPED AT FORTY THOUSAND. NO EXCEPTIONS."

with the marker sweep under "forty thousand", then the receipt line:

`[MAYA · VERIFIED VERBATIM · TUE 4:32 PM · KITCHEN]`

## 3. Per-tab color chapters, mapped to dashboard routes

The iOS tabs become dashboard routes. Each route owns its own full-bleed field. Navigation reads as color change, not tab-bar highlight.

| Dashboard route | Chapter | Field | Accent owner | Type |
|---|---|---|---|---|
| `/` Live | Capture | Olive Night `#22251C` (`.onr-field-olive`) | Acid Lime for the record button and verified receipts; Tangerine for REVIEW flags | Bone body, Warm Gray mono labels, ink-on-lime record button |
| `/people` People | People | Deep Plum `#2A1428` (`.onr-field-plum`) | Hot Magenta for UNKNOWN speakers and enrollment; Tangerine for one heat sticker per speaker card | Bone body, Warm Gray mono labels |
| `/search` Search | Recall | Espresso Night `#201616` (`.onr-field-espresso`) | Acid Lime for answer receipts and the marker sweep; Focus Indigo `#4B4BD8` as the input focus ring and listen halo (the only blue, rationed under 3%) | Bone body, Warm Gray mono labels |
| `/status` Status | Day | Warm Paper `#F4F2ED` (`.onr-field-paper`) | Acid Lime for verified receipts (as stickers with dark outlines); Tangerine for REVIEW flags | Ink body, Warm Gray Deep mono labels |

Rationale for the mapping: the dashboard's Live view is the capture surface, so it takes the capture field. Search is where OnRecord answers from memory, which is the Ask tab's job on iOS, so it takes the recall field and the blue focus exception. Status is operational and day-facing, so it takes the day field. The floating pill nav tints per chapter; Part 2 specifies it.

## 4. Component notes

These translate the iOS components to web markup and classes. All classes exist in `theme.css`.

### QuoteCard

A raised card on the chapter field. A status pill pinned to its top edge, the quote hero inside, one lime marker sweep on the key phrase, one mono receipt line beneath. Heat never exceeds one sticker per card.

```html
<article class="onr-card onr-quote-card">
  <span class="onr-status-pill is-verified">Verified</span>
  <p class="onr-quote-hero">
    Maya said the budget is <span class="onr-sweep">forty-two thousand</span>, not thirty.
  </p>
  <p class="onr-micro onr-receipt">[MAYA · VERIFIED VERBATIM · 4:32 PM]</p>
</article>
```

Real sample copy: "Maya said the budget is forty-two thousand, not thirty." with `[MAYA · VERIFIED VERBATIM · 4:32 PM]` underneath. A REVIEW variant: "Pranav mentioned the roof deck" with `[REVIEW · 2 VOICES OVERLAPPED]` and a tangerine pill reading Review.

### SpeakerSticker

The speaker's name in the display face on a loud fill: magenta for unknown, tangerine for heat, lime for verified-enrolled. 2px Ink outline, hard offset shadow (`3px 3px 0` Ink), tilted about 3 degrees, pasted overlapping the photo's corner. On web the sticker absolutely positions over a corner of the rectangular speaker photo (hard edges, thin gutters, no circular avatars).

```html
<figure class="onr-speaker">
  <img class="onr-photo" src="maya.jpg" alt="Maya">
  <figcaption class="onr-speaker-sticker is-verified">Maya</figcaption>
</figure>
<p class="onr-micro">[MAYA · ENROLLED 14 OCT · VOICE + FACE]</p>
```

An UNKNOWN speaker card shows magenta art from the brand set behind the sticker with the label `[UNKNOWN VOICE · ENROLL?]`. Never a gray silhouette. After enrollment the sticker converts to lime: `[ENROLLED 14 OCT · SAY HER NAME TO TAG HER]`.

### StatusChip

The verdict sticker. Semibold, uppercase, 2px Ink outline, pill radius, hard offset shadow, tilted about 1.5 degrees. Status pills are not mono labels: the pill carries the verdict in color, the mono receipt line beneath carries the proof. One per card maximum.

```html
<span class="onr-status-pill is-review">Review</span>
<p class="onr-micro">[REVIEW · LOW CONFIDENCE · 6:12 PM]</p>
```

The paused/listening state is a teal pill reading `[TAKING A NAP]` semantics ("Taking a nap"), quiet by design.

## 5. Motion notes

CSS equivalents of the iOS motion language. The signature ease is `--onr-ease: cubic-bezier(0.625, 0.05, 0, 1)`. Use it on every branded transition. Durations: fast 120ms (press feedback), base 240ms (hovers, reveals), slow 480ms (chapter changes), sweep 600ms (marker sweep).

- **Press feedback:** the record button and pills scale to 0.92 on `:active` with the fast duration (`.onr-record-button` does this). Part 2 specifies the 0.92 press spring for the CTA pair; this is the CSS equivalent.
- **Marker sweep:** `.onr-sweep::before` scales from 0 to 1 on the X axis with `transform-origin: left`, 600ms, the signature ease. Reads as a highlighter pulled across the exact words.
- **Marquee ticker:** `.onr-ticker` with an inner `.onr-ticker-track` animating `translateX(-50%)` over 30s linear, infinite. Duplicate the track content in markup for a seamless loop. Sample: `MAYA SAID THE BUDGET IS $300 ✦ PRANAV MENTIONED THE ROOF DECK ✦ LEXIA READING MODE IS ON THE FEATURE BRANCH ✦` in mono caps on a lime strip.
- **Reveals:** masked reveals and the sticker-ceremony choreography are Part 2's domain. On web, gate reveals behind `IntersectionObserver` and reuse the base/slow durations with the signature ease. Always honor `prefers-reduced-motion`: `theme.css` already disables the sweep and ticker under that media query.

## 6. Imagery on web

The 9 generated brand images live in `~/workspace/bay-valley-hacks/bayvalleyhacks/design-assets/`. Placement mirrors the iOS spec:

- `onrecord-hero-01.png`: marketing hero and the Ask/Search route backdrop. Giant display type knocks out over it in Bone.
- `onrecord-hero-02.png`: onboarding finale and the review/loading moment behind the quote reveal.
- `onrecord-empty-state-03.png`: the nothing-captured-yet state, paired with `[NO MEMORIES FOUND. YET.]`. Empty states get art, not gray illustrations.
- `onrecord-onboarding-04.png`: the enrollment ceremony backdrop. The glowing core sits behind the captured face still during "say her name to tag her."
- `onrecord-card-memory-vortex-05.png`: memory vortex cards on the Live route, e.g. the "one week ago" recap card.
- `onrecord-card-portrait-fragments-06.png`: People route section art behind the speaker rail.
- `onrecord-dashboard-3d-07.png`: the stats/weekly-recap chapter art: "You captured 41 conversations this week."
- `onrecord-tile-glass-09.png`: small tile crops inside transcript rows and the People grid.
- `onr-tile-waveform-08.png` (waveform tile art): the playback header and waveform hero tiles.

Treatment rules on web: rectangular crops with hard edges and thin gutters (`img { object-fit: cover }`); tiles get one consistent 12px radius with the 2px Ink outline (`.onr-tile`) so art reads as a sticker, not a photo. Film grain lives only on the generated art, baked per asset, never as a full-screen overlay over app UI. Where art is busy behind type, raise the 40 percent Espresso Night scrim from the bottom (`.onr-scrim`), never a full-bleed white scrim. Art can sit inline inside giant headlines (a small rounded crop of the rings art between words, e.g. "EVERY [art] WORD, KEPT."), and a tiny waveform tile can sit inline inside an answer headline where the speaker's name appears. Pixel-art or thick-outline stickers (quote marks, waveform glyphs, tiny glasses icon) paste overlapping art corners, tilted 2 to 4 degrees.

Speaker photos vs abstract art: speaker cards get rectangular photos with one warm grade applied to all captures. Abstract art is the realm of memory (empty states, recaps, stats, chapter headers), never a substitute for a speaker's face.

## 7. Voice and sample copy

The voice: playful, honest, belonging-first. Identity before features. Real samples for the dashboard:

- Hero eyebrow: `[CRITERION FOR ENTRY: OWN A PAIR OF GLASSES]`
- Hero: "The least forgetful app you'll ever wear."
- Subline: "A LIVING ARCHIVE OF YOUR CONVERSATIONS"
- Identity line: "Rememberers, welcome home."
- Search placeholder: "Ask what anyone said. We kept the receipts."
- Weekly recap: "You captured 41 conversations this week. Here is what mattered."
- Empty search: `[NO MEMORIES FOUND. YET.]`
- Dead end: "Nothing here. Your glasses were off, or the room was boring."
- Copy toast: "Quote copied" (2 seconds)
- CTA pair: pill "Ask about Maya" plus the lime satellite circle with the arrow. On a quote card: "Play the moment" with the satellite carrying the arrow.

No lorem ipsum anywhere. Every screen ships with real words in this voice. Corners never sit empty: microcopy, mono labels, and small stickers live in margins. One centered card on a blank screen is barren, not minimal.

## 8. Adoption

`theme.css` sits at `dashboard/theme.css`. It is plain CSS with no framework dependency and is Tailwind v4 safe. To use it:

1. Add `@import "./theme.css";` to `app/globals.css`, after the `@import "tailwindcss";` line.
2. Wrap a route in its chapter class (`.onr-field-olive`, `.onr-field-plum`, `.onr-field-espresso`, `.onr-field-paper`) or put `.onr-dark` on `<body>` for the capture chapter default.
3. Apply `.onr-*` type and component classes in markup. No existing markup restyles until you do.

Open decisions left for later workers: the bundled display face (`"OnRecordDisplay"` placeholder in the font stack), the floating pill nav and CTA satellite pair (Part 2), and the enrollment ceremony choreography. Nothing new should introduce a third typeface, a new hue, or a generic modal.
