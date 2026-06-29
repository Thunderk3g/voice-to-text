// Hand-written TS mirrors of app/models/schemas.py + enums.py.
// Field names kept snake_case to match the Python contract.

// ============================================================================
// Enums (StrEnum values)
// ============================================================================
export type UUIDString = string;

export const Speaker = {
  AGENT: "AGENT",
  CUSTOMER: "CUSTOMER",
  UNKNOWN: "UNKNOWN",
} as const;
export type Speaker = (typeof Speaker)[keyof typeof Speaker];

export const Language = {
  HINDI: "hi",
  ENGLISH: "en",
  HINGLISH: "hi-en",
  ROMAN_HINDI: "hi-roman",
  TAMIL: "ta",
  TELUGU: "te",
  OTHER: "other",
} as const;
export type Language = (typeof Language)[keyof typeof Language];

export const ALL_LANGUAGES: Language[] = [
  Language.HINDI,
  Language.ENGLISH,
  Language.HINGLISH,
  Language.ROMAN_HINDI,
  Language.TAMIL,
  Language.TELUGU,
  Language.OTHER,
];

export const Intent = {
  POLICY_DETAILS: "policy_details",
  PREMIUM_PAYMENT: "premium_payment",
  CLAIM_PROCESS: "claim_process",
  CLAIM_REJECTION: "claim_rejection",
  RENEWAL: "renewal",
  NOMINEE_UPDATE: "nominee_update",
  DOCUMENT_REQUEST: "document_request",
  CANCELLATION: "cancellation",
  MATURITY_BENEFIT: "maturity_benefit",
  HEALTH_COVERAGE: "health_coverage",
  EXCLUSIONS: "exclusions",
  AGENT_COMPLAINT: "agent_complaint",
  GRIEVANCE: "grievance",
  OTHER_INSURANCE: "other_insurance",
  OTHER: "other",
} as const;
export type Intent = (typeof Intent)[keyof typeof Intent];

export const ALL_INTENTS: Intent[] = [
  Intent.POLICY_DETAILS,
  Intent.PREMIUM_PAYMENT,
  Intent.CLAIM_PROCESS,
  Intent.CLAIM_REJECTION,
  Intent.RENEWAL,
  Intent.NOMINEE_UPDATE,
  Intent.DOCUMENT_REQUEST,
  Intent.CANCELLATION,
  Intent.MATURITY_BENEFIT,
  Intent.HEALTH_COVERAGE,
  Intent.EXCLUSIONS,
  Intent.AGENT_COMPLAINT,
  Intent.GRIEVANCE,
  Intent.OTHER_INSURANCE,
  Intent.OTHER,
];

export const CallStatus = {
  PENDING: "pending",
  STT_RUNNING: "stt_running",
  STT_DONE: "stt_done",
  DIARIZATION_RUNNING: "diarization_running",
  DIARIZATION_DONE: "diarization_done",
  EXTRACTION_RUNNING: "extraction_running",
  EXTRACTION_DONE: "extraction_done",
  EMBEDDING_DONE: "embedding_done",
  CLUSTERED: "clustered",
  FAILED: "failed",
} as const;
export type CallStatus = (typeof CallStatus)[keyof typeof CallStatus];

export const QuestionType = {
  QUESTION: "question",
  COMPLAINT: "complaint",
  DOUBT: "doubt",
  INTENT: "intent",
} as const;
export type QuestionType = (typeof QuestionType)[keyof typeof QuestionType];

export const FeedbackAction = {
  MERGE_CLUSTERS: "merge_clusters",
  SPLIT_CLUSTER: "split_cluster",
  RELABEL_INTENT: "relabel_intent",
  REGENERATE_FAQ: "regenerate_faq",
  REASSIGN_QUESTION: "reassign_question",
} as const;
export type FeedbackAction = (typeof FeedbackAction)[keyof typeof FeedbackAction];

export const EdgeRelation = {
  LEADS_TO: "leads_to",
  RELATED_TO: "related_to",
  SUBSET_OF: "subset_of",
  OPPOSES: "opposes",
  CAUSED_BY: "caused_by",
  CO_OCCURS: "co_occurs",
} as const;
export type EdgeRelation = (typeof EdgeRelation)[keyof typeof EdgeRelation];

// ============================================================================
// Calls
// ============================================================================
export interface CallMetadata {
  agent_id?: string | null;
  customer_id?: string | null;
  campaign?: string | null;
  channel?: string | null;
  stt_provider?: string | null;
  received_at?: string | null;
  extra?: Record<string, unknown>;
}

export interface CallCreate {
  source_uri: string;
  is_transcript?: boolean;
  metadata?: CallMetadata;
}

export interface UploadResponse {
  call_id: string;
  source_uri: string;
  is_transcript: boolean;
}

export interface CallRead {
  id: UUIDString;
  source_uri: string;
  is_transcript: boolean;
  status: CallStatus;
  detected_language?: Language | null;
  duration_seconds?: number | null;
  created_at: string;
  updated_at: string;
  metadata: CallMetadata;
  langsmith_trace_id?: string | null;
  error_message?: string | null;
}

// ============================================================================
// Utterances
// ============================================================================
export interface UtteranceSchema {
  id?: UUIDString | null;
  call_id: UUIDString;
  speaker: Speaker;
  speaker_id?: string | null;
  start_ts: number;
  end_ts: number;
  text: string;
  language: Language;
  confidence: number;
  words?: Array<Record<string, unknown>> | null;
}

// ============================================================================
// Extracted Questions
// ============================================================================
export interface ExtractedQuestion {
  id?: UUIDString | null;
  call_id: UUIDString;
  utterance_id?: UUIDString | null;
  raw_text: string;
  normalized_text: string;
  english_gloss?: string | null;
  question_type: QuestionType;
  intent: Intent;
  secondary_intents: Intent[];
  language: Language;
  confidence: number;
  extracted_at?: string | null;
}

export interface ExtractionResult {
  call_id: UUIDString;
  questions: ExtractedQuestion[];
  used_model: string;
  raw_response?: string | null;
}

// ============================================================================
// Embeddings
// ============================================================================
export interface EmbeddingRecord {
  id?: UUIDString | null;
  question_id: UUIDString;
  model: string;
  dim: number;
  vector: number[];
  created_at?: string | null;
}

// ============================================================================
// Clusters
// ============================================================================
export interface ClusterRecord {
  id: UUIDString;
  label?: string | null;
  canonical_question?: string | null;
  centroid: number[];
  dominant_language: Language;
  dominant_intents: Intent[];
  frequency: number;
  last_updated: string;
  representative_question_ids: UUIDString[];
  is_stable: boolean;
}

export interface ClusterMember {
  cluster_id: UUIDString;
  question_id: UUIDString;
  similarity: number;
  assigned_at: string;
}

export interface ClusterDetail {
  cluster: ClusterRecord;
  canonical_faq?: CanonicalFAQ | null;
  examples: ExtractedQuestion[];
  intent_distribution: Partial<Record<Intent, number>>;
  language_distribution: Partial<Record<Language, number>>;
}

// ============================================================================
// Canonical FAQ
// ============================================================================
export interface CanonicalFAQ {
  id: UUIDString;
  cluster_id: UUIDString;
  canonical_question: string;
  canonical_question_en?: string | null;
  suggested_answer?: string | null;
  language: Language;
  confidence: number;
  version: number;
  created_at: string;
  updated_at: string;
}

// ============================================================================
// Memory Graph
// ============================================================================
export interface MemoryEdge {
  id?: UUIDString | null;
  source_cluster_id: UUIDString;
  target_cluster_id: UUIDString;
  relation: EdgeRelation;
  weight: number;
  reason?: string | null;
  created_at?: string | null;
}

export interface MemoryGraph {
  nodes: ClusterRecord[];
  edges: MemoryEdge[];
}

// ============================================================================
// Feedback
// ============================================================================
export interface FeedbackAnnotation {
  id?: UUIDString | null;
  action: FeedbackAction;
  payload: Record<string, unknown>;
  author?: string | null;
  note?: string | null;
  created_at?: string | null;
}

// ============================================================================
// Search
// ============================================================================
export interface SearchRequest {
  query: string;
  top_k?: number;
  language?: Language | null;
  intents?: Intent[] | null;
  min_score?: number;
}

export interface SearchHit {
  question: ExtractedQuestion;
  cluster_id?: UUIDString | null;
  score: number;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  cluster_aggregates: Array<Record<string, unknown>>;
}

// ============================================================================
// Analytics
// ============================================================================
export interface AnalyticsSummary {
  total_calls: number;
  total_questions: number;
  total_clusters: number;
  language_distribution: Partial<Record<Language, number>>;
  intent_distribution: Partial<Record<Intent, number>>;
  top_clusters: Array<Record<string, unknown>>;
  cluster_growth: Array<Record<string, unknown>>;
  emerging_topics: Array<Record<string, unknown>>;
}

// ============================================================================
// Call Analysis & Detail
// ============================================================================
export interface SentimentBreakdown {
  negative: number;
  neutral: number;
  positive: number;
}

export interface Violation {
  time: number;
  title: string;
  severity: "LOW" | "MEDIUM" | "HIGH";
  quote: string;
  note?: string | null;
}

export interface TranscriptSegmentDetail {
  time_start: number;
  time_end: number;
  speaker: Speaker;
  text: string;
  flagged: boolean;
}

export interface CallDetailResponse {
  id: UUIDString;
  agent_name?: string | null;
  customer_name?: string | null;
  date?: string | null;
  duration?: number | null;
  risk_score: number;
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  confidence: number;
  tone?: string | null;
  violation_count: number;
  sentiment: SentimentBreakdown;
  summary?: string | null;
  violations: Violation[];
  transcript: TranscriptSegmentDetail[];
  audio_url: string;
}

export interface TranscriptSegment {
  speaker: Speaker;
  text: string;
  start_ts: number;
  end_ts: number;
}

export interface TranscriptionResponse {
  call_id: UUIDString;
  audio_url: string;
  transcript_with_timing: TranscriptSegment[];
  language?: Language | null;
  duration_seconds?: number | null;
}

export const SentimentLabel = {
  POSITIVE: "positive",
  NEUTRAL: "neutral",
  NEGATIVE: "negative",
} as const;
export type SentimentLabel = (typeof SentimentLabel)[keyof typeof SentimentLabel];

export const CallDisposition = {
  RESOLVED: "resolved",
  ESCALATED: "escalated",
  CALLBACK: "callback",
  OTHER: "other",
} as const;
export type CallDisposition = (typeof CallDisposition)[keyof typeof CallDisposition];

export interface Lead {
  score: number;
  quality: "high" | "medium" | "low";
  reasoning?: string | null;
}

export interface CallAnalysisResponse {
  call_id: UUIDString;
  sentiment: SentimentLabel;
  sentiment_confidence: number;
  disposition: CallDisposition;
  disposition_confidence: number;
  disposition_rationale?: string | null;
  intent?: Intent | null;
  secondary_intents: Intent[];
  escalation: boolean;
  lead: Lead;
  keywords: string[];
  quality_score: number;
  call_metadata: CallMetadata;
  language?: Language | null;
  duration_seconds?: number | null;
}

// ============================================================================
// Display helpers
// ============================================================================
export const LANGUAGE_LABEL: Record<Language, string> = {
  [Language.HINDI]: "Hindi",
  [Language.ENGLISH]: "English",
  [Language.HINGLISH]: "Hinglish",
  [Language.ROMAN_HINDI]: "Roman Hindi",
  [Language.TAMIL]: "Tamil",
  [Language.TELUGU]: "Telugu",
  [Language.OTHER]: "Other",
};

export const STT_PROVIDERS = ["whisper", "sarvam", "indic_conformer"] as const;
export type STTProvider = (typeof STT_PROVIDERS)[number];
export const STT_PROVIDER_LABEL: Record<STTProvider, string> = {
  whisper: "Whisper (local)",
  sarvam: "Sarvam (cloud)",
  indic_conformer: "IndicConformer (local, 22 Indic languages)",
};

// ============================================================================
// Admin — Sarvam API key pool health (GET /admin/keys)
// ============================================================================
export const KeyState = {
  HEALTHY: "healthy",
  COOLDOWN: "cooldown",
  DISABLED: "disabled",
} as const;
export type KeyState = (typeof KeyState)[keyof typeof KeyState];

export interface AdminKeyRead {
  masked: string;
  state: KeyState;
  /** Epoch seconds when a cooled-down key becomes available again. */
  available_at: number | null;
  ok_count: number;
  err_count: number;
}

// ============================================================================
// Pipeline stage mapping (raw CallStatus -> friendly stage progression)
// ============================================================================
export const PIPELINE_STAGES = [
  "Transcribe",
  "Diarize",
  "Extract",
  "Embed",
  "Cluster",
] as const;

export interface StageInfo {
  /** Short friendly label for the current state. */
  label: string;
  /** Number of pipeline stages fully completed (0..PIPELINE_STAGES.length). */
  completed: number;
  /** Index of the stage currently running, or null if idle/terminal. */
  activeIndex: number | null;
  kind: "queued" | "running" | "done" | "failed";
}

export function stageForStatus(status: CallStatus | undefined): StageInfo {
  switch (status) {
    case CallStatus.PENDING:
      return { label: "Queued", completed: 0, activeIndex: null, kind: "queued" };
    case CallStatus.STT_RUNNING:
      return { label: "Transcribing", completed: 0, activeIndex: 0, kind: "running" };
    case CallStatus.STT_DONE:
      return { label: "Diarizing", completed: 1, activeIndex: 1, kind: "running" };
    case CallStatus.DIARIZATION_RUNNING:
      return { label: "Diarizing", completed: 1, activeIndex: 1, kind: "running" };
    case CallStatus.DIARIZATION_DONE:
      return { label: "Extracting", completed: 2, activeIndex: 2, kind: "running" };
    case CallStatus.EXTRACTION_RUNNING:
      return { label: "Extracting", completed: 2, activeIndex: 2, kind: "running" };
    case CallStatus.EXTRACTION_DONE:
      return { label: "Embedding", completed: 3, activeIndex: 3, kind: "running" };
    case CallStatus.EMBEDDING_DONE:
      return { label: "Clustering", completed: 4, activeIndex: 4, kind: "running" };
    case CallStatus.CLUSTERED:
      return { label: "Done", completed: 5, activeIndex: null, kind: "done" };
    case CallStatus.FAILED:
      return { label: "Failed", completed: 0, activeIndex: null, kind: "failed" };
    default:
      return { label: "Loading", completed: 0, activeIndex: null, kind: "queued" };
  }
}

export function isTerminalStatus(status: CallStatus | undefined): boolean {
  return status === CallStatus.CLUSTERED || status === CallStatus.FAILED;
}

export const INTENT_LABEL: Record<Intent, string> = {
  [Intent.POLICY_DETAILS]: "Policy Details",
  [Intent.PREMIUM_PAYMENT]: "Premium Payment",
  [Intent.CLAIM_PROCESS]: "Claim Process",
  [Intent.CLAIM_REJECTION]: "Claim Rejection",
  [Intent.RENEWAL]: "Renewal",
  [Intent.NOMINEE_UPDATE]: "Nominee Update",
  [Intent.DOCUMENT_REQUEST]: "Document Request",
  [Intent.CANCELLATION]: "Cancellation",
  [Intent.MATURITY_BENEFIT]: "Maturity Benefit",
  [Intent.HEALTH_COVERAGE]: "Health Coverage",
  [Intent.EXCLUSIONS]: "Exclusions",
  [Intent.AGENT_COMPLAINT]: "Agent Complaint",
  [Intent.GRIEVANCE]: "Grievance",
  [Intent.OTHER_INSURANCE]: "Other Insurance",
  [Intent.OTHER]: "Other",
};

// Color per intent for graph node fill and badges.
// Tuned for legibility on dark surfaces.
export const INTENT_COLOR: Record<Intent, string> = {
  [Intent.POLICY_DETAILS]: "#7BA7F7",
  [Intent.PREMIUM_PAYMENT]: "#4FD1A1",
  [Intent.CLAIM_PROCESS]: "#F2B65C",
  [Intent.CLAIM_REJECTION]: "#F2807B",
  [Intent.RENEWAL]: "#B59CF5",
  [Intent.NOMINEE_UPDATE]: "#5BCBE3",
  [Intent.DOCUMENT_REQUEST]: "#6BBCF2",
  [Intent.CANCELLATION]: "#F79E66",
  [Intent.MATURITY_BENEFIT]: "#7BD489",
  [Intent.HEALTH_COVERAGE]: "#52C9B7",
  [Intent.EXCLUSIONS]: "#C89BF2",
  [Intent.AGENT_COMPLAINT]: "#F2748F",
  [Intent.GRIEVANCE]: "#EE6B5F",
  [Intent.OTHER_INSURANCE]: "#9AA0F0",
  [Intent.OTHER]: "#9BA3AF",
};
