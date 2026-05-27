"""
Shared enums used across services, DB models, API contracts.

Single source of truth — DO NOT redefine these elsewhere.
"""

from __future__ import annotations

from enum import StrEnum


class Speaker(StrEnum):
    AGENT = "AGENT"
    CUSTOMER = "CUSTOMER"
    UNKNOWN = "UNKNOWN"


class Language(StrEnum):
    """Supported / detected languages.

    Note: code-switched audio gets the dominant language assigned, but the
    `language_mix` field on Utterance preserves the full distribution.
    """

    HINDI = "hi"
    ENGLISH = "en"
    HINGLISH = "hi-en"           # Romanized Hindi + English code-switch
    ROMAN_HINDI = "hi-roman"     # Hindi written in Latin script
    TAMIL = "ta"
    TELUGU = "te"
    OTHER = "other"


class Intent(StrEnum):
    """Customer intent labels. Closed set."""

    POLICY_DETAILS = "policy_details"
    PREMIUM_PAYMENT = "premium_payment"
    CLAIM_PROCESS = "claim_process"
    CLAIM_REJECTION = "claim_rejection"
    RENEWAL = "renewal"
    NOMINEE_UPDATE = "nominee_update"
    DOCUMENT_REQUEST = "document_request"
    CANCELLATION = "cancellation"
    MATURITY_BENEFIT = "maturity_benefit"
    HEALTH_COVERAGE = "health_coverage"
    EXCLUSIONS = "exclusions"
    AGENT_COMPLAINT = "agent_complaint"
    GRIEVANCE = "grievance"
    OTHER_INSURANCE = "other_insurance"
    OTHER = "other"


class CallStatus(StrEnum):
    PENDING = "pending"
    STT_RUNNING = "stt_running"
    STT_DONE = "stt_done"
    DIARIZATION_RUNNING = "diarization_running"
    DIARIZATION_DONE = "diarization_done"
    EXTRACTION_RUNNING = "extraction_running"
    EXTRACTION_DONE = "extraction_done"
    EMBEDDING_DONE = "embedding_done"
    CLUSTERED = "clustered"
    FAILED = "failed"


class QuestionType(StrEnum):
    QUESTION = "question"
    COMPLAINT = "complaint"
    DOUBT = "doubt"
    INTENT = "intent"


class FeedbackAction(StrEnum):
    MERGE_CLUSTERS = "merge_clusters"
    SPLIT_CLUSTER = "split_cluster"
    RELABEL_INTENT = "relabel_intent"
    REGENERATE_FAQ = "regenerate_faq"
    REASSIGN_QUESTION = "reassign_question"


class EdgeRelation(StrEnum):
    """Semantic relationships between clusters in the memory graph."""

    LEADS_TO = "leads_to"           # cluster A often precedes B in customer journey
    RELATED_TO = "related_to"       # general semantic relation
    SUBSET_OF = "subset_of"         # A is a specialization of B
    OPPOSES = "opposes"             # opposite stance (e.g. complaint vs satisfaction)
    CAUSED_BY = "caused_by"         # A is a downstream effect of B
    CO_OCCURS = "co_occurs"         # frequently appear in same call
