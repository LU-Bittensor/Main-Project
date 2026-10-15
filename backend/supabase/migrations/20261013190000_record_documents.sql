BEGIN;

-- File bytes are stored in the record itself, not a Storage bucket or local disk.
ALTER TABLE public.records
  ADD COLUMN file_content BYTEA,
  ADD COLUMN file_mime_type TEXT,
  ADD COLUMN file_size INTEGER;

ALTER TABLE public.records ADD CONSTRAINT records_document_valid CHECK (
  (file_content IS NULL AND file_mime_type IS NULL AND file_size IS NULL)
  OR
  (file_content IS NOT NULL AND file_mime_type IS NOT NULL AND file_size IS NOT NULL
   AND file_mime_type IN ('application/pdf', 'image/png', 'image/jpeg', 'text/plain')
   AND file_size BETWEEN 1 AND 10485760
   AND octet_length(file_content) = file_size)
);

-- Existing owner-only RLS continues to protect direct client reads. Shared
-- recipients use the grant API, which checks permission before reading bytes.
-- Restrict document writes to the API so editable REST fields cannot bypass
-- patient-role, file-type, and upload validation. Preserve metadata editing.
REVOKE INSERT, UPDATE ON public.records FROM PUBLIC, anon, authenticated;
GRANT INSERT (id, owner_id, name, kind, record_date, source, tags, created_at)
  ON public.records TO authenticated;
GRANT UPDATE (name, kind, record_date, source, tags)
  ON public.records TO authenticated;
GRANT ALL ON public.records TO service_role;

COMMENT ON COLUMN public.records.file_content IS 'Original uploaded file bytes; delivered to shared recipients only through the authorized grant API.';
NOTIFY pgrst, 'reload schema';
COMMIT;
