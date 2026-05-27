import { INTENT_COLOR, INTENT_LABEL, type Intent } from "@/lib/types";
import { Badge } from "./Badge";

export function IntentBadge({ intent }: { intent: Intent }): JSX.Element {
  return <Badge color={INTENT_COLOR[intent]}>{INTENT_LABEL[intent]}</Badge>;
}

export default IntentBadge;
