-- Additive: existing server-encrypted documents retain their original format.
BEGIN;
ALTER TABLE public.records ADD COLUMN client_encrypted BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE public.storage_objects ADD COLUMN client_info JSONB;
ALTER TABLE public.storage_objects DROP CONSTRAINT storage_objects_check;
ALTER TABLE public.storage_objects DROP CONSTRAINT storage_objects_encryption_format_check;
ALTER TABLE public.storage_objects ADD CONSTRAINT storage_objects_encryption_valid CHECK (
  (encryption_format='aes256gcm-v1' AND client_info IS NULL AND ciphertext_size=plaintext_size+33)
  OR (encryption_format='client-x25519-v1' AND client_info IS NOT NULL
      AND jsonb_typeof(client_info)='object' AND ciphertext_size=plaintext_size+28)
);
CREATE TABLE public.document_key_envelopes (
  object_id UUID NOT NULL,
  owner_id UUID NOT NULL,
  recipient_id UUID NOT NULL REFERENCES auth.users(id),
  envelope JSONB NOT NULL CHECK (jsonb_typeof(envelope)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (object_id, recipient_id),
  FOREIGN KEY (object_id, owner_id) REFERENCES public.storage_objects(id, owner_id)
);
ALTER TABLE public.document_key_envelopes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.document_key_envelopes FROM PUBLIC, anon, authenticated;
GRANT ALL ON public.document_key_envelopes TO service_role;
CREATE INDEX document_key_envelopes_recipient_idx ON public.document_key_envelopes(recipient_id);
CREATE FUNCTION public.set_document_encryption_flag() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path='' AS $$
BEGIN
  IF NEW.storage_object_id IS NOT NULL THEN
    SELECT encryption_format='client-x25519-v1' INTO NEW.client_encrypted
    FROM public.storage_objects WHERE id=NEW.storage_object_id AND owner_id=NEW.owner_id;
  ELSE
    NEW.client_encrypted := false;
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.set_document_encryption_flag() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER records_encryption_flag BEFORE INSERT OR UPDATE OF storage_object_id ON public.records
FOR EACH ROW EXECUTE FUNCTION public.set_document_encryption_flag();
COMMENT ON COLUMN public.storage_objects.client_info IS 'Signed client manifest and owner-wrapped DEK; no plaintext content keys. Recipient envelopes are released only after current API/on-chain authorization.';
NOTIFY pgrst, 'reload schema';
COMMIT;
