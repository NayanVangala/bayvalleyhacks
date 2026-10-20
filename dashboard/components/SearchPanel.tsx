"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Person, QueryResult } from "@/lib/types";
import { formatTime, listPeople, searchMemory } from "@/lib/api";

export function emptyReasonMessage(reason: string | null): string {
  switch (reason) {
    case "NO_MATCHES":
      return "Nothing in memory matches that yet.";
    case "NO_VERIFIED_SEGMENTS":
      return "Matches exist only in unverified segments, so they are not shown.";
    case "PERSON_NOT_FOUND":
      return "That person is not enrolled.";
    case "AMBIGUOUS_NAME":
      return "That name matches more than one enrolled person. Pick the person in the filter and search again.";
    case "NO_PEOPLE":
      return "Nobody is enrolled yet, so there is no memory to search.";
    case "BACKEND_UNREACHABLE":
      return "The memory server is not reachable right now.";
    case null:
      return "Nothing in memory matches that yet.";
    default:
      // Unknown codes and server detail strings are shown as-is: an
      // empty result must never render as a blank, unexplained panel.
      return `No results (reason: ${reason})`;
  }
}

export default function SearchPanel() {
  const [query, setQuery] = useState("");
  const [personId, setPersonId] = useState("");
  const [people, setPeople] = useState<Person[]>([]);
  const [peopleError, setPeopleError] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const requestIdRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    listPeople()
      .then((items) => {
        if (!cancelled) {
          setPeople(items);
          setPeopleError(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPeople([]);
          setPeopleError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || loading) {
      return;
    }
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setSearched(true);
    try {
      const next = await searchMemory(trimmed, personId || undefined);
      // A slower earlier search must not overwrite a newer result.
      if (requestId === requestIdRef.current) {
        setResult(next);
      }
    } catch {
      if (requestId === requestIdRef.current) {
        setResult({
          status: "EMPTY",
          reason: "BACKEND_UNREACHABLE",
          results: [],
        });
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }

  return (
    <div className="space-y-6">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-2">
          <label htmlFor="memory-query" className="block text-sm font-medium">
            Search memory
          </label>
          <input
            id="memory-query"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="What did someone say?"
            className="w-full rounded border px-3 py-2 text-sm"
          />
        </div>
        <div className="space-y-2">
          <label htmlFor="person-filter" className="block text-sm font-medium">
            Person
          </label>
          <select
            id="person-filter"
            value={personId}
            onChange={(event) => setPersonId(event.target.value)}
            className="w-full rounded border px-3 py-2 text-sm"
          >
            <option value="">Everyone</option>
            {people.map((person) => (
              <option key={person.id} value={person.id}>
                {person.name}
              </option>
            ))}
          </select>
          {peopleError && (
            <p className="text-sm text-gray-600">
              Could not load the enrolled people list from the server, so
              the person filter only offers Everyone.
            </p>
          )}
        </div>
        <button
          type="submit"
          disabled={loading || !query.trim()}
          className="rounded border px-4 py-2 text-sm font-medium disabled:opacity-50"
        >
          {loading ? "Searching..." : "Search"}
        </button>
      </form>

      {!searched && (
        <p className="text-sm text-gray-600">
          Ask what someone said, for example: what did Maya say about the
          budget?
        </p>
      )}

      {loading && <p className="text-sm text-gray-600">Searching...</p>}

      {!loading && result && result.status === "EMPTY" && (
        <div className="rounded border p-4">
          <h2 className="font-medium">No results</h2>
          <p className="mt-1 text-sm text-gray-600">
            {emptyReasonMessage(result.reason)}
          </p>
        </div>
      )}

      {!loading && result && result.status === "OK" && (
        <div className="space-y-4">
          {result.results.map((hit) => (
            <div key={hit.segment_id} className="rounded border p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">{hit.speaker_name}</span>
                <span className="text-sm text-gray-600">
                  {formatTime(hit.start_iso) ||
                    formatTime(hit.end_iso) ||
                    "time unavailable"}
                </span>
              </div>
              <blockquote className="mt-2">{hit.text}</blockquote>
              {typeof hit.score === "number" && (
                <p className="mt-2 text-sm text-gray-600">
                  match {hit.score.toFixed(2)}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
