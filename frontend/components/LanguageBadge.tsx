import { LANGUAGE_LABEL, type Language } from "@/lib/types";
import { Badge } from "./Badge";

// Tuned for legibility on dark surfaces.
const LANG_COLOR: Record<Language, string> = {
  hi: "#56C2D6",
  en: "#7BA7F7",
  "hi-en": "#C89BF2",
  "hi-roman": "#5BCBE3",
  ta: "#7BD489",
  te: "#F2B65C",
  other: "#9BA3AF",
};

export function LanguageBadge({ language }: { language: Language }): JSX.Element {
  return <Badge color={LANG_COLOR[language]}>{LANGUAGE_LABEL[language]}</Badge>;
}

export default LanguageBadge;
