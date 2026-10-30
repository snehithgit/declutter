-- M6 safety: rollback must identify the exact inode created by PhotoSift.
-- Prior exports have NULL identity and must not be unlinked automatically.
ALTER TABLE export_operations ADD COLUMN output_device INTEGER;
ALTER TABLE export_operations ADD COLUMN output_inode INTEGER;
