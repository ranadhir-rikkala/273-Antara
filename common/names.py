class Workflows:
    INGEST = "IngestWorkflow"
    AGENT_RUN = "AgentRunWorkflow"
    RECONCILER = "ReconcilerWorkflow"
    DELETE_DOCUMENT = "DeleteDocumentWorkflow"


class Activities:
    EXTRACT = "extract_document"
    CHUNK = "chunk_document"
    SELECT_EMBED_QUEUE = "select_embed_queue"
    EMBED = "generate_embeddings"
    PUBLISH = "publish_version"
    MARK_FAILED = "mark_version_failed"
    PURGE = "purge_document"
    RETRIEVE = "retrieve_documents"
    GENERATE = "generate_answer"
    EXECUTE_TOOL = "execute_tool"
    OPEN_APPROVAL = "open_approval"
    ESCALATE_APPROVAL = "escalate_approval"
    FIND_INCONSISTENT = "find_inconsistent_versions"


class Signals:
    APPROVAL = "approval"


class Queries:
    STATE = "state"


class Errors:
    CORRUPT_DOCUMENT = "CorruptDocumentError"
    PERMISSION_DENIED = "PermissionDeniedError"
    UNKNOWN_TOOL = "UnknownToolError"
