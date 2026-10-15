-- Run after all migrations on local/staging Supabase; all fixtures roll back.
BEGIN;
INSERT INTO auth.users (id, email, raw_user_meta_data, created_at) VALUES
  ('f96a9c87-4001-4401-8801-000000000001', 'doc-owner@example.invalid', '{"role":"patient"}', now()),
  ('f96a9c87-4001-4401-8801-000000000002', 'doc-other@example.invalid', '{"role":"provider"}', now());
INSERT INTO public.records (id, owner_id, name, kind, tags, file_content, file_mime_type, file_size)
VALUES ('f96a9c87-4001-4401-8801-000000000003', 'f96a9c87-4001-4401-8801-000000000001',
        'test.txt', 'Document', 'test', decode('6869', 'hex'), 'text/plain', 2);
DO $$ BEGIN
  BEGIN
    UPDATE public.records SET file_size = 3 WHERE id = 'f96a9c87-4001-4401-8801-000000000003';
    RAISE EXCEPTION 'Length constraint did not reject inconsistent bytes';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', 'f96a9c87-4001-4401-8801-000000000001', true);
DO $$ BEGIN
  ASSERT EXISTS (SELECT 1 FROM public.records WHERE id = 'f96a9c87-4001-4401-8801-000000000003'
    AND file_content = decode('6869', 'hex')), 'owner cannot read own bytes';
  ASSERT NOT has_column_privilege(current_user, 'public.records', 'file_content', 'INSERT'), 'client can bypass upload validation';
  ASSERT NOT has_column_privilege(current_user, 'public.records', 'file_content', 'UPDATE'), 'client can replace bytes';
  ASSERT NOT has_column_privilege(current_user, 'public.records', 'owner_id', 'UPDATE'), 'client can transfer ownership';
  ASSERT has_column_privilege(current_user, 'public.records', 'tags', 'UPDATE'), 'metadata editing lost';
END $$;
SELECT set_config('request.jwt.claim.sub', 'f96a9c87-4001-4401-8801-000000000002', true);
DO $$ BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM public.records WHERE id = 'f96a9c87-4001-4401-8801-000000000003'), 'unrelated account can read bytes';
END $$;
RESET ROLE;
ROLLBACK;
