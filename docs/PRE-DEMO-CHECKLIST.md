# OnRecord Pre-Demo Checklist

Run top to bottom. Check each box as you go.

## 1. Night Before (Sat Oct 24)

- [ ] Verify code freeze: git status clean, nothing uncommitted
- [ ] Finish claim audit: every public claim is demo-proven or deleted
- [ ] Log two clean demo runs
- [ ] Write Devpost draft fully, with placeholders for event photos
- [ ] Create fresh public repo locally for the morning replay, do not push yet
- [ ] Put backup zip of the repo and a Postgres dump on a USB stick
- [ ] Put backup demo videos on laptop desktop and phone local storage, findable in 5 seconds, not cloud
- [ ] Save precoding-proof screenshot on the phone
- [ ] Charge laptop to 100%
- [ ] Charge iPhone to 100%
- [ ] Charge glasses in case to 100%
- [ ] Charge power bank to 100%
- [ ] Pack laptop
- [ ] Pack laptop charger
- [ ] Pack iPhone
- [ ] Pack iPhone cable
- [ ] Pack glasses
- [ ] Pack glasses case
- [ ] Pack glasses charging cable
- [ ] Pack Neural Band
- [ ] Pack power bank 20,000 mAh
- [ ] Pack power strip
- [ ] Pack extension cord
- [ ] Pack USB-C hub
- [ ] Pack USB stick
- [ ] Pack printed waiver with parent signature
- [ ] Save Luma registration confirmation screenshot offline
- [ ] Pack printed demo cheat card
- [ ] Pack water
- [ ] Pack snacks
- [ ] Put bag by the door
- [ ] Set alarm for 6:15am
- [ ] Screens off at 10:30pm

## 2. Morning Of (Oct 25, Home)

- [ ] Leave Fremont about 6:45am
- [ ] Make no code changes after leaving the house
- [ ] Set phone to Do Not Disturb except calls from family

## 3. Station Setup (8:00 to 9:00, Venue)

- [ ] Tape down power strip, plug everything in
- [ ] Start Postgres.app
- [ ] Run make run up
- [ ] Check GET /health returns 200
- [ ] Check MCP server lists 5 tools
- [ ] Open dashboard live on :3000
- [ ] Host insight-net on laptop
- [ ] Join insight-net on iPhone
- [ ] Connect bridge
- [ ] Take glasses out of case
- [ ] Verify glasses charge
- [ ] Run one test capture and confirm it lands in Postgres
- [ ] Make network decision by 9:00, insight-net vs hotspot
- [ ] Write network decision on the cheat card

## 4. Warmup (Before Judging)

- [ ] Enroll one test speaker
- [ ] Verify speaker card on lens
- [ ] Verify speaker card on dashboard
- [ ] Run one full warmup demo end to end: conversation, query, Slack digest lands
- [ ] Call E2E verdict by 10:45: working or fallback declared
- [ ] Put glasses back in case to charge between runs
- [ ] Open Slack demo channel on the phone
- [ ] Locate backup video and play-test it muted

## 5. T-10 Before Each Judge

- [ ] Put glasses on face, confirm charged
- [ ] Set dashboard to live view
- [ ] Show Postgres table visible
- [ ] Hold prompt card with 3 facts for the volunteer judge
- [ ] Put phone face-up with Slack open
- [ ] Drink water, rest voice
- [ ] Memorize the fallback line: "Live demos break, so I built the pipeline to fail gracefully, same backend, alternate input. Watch."

## 6. Between Judges

- [ ] Put glasses into case to charge
- [ ] Check battery on laptop
- [ ] Check battery on iPhone
- [ ] Check battery on glasses
- [ ] Jot judge name and one reaction in phone notes for the Devpost accomplishments line
- [ ] Reset live table view to empty
- [ ] Touch no code, config only if a port or key broke

## 7. End of Night

- [ ] Submit Devpost by 8:00pm
- [ ] Save Devpost confirmation screenshot
- [ ] Return glasses to friend with thanks and a photo
- [ ] Run pack-out check against the night-before list so nothing stays at the venue
