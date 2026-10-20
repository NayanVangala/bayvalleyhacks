import SearchPanel from "@/components/SearchPanel";

export default function SearchPage() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 p-6">
      <h1 className="text-2xl font-semibold">Memory search</h1>
      <SearchPanel />
    </main>
  );
}
