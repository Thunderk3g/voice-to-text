import { LANGUAGE_LABEL, type Language } from "@/lib/types";
import { Badge } from "./Badge";

const LANG_COLOR: Record<Language, string> = {
  hi: "#0e7490",
  en: "#1f5cf5",
  "hi-en": "#9333ea",
  "hi-roman": "#0891b2",
  ta: "#15803d",
  te: "#b45309",
  other: "#64748b",
};

export function LanguageBadge({ language }: { language: Language }): JSX.Element {
  return <Badge color={LANG_COLOR[language]}>{LANGUAGE_LABEL[language]}</Badge>;
}

export default LanguageBadge;
