-- Distinguish what KIND of unit a provision row is.
--
-- Before this, every row was implicitly a section of an Act, and a judgment
-- paragraph stored in `section_number` was cited as `... s 2`. The unit kind
-- cannot be derived from `documents.doc_type`: one judgment holds paragraphs
-- of reasons, a block of orders, and - when the report carries no paragraph
-- numbers at all - a single whole-document row. So it is recorded per row.
--
-- 'section'   a section or Schedule clause of an Act, Regulation or Award.
-- 'paragraph' a numbered paragraph of a court's reasons for judgment.
-- 'order'     the orders/declarations a court made, as one block.
-- 'document'  the whole document, because it carries no internal numbering.
--
-- Existing rows default to 'section', which is what they were written as.
-- The one mechanical correction applied here is the `(whole document)`
-- sentinel `split_sections()` already emitted: that is not a section and
-- never was, and it is identifiable without guessing.

ALTER TABLE provisions
    ADD COLUMN IF NOT EXISTS unit_type text NOT NULL DEFAULT 'section';

ALTER TABLE provisions DROP CONSTRAINT IF EXISTS provisions_unit_type_check;
ALTER TABLE provisions
    ADD CONSTRAINT provisions_unit_type_check
    CHECK (unit_type IN ('section', 'paragraph', 'order', 'document'));

UPDATE provisions
   SET unit_type = 'document'
 WHERE section_number = '(whole document)'
   AND unit_type <> 'document';

CREATE INDEX IF NOT EXISTS provisions_unit_type_idx ON provisions (unit_type);
