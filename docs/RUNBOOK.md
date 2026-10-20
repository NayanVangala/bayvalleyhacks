# OnRecord: Event Day Runbook

Date: Saturday, Oct 25, 2026
Venue: Mountain House High School, 1090 S Central Pkwy
Doors: 8:00am
Event ends: 10:00pm
Builder: Nayan Vangala, solo

Project in one line: Conversation memory on a borrowed Ray-Ban Meta Display with Neural Band. Glasses capture, iPhone bridges, laptop transcribes and identifies enrolled speakers, dashboard shows who said what, Slack gets the digest.

Pipeline for the day:
Glasses to iPhone bridge (Meta Wearables Device Access Toolkit 1.0) to laptop FastAPI ingest on port 8000 to faster-whisper small.en on laptop to Postgres and ChromaDB (face_prints, voice_prints, transcript_chunks) to MCP server to Next.js dashboard on port 3000 to Slack digest.

Identity rule, Revision 2:
- Speakers enroll once: one photo plus about 10 seconds of voice.
- Enrollment creates an ArcFace face print and an ECAPA voice print in ChromaDB.
- Live segments get a name only when face and voice agree above threshold.
- If they disagree or fall below threshold, the segment is Unknown.
- Never guess a name. Unknown is the correct answer when unsure.
- No QR badges anywhere in this design. No face recognition for unenrolled strangers beyond the Unknown label.

Network rule for the day:
- Primary: laptop hosted WiFi named insight-net.
- Venue WiFi is never trusted for the bridge path.
- Phone only fallback: same session continues on phone mic, target switch under 30 seconds, still on insight-net.

Day rules:
- Hard deadlines in this runbook are hard. Move to the fallback or the next block.
- Config and data changes are not code changes after freeze. Know the difference.
- If it was not surfaced by the end to end test or a rehearsal, it does not get fixed on event day.
- Keep laptop and iPhone on chargers whenever at the table.

---

## 8:00 to 9:00 ARRIVE + SETUP

Goal: Power claimed, network up, glasses verified, table set.

- [ ] Arrive, check in, find table.
- [ ] Claim power first. Power strip down, confirm outlet works.
- [ ] Laptop on charger. iPhone on charger.
- [ ] Start laptop hosted WiFi: insight-net.
- [ ] Join insight-net from laptop and from iPhone. Confirm both stay joined.
- [ ] Glasses out of case. Verify full charge before anything else.
- [ ] Glasses paired and visible to the iPhone bridge.
- [ ] FastAPI ingest up on port 8000. Dashboard up on port 3000.
- [ ] Postgres up. ChromaDB up. Confirm all three collections load: face_prints, voice_prints, transcript_chunks.
- [ ] Table layout set: laptop center, glasses case visible, charging cables tidy, water off the desk edge.
- [ ] Take table layout photo for Devpost. Save it, do not post it yet.
- [ ] Backup video gear ready: phone tripod or laptop screen recorder tested.

HARD CHECKPOINT 9:00: Network decision locked.
Which network carries the bridge today, decided at 9:00, no more debate after that. Default answer is insight-net. Only switch if insight-net failed a direct bridge test before 9:00. Write the decision in phone notes.

If setup is behind:
- Cut in this order: table styling, extra monitors, extra cables.
- Do not cut: power, insight-net test, glasses charge check.

---

## 9:00 to 10:00 FRESH REPO + COMMIT REPLAY

Goal: Fresh public repo live with final code replayed as commits across event hours.

This is a protected hour. No demos, no debugging side quests, no feature talk.

- [ ] Create fresh public repo for event day submission.
- [ ] Replay final code as about 10 commits spread across event hours.
- [ ] Commit order follows build order: scaffold, ingest, transcription, storage, identity, MCP, dashboard, Slack, docs, polish.
- [ ] Each commit message plain and specific. What changed, not why it is clever.
- [ ] Push early. Confirm repo loads on phone browser over data, not just on laptop.
- [ ] Link fresh repo in Devpost draft notes.

If GitHub fights back:
- [ ] Cut to 6 commits: scaffold, backend, identity and storage, dashboard, Slack and MCP, final.
- [ ] If auth or push fails twice, stop, screenshot the error, move on. Fix window is later.
- [ ] Never debug past 10:00. At 10:00 this block ends either way.

Done means: Repo public, code present, commits visible, link saved.

---

## 10:00 to 11:00 END TO END TEST

Goal: Prove the full chain once, or declare fallback by 10:45.

Test chain in order:
Glasses to bridge to whisper to Postgres to dashboard to Slack digest.

- [ ] Start a fresh session.
- [ ] Speak a short test segment wearing the glasses.
- [ ] Confirm audio reaches the iPhone bridge.
- [ ] Confirm audio reaches FastAPI ingest on port 8000.
- [ ] Confirm faster-whisper small.en returns a transcript chunk.
- [ ] Confirm chunk lands in Postgres and in ChromaDB transcript_chunks.
- [ ] Confirm segment appears on the Next.js dashboard on port 3000.
- [ ] Confirm speaker card appears on the lens display.
- [ ] Trigger Slack digest. Confirm digest posts with transcript and speaker labels.
- [ ] Note latency per hop in phone notes: capture to bridge, bridge to ingest, ingest to transcript, transcript to dashboard, dashboard to Slack. Times as observed, no rounding up into claims.
- [ ] Run a second segment with a second enrolled voice if enrollment test data exists. Confirm Unknown path also works for an unenrolled voice.

HARD DEADLINE 10:45: Declare stack WORKING or FALLBACK.
- WORKING means: full chain above passed at least once end to end.
- FALLBACK means: phone only mode on laptop network. Same session, phone mic as capture, rest of chain unchanged.
- Write the declaration and the time in phone notes.
- If FALLBACK, set phone mic up at table now and retest dashboard plus Slack once before 11:00.

If a single hop fails:
- Restart that service only, retest that hop only, then retest full chain once.
- Two full chain failures is fallback. Do not run a third attempt in this block.

---

## 11:00 to 12:00 ENROLLMENT + HARDWARE SOAK

Goal: Real demo speakers enrolled on the real glasses, soak passed, battery rate known.

Enrollment list:
- [ ] Nayan
- [ ] Rehearsal partner 1
- [ ] Rehearsal partner 2

For each speaker:
- [ ] Enroll photo for ArcFace face print.
- [ ] Enroll about 10 seconds of voice for ECAPA voice print.
- [ ] Confirm both prints saved in ChromaDB.
- [ ] Run one live segment. Confirm name shows only when face and voice agree above threshold.
- [ ] Run one mismatch or off camera segment. Confirm result is Unknown, not a guessed name.
- [ ] Verify speaker card on lens: correct name or Unknown, correct transcript snippet.
- [ ] Verify same segment on dashboard with same label.

Hardware soak:
- [ ] Start 20 minute continuous capture soak on the glasses.
- [ ] Normal demo distance, normal speaking volume, normal room light.
- [ ] Note battery level at soak start.
- [ ] Note battery level at soak end. Write drain rate as observed over 20 minutes.
- [ ] Note any dropouts, bridge disconnects, or heat issues in phone notes.
- [ ] End soak. Glasses back in charging case. Case plugged in if possible.

Done means: Three speakers enrolled and verified, soak logged, glasses charging.

---

## 12:00 to 1:00 LUNCH + REST

Goal: Eat, charge, log problems. No demos.

- [ ] Eat lunch away from the laptop if possible.
- [ ] No demos in this block, even if someone asks. Say demos restart after rehearsal.
- [ ] Phone charging the whole block.
- [ ] Laptop charging. Glasses in charging case.
- [ ] Write morning issues list in phone notes: what failed, what was slow, what confused anyone.
- [ ] Sort issues into two piles: must fix for demo, ignore today.
- [ ] Only must fix items go into the 2:00 fix window.
- [ ] Drink water. Walk once.

---

## 1:00 to 2:00 REHEARSAL 1: SOLO, TIMED

Goal: Full 3 minute script runs clean solo. Wording fixed, nothing else.

- [ ] Phone timer set to 3 minutes, visible while rehearsing.
- [ ] Record the full run: phone video or laptop screen capture plus audio.
- [ ] Run order: problem, live capture, enrollment idea in one sentence, named segment, Unknown segment, semantic search query with verbatim quote receipt, Slack digest.
- [ ] Do not stop mid run for small errors. Finish, then note them.
- [ ] Watch playback right away.
- [ ] Fix wording only: shorten long sentences, cut jargon, fix any line that stumbled twice.
- [ ] No code changes in this block. Code issues go on the fix window list.
- [ ] Run once more if first run went over time or missed a step.

HARD: Script LOCKED by 1:45.
After 1:45, wording does not change except to remove a broken line. Print or save locked script where it can be read without unlocking the demo laptop.

---

## 2:00 to 3:00 CONTINGENCY / FIX WINDOW

Goal: Fix only what the end to end test or Rehearsal 1 surfaced.

Allowed:
- [ ] Bugs from the 10:00 end to end test.
- [ ] Bugs or confusion points from Rehearsal 1.
- [ ] Config fixes: ports, network join, Slack target, dashboard session.
- [ ] Data fixes: re enroll a speaker whose print failed verification.

Not allowed:
- No new features.
- No new integrations.
- No model swaps.
- No UI redesign.
- No threshold tuning based on a guess. Tune only if the soak or enrollment test showed a specific wrong label, and retest after the change.

Also in this block:
- [ ] Q and A drills, solo, out loud: What if face and voice disagree. What is stored. What happens on venue WiFi failure. What is phone only fallback. Why Unknown instead of a guess.
- [ ] Glasses charge top up. Glasses in case and charging when not in a test.
- [ ] Retest full chain once after any fix. One pass is enough to close the fix.

Done means: Fix list closed or explicitly dropped, full chain passing, glasses charging.

---

## 3:00 to 4:00 CODE FREEZE + DEVPOST DRAFT

Goal: Code frozen, Devpost drafted, backup video captured.

HARD CODE FREEZE 3:30pm.
- After 3:30pm: config and data only. No code edits, no dependency changes, no rebuilds for style.
- If code breaks after freeze and config cannot fix it, use phone only fallback or the backup video. Do not reopen code.

- [ ] At or before 3:30pm, tag the freeze: v1.0-freeze.
- [ ] Confirm fresh repo shows the freeze tag.
- [ ] Draft all Devpost sections: title, tagline, inspiration, what it does, how it was built, challenges, accomplishments, what was learned, what is next, built with list.
- [ ] Add table layout photo and architecture notes.
- [ ] Keep claims to what was measured today: observed hop times, soak drain as observed, enrollment steps as run. Targets stay labeled as targets.
- [ ] Screen cap backup video: full demo run recorded on laptop, saved locally in two places.
- [ ] Test backup video plays with sound off a copy, not the only copy.

Done means: Freeze tagged, Devpost draft complete enough to submit as is, backup video saved.

---

## 4:00 to 5:00 REHEARSAL 2: LIVE AUDIENCE

Goal: Full demo in front of real people, including a stranger enrollment.

- [ ] Grab 1 to 2 nearby hackers. Ask for 5 minutes, no more.
- [ ] Run the full locked script. Do not explain extra steps mid demo.
- [ ] Include live enrollment of a stranger: photo plus about 10 seconds of voice, then a live named segment.
- [ ] Include one Unknown case so the audience sees the system refuse to guess.
- [ ] Include semantic search with a verbatim quote receipt.
- [ ] Close with Slack digest visible.
- [ ] Ask the audience two questions only: What did you think it did. Where did you get confused.
- [ ] Note confusion points word for word in phone notes.
- [ ] Fix confusion with wording or table signage only. Code is frozen.

If stranger enrollment fails:
- Note it, show Unknown path, move on. Do not debug live in front of the audience.

---

## 5:00 to 6:00 DINNER + REST

Goal: Eat, rest, charge everything. No code.

- [ ] Eat dinner.
- [ ] No code in this block. Laptop closed or on dashboard idle screen.
- [ ] Glasses on charger the whole block.
- [ ] Phone on charger.
- [ ] Check Devpost draft once on phone. Read only, no rewrite spiral.
- [ ] Review judge log format for Block 1: judge number, what they asked, what landed, what confused them.
- [ ] Water, walk, rest eyes.

---

## 6:00 to 7:30 JUDGING BLOCK 1: EXPO

Goal: Every judge gets the full locked demo with full attention.

- [ ] Stay at the table the whole block. No roaming, no side quests.
- [ ] One judge at a time. Full attention on that judge. No phone checks mid demo.
- [ ] Glasses charged and ready at block start. Spare capture path ready: phone mic fallback on insight-net.
- [ ] Run the locked script. Same order every time.
- [ ] If a judge asks to try it, do live enrollment for them if time allows, otherwise show the stranger enrollment result from Rehearsal 2 notes and the Unknown path live.
- [ ] Answer questions plainly. If a number was not measured today, say it is a target or say it was not measured.
- [ ] After each judge leaves, log in phone notes: who they were if known, questions asked, reaction, confusion points, follow up if any.
- [ ] Between judges: glasses back in case to top up, dashboard reset to a clean session, water.

Done means: No judge missed because the table was empty, every reaction logged.

---

## 7:30 to 8:30 DEVPOST SUBMISSION

Goal: Submitted, confirmed, screenshot saved. Perfection is not the goal.

HARD: Submit by 8:00pm regardless of perfection.
- Submit what exists at 8:00pm. A submitted good draft beats a perfect draft that misses the deadline.

- [ ] Fill any Devpost fields still empty from the 3:00 draft.
- [ ] Confirm fresh repo link is correct and public.
- [ ] Confirm video link or backup video note is correct if a video is included.
- [ ] Confirm photos load: table layout, dashboard, speaker card if captured.
- [ ] Submit by 8:00pm.
- [ ] Screenshot the confirmation page.
- [ ] Save screenshot in two places: phone photos and laptop.
- [ ] After submission, only edit Devpost if the form allows it and the edit is a factual fix. No new claims.

---

## 8:30 to 9:30 JUDGING BLOCK 2 / ROAM

Goal: Cover the table, short roam only, stay reachable.

- [ ] Default position is the table. Demos still run in this block if judges or hackers come by.
- [ ] Max 20 minutes away from the table total in this block.
- [ ] If away, laptop stays locked, glasses stay at the table in the case unless a demo is running.
- [ ] Roam with purpose: see two or three projects, thank organizers, find the glasses owner if they are on site and confirm the 9:30 return plan.
- [ ] Back at table by 9:10 at the latest to prep teardown.
- [ ] Keep logging reactions in phone notes for any late demos.

---

## 9:30 to 10:00 TEARDOWN + RETURN GLASSES

Goal: Demos stopped, gear packed, borrowed glasses returned with thanks.

- [ ] Stop demos at 9:30. No last extra demo after 9:30.
- [ ] Stop services cleanly: dashboard, MCP server, ingest, whisper session.
- [ ] Save final data: session export, Slack digest link, judge notes, backup video, Devpost confirmation screenshot.
- [ ] Pack laptop, chargers, cables, power strip. Check under the table.
- [ ] Return borrowed glasses to friend with thanks.
- [ ] Give or send a photo of the glasses working during the demo.
- [ ] Confirm glasses and case are both handed over, not just the glasses.
- [ ] Confirm Devpost screenshot saved in phone photos before leaving.
- [ ] Leave by 10:00pm.

---

## 3 Highest Risk Hours

### 1. 9:00 to 10:00 Replay Hour

Risk: GitHub auth, push errors, or commit ordering eats the morning and pulls focus from the hardware test.
Mitigation: Protected hour with a hard stop at 10:00. Cut to 6 commits if GitHub fights back. Screenshot errors and defer them. The demo does not depend on commit count.

### 2. 10:00 to 11:00 End To End Hour

Risk: One hop in the chain fails and solo debugging loops with no decision.
Mitigation: Hard deadline at 10:45 to declare WORKING or FALLBACK. Fallback is phone only mode on insight-net, tested in the same block. Two failed full chain runs means fallback, no third attempt in this block.

### 3. 6:00 to 7:30 Judging Block 1

Risk: Empty table, flat battery, or a first judge hitting an untested path and setting the tone for the block.
Mitigation: Stay at the table the whole block, glasses topped up between judges, locked script only, backup video ready, phone mic fallback ready on insight-net. Log each judge right after they leave so fixes in wording carry to the next judge.

---

## Honesty Guardrail

If an organizer or judge asks directly whether code was written before the event, answer YES plainly. Precoding was allowed by the organizers. Do not hedge, do not reframe, do not volunteer extra detail beyond the question. Same rule for enrollment: say speakers were enrolled, say the system labels Unknown when face and voice do not agree, and do not invent numbers that were not measured today.
