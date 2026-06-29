"use client";

import { Card } from "@/components/Card";

export interface RiskMetrics {
  riskScore: number; // 0-1
  riskLevel: "low" | "medium" | "high"; // derived from score
  confidence: number; // 0-1
  tone: string; // e.g., "professional", "frustrated"
  violations: number;
}

export interface VoiceRiskPanelProps {
  metrics?: RiskMetrics;
  isLoading?: boolean;
}

function getRiskColor(score: number): string {
  if (score < 0.33) return "#6FD195"; // Green for low
  if (score < 0.66) return "#EFC368"; // Amber for medium
  return "#F4837F"; // Red for high
}

function getRiskLabel(score: number): string {
  if (score < 0.33) return "Low";
  if (score < 0.66) return "Medium";
  return "High";
}

/**
 * Circular progress ring SVG (110px viewport)
 */
function RiskRing({ score }: { score: number }): JSX.Element {
  const radius = 40;
  const circumference = 2 * Math.PI * radius;
  const percent = Math.min(1, Math.max(0, score));
  const strokeDashoffset = circumference * (1 - percent);
  const color = getRiskColor(score);

  return (
    <svg
      width="110"
      height="110"
      viewBox="0 0 110 110"
      className="transform -rotate-90"
      aria-label={`Risk score is ${Math.round(score * 100)}%`}
      role="img"
    >
      <title>Risk Score: {Math.round(score * 100)}%</title>
      {/* Background ring */}
      <circle
        cx="55"
        cy="55"
        r={radius}
        fill="none"
        stroke="#262B38"
        strokeWidth="6"
      />
      {/* Progress ring */}
      <circle
        cx="55"
        cy="55"
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth="6"
        strokeDasharray={circumference}
        strokeDashoffset={strokeDashoffset}
        strokeLinecap="round"
        style={{
          transition: "stroke-dashoffset 0.35s ease",
        }}
      />
      {/* Center text */}
      <text
        x="55"
        y="50"
        textAnchor="middle"
        fontSize="28"
        fontWeight="600"
        fill={color}
        dominantBaseline="middle"
        fontFamily="system-ui"
      >
        {Math.round(score * 100)}
      </text>
      <text
        x="55"
        y="70"
        textAnchor="middle"
        fontSize="11"
        fill="#9BA3AF"
        dominantBaseline="middle"
        fontFamily="system-ui"
        letterSpacing="1"
      >
        RISK
      </text>
    </svg>
  );
}

export function VoiceRiskPanel({
  metrics,
  isLoading,
}: VoiceRiskPanelProps): JSX.Element {
  if (isLoading || !metrics) {
    return (
      <Card title="Risk Analysis">
        <div className="flex h-32 items-center justify-center text-sm text-ink-400">
          Analyzing...
        </div>
      </Card>
    );
  }

  const riskColor = getRiskColor(metrics.riskScore);
  const riskLabel = getRiskLabel(metrics.riskScore);

  return (
    <Card title="Risk Analysis">
      <div className="flex flex-col gap-4">
        {/* Risk Score Ring + Level */}
        <div className="flex items-center gap-6">
          {/* Ring */}
          <div className="shrink-0">
            <RiskRing score={metrics.riskScore} />
          </div>

          {/* Level & Label */}
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-400">
              Risk Level
            </div>
            <div
              className="mt-1 text-2xl font-semibold capitalize"
              style={{ color: riskColor }}
            >
              {riskLabel}
            </div>
            <div
              className="mt-2 inline-block rounded-full px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.08em]"
              style={{
                backgroundColor: `${riskColor}26`,
                color: riskColor,
                border: `1px solid ${riskColor}4d`,
              }}
            >
              {(metrics.riskScore * 100).toFixed(0)}% score
            </div>
          </div>
        </div>

        {/* Metrics Grid */}
        <div className="grid grid-cols-3 gap-3 border-t border-ink-200 pt-4">
          {/* Confidence */}
          <div>
            <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-400">
              Confidence
            </div>
            <div className="mt-1.5 text-lg font-semibold text-jade-600">
              {(metrics.confidence * 100).toFixed(0)}%
            </div>
          </div>

          {/* Tone */}
          <div>
            <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-400">
              Tone
            </div>
            <div className="mt-1.5 truncate text-lg font-semibold text-brand-600">
              {metrics.tone}
            </div>
          </div>

          {/* Violations */}
          <div>
            <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-400">
              Violations
            </div>
            <div className="mt-1.5 text-lg font-semibold text-warn-500">
              {metrics.violations}
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}

export default VoiceRiskPanel;
