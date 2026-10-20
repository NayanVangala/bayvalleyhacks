"""Tap-to-tag manual correction layer for OnRecord (Revision 2).

Automatic fusion is the primary attribution path. This module is how a
human fixes or overrides it on stage: the wearer taps a speaker name for
a segment and this service records the change and re-labels the segment.

Audit rule: every manual change is a new row in the corrections table.
Nothing is ever overwritten in the log, and no correction row is ever
updated or deleted by this service. The segment row itself is updated
to show the current speaker, but its raw transcript text is never
touched, and neither are the original face and voice scores: those
scores are the evidence for the attribution the human just overrode,
so a correction carries them through unchanged instead of erasing
them. The applied update is also verified, so a correction never
reports success while the segment still shows the old speaker.

No em-dashes in this codebase. Keep comments plain.
"""

from __future__ import annotations

from .interfaces import ATTR_MANUAL


class CorrectionService:
    """Manual speaker assignment on top of a Store.

    The store is expected to provide:
      get_segment(segment_id) -> dict or None
      get_person(person_id) -> dict or None
      add_correction(segment_id, old_person_id, new_person_id, operator_id) -> dict
      set_segment_speaker(segment_id, person_id, label, source,
                          face_score=None, voice_score=None) -> dict
      list_corrections(segment_id=None) -> list of dicts
    """

    def __init__(self, store) -> None:
        self._store = store

    def _apply_speaker(self, segment, segment_id, person_id, label) -> None:
        """Write the segment speaker, preserving its original scores.

        The store update rewrites face_score and voice_score with
        whatever it is given, and its defaults are None, so the
        segment's existing scores are passed through explicitly: a
        manual correction changes who is credited, never the recorded
        evidence for the automatic attribution it replaced.

        Raises RuntimeError if the write does not land (the store
        returned no row and a re-read does not show the new speaker).
        The correction row has already been appended at that point and
        stays in the audit log, as an append-only record should.
        """
        updated = self._store.set_segment_speaker(
            segment_id,
            person_id,
            label,
            ATTR_MANUAL,
            face_score=segment.get("face_score"),
            voice_score=segment.get("voice_score"),
        )
        if updated is not None:
            return
        current = self._store.get_segment(segment_id)
        if current is not None and current.get("speaker_person_id") == person_id:
            return
        raise RuntimeError(
            f"correction recorded but segment update failed: {segment_id}"
        )

    def assign_speaker(
        self, segment_id: str, person_id: str, operator_id: str = "wearer"
    ) -> dict:
        """Assign a person as the speaker of a segment.

        Raises KeyError if the segment or the person does not exist.
        Appends a correction row recording the old and new speaker, then
        updates the segment to the new speaker with source ATTR_MANUAL
        and the person name as the label. Returns the correction dict.
        """
        segment = self._store.get_segment(segment_id)
        if segment is None:
            raise KeyError(f"segment not found: {segment_id}")
        person = self._store.get_person(person_id)
        if person is None:
            raise KeyError(f"person not found: {person_id}")

        old_person_id = segment.get("speaker_person_id")
        label = person.get("name") or person.get("display_name") or person_id

        correction = self._store.add_correction(
            segment_id, old_person_id, person_id, operator_id
        )
        self._apply_speaker(segment, segment_id, person_id, label)
        return correction

    def unassign_speaker(
        self, segment_id: str, operator_id: str = "wearer"
    ) -> dict:
        """Clear the speaker of a segment back to Unknown.

        Raises KeyError if the segment does not exist. Appends a
        correction row with the new speaker set to None, then updates
        the segment to no speaker, label "Unknown", and source
        ATTR_MANUAL. Returns the correction dict.
        """
        segment = self._store.get_segment(segment_id)
        if segment is None:
            raise KeyError(f"segment not found: {segment_id}")

        old_person_id = segment.get("speaker_person_id")

        correction = self._store.add_correction(
            segment_id, old_person_id, None, operator_id
        )
        self._apply_speaker(segment, segment_id, None, "Unknown")
        return correction

    def history(self, segment_id: str) -> list:
        """Return the correction history for a segment for audit display."""
        return self._store.list_corrections(segment_id)


__all__ = ["CorrectionService"]
