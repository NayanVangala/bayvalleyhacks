import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "OnRecord Dashboard",
  description: "OnRecord Dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <header className="border-b px-4 py-2 flex gap-4">
          <span className="font-bold">OnRecord Dashboard</span>
          <nav className="flex gap-4">
            <Link href="/" className="underline">
              Live
            </Link>
            <Link href="/people" className="underline">
              People
            </Link>
            <Link href="/search" className="underline">
              Search
            </Link>
            <Link href="/status" className="underline">
              Status
            </Link>
          </nav>
        </header>
        <main className="px-4 py-2">{children}</main>
      </body>
    </html>
  );
}
