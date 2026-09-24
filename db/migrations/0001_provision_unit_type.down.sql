-- Reverse 0001.
--
-- Dropping the column discards which rows were paragraphs, orders or whole
-- documents. That information is re-derivable only by re-ingesting, so this
-- rollback is structurally reversible but not information-preserving, and it
-- says so rather than pretending otherwise.

DROP INDEX IF EXISTS provisions_unit_type_idx;
ALTER TABLE provisions DROP CONSTRAINT IF EXISTS provisions_unit_type_check;
ALTER TABLE provisions DROP COLUMN IF EXISTS unit_type;
