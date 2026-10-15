BEGIN;

-- Supabase Auth generates the UUID and manages the salted password hash.
-- Keep the same identity used by records.owner_id and the grant API.
CREATE TABLE public.patients (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.patients ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.patients FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.patients TO authenticated;
GRANT ALL ON public.patients TO service_role;
CREATE POLICY "patients read own account" ON public.patients
  FOR SELECT TO authenticated USING ((SELECT auth.uid()) = id);

-- This replaces the existing signup trigger function, preserving profiles
-- and roles that the current frontend/API depend on.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  _role public.app_role;
BEGIN
  _role := COALESCE(NULLIF(NEW.raw_user_meta_data->>'role', ''), 'patient')::public.app_role;
  INSERT INTO public.profiles (id, full_name, organization)
  VALUES (NEW.id, COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
          NULLIF(NEW.raw_user_meta_data->>'organization', ''))
  ON CONFLICT (id) DO NOTHING;
  INSERT INTO public.user_roles (user_id, role) VALUES (NEW.id, _role)
  ON CONFLICT (user_id, role) DO NOTHING;
  IF _role = 'patient' THEN
    INSERT INTO public.patients (id, name, email, created_at)
    VALUES (NEW.id, COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
            COALESCE(NEW.email, ''), COALESCE(NEW.created_at, now()));
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.handle_new_user() FROM PUBLIC, anon, authenticated;

-- Backfill existing patient accounts using the stored role, not editable metadata.
INSERT INTO public.patients (id, name, email, created_at)
SELECT u.id, COALESCE(p.full_name, ''), COALESCE(u.email, ''), u.created_at
FROM auth.users u
JOIN public.user_roles r ON r.user_id = u.id AND r.role = 'patient'
LEFT JOIN public.profiles p ON p.id = u.id;

-- Keep the profile name and confirmed account email consistent after edits.
CREATE FUNCTION public.sync_patient_name()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  UPDATE public.patients SET name = NEW.full_name WHERE id = NEW.id;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.sync_patient_name() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER on_patient_profile_name_updated
AFTER UPDATE OF full_name ON public.profiles
FOR EACH ROW EXECUTE FUNCTION public.sync_patient_name();

CREATE FUNCTION public.sync_patient_email()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  UPDATE public.patients SET email = COALESCE(NEW.email, '') WHERE id = NEW.id;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.sync_patient_email() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER on_patient_auth_email_updated
AFTER UPDATE OF email ON auth.users
FOR EACH ROW EXECUTE FUNCTION public.sync_patient_email();

COMMIT;
