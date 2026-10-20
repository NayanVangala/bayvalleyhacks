"""Tap-to-tag manual correction layer for OnRecord (Revision 2).

Automatic fusion is the primary attribution path. This module is how a
human fixes or overrides it on stage: the wearer taps a speaker name for
a segment and this service records the change and re-labels the segment.

Audit rule: every manual change is a new row in the corrections table.
Nothing is ever overwritten in the log, and no correction row is ever
updated or deleted by this service. The segment row itself is updated
to show the current speaker, but its raw transcript text is never
touched.

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
        self._store.set_segment_speaker(
            segment_id, person_id, label, ATTR_MANUAL
        )
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
        self._store.set_segment_speaker(
            segment_id, None, "Unknown", ATTR_MANUAL
        )
        return correction

    def history(self, segment_id: str) -> list:
        """Return the correction history for a segment for audit display."""
        return self._store.list_corrections(segment_id)


__all__ = ["CorrectionService"]
