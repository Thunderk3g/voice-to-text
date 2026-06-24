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
    ANALYSIS_RUNNING = "analysis_running"
    ANALYSIS_DONE = "analysis_done"
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
    """Semantic relationships used across two graphs.

    Two families share this enum:

    * **Cluster relations** (memory graph): inter-cluster semantic links emitted
      by ``app.services.memory_graph`` — ``LEADS_TO`` … ``CO_OCCURS``.
    * **Entity relations** (call knowledge graph): typed edges between the
      LEAD / CALL / AGENT / CAMPAIGN / PRODUCT / DISPOSITION / SENTIMENT
      entities emitted by ``app.services.knowledge_graph.build``. Each carries an
      inline ``# src->dst`` node-type hint.
    """

    # --- Cluster relations (memory graph) --------------------------------- #
    LEADS_TO = "leads_to"           # cluster A often precedes B in customer journey
    RELATED_TO = "related_to"       # general semantic relation
    SUBSET_OF = "subset_of"         # A is a specialization of B
    OPPOSES = "opposes"             # opposite stance (e.g. complaint vs satisfaction)
    CAUSED_BY = "caused_by"         # A is a downstream effect of B
    CO_OCCURS = "co_occurs"         # frequently appear in same call

    # --- Entity relations (call knowledge graph) -------------------------- #
    RECEIVED_CALL = "received_call"     # lead->call
    HANDLED_BY = "handled_by"           # call->agent
    HAS_DISPOSITION = "has_disposition"  # call->disposition
    HAS_SENTIMENT = "has_sentiment"     # call->sentiment
    ABOUT_PRODUCT = "about_product"     # call->product
    INTERESTED_IN = "interested_in"     # lead->product
    IN_CAMPAIGN = "in_campaign"         # lead->campaign
    SIMILAR_TO = "similar_to"           # lead->lead (declared; not emitted by the pure builder)


class CallDisposition(StrEnum):
    """Outcome of an inbound service call (separate axis from the LMS DISPOSITION)."""

    RESOLVED = "resolved"
    INFO_PROVIDED = "info_provided"
    CALLBACK_REQUESTED = "callback_requested"
    FOLLOW_UP_PAYMENT = "follow_up_payment"
    COMPLAINT = "complaint"
    ESCALATION = "escalation"
    NOT_INTERESTED = "not_interested"
    NOT_ELIGIBLE = "not_eligible"
    SERVICE_REQUEST = "service_request"
    WRONG_NUMBER = "wrong_number"
    DND = "dnd"
    NO_RESPONSE = "no_response"
    OTHER = "other"


class SentimentLabel(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class CallDirection(StrEnum):
    """Direction of a telephony call, as seen from the contact-centre.

    Crux CDRs encode this as MT (mobile-terminated -> inbound) / MO
    (mobile-originated -> outbound); see app.services.cdr.loader._parse_direction.
    """

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    UNKNOWN = "unknown"
