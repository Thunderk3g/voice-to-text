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
}

// ============================================================================
// Utterances
// ============================================================================
export interface UtteranceSchema {
  id?: UUIDString | null;
  call_id: UUIDString;
  speaker: Speaker;
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
export const INTENT_COLOR: Record<Intent, string> = {
  [Intent.POLICY_DETAILS]: "#3b82f6",
  [Intent.PREMIUM_PAYMENT]: "#10b981",
  [Intent.CLAIM_PROCESS]: "#f59e0b",
  [Intent.CLAIM_REJECTION]: "#ef4444",
  [Intent.RENEWAL]: "#8b5cf6",
  [Intent.NOMINEE_UPDATE]: "#06b6d4",
  [Intent.DOCUMENT_REQUEST]: "#0ea5e9",
  [Intent.CANCELLATION]: "#f97316",
  [Intent.MATURITY_BENEFIT]: "#22c55e",
  [Intent.HEALTH_COVERAGE]: "#14b8a6",
  [Intent.EXCLUSIONS]: "#a855f7",
  [Intent.AGENT_COMPLAINT]: "#e11d48",
  [Intent.GRIEVANCE]: "#dc2626",
  [Intent.OTHER_INSURANCE]: "#6366f1",
  [Intent.OTHER]: "#64748b",
};
