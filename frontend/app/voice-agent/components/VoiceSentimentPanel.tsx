"use client";

import { Card } from "@/components/Card";

export interface SentimentDistribution {
  negative: number;
  neutral: number;
  positive: number;
}

export interface VoiceSentimentPanelProps {
  distribution?: SentimentDistribution;
  overall?: "positive" | "neutral" | "negative";
  isLoading?: boolean;
}

const SENTIMENT_COLORS = {
  positive: "#6FD195",
  neutral: "#EFC368",
  negative: "#F4837F",
};

/**
 * Simple SVG donut chart (90px) with sentiment distribution
 */
function SentimentDonut({
  distribution,
}: {
  distribution: SentimentDistribution;
}): JSX.Element {
  const total = distribution.positive + distribution.neutral + distribution.negative;
  if (total === 0) {
    return (
      <svg width="90" height="90" viewBox="0 0 90 90" aria-label="Sentiment distribution" role="img">
        <title>Sentiment Distribution Chart</title>
        <circle cx="45" cy="45" r="35" fill="none" stroke="#262B38" strokeWidth="8" />
      </svg>
    );
  }

  const positivePercent = distribution.positive / total;
  const neutralPercent = distribution.neutral / total;
  const negativePercent = distribution.negative / total;

  const radius = 35;
  const circumference = 2 * Math.PI * radius;

  // Calculate starting points for each segment
  let currentPercent = 0;

  const segmentStartDashoffset = (percent: number, start: number) => {
    return circumference * (1 - start);
  };

  return (
    <svg width="90" height="90" viewBox="0 0 90 90" className="transform -rotate-90" aria-label="Sentiment distribution" role="img">
      <title>Sentiment Distribution Chart</title>
      {/* Positive */}
      <circle
        cx="45"
        cy="45"
        r={radius}
        fill="none"
        stroke={SENTIMENT_COLORS.positive}
        strokeWidth="8"
        strokeDasharray={circumference * positivePercent}
        strokeDashoffset={circumference * (1 - currentPercent)}
        strokeLinecap="round"
      >
        <title>{`Positive: ${(positivePercent * 100).toFixed(1)}%`}</title>
      </circle>

      {/* Neutral */}
      {(() => {
        const start = currentPercent;
        currentPercent += neutralPercent;
        return (
          <circle
            cx="45"
            cy="45"
            r={radius}
            fill="none"
            stroke={SENTIMENT_COLORS.neutral}
            strokeWidth="8"
            strokeDasharray={circumference * neutralPercent}
            strokeDashoffset={circumference * (1 - start)}
            strokeLinecap="round"
          >
            <title>{`Neutral: ${(neutralPercent * 100).toFixed(1)}%`}</title>
          </circle>
        );
      })()}

      {/* Negative */}
      {(() => {
        const start = currentPercent;
        return (
          <circle
            cx="45"
            cy="45"
            r={radius}
            fill="none"
            stroke={SENTIMENT_COLORS.negative}
            strokeWidth="8"
            strokeDasharray={circumference * negativePercent}
            strokeDashoffset={circumference * (1 - start)}
            strokeLinecap="round"
          >
            <title>{`Negative: ${(negativePercent * 100).toFixed(1)}%`}</title>
          </circle>
        );
      })()}

      {/* Center dot */}
      <circle cx="45" cy="45" r="8" fill="#262B38" />
    </svg>
  );
}

export function VoiceSentimentPanel({
  distribution,
  overall,
  isLoading,
}: VoiceSentimentPanelProps): JSX.Element {
  if (isLoading || !distribution) {
    return (
      <Card title="Sentiment Analysis">
        <div className="flex h-32 items-center justify-center text-sm text-ink-400">
          Analyzing...
        </div>
      </Card>
    );
  }

  const total =
    distribution.positive + distribution.neutral + distribution.negative;
  const positivePercent = total > 0 ? (distribution.positive / total) * 100 : 0;
  const neutralPercent = total > 0 ? (distribution.neutral / total) * 100 : 0;
  const negativePercent = total > 0 ? (distribution.negative / total) * 100 : 0;

  return (
    <Card title="Sentiment Analysis">
      <div className="flex flex-col gap-4">
        {/* Chart + Legend */}
        <div className="flex items-center gap-6">
          {/* Donut */}
          <div className="shrink-0">
            <SentimentDonut distribution={distribution} />
          </div>

          {/* Legend */}
          <div className="flex flex-col gap-3">
            {/* Positive */}
            <div className="flex items-center gap-2">
              <div
                className="h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: SENTIMENT_COLORS.positive }}
              />
              <div>
                <div className="text-xs font-medium text-ink-700">Positive</div>
                <div className="font-mono text-[9px] text-ink-500">
                  {distribution.positive} ({positivePercent.toFixed(0)}%)
                </div>
              </div>
            </div>

            {/* Neutral */}
            <div className="flex items-center gap-2">
              <div
                className="h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: SENTIMENT_COLORS.neutral }}
              />
              <div>
                <div className="text-xs font-medium text-ink-700">Neutral</div>
                <div className="font-mono text-[9px] text-ink-500">
                  {distribution.neutral} ({neutralPercent.toFixed(0)}%)
                </div>
              </div>
            </div>

            {/* Negative */}
            <div className="flex items-center gap-2">
              <div
                className="h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: SENTIMENT_COLORS.negative }}
              />
              <div>
                <div className="text-xs font-medium text-ink-700">Negative</div>
                <div className="font-mono text-[9px] text-ink-500">
                  {distribution.negative} ({negativePercent.toFixed(0)}%)
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Overall Sentiment */}
        {overall && (
          <div className="border-t border-ink-200 pt-4">
            <div className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-400">
              Overall Sentiment
            </div>
            <div
              className="mt-2 inline-block rounded-full px-3 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.08em]"
              style={{
                backgroundColor: `${SENTIMENT_COLORS[overall]}26`,
                color: SENTIMENT_COLORS[overall],
                border: `1px solid ${SENTIMENT_COLORS[overall]}4d`,
              }}
            >
              {overall}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

export default VoiceSentimentPanel;
