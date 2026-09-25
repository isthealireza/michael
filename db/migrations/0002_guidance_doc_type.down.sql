-- Reverse 0002.
--
-- This does NOT delete guidance documents to make the narrower constraint
-- fit. If any row still has doc_type = 'guidance', ADD CONSTRAINT fails, the
-- migration's transaction rolls back, and the database is left as it was.
-- Removing guidance from the corpus is a decision about the corpus, taken by
-- deleting those documents on purpose first - not a side effect of a
-- rollback.

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_doc_type_check;
ALTER TABLE documents
    ADD CONSTRAINT documents_doc_type_check
    CHECK (doc_type IN ('act', 'regulation', 'award', 'case'));
