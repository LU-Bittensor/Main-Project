-- Requires record_documents migration; independent of the optional EHR intake migration.
-- Existing bytes remain readable. New writes use encrypted Hippius objects.
BEGIN;
CREATE TABLE public.patient_storage (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL UNIQUE REFERENCES auth.users(id),
  patient_hash TEXT NOT NULL UNIQUE CHECK (patient_hash ~ '^[a-z2-7]{52}$'),
  bucket_name TEXT NOT NULL UNIQUE CHECK (bucket_name ~ '^ml-p-[a-z2-7]{52}$'),
  namespace TEXT NOT NULL,
  hash_key_version TEXT NOT NULL,
  encryption_key_version TEXT NOT NULL,
  wrapped_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'provisioning' CHECK (status IN ('provisioning','ready')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id, owner_id)
);
CREATE TABLE public.storage_objects (
  id UUID PRIMARY KEY,
  owner_id UUID NOT NULL,
  patient_storage_id UUID NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','uploaded','ready')),
  mime_type TEXT NOT NULL,
  plaintext_size INTEGER NOT NULL CHECK (plaintext_size BETWEEN 1 AND 10485760),
  ciphertext_size INTEGER NOT NULL CHECK (ciphertext_size = plaintext_size + 33),
  ciphertext_sha256 TEXT NOT NULL CHECK (ciphertext_sha256 ~ '^[a-f0-9]{64}$'),
  encryption_format TEXT NOT NULL DEFAULT 'aes256gcm-v1' CHECK (encryption_format = 'aes256gcm-v1'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (patient_storage_id, owner_id) REFERENCES public.patient_storage(id, owner_id),
  UNIQUE (id, owner_id)
);
CREATE INDEX storage_objects_patient_idx ON public.storage_objects(patient_storage_id);
CREATE INDEX storage_objects_owner_idx ON public.storage_objects(owner_id);
CREATE INDEX storage_objects_pending_idx ON public.storage_objects(created_at) WHERE status <> 'ready';
ALTER TABLE public.records ADD COLUMN storage_object_id UUID;
ALTER TABLE public.records ADD CONSTRAINT records_storage_owner_fk
  FOREIGN KEY (storage_object_id, owner_id) REFERENCES public.storage_objects(id, owner_id);
CREATE UNIQUE INDEX records_storage_object_unique ON public.records(storage_object_id) WHERE storage_object_id IS NOT NULL;
ALTER TABLE public.records DROP CONSTRAINT records_document_valid;
ALTER TABLE public.records ADD CONSTRAINT records_document_valid CHECK (
  (file_content IS NULL AND storage_object_id IS NULL AND file_mime_type IS NULL AND file_size IS NULL)
  OR
  (file_mime_type IS NOT NULL AND file_size IS NOT NULL
   AND file_mime_type IN ('application/pdf','image/png','image/jpeg','text/plain')
   AND file_size BETWEEN 1 AND 10485760
   AND ((storage_object_id IS NULL AND file_content IS NOT NULL AND octet_length(file_content) = file_size)
        OR (storage_object_id IS NOT NULL AND file_content IS NULL)))
);
ALTER TABLE public.patient_storage ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.storage_objects ENABLE ROW LEVEL SECURITY;
-- Buckets, keys and wrapped encryption keys are backend-only. Browser gets record metadata.
REVOKE ALL ON public.patient_storage, public.storage_objects FROM PUBLIC, anon, authenticated;
GRANT ALL ON public.patient_storage, public.storage_objects TO service_role;
-- No client write privilege on the new records column; existing owner RLS remains.

-- Atomically publish a verified upload with a catalog entry. A retry of the same
-- record/object is safe after a lost HTTP response. Existing content is not replaced.
CREATE FUNCTION public.publish_hippius_document(
  p_owner UUID, p_record UUID, p_object UUID, p_name TEXT, p_tags TEXT,
  p_kind TEXT DEFAULT 'Document', p_created_at TIMESTAMPTZ DEFAULT NULL
) RETURNS SETOF public.records
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE obj public.storage_objects; existing public.records;
BEGIN
  SELECT * INTO obj FROM public.storage_objects WHERE id=p_object AND owner_id=p_owner FOR UPDATE;
  IF NOT FOUND OR obj.status NOT IN ('uploaded','ready') THEN
    RAISE EXCEPTION 'Verified upload unavailable';
  END IF;
  SELECT * INTO existing FROM public.records WHERE id=p_record;
  IF FOUND THEN
    IF existing.owner_id <> p_owner OR existing.storage_object_id IS DISTINCT FROM p_object THEN
      RAISE EXCEPTION 'Record already exists';
    END IF;
  ELSE
    INSERT INTO public.records(id,owner_id,name,kind,tags,file_mime_type,file_size,storage_object_id,created_at)
    VALUES(p_record,p_owner,p_name,p_kind,p_tags,obj.mime_type,obj.plaintext_size,p_object,COALESCE(p_created_at,now()));
  END IF;
  UPDATE public.storage_objects SET status='ready' WHERE id=p_object;
  RETURN QUERY SELECT * FROM public.records WHERE id=p_record;
END;
$$;
REVOKE ALL ON FUNCTION public.publish_hippius_document(UUID,UUID,UUID,TEXT,TEXT,TEXT,TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.publish_hippius_document(UUID,UUID,UUID,TEXT,TEXT,TEXT,TIMESTAMPTZ) TO service_role;
COMMENT ON TABLE public.patient_storage IS 'Private storage bindings and wrapped keys; do not cascade-delete before deleting remote ciphertext.';
COMMENT ON TABLE public.storage_objects IS 'Immutable ciphertext pointers. Pending/uploaded entries are retained for reconciliation after interrupted writes.';
NOTIFY pgrst, 'reload schema';
COMMIT;
