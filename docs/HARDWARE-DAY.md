# HARDWARE-DAY.md

OnRecord hardware plan. Demo glasses are borrowed. Ray-Ban Meta Display with Neural Band. Setup at home on home WiFi. Never do first time setup at the venue.

Key facts:
- Capture: Meta Wearables Device Access Toolkit (DAT) 1.0 via iPhone bridge app forked from Findr
- Pin SDK 1.0.1 or later. 1.0.0 froze camera frames
- Glasses mic path through toolkit is experimental. Phone mic is the deterministic audio path. Glasses stay on photo and display duty unless the mic proves stable
- Neural Band: pinch and swipe only. No custom gestures. No raw EMG
- Glasses hold roughly 3 to 4 hours of use per charge. Charge only while docked in case. Never while worn
- Shutter sound and LED cannot be suppressed. Pitch that as a privacy feature

## 1. BEFORE PICKUP (message the friend)

- [ ] Confirm full day loan Oct 25
- [ ] Confirm setup day Oct 23 or Oct 24 at Nayan's home
- [ ] Friend backs up their own captures first
- [ ] Agree handover contents:
  - [ ] Glasses
  - [ ] Charging case
  - [ ] Cable
  - [ ] Neural Band
  - [ ] Neural Band charger if separate
- [ ] Agree friend unpairs from their phone at handover
  - [ ] Unpair in Meta AI app
  - [ ] Forget in Bluetooth settings

## 2. EARLY DE-RISK (do on Nayan's own phone BEFORE pickup day)

- [ ] Test Developer Mode toggle appears
  - [ ] Open Meta AI app
  - [ ] Settings, then App Info
  - [ ] Tap version number 5 times
  - [ ] Confirm Developer Mode toggle appears
  - [ ] Note: known bug, toggle never appears on some iOS builds. Test early on Nayan's own phone
- [ ] Create Meta Wearables developer account
- [ ] Create org in Meta developer console
- [ ] Create project in Meta developer console
  - [ ] Note: console may have an age gate (16+). Fallback is a parent's account for the console project
- [ ] Run Meta's CameraAccess sample end to end
  - [ ] Run on any available pair, or the simulator path the toolkit provides
  - [ ] Confirm photo capture works in the sample
- [ ] Decide transport and freeze it
  - [ ] Bluetooth first
  - [ ] Laptop hosted WiFi for photos
  - [ ] Write down the frozen choice. Do not change after this

## 3. HANDOVER + SETUP (at home, on home WiFi)

- [ ] Friend unpairs glasses on their phone
  - [ ] Unpair in Meta AI app
  - [ ] Forget in Bluetooth settings
- [ ] Factory reset if pairing fights back
- [ ] Pair glasses to Nayan's iPhone
- [ ] Run firmware update
- [ ] Enable Developer Mode on Nayan's phone
  - [ ] Meta AI app, Settings, App Info, tap version number 5 times
- [ ] Register bridge app once via Meta AI callback
- [ ] Verify app logs isDevMode == true before any session starts
- [ ] Install frozen bridge build
- [ ] Re-sign note: free Apple ID signing expires in about 7 days
  - [ ] Install final build Oct 24, not earlier in the week

## 4. FIRST-HOUR VERIFICATION

Run in this order. Check off each. Any failure stops the line. Fix it, or phone-only plan becomes primary.

- [ ] (1) isDevMode == true logged
- [ ] (2) Session starts, photo returns
  - [ ] Measure photo trigger to bytes latency at each resolution
  - [ ] Lock the setting used for enrollment photos
  - [ ] Write down locked resolution
- [ ] (3) Known-signal test on phone mic path
  - [ ] Record digits and claps
  - [ ] Transcript pitch and content correct
  - [ ] No drops over 60s
- [ ] (4) Neural Band test
  - [ ] Pinch fires the tag action
  - [ ] Swipe fires re-query
  - [ ] Fit is snug
  - [ ] False trigger test: type, wave, grip objects for 2 minutes, zero tags
- [ ] (5) Weak-link walk test
  - [ ] Walk to far room
  - [ ] Session survives, or fails over cleanly
- [ ] (6) Hinge-close test
  - [ ] Fold glasses, session tears down
  - [ ] Reopen glasses, restart works
- [ ] (7) Kill-switch drill
  - [ ] Kill glasses mid conversation
  - [ ] Phone-only mode live in under 30 seconds
  - [ ] Same session continues
- [ ] (8) Enroll one test speaker end to end
  - [ ] Verify speaker card on the lens

## 5. BATTERY DISCIPLINE

- [ ] Glasses live in plugged-in case whenever not on a face
- [ ] Charge to 100% the night before
- [ ] Event day: glasses come out for demo bursts only
- [ ] Top up at lunch
- [ ] Top up at 5pm
- [ ] Disable Meta AI auto-updates after Oct 23
- [ ] Freeze app builds after Oct 24 install
- [ ] iPhone on 20,000 mAh power bank, tethered
- [ ] iPhone Low Power Mode off

## 6. EVENT-DAY HARDWARE RULES

- [ ] Friend's phone Bluetooth OFF, or the phone stays home
  - [ ] Reason: friend's phone must not steal the connection
- [ ] Nobody else pairs to the glasses
- [ ] If glasses die mid demo, switch to phone-only mode
  - [ ] Phone-only switch is a rehearsed feature, not an apology

## 7. RETURN CHECKLIST (Oct 25 night or Oct 26)

- [ ] Back up any of the friend's remaining data, untouched
- [ ] Unpair from Nayan's phone only if the friend wants it
- [ ] Hand back all items:
  - [ ] Glasses
  - [ ] Case
  - [ ] Cable
  - [ ] Neural Band
  - [ ] Neural Band charger if separate
- [ ] Send thank-you
- [ ] Send photo of the glasses working at the event
