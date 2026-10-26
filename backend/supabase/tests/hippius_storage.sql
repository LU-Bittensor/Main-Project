-- Run as database administrator after migrations. Synthetic fixtures roll back.
BEGIN;
DO $$
DECLARE
  owner UUID := gen_random_uuid(); other_owner UUID := gen_random_uuid();
  store UUID := gen_random_uuid(); obj UUID := gen_random_uuid(); rec UUID := gen_random_uuid();
BEGIN
  INSERT INTO auth.users(id,email,raw_user_meta_data,created_at) VALUES
    (owner,owner::text || '@example.invalid','{"role":"patient"}',now()),
    (other_owner,other_owner::text || '@example.invalid','{"role":"patient"}',now());
  INSERT INTO public.patient_storage(id,owner_id,patient_hash,bucket_name,namespace,hash_key_version,encryption_key_version,wrapped_key)
    VALUES(store,owner,repeat('a',52),'ml-p-' || repeat('a',52),'test','v1','v1','synthetic-wrapped-key');
  INSERT INTO public.storage_objects(id,owner_id,patient_storage_id,object_key,mime_type,plaintext_size,ciphertext_size,ciphertext_sha256)
    VALUES(obj,owner,store,'documents/' || obj::text || '.enc','text/plain',2,35,repeat('a',64));
  BEGIN
    PERFORM public.publish_hippius_document(owner,rec,obj,'file.txt','');
    RAISE EXCEPTION 'Pending upload was published';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Verified upload unavailable' THEN RAISE; END IF;
  END;
  UPDATE public.storage_objects SET status='uploaded' WHERE id=obj;
  PERFORM public.publish_hippius_document(owner,rec,obj,'file.txt','');
  PERFORM public.publish_hippius_document(owner,rec,obj,'file.txt','');
  ASSERT (SELECT count(*) FROM public.records WHERE id=rec) = 1, 'Retry created duplicate';
  ASSERT (SELECT status FROM public.storage_objects WHERE id=obj) = 'ready';
  ASSERT (SELECT file_content IS NULL AND storage_object_id=obj FROM public.records WHERE id=rec);
  BEGIN
    UPDATE public.records SET owner_id=other_owner WHERE id=rec;
    RAISE EXCEPTION 'Cross-owner object reference accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
  BEGIN
    UPDATE public.storage_objects SET owner_id=other_owner WHERE id=obj;
    RAISE EXCEPTION 'Cross-owner storage reference accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
  BEGIN
    UPDATE public.records SET file_content=decode('6869','hex') WHERE id=rec;
    RAISE EXCEPTION 'Mixed storage accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  INSERT INTO public.records(owner_id,name,kind,file_mime_type,file_size,file_content)
    VALUES(owner,'legacy.txt','Document','text/plain',2,decode('6869','hex'));
  PERFORM set_config('test.hippius_owner',owner::text,true);
  PERFORM set_config('test.hippius_other',other_owner::text,true);
  PERFORM set_config('test.hippius_record',rec::text,true);
END $$;
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claim.sub',current_setting('test.hippius_owner'),true);
DO $$ BEGIN
  ASSERT NOT has_table_privilege(current_user,'public.patient_storage','SELECT'), 'Wrapped keys exposed';
  ASSERT NOT has_table_privilege(current_user,'public.storage_objects','INSERT'), 'Client can publish pointers';
  ASSERT NOT has_column_privilege(current_user,'public.records','storage_object_id','UPDATE'), 'Client can replace pointer';
  ASSERT NOT has_function_privilege(current_user,'public.publish_hippius_document(uuid,uuid,uuid,text,text,text,timestamptz)','EXECUTE'), 'Client can publish';
  ASSERT EXISTS(SELECT 1 FROM public.records WHERE id=current_setting('test.hippius_record')::uuid), 'Owner metadata unavailable';
END $$;
SELECT set_config('request.jwt.claim.sub',current_setting('test.hippius_other'),true);
DO $$ BEGIN
  ASSERT NOT EXISTS(SELECT 1 FROM public.records WHERE id=current_setting('test.hippius_record')::uuid), 'Other owner sees record';
END $$;
RESET ROLE;
ROLLBACK;
