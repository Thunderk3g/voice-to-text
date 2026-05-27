"use client";

import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";

export function SearchBar({
  initial = "",
  placeholder = "Search...",
  onSubmit,
}: {
  initial?: string;
  placeholder?: string;
  onSubmit: (q: string) => void;
}): JSX.Element {
  const [value, setValue] = useState(initial);
  function handle(e: FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    onSubmit(value.trim());
  }
  return (
    <form onSubmit={handle} className="relative w-full">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        className="input pl-9"
      />
    </form>
  );
}

export default SearchBar;
