import type { Person } from "@/lib/types";
import { formatClock } from "@/lib/api";

type SpeakerCardsProps = {
  people: Person[];
  activeSpeakerId: string | null;
  talkTimeByPerson?: Record<string, number>;
};

export default function SpeakerCards({
  people,
  activeSpeakerId,
  talkTimeByPerson,
}: SpeakerCardsProps) {
  return (
    <section>
      <h2 className="text-base font-semibold">Speakers</h2>
      {people.length === 0 ? (
        <p>No one enrolled yet.</p>
      ) : (
        <div className="space-y-2">
          {people.map((person) => {
            const talk =
              talkTimeByPerson?.[person.id] ?? person.talk_time_s ?? 0;
            return (
              <div key={person.id} className="border rounded p-3">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{person.name}</span>
                  {person.id === activeSpeakerId ? (
                    <span className="border rounded px-1 text-xs">
                      Speaking now
                    </span>
                  ) : null}
                </div>
                <p>Talk time: {formatClock(talk)}</p>
                {person.segment_count !== undefined ? (
                  <p>Segments: {person.segment_count}</p>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
