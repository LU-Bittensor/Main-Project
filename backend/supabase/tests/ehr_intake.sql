-- Run as the database administrator after all migrations, on local/staging Supabase.
-- Synthetic fixtures only; all changes roll back.
BEGIN;
DO $$
DECLARE
  owner UUID := gen_random_uuid(); other_owner UUID := gen_random_uuid();
  conn UUID := gen_random_uuid(); other_conn UUID := gen_random_uuid();
  job UUID := gen_random_uuid(); other_job UUID := gen_random_uuid();
  resource UUID := gen_random_uuid(); catalog UUID := gen_random_uuid();
BEGIN
  INSERT INTO auth.users(id, email, raw_user_meta_data, created_at) VALUES
    (owner, owner::text || '@example.invalid', '{"role":"patient"}', now()),
    (other_owner, other_owner::text || '@example.invalid', '{"role":"patient"}', now());
  INSERT INTO public.ehr_connections(id, owner_id, fhir_base_url, external_patient_id) VALUES
    (conn, owner, 'https://example.invalid/FHIR/R4', 'patient-1'),
    (other_conn, other_owner, 'https://example.invalid/FHIR/R4', 'patient-2');
  INSERT INTO public.ehr_import_jobs(id, owner_id, connection_id) VALUES
    (job, owner, conn), (other_job, other_owner, other_conn);
  INSERT INTO public.ehr_resources(id, owner_id, connection_id, resource_type, external_id, payload, content_hash, import_job_id)
    VALUES (resource, owner, conn, 'Patient', 'patient-1', '{"resourceType":"Patient","id":"patient-1"}', repeat('a',64), job);
  BEGIN
    INSERT INTO public.ehr_resources(owner_id, connection_id, resource_type, external_id, payload, content_hash, import_job_id)
      VALUES (owner, conn, 'Patient', 'patient-1', '{"resourceType":"Patient","id":"patient-1"}', repeat('a',64), job);
    RAISE EXCEPTION 'Duplicate resource accepted';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  BEGIN
    UPDATE public.ehr_resources SET import_job_id = other_job WHERE id = resource;
    RAISE EXCEPTION 'Cross-account job accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN
    UPDATE public.ehr_resources SET payload = '{"resourceType":"Observation","id":"patient-1"}' WHERE id = resource;
    RAISE EXCEPTION 'Mismatched resource payload accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
  UPDATE public.ehr_resources SET last_seen_at = now() WHERE id = resource;
  ASSERT (SELECT count(*) FROM public.ehr_resource_versions WHERE resource_id = resource) = 0;
  UPDATE public.ehr_resources SET payload = payload || '{"active":true}', content_hash = repeat('b',64) WHERE id = resource;
  ASSERT (SELECT count(*) FROM public.ehr_resource_versions WHERE resource_id = resource) = 1;
  ASSERT (SELECT payload FROM public.ehr_resource_versions WHERE resource_id = resource) = '{"resourceType":"Patient","id":"patient-1"}'::jsonb;
  INSERT INTO public.records(id, owner_id, name, kind, origin, ehr_resource_id, ehr_connection_id, import_job_id)
    VALUES (catalog, owner, 'Synthetic patient', 'Patient', 'ehr_import', resource, conn, job);
  INSERT INTO public.records(owner_id, name, kind, origin, ehr_resource_id, ehr_connection_id, import_job_id, parent_record_id, source_attachment_key)
    VALUES (owner, 'Attachment 1', 'Document', 'ehr_import', resource, conn, job, catalog, '1'),
           (owner, 'Attachment 2', 'Document', 'ehr_import', resource, conn, job, catalog, '2');
  BEGIN
    INSERT INTO public.records(owner_id, name, kind, origin, ehr_resource_id, ehr_connection_id, import_job_id)
      VALUES (other_owner, 'Wrong owner', 'Patient', 'ehr_import', resource, conn, job);
    RAISE EXCEPTION 'Cross-account record accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  INSERT INTO public.records(owner_id, name, kind) VALUES (owner, 'Existing upload path', 'Document');
  PERFORM set_config('test.ehr_owner', owner::text, true);
  PERFORM set_config('test.ehr_other', other_owner::text, true);
  PERFORM set_config('test.ehr_resource', resource::text, true);
END $$;
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', current_setting('test.ehr_owner'), true);
DO $$ BEGIN
  ASSERT EXISTS (SELECT 1 FROM public.ehr_resources WHERE id = current_setting('test.ehr_resource')::uuid), 'owner cannot read';
  ASSERT NOT has_table_privilege(current_user, 'public.ehr_resources', 'INSERT'), 'client can import directly';
  ASSERT NOT has_column_privilege(current_user, 'public.records', 'ehr_resource_id', 'UPDATE'), 'client can change provenance';
  ASSERT NOT has_column_privilege(current_user, 'public.ehr_import_steps', 'pagination_checkpoint', 'SELECT'), 'checkpoint exposed';
END $$;
SELECT set_config('request.jwt.claim.sub', current_setting('test.ehr_other'), true);
DO $$ BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM public.ehr_resources WHERE id = current_setting('test.ehr_resource')::uuid), 'cross-account resource exposed';
  ASSERT NOT EXISTS (SELECT 1 FROM public.ehr_resource_versions WHERE resource_id = current_setting('test.ehr_resource')::uuid), 'cross-account history exposed';
END $$;
RESET ROLE;
ROLLBACK;
