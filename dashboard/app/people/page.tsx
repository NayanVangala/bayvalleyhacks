"use client";

import { useEffect, useState } from "react";
import EnrollmentForm from "@/components/EnrollmentForm";
import type { Person } from "@/lib/types";
import { formatClock, formatTime, listPeople } from "@/lib/api";

export default function PeoplePage() {
  const [people, setPeople] = useState<Person[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listPeople()
      .then((items) => {
        if (!cancelled) {
          setPeople(items);
          setLoadError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setPeople([]);
          setLoadError(
            err instanceof Error && err.message
              ? err.message
              : "Could not load people from the server.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function handleEnrolled(person: Person) {
    setPeople((prev) => {
      const without = prev.filter((item) => item.id !== person.id);
      return [person, ...without];
    });
  }

  return (
    <div className="space-y-3">
      <h1>People</h1>
      <EnrollmentForm onEnrolled={handleEnrolled} />
      {loadError && (
        <p role="alert">
          Could not load the enrolled people list: {loadError}. The list
          below may be empty because the server is down, not because
          nobody is enrolled.
        </p>
      )}
      {loaded && !loadError && people.length === 0 && (
        <p>No one enrolled yet. Add the first person above.</p>
      )}
      {people.length > 0 && (
        <div className="space-y-3">
          {people.map((person) => (
            <div
              key={person.id}
              className="border rounded px-2 py-1 space-y-3"
            >
              <p>{person.name}</p>
              {person.enrolled_at && (
                <p>Enrolled {formatTime(person.enrolled_at)}</p>
              )}
              {person.segment_count !== undefined && (
                <p>Segments: {person.segment_count}</p>
              )}
              {person.talk_time_s !== undefined && (
                <p>Talk time: {formatClock(person.talk_time_s)}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
