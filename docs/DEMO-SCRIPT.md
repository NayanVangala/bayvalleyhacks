# OnRecord Demo Script

Total time: 3 minutes
Performer: Nayan Vangala, solo
Setup on stage: Nayan wearing Ray-Ban Meta Display glasses. Laptop open, visible to judges. Laptop shows glasses POV feed and an empty Postgres table. Phone on table, face up, Slack open. Prompt card in hand for Judge 1.

Rule for the whole demo: speak the lines as written. Do not improvise numbers. Do not add claims.

---

## 0:00 to 0:20 HOOK

Stage direction: Stand center. Look at judges, not the laptop. Glasses POV is live on the laptop behind you. Postgres table is empty and visible.

Nayan says:
"Findr was visual memory, what I saw. This is conversation intelligence, who said what. And I can prove it with you, live, in 3 minutes."

Stage direction: Pause one beat. Turn slightly so judges can see both the glasses and the laptop screen.

---

## 0:20 to 0:40 RECRUIT

Stage direction: Walk to Judge 1. Hand Judge 1 the prompt card. Do not read the card out loud. Do not show the card to the camera or to Judge 2.

Nayan says:
"You be Maya for 30 seconds. The card has 3 facts. Say them in your own words when we talk."

Stage direction: Step back to your mark. Give Judge 1 a second to read the card. Nod once when they look up.

Note: The 3 facts on the card are community garden, a nursery is donating tomato seedlings, still need $300 for soil, planting Saturday November 7th. Judges hear these for the first time in the conversation beat. Do not preview them here.

---

## 0:40 to 1:00 LIVE ENROLLMENT

Stage direction: Face Judge 1. Start enrollment from the dashboard. Judge 1 faces the glasses directly, about an arm length away, good light on face.

Nayan says:
"Maya, look at the glasses. Say your name and one sentence, any sentence."

Judge 1 says: Their name as Maya plus one sentence, in their own words.

Stage direction: Hold still. Let the capture finish. Speaker card appears on the lens and on the dashboard.

On screen: SPEAKER ENROLLED: MAYA

Nayan says:
"Twenty seconds. The glasses now hold a face print and a voice print for Maya, stored locally on this laptop. No cloud, and she can ask me to delete both, any time."

Stage direction: Point once at the speaker card on the dashboard. Do not touch the keyboard yet.

Tech note for performer only, do not say: Face print is ArcFace. Voice print is ECAPA. Both prints go to ChromaDB on the laptop.

---

## 1:00 to 1:30 THE CONVERSATION

Stage direction: Start the session recording. Keep it as a normal conversation. Eye contact with Judge 1. Judge 2 should be watching the laptop.

Nayan says:
"Maya, tell me about your project."

Judge 1 as Maya says, in their own words, from the card:
- A community garden project.
- A nursery is donating tomato seedlings.
- Still need $300 for soil.
- Planting is Saturday November 7th.

Stage direction: Let Maya finish. Ask no follow up questions. Let a short silence land so the segment closes cleanly.

On laptop: Transcript fills in as the conversation happens. Speaker label shows MAYA where face and voice agree.

Nayan says nothing during this beat except the opening question. Let the transcript be the visual.

---

## 1:30 to 1:50 STORE AND GATE

Stage direction: Glance at laptop. Wait for transcription to finish. faster-whisper is running locally, do not rush this beat.

On laptop: A row lands in Postgres with speaker, text, and timestamp. One low confidence fragment is visibly flagged as REVIEW.

Nayan says:
"Anything under the confidence threshold gets flagged, never guessed into the record."

Stage direction: Point at the flagged REVIEW fragment. Then point at the clean stored row.

Nayan continues, short:
"Flagged means excluded from answers until a human clears it."

---

## 1:50 to 2:40 THE QUERY

Stage direction: This is the payoff. Slow down. Face judges. Ask the question out loud, clearly, toward the glasses mic.

Nayan asks out loud:
"What did Maya say about the budget?"

Stage direction: Wait for the MCP query to return. Answer renders on the dashboard and on the lens.

On screen: Answer with her verbatim quote "$300 for soil" plus the timestamp for that segment.

Stage direction: Click the citation in the answer.

On laptop: The exact Postgres row highlights. The verbatim words and timestamp line up.

Nayan says:
"That is not the model remembering. That is the memory quoting her, with the receipt."

Stage direction: Pause. Let judges read the quote, the timestamp, and the highlighted row at the same time. Do not add another example unless a judge asks.

Backup if time remains in this beat: Point at the timestamp and say:
"Same quote, same timestamp, same row. Nothing paraphrased."

---

## 2:40 to 3:00 SLACK DIGEST AND CLOSE

Stage direction: Listen for the phone buzz. Pick up the phone only after it buzzes, or point at it on the table.

On phone: Slack digest lands. Participants, key quotes, open items. Open item includes the $300 for soil and planting Saturday November 7th.

Nayan says:
"That digest posts to Slack after the conversation. Participants, key quotes, open items. No notes typed by me."

Stage direction: Put the phone down. Face judges for the close. No laptop in this last line.

Nayan says:
"Findr remembered what I saw. This remembers who said what."

Stage direction: Stop. Smile. Wait for questions. Do not add a tagline.

End of script at 3:00.

---

## FALLBACK LINES

Use these only if the named failure happens. Say the line as written. Fix the state. Keep moving. Do not explain the bug.

### (a) ID returns Unknown on a segment

When: A segment labels as Unknown instead of MAYA during the conversation or in the stored row.

Nayan says:
"That is the system working as designed. It will not pin a name on a voice it is not sure about. I tap the name here, the correction is logged, and the segment joins her record."

Stage direction: Tap the name chip MAYA for that segment on the dashboard, live, in front of judges. This is tap to tag as the manual correction layer. Confirm the segment updates to MAYA and the correction is logged. Continue to the next beat.

Rule: Face and voice must agree above threshold or the segment stays Unknown. Never argue with an Unknown on stage. Own it.

### (b) Enrollment fails twice

When: Enrollment capture fails two times in a row for Judge 1.

Nayan says:
"Glasses enrollment is not taking here, so I will enroll her on the phone instead. Same face print, same voice print, same store."

Stage direction: Pick up the phone. Enroll Judge 1 with the phone camera and phone mic. Same name, same one sentence. Confirm SPEAKER ENROLLED: MAYA appears on the dashboard. Put the phone down and resume at the conversation beat.

Note in one line only: Input changed, pipeline did not.

### (c) Slack digest is late

When: The digest does not arrive in Slack by 2:50.

Nayan says:
"the digest is generated, Slack is just the mailbox, here is the payload it sends."

Stage direction: Show the digest payload on the dashboard. Read the three parts off the payload: participants, key quotes, open items. Do not wait for Slack. Do not refresh on stage. Close with the normal close line.

### (d) MCP query errors

When: The spoken query returns an error or times out.

Nayan says:
"Query layer hit an error, so I will run the same question as a plain search on the dashboard."

Stage direction: Type the same question, "What did Maya say about the budget?", into the dashboard search. Show the same verbatim quote, "$300 for soil", with its timestamp. Click the citation and highlight the row. Then say the payoff line:
"That is not the model remembering. That is the memory quoting her, with the receipt."

---

## PHONE ONLY SWITCHOVER SCRIPT

Use this the second the glasses die, disconnect, or lose POV. Use it once. Do not debug.

Golden rule line, say this first, verbatim:
"Live demos break, so I built the pipeline to fail gracefully, same backend, alternate input. Watch."

Then do this, target under 30 seconds:
1. Take off the glasses and set them down in view. No apology.
2. Pick up the phone. Open the same session on the phone.
3. Point the phone mic toward the speaker and resume recording in the same session.
4. Confirm on the laptop that the transcript resumes in the same Postgres session, same speaker labels, same timestamps.

Nayan says while switching:
"the glasses are one input; the memory does not care where the audio came from"

Stage direction: Say that line as a feature, not an excuse. Keep eye contact with judges, not the phone settings. If enrollment already finished, do not re enroll. If enrollment has not finished, enroll on the phone per Fallback (b).

Rules for this beat:
- No apologies.
- No debugging on stage.
- No settings menus on screen.
- Same backend, same ChromaDB store, same Postgres record, same query path.

Close the switch with:
"Same session. We keep going."

Then pick up the script at whatever beat you left.

---

## DELIVERY NOTES

- Never say "real time". Say "live" or "as it happens" or name the step that is running.
- Never quote an accuracy percentage. You have not earned a number on stage. If a judge asks for one, see the unmeasured rule below.
- Never say "AI powered" without naming the component. Name it: ArcFace for the face print, ECAPA for the voice print, faster-whisper for transcription, ChromaDB for the prints and transcript chunks, Postgres for the record, MCP server for the query.
- State limits out loud before judges find them. Work these into the beats, do not save them for Q and A:
  - Speakers must enroll first. No enrollment, no name. The label is Unknown.
  - Face and voice must agree above threshold or the segment is Unknown.
  - Low confidence transcription gets flagged REVIEW and is excluded from answers.
  - Prints and records are stored locally on this laptop for this demo.
  - A speaker can ask for both prints to be deleted at any time.
- If asked something unmeasured, say this line first:
  "I have not measured that"
  Then name what you would measure, in one sentence. Example shape: I have not measured that. I would measure it by enrolling 10 speakers, running the same scripted conversation in a noisy room, and counting Unknowns and wrong labels per segment.
- Keep lines speakable. Short sentences. One claim per sentence.
- Point at proof, not at slides. Every claim in this script has a screen to point at: speaker card, flagged REVIEW row, highlighted citation row, Slack payload.
- If a beat runs long, cut words, not proof. Never cut the enrollment, the flagged row, or the citation click.
- Eye contact order: judges first, laptop second, phone last.

---

## WHAT NOT TO SAY

- Do not say "real time".
- Do not say "AI powered" on its own.
- Do not quote any accuracy, precision, or error rate percentage.
- Do not say "the model remembers" or "the AI remembers". Say "the memory quotes" and point at the row.
- Do not say "it works for anyone, anywhere" or "works in any room". You demoed enrolled speakers in this room.
- Do not say "fully private" or "secure". Say "stored locally on this laptop" and "can be deleted on request", which is what you can prove.
- Do not say "seamless", "revolutionary", "game changing", or "cutting edge".
- Do not apologize for a fallback. Fallbacks are design. Say the fallback line and move.
- Do not guess a name for an Unknown segment out loud.
- Do not read the prompt card out loud.
