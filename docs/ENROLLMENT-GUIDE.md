# Enrollment Guide

## 1. What enrollment is and why

Enrollment gives OnRecord a reference for one speaker so live segments can be named when face and voice agree. Prints are stored locally in Chroma on the laptop, with no cloud copy. No photos of faces are kept as a biometric database beyond the embedding vectors. Stored per person: one face embedding (ArcFace, 512-d), one voice embedding (ECAPA, 192-d), name, and enrolled-at timestamp. The person row lives in Postgres, the two prints live in ChromaDB.

## 2. The 20 second script

Total time is about 20 seconds. Do it in this order.

1. Position: speaker stands at arm's length from the glasses wearer, faces the lens, plain background if possible.
2. Speaker looks at the camera and stays still.
3. Speaker says the standard line: "Hi, my name is [NAME]."
4. Speaker adds one natural sentence in the same take. Suggested line: "I am working on a community garden project this fall." Or let them describe their project in one sentence.
5. Use normal volume. No whispering, no shouting.
6. System captures a still photo for the face print plus about 10 seconds of voice for the voice print.
7. Check the take against Section 4. If the first take fails the quality check, do it a second time. Do not enroll a bad take.

The dashboard speaker card and the `get_person_profile` MCP tool read from this enrollment. Manual tap-to-tag is a correction layer only, not a substitute for a good enrollment.

## 3. Consent

California is a two-party consent state. Read this out loud before enrolling anyone. Do not skip it.

Nayan reads verbatim:

> "These glasses record conversations for this demo. Your face print and voice print stay on this laptop only. They are not sent to the cloud. You can ask me to delete them at any time, and I will delete them on the spot."

Wait for a clear yes before starting capture.

## 4. Quality checklist

Check every box before saving. If any box fails, redo the take.

FACE:
- [ ] Exactly one face in frame
- [ ] Face centered and at least a fist-width tall in frame
- [ ] Even light on the face, no strong backlight or window behind
- [ ] No hat brim shadow over the eyes
- [ ] Glasses wearer holds still for 2 seconds

VOICE:
- [ ] No other person talking during the sample
- [ ] No music or loud fan in the room
- [ ] Normal conversational volume
- [ ] Full sentences, not single words
- [ ] At least 8 seconds of actual speech
- [ ] No hand over mouth

## 5. Verification step

This step is mandatory. Do it right after enrolling, before moving on.

1. Speaker says one test line: "The budget meeting moved to Friday."
2. Nayan checks the dashboard speaker card and the lens card.
3. Both must show the correct name.

Naming rule for reference: a segment gets a name only when face match and voice match agree on the same person. Face threshold is 0.45 cosine in config. Voice threshold is 0.75 cosine in config. Otherwise the segment is Unknown.

If the test lands Unknown, re-enroll once and test again. If it still fails, note the person as tap-to-tag only for the demo and move on.

## 6. Re-enroll triggers

Re-enroll if any of these apply:

- Repeated Unknowns for an enrolled person
- Voice is hoarse, sick, or very different from enrollment
- Big lighting change between the enrollment room and the demo table
- Face changed, for example hat on or off, or a haircut
- Thresholds were changed in config
- Enrollment was done in a noisy room

After re-enrolling, run the verification step in Section 5 again.

## 7. Deletion on request

If a person asks for deletion, do it on the spot.

1. Delete the person row in Postgres.
2. Delete both Chroma prints, the face print and the voice print.
3. Open the dashboard people list and confirm the person is gone.

Offer this out loud during the demo. It is a feature, not a footnote.

## 8. Demo-day notes

- Enroll demo speakers on the Oct 23 or 24 setup day at home, not on event morning.
- Keep enrollment to 2 to 3 demo speakers plus judges.
- Event-day live enrollment of a judge is a scripted demo beat. It uses the same steps in this guide, including consent and verification.
- Do not add extra speakers on event day unless there is time to enroll and verify them properly.
