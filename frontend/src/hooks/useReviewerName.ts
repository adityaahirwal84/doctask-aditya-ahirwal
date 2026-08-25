import { useEffect, useState } from "react";

const KEY = "doctask.reviewer_name";

export function useReviewerName(): [string, (name: string) => void] {
  const [name, setName] = useState(() => localStorage.getItem(KEY) ?? "");
  useEffect(() => {
    if (name) localStorage.setItem(KEY, name);
  }, [name]);
  return [name, setName];
}
