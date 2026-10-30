-- Immutable M6 export plan metadata snapshot; preserves existing export journal.
ALTER TABLE export_operations ADD COLUMN category TEXT;
ALTER TABLE export_operations ADD COLUMN decision TEXT;
ALTER TABLE export_operations ADD COLUMN model_reason TEXT;
ALTER TABLE export_operations ADD COLUMN capture_time TEXT;
ALTER TABLE export_operations ADD COLUMN date_source TEXT;
CREATE INDEX export_operations_state ON export_operations(export_id,state,id);
