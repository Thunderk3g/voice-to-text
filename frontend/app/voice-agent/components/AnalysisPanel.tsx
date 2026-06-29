"use client";

import useSWR from "swr";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { type Intent, type CallDetailResponse, type Violation, INTENT_LABEL, INTENT_COLOR } from "@/lib/types";
import { Card } from "@/components/Card";
import { Badge } from "@/components/Badge";
import { IntentBadge } from "@/components/IntentBadge";
import { LoadingBlock } from "@/components/Spinner";

type AnalysisTab = "sentiment" | "intent" | "pii" | "keywords" | "quality" | "summary" | "lead_rating" | "disposition";

interface AnalysisPanelProps {
  callId: string;
  tab: AnalysisTab;
  isDone: boolean;
}

export function AnalysisPanel({
  callId,
  tab,
  isDone,
}: AnalysisPanelProps): JSX.Element {
  // Unified endpoint: GET /calls/{id}/detail returns all analysis data
  const { data, error, isLoading } = useSWR<CallDetailResponse>(
    isDone ? `/calls/${callId}/detail` : null,
  );

  if (!isDone) {
    return (
      <Card>
        <div className="flex items-start gap-3">
          <AlertCircle className="h-4 w-4 text-warn-400 mt-0.5 shrink-0" />
          <p className="text-sm text-ink-500">
            Analysis will be available once processing is complete.
          </p>
        </div>
      </Card>
    );
  }

  if (isLoading) {
    return (
      <Card>
        <LoadingBlock label="Loading analysis..." />
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <div className="flex items-start gap-3">
          <AlertCircle className="h-4 w-4 text-warn-400 mt-0.5 shrink-0" />
          <p className="text-sm text-ink-500">
            Failed to load analysis: {error.message}
          </p>
        </div>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <div className="flex items-start gap-3">
          <AlertCircle className="h-4 w-4 text-warn-400 mt-0.5 shrink-0" />
          <p className="text-sm text-ink-500">No analysis data available.</p>
        </div>
      </Card>
    );
  }

  return (
    <>
      {tab === "sentiment" && <SentimentTab sentiment={data.sentiment} />}
      {tab === "intent" && <IntentTab riskScore={data.risk_score} violations={data.violations} />}
      {tab === "pii" && <PIITab violations={data.violations} violationCount={data.violation_count} />}
      {tab === "keywords" && <KeywordsTab violationCount={data.violation_count} />}
      {tab === "quality" && <QualityTab riskScore={data.risk_score} />}
      {tab === "summary" && <SummaryTab summary={data.summary} />}
      {tab === "lead_rating" && <LeadRatingTab riskScore={data.risk_score} />}
      {tab === "disposition" && <DispositionTab riskLevel={data.risk_level} violations={data.violations} />}
    </>
  );
}

// ============================================================================
// Sentiment Tab
// ============================================================================
interface SentimentBreakdown {
  negative: number;
  neutral: number;
  positive: number;
}

function SentimentTab({ sentiment }: { sentiment?: SentimentBreakdown }): JSX.Element {
  if (!sentiment) {
    return (
      <Card>
        <LoadingBlock label="Analyzing sentiment..." />
      </Card>
    );
  }

  const total = sentiment.positive + sentiment.neutral + sentiment.negative;
  const positive = total > 0 ? (sentiment.positive / total) * 100 : 0;
  const neutral = total > 0 ? (sentiment.neutral / total) * 100 : 0;
  const negative = total > 0 ? (sentiment.negative / total) * 100 : 0;

  const overallSentiment =
    positive > neutral && positive > negative
      ? "positive"
      : negative > neutral && negative > positive
        ? "negative"
        : "neutral";

  const sentimentColor = {
    positive: "#6FD195",
    neutral: "#EFC368",
    negative: "#F4837F",
  }[overallSentiment];

  return (
    <Card>
      <div className="flex flex-col gap-4">
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4">
          <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
            Overall Sentiment
          </div>
          <div
            className="text-2xl font-semibold capitalize"
            style={{ color: sentimentColor }}
          >
            {overallSentiment}
          </div>
        </div>

        {/* Sentiment Distribution */}
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4">
            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
              Positive
            </div>
            <div className="text-xl font-semibold text-green-500">
              {positive.toFixed(0)}%
            </div>
            <div className="text-xs text-ink-500 mt-1">{sentiment.positive.toFixed(1)} points</div>
          </div>

          <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4">
            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
              Neutral
            </div>
            <div className="text-xl font-semibold text-yellow-500">
              {neutral.toFixed(0)}%
            </div>
            <div className="text-xs text-ink-500 mt-1">{sentiment.neutral.toFixed(1)} points</div>
          </div>

          <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4">
            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
              Negative
            </div>
            <div className="text-xl font-semibold text-red-500">
              {negative.toFixed(0)}%
            </div>
            <div className="text-xs text-ink-500 mt-1">{sentiment.negative.toFixed(1)} points</div>
          </div>
        </div>
      </div>
    </Card>
  );
}

// ============================================================================
// Intent Tab
// ============================================================================
interface IntentTabProps {
  riskScore?: number;
  violations?: Violation[];
}

function IntentTab({ riskScore, violations }: IntentTabProps): JSX.Element {
  return (
    <Card>
      <div className="flex flex-col gap-4">
        {/* Risk Score Display */}
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4">
          <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
            Risk Assessment
          </div>
          <div className="text-2xl font-semibold" style={{ color: getRiskColor(riskScore ?? 50) }}>
            {riskScore ?? 50}%
          </div>
          <div className="text-xs text-ink-500 mt-1">Risk Score</div>
        </div>

        {/* Violations */}
        {violations && violations.length > 0 && (
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
              Detected Violations
            </div>
            <div className="space-y-2">
              {violations.slice(0, 3).map((v, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-ink-200 bg-ink-100/50 p-3"
                >
                  <div className="flex items-start justify-between mb-1">
                    <span className="text-sm font-semibold text-ink-800">{v.title}</span>
                    <Badge>{v.severity}</Badge>
                  </div>
                  <p className="text-xs text-ink-600 italic">&quot;{v.quote}&quot;</p>
                  {v.note && <p className="text-xs text-ink-500 mt-1">{v.note}</p>}
                </div>
              ))}
            </div>
          </div>
        )}

        {!violations || violations.length === 0 && (
          <div className="flex items-center gap-2 text-sm text-ok-400">
            <CheckCircle2 className="h-4 w-4" />
            <span>No violations detected</span>
          </div>
        )}
      </div>
    </Card>
  );
}

function getRiskColor(score: number): string {
  if (score >= 66) return "#F4837F";
  if (score >= 33) return "#EFC368";
  return "#6FD195";
}

// ============================================================================
// PII Tab
// ============================================================================
interface PIITabProps {
  violations?: Violation[];
  violationCount?: number;
}

function PIITab({ violations, violationCount }: PIITabProps): JSX.Element {
  const piiViolations = violations?.filter(v => v.title.toLowerCase().includes("pii") || v.title.toLowerCase().includes("sensitive")) ?? [];
  const hasPII = violationCount !== undefined && violationCount > 0;

  return (
    <Card>
      <div className="flex flex-col gap-4">
        {/* PII Summary */}
        <div className="flex items-center gap-3">
          {hasPII ? (
            <AlertCircle className="h-5 w-5 text-warn-400" />
          ) : (
            <CheckCircle2 className="h-5 w-5 text-ok-400" />
          )}
          <div>
            <div className="font-semibold text-ink-900">
              {hasPII ? "Sensitive Data Detected" : "No Sensitive Data Detected"}
            </div>
            <div className="text-xs text-ink-500">
              {violationCount ?? 0} issue{violationCount !== 1 ? "s" : ""} found
            </div>
          </div>
        </div>

        {/* PII Violations */}
        {piiViolations.length > 0 && (
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
              Sensitive Data Issues
            </div>
            <div className="space-y-2">
              {piiViolations.map((v, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-ink-200 bg-ink-100/50 p-3"
                >
                  <div className="flex items-start justify-between mb-1">
                    <span className="text-sm font-semibold text-ink-800">{v.title}</span>
                    <Badge>{v.severity}</Badge>
                  </div>
                  <p className="text-xs text-ink-600 italic">&quot;{v.quote}&quot;</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

// ============================================================================
// Keywords Tab
// ============================================================================
interface KeywordsTabProps {
  violationCount?: number;
}

function KeywordsTab({ violationCount }: KeywordsTabProps): JSX.Element {
  return (
    <Card>
      <div className="flex flex-col gap-4">
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4">
          <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
            Keyword Analysis
          </div>
          <p className="text-sm text-ink-700">
            Keywords extracted from call questions and context analysis. Total issues flagged: {violationCount ?? 0}
          </p>
        </div>

        {/* Placeholder: Keywords data will be loaded from analysis endpoint */}
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4 text-center">
          <p className="text-xs text-ink-500">
            Keyword analysis available through the /analysis endpoint
          </p>
        </div>
      </div>
    </Card>
  );
}

// ============================================================================
// Quality Tab
// ============================================================================
interface QualityTabProps {
  riskScore?: number;
}

function QualityTab({ riskScore }: QualityTabProps): JSX.Element {
  const qualityScore = riskScore !== undefined ? 100 - riskScore : 50;

  const getQualityColor = (score: number) => {
    if (score >= 80) return "#6FD195";
    if (score >= 60) return "#EFC368";
    return "#F4837F";
  };

  return (
    <Card>
      <div className="flex flex-col gap-4">
        {/* Overall Quality Score */}
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4">
          <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
            Overall Quality Score
          </div>
          <div
            className="text-4xl font-semibold"
            style={{ color: getQualityColor(qualityScore) }}
          >
            {qualityScore}
          </div>
          <div className="mt-2 text-xs text-ink-500">out of 100</div>
        </div>

        {/* Quality Factors */}
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-3">
            Quality Factors
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between rounded-lg border border-ink-200 bg-ink-100/50 p-3">
              <div>
                <div className="font-semibold text-sm text-ink-800">Risk Assessment</div>
                <div className="text-xs text-ink-500 mt-0.5">Call risk level</div>
              </div>
              <div
                className="font-semibold text-sm"
                style={{ color: getQualityColor(100 - (riskScore ?? 50)) }}
              >
                {100 - (riskScore ?? 50)}
              </div>
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}

// ============================================================================
// Summary Tab
// ============================================================================
interface SummaryTabProps {
  summary?: string | null;
}

function SummaryTab({ summary }: SummaryTabProps): JSX.Element {
  return (
    <Card>
      <div className="flex flex-col gap-4">
        {/* Call Summary */}
        {summary ? (
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
              Call Summary
            </div>
            <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4 text-sm leading-relaxed text-ink-700">
              {summary}
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4 text-center">
            <p className="text-xs text-ink-500">No summary available yet</p>
          </div>
        )}
      </div>
    </Card>
  );
}

// ============================================================================
// Lead Rating Tab
// ============================================================================
interface LeadRatingTabProps {
  riskScore?: number;
}

function LeadRatingTab({ riskScore }: LeadRatingTabProps): JSX.Element {
  const leadScore = riskScore !== undefined ? 100 - riskScore : 50;
  const qualityColor =
    leadScore >= 66 ? "#6FD195" : leadScore >= 33 ? "#EFC368" : "#F4837F";
  const quality = leadScore >= 66 ? "high" : leadScore >= 33 ? "medium" : "low";

  return (
    <Card>
      <div className="flex flex-col gap-4">
        {/* Lead Score */}
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-4">
          <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
            Lead Score
          </div>
          <div
            className="text-4xl font-semibold"
            style={{ color: qualityColor }}
          >
            {leadScore}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <div
              className="inline-block px-2.5 py-0.5 rounded-full font-mono text-[10px] font-medium uppercase tracking-[0.08em]"
              style={{
                backgroundColor: `${qualityColor}1a`,
                color: qualityColor,
                border: `1px solid ${qualityColor}3d`,
              }}
            >
              {quality} quality
            </div>
          </div>
        </div>

        {/* Information */}
        <div className="rounded-lg border border-ink-200 bg-ink-100/50 p-3 text-sm text-ink-700">
          <p>
            Lead quality is assessed based on call risk score and overall engagement metrics.
          </p>
        </div>
      </div>
    </Card>
  );
}

// ============================================================================
// Disposition Tab
// ============================================================================
interface DispositionTabProps {
  riskLevel?: "LOW" | "MEDIUM" | "HIGH";
  violations?: Violation[];
}

function DispositionTab({ riskLevel, violations }: DispositionTabProps): JSX.Element {
  const priorityColor =
    riskLevel === "HIGH" ? "#F4837F" : riskLevel === "MEDIUM" ? "#EFC368" : "#6FD195";

  const needsFollowUp = riskLevel === "HIGH" || riskLevel === "MEDIUM";

  return (
    <Card>
      <div className="flex flex-col gap-4">
        {/* Risk Level */}
        <div className="rounded-lg border-2 p-3" style={{ borderColor: priorityColor }}>
          <div className="font-mono text-[10px] uppercase tracking-[0.14em] mb-2" style={{ color: priorityColor }}>
            Risk Level
          </div>
          <div className="text-lg font-semibold capitalize" style={{ color: priorityColor }}>
            {riskLevel ?? "MEDIUM"}
          </div>
        </div>

        {/* Followup Required */}
        <div className="flex items-center gap-2">
          {needsFollowUp ? (
            <>
              <AlertCircle className="h-5 w-5 text-warn-400" />
              <span className="text-sm font-semibold text-ink-800">
                Follow-up Required
              </span>
            </>
          ) : (
            <>
              <CheckCircle2 className="h-5 w-5 text-ok-400" />
              <span className="text-sm font-semibold text-ink-800">
                No Follow-up Needed
              </span>
            </>
          )}
        </div>

        {/* Violations Summary */}
        {violations && violations.length > 0 && (
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-400 mb-2">
              Actions Required
            </div>
            <div className="space-y-2">
              {violations.slice(0, 2).map((v, i) => (
                <div
                  key={i}
                  className="flex gap-2 text-sm text-ink-700 rounded-lg border border-ink-200 bg-ink-100/50 p-3"
                >
                  <span className="font-semibold text-brand-500 shrink-0">
                    {i + 1}
                  </span>
                  <span>{v.title}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

export default AnalysisPanel;
