-- Admit departmental guidance as a document type.
--
-- 'guidance' is how a government department says it applies the law - for
-- now, pages from immigration.homeaffairs.gov.au. It is not the law and is
-- never cited or judged as legislation: pinpoint() renders it as
-- "(departmental guidance, not legislation)", the verifier never lets it
-- alone carry a claim, and ingestion binds the type to the host in both
-- directions (sources.check_doc_type).
--
-- The constraint is replaced, not altered: Postgres cannot widen a CHECK in
-- place. DROP ... IF EXISTS then ADD makes this converge whether the table
-- was created before this migration (the inline CHECK, which Postgres named
-- documents_doc_type_check) or after it, by the updated schema.py.

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_doc_type_check;
ALTER TABLE documents
    ADD CONSTRAINT documents_doc_type_check
    CHECK (doc_type IN ('act', 'regulation', 'award', 'case', 'guidance'));
