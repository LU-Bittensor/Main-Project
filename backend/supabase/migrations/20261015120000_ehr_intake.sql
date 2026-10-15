-- Apply after the existing patients and record_documents migrations.
-- Schema only: no Epic requests, credential migration, or EHR import occurs.
BEGIN;

CREATE TABLE public.ehr_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL DEFAULT 'epic',
  fhir_base_url TEXT NOT NULL CHECK (fhir_base_url ~ '^https://'),
  fhir_version TEXT NOT NULL DEFAULT 'R4',
  external_patient_id TEXT NOT NULL CHECK (length(external_patient_id) > 0),
  granted_scopes TEXT[] NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'connected' CHECK (status IN ('connected', 'expired', 'disconnected')),
  authorized_at TIMESTAMPTZ,
  token_expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (owner_id, fhir_base_url, external_patient_id),
  UNIQUE (id, owner_id)
);
COMMENT ON TABLE public.ehr_connections IS
  'Stable owner/provider/patient binding. Canonicalize FHIR base URLs before upsert. No OAuth credentials belong here; readiness is verified by the backend.';

CREATE TABLE public.ehr_import_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL,
  connection_id UUID NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'partial', 'failed', 'cancelled')),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  resources_received INTEGER NOT NULL DEFAULT 0 CHECK (resources_received >= 0),
  resources_created INTEGER NOT NULL DEFAULT 0 CHECK (resources_created >= 0),
  resources_updated INTEGER NOT NULL DEFAULT 0 CHECK (resources_updated >= 0),
  error_summary TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (connection_id, owner_id) REFERENCES public.ehr_connections(id, owner_id) ON DELETE CASCADE,
  UNIQUE (id, connection_id, owner_id)
);
COMMENT ON COLUMN public.ehr_import_jobs.error_summary IS 'Sanitized user-facing summary only; no tokens or raw upstream responses.';

CREATE TABLE public.ehr_import_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID NOT NULL,
  connection_id UUID NOT NULL,
  owner_id UUID NOT NULL,
  resource_type TEXT NOT NULL CHECK (resource_type ~ '^[A-Z][A-Za-z0-9]*$'),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'partial', 'failed', 'skipped')),
  pagination_checkpoint JSONB,
  resources_received INTEGER NOT NULL DEFAULT 0 CHECK (resources_received >= 0),
  error_summary TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (job_id, connection_id, owner_id) REFERENCES public.ehr_import_jobs(id, connection_id, owner_id) ON DELETE CASCADE,
  UNIQUE (job_id, resource_type)
);
COMMENT ON COLUMN public.ehr_import_steps.pagination_checkpoint IS
  'Backend-only opaque cursor. Never store bearer tokens. Validate destination against the configured provider before following pagination links.';

CREATE TABLE public.ehr_resources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL,
  connection_id UUID NOT NULL,
  resource_type TEXT NOT NULL CHECK (resource_type ~ '^[A-Z][A-Za-z0-9]*$'),
  external_id TEXT NOT NULL CHECK (length(external_id) > 0),
  payload JSONB NOT NULL,
  source_version TEXT,
  source_updated_at TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  import_job_id UUID NOT NULL,
  first_imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (connection_id, owner_id) REFERENCES public.ehr_connections(id, owner_id) ON DELETE CASCADE,
  FOREIGN KEY (import_job_id, connection_id, owner_id) REFERENCES public.ehr_import_jobs(id, connection_id, owner_id),
  CONSTRAINT ehr_resource_payload_matches CHECK (
    jsonb_typeof(payload) = 'object'
    AND (payload ->> 'resourceType') IS NOT NULL AND payload ->> 'resourceType' = resource_type
    AND (payload ->> 'id') IS NOT NULL AND payload ->> 'id' = external_id
  ),
  UNIQUE (connection_id, resource_type, external_id),
  UNIQUE (id, connection_id, owner_id)
);
COMMENT ON COLUMN public.ehr_resources.content_hash IS
  'SHA-256 of a consistently canonicalized resource JSON, computed by the importer. Used for change detection, not authentication.';

CREATE TABLE public.ehr_resource_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id UUID NOT NULL,
  connection_id UUID NOT NULL,
  owner_id UUID NOT NULL,
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  source_version TEXT,
  source_updated_at TIMESTAMPTZ,
  content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  import_job_id UUID NOT NULL,
  received_at TIMESTAMPTZ NOT NULL,
  archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (resource_id, connection_id, owner_id) REFERENCES public.ehr_resources(id, connection_id, owner_id) ON DELETE CASCADE,
  FOREIGN KEY (import_job_id, connection_id, owner_id) REFERENCES public.ehr_import_jobs(id, connection_id, owner_id)
);

-- Archive old content atomically whenever the current payload changes.
CREATE FUNCTION public.archive_ehr_resource_version() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  IF ROW(NEW.id, NEW.owner_id, NEW.connection_id, NEW.resource_type, NEW.external_id)
     IS DISTINCT FROM ROW(OLD.id, OLD.owner_id, OLD.connection_id, OLD.resource_type, OLD.external_id) THEN
    RAISE EXCEPTION 'EHR resource identity cannot be changed';
  END IF;
  IF NEW.payload IS DISTINCT FROM OLD.payload THEN
    INSERT INTO public.ehr_resource_versions
      (resource_id, connection_id, owner_id, payload, source_version, source_updated_at,
       content_hash, import_job_id, received_at)
    VALUES (OLD.id, OLD.connection_id, OLD.owner_id, OLD.payload, OLD.source_version,
            OLD.source_updated_at, OLD.content_hash, OLD.import_job_id, OLD.last_imported_at);
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.archive_ehr_resource_version() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER archive_ehr_resource_version BEFORE UPDATE ON public.ehr_resources
FOR EACH ROW EXECUTE FUNCTION public.archive_ehr_resource_version();

ALTER TABLE public.records
  ADD COLUMN ehr_resource_id UUID,
  ADD COLUMN ehr_connection_id UUID,
  ADD COLUMN import_job_id UUID,
  ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual_upload' CHECK (origin IN ('manual_upload', 'ehr_import')),
  ADD COLUMN clinical_datetime TIMESTAMPTZ,
  ADD COLUMN parent_record_id UUID,
  ADD COLUMN source_attachment_key TEXT,
  ADD CONSTRAINT records_id_owner_unique UNIQUE (id, owner_id),
  ADD CONSTRAINT records_ehr_resource_fk FOREIGN KEY (ehr_resource_id, ehr_connection_id, owner_id)
    REFERENCES public.ehr_resources(id, connection_id, owner_id),
  ADD CONSTRAINT records_ehr_job_fk FOREIGN KEY (import_job_id, ehr_connection_id, owner_id)
    REFERENCES public.ehr_import_jobs(id, connection_id, owner_id),
  ADD CONSTRAINT records_parent_owner_fk FOREIGN KEY (parent_record_id, owner_id)
    REFERENCES public.records(id, owner_id) ON DELETE CASCADE,
  ADD CONSTRAINT records_ehr_origin_valid CHECK (
    (origin = 'manual_upload' AND ehr_resource_id IS NULL AND ehr_connection_id IS NULL AND import_job_id IS NULL)
    OR (origin = 'ehr_import' AND ehr_resource_id IS NOT NULL AND ehr_connection_id IS NOT NULL AND import_job_id IS NOT NULL)
  ),
  ADD CONSTRAINT records_attachment_valid CHECK (
    (parent_record_id IS NULL AND source_attachment_key IS NULL)
    OR (origin = 'ehr_import' AND parent_record_id IS NOT NULL AND parent_record_id <> id
        AND source_attachment_key IS NOT NULL AND length(source_attachment_key) > 0)
  );
-- One catalog entry per resource; attachment rows can share that resource.
CREATE UNIQUE INDEX records_ehr_catalog_unique ON public.records(ehr_resource_id) WHERE parent_record_id IS NULL;
CREATE UNIQUE INDEX records_ehr_attachment_unique ON public.records(parent_record_id, source_attachment_key) WHERE parent_record_id IS NOT NULL;
CREATE INDEX records_ehr_job_idx ON public.records(import_job_id);
CREATE INDEX records_ehr_connection_idx ON public.records(ehr_connection_id);
CREATE INDEX ehr_jobs_owner_created_idx ON public.ehr_import_jobs(owner_id, created_at DESC);
CREATE INDEX ehr_jobs_connection_idx ON public.ehr_import_jobs(connection_id);
CREATE INDEX ehr_steps_owner_idx ON public.ehr_import_steps(owner_id);
CREATE INDEX ehr_resources_owner_type_idx ON public.ehr_resources(owner_id, resource_type);
CREATE INDEX ehr_resources_job_idx ON public.ehr_resources(import_job_id);
CREATE INDEX ehr_versions_resource_idx ON public.ehr_resource_versions(resource_id, received_at DESC);
CREATE INDEX ehr_versions_job_idx ON public.ehr_resource_versions(import_job_id);
CREATE INDEX ehr_versions_owner_idx ON public.ehr_resource_versions(owner_id);

-- Owner-only reads. All intake writes require the trusted backend service role.
ALTER TABLE public.ehr_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ehr_import_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ehr_import_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ehr_resources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ehr_resource_versions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ehr_connections, public.ehr_import_jobs, public.ehr_import_steps,
  public.ehr_resources, public.ehr_resource_versions FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.ehr_connections, public.ehr_import_jobs, public.ehr_resources,
  public.ehr_resource_versions TO authenticated;
-- Exclude backend pagination checkpoints from browser reads.
GRANT SELECT (id, job_id, connection_id, owner_id, resource_type, status, resources_received,
  error_summary, updated_at) ON public.ehr_import_steps TO authenticated;
GRANT ALL ON public.ehr_connections, public.ehr_import_jobs, public.ehr_import_steps,
  public.ehr_resources, public.ehr_resource_versions TO service_role;
CREATE POLICY "owners read EHR connections" ON public.ehr_connections FOR SELECT TO authenticated USING ((SELECT auth.uid()) = owner_id);
CREATE POLICY "owners read EHR jobs" ON public.ehr_import_jobs FOR SELECT TO authenticated USING ((SELECT auth.uid()) = owner_id);
CREATE POLICY "owners read EHR steps" ON public.ehr_import_steps FOR SELECT TO authenticated USING ((SELECT auth.uid()) = owner_id);
CREATE POLICY "owners read EHR resources" ON public.ehr_resources FOR SELECT TO authenticated USING ((SELECT auth.uid()) = owner_id);
CREATE POLICY "owners read EHR versions" ON public.ehr_resource_versions FOR SELECT TO authenticated USING ((SELECT auth.uid()) = owner_id);
-- Existing records column privileges keep imported provenance backend-only.
-- Existing document MIME types and 10 MB cap intentionally remain enforced.
NOTIFY pgrst, 'reload schema';
COMMIT;
