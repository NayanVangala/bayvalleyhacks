"use client";

import { useCallback, useEffect, useState } from "react";
import { getHealth, getStats } from "@/lib/api";
import type { Stats } from "@/lib/types";

type HealthResult = Awaited<ReturnType<typeof getHealth>>;

const KNOWN_COLLECTIONS = [
  "face_prints",
  "voice_prints",
  "transcript_chunks",
] as const;

function formatValue(value: unknown): string {
  if (value !== null && typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function formatCount(value: number | undefined): string {
  return value === undefined ? "unknown" : String(value);
}

export default function StatusPage() {
  const [health, setHealth] = useState<HealthResult | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    // Without this, Refresh fetched in the background with no visible
    // feedback and the button looked dead while the request was slow.
    setLoading(true);
    try {
      // Settled separately: getStats throws when the API surface is
      // down, and that must not also blank out a health result that
      // succeeded (or the other way around).
      const [healthOutcome, statsOutcome] = await Promise.allSettled([
        getHealth(),
        getStats(),
      ]);
      setHealth(
        healthOutcome.status === "fulfilled"
          ? healthOutcome.value
          : { ok: false, data: null },
      );
      setStats(statsOutcome.status === "fulfilled" ? statsOutcome.value : null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const healthData =
    health !== null &&
    health.data !== null &&
    typeof health.data === "object"
      ? (health.data as Record<string, unknown>)
      : null;

  const collections: Record<string, number> = stats?.collections ?? {};
  const extraCollections = Object.keys(collections).filter(
    (key) => !(KNOWN_COLLECTIONS as readonly string[]).includes(key),
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-bold">System status</h1>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="border rounded px-3 py-1 disabled:opacity-50"
        >
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {loading ? (
        <p>Loading...</p>
      ) : (
        <>
          <section className="flex flex-col gap-2">
            <p>Server: {health?.ok ? "reachable" : "unreachable"}</p>
            {healthData && (
              <dl className="border px-3 py-2">
                {Object.entries(healthData).map(([key, value]) => (
                  <div key={key} className="flex gap-2">
                    <dt className="font-medium">{key}</dt>
                    <dd>{formatValue(value)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>

          <section className="flex flex-col gap-2">
            <h2 className="font-bold">Counts</h2>
            <table className="border-collapse border w-fit">
              <thead>
                <tr>
                  <th className="border px-3 py-1 text-left">Metric</th>
                  <th className="border px-3 py-1 text-left">Count</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="border px-3 py-1">People</td>
                  <td className="border px-3 py-1">
                    {formatCount(stats?.people)}
                  </td>
                </tr>
                <tr>
                  <td className="border px-3 py-1">Sessions</td>
                  <td className="border px-3 py-1">
                    {formatCount(stats?.sessions)}
                  </td>
                </tr>
                <tr>
                  <td className="border px-3 py-1">Segments</td>
                  <td className="border px-3 py-1">
                    {formatCount(stats?.segments)}
                  </td>
                </tr>
              </tbody>
            </table>
          </section>

          <section className="flex flex-col gap-2">
            <h2 className="font-bold">Collections</h2>
            <table className="border-collapse border w-fit">
              <thead>
                <tr>
                  <th className="border px-3 py-1 text-left">Collection</th>
                  <th className="border px-3 py-1 text-left">Count</th>
                </tr>
              </thead>
              <tbody>
                {KNOWN_COLLECTIONS.map((name) => (
                  <tr key={name}>
                    <td className="border px-3 py-1">{name}</td>
                    <td className="border px-3 py-1">
                      {formatCount(collections[name])}
                    </td>
                  </tr>
                ))}
                {extraCollections.map((name) => (
                  <tr key={name}>
                    <td className="border px-3 py-1">{name}</td>
                    <td className="border px-3 py-1">
                      {formatCount(collections[name])}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
