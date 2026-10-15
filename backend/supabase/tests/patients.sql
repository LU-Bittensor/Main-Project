-- Run against a local/staging Supabase database after all migrations.
-- Fixtures and their changes are rolled back; no real credentials are used.
BEGIN;
INSERT INTO auth.users (id, email, raw_user_meta_data, created_at)
VALUES
  ('e04f664e-9e4e-4912-8ed7-405244dfcc01', 'patient-test@example.invalid',
   '{"role":"patient","full_name":"Patient test"}', now()),
  ('e04f664e-9e4e-4912-8ed7-405244dfcc02', 'provider-test@example.invalid',
   '{"role":"provider","full_name":"Provider test"}', now());
DO $$ BEGIN
  ASSERT EXISTS (SELECT 1 FROM public.patients
    WHERE id = 'e04f664e-9e4e-4912-8ed7-405244dfcc01'
      AND name = 'Patient test' AND email = 'patient-test@example.invalid'
      AND created_at = now()), 'patient signup row missing';
  ASSERT NOT EXISTS (SELECT 1 FROM public.patients
    WHERE id = 'e04f664e-9e4e-4912-8ed7-405244dfcc02'), 'provider created a patient row';
END $$;

UPDATE public.profiles SET full_name = 'Updated name'
WHERE id = 'e04f664e-9e4e-4912-8ed7-405244dfcc01';
UPDATE auth.users SET email = 'updated@example.invalid'
WHERE id = 'e04f664e-9e4e-4912-8ed7-405244dfcc01';

SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub', 'e04f664e-9e4e-4912-8ed7-405244dfcc01', true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM public.patients) = 1, 'patient RLS failed';
  ASSERT EXISTS (SELECT 1 FROM public.patients
    WHERE name = 'Updated name' AND email = 'updated@example.invalid'), 'sync failed';
  ASSERT NOT has_table_privilege(current_user, 'public.patients', 'INSERT'), 'client can insert patients';
  ASSERT NOT has_table_privilege(current_user, 'public.patients', 'UPDATE'), 'client can overwrite patients';
END $$;
SELECT set_config('request.jwt.claim.sub', 'e04f664e-9e4e-4912-8ed7-405244dfcc02', true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM public.patients) = 0, 'provider can read patient accounts';
END $$;
RESET ROLE;

DELETE FROM auth.users WHERE id = 'e04f664e-9e4e-4912-8ed7-405244dfcc01';
DO $$ BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM public.patients
    WHERE id = 'e04f664e-9e4e-4912-8ed7-405244dfcc01'), 'delete did not cascade';
END $$;
ROLLBACK;
