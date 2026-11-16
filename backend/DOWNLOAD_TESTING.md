# Preview and download sharing

Restart/redeploy the backend before deploying the frontend. No Supabase migration is needed: the API creates `document_permissions` in its existing SQLite database. Missing permission rows default to preview. Keep GRANT_DATA_DIR persistent; losing the permission table removes download permission (fails closed). Cloud Run ephemeral storage does not preserve these settings across instance replacement.

The permission controls the application's download action. Preview necessarily delivers decryptable content to the browser and cannot prevent screenshots, browser save tools, or a modified client from retaining it. Neither level grants editing rights. Revocation cannot erase copies already saved.

## Browser test

1. Use two separate browser profiles: patient/owner A and recipient B. Unlock both vaults and enroll them through the access panel.
2. As A, upload a harmless text file, PDF, and image. Share with B using Preview.
3. As B, refresh the records/access panel and open each file. Expect a preview, no Download record button, and no editing action.
4. As A, change the active share to Preview and download. As B, close and reopen the viewer. Download each file; compare its contents to the original. Client-encrypted files must save as the original format, not ciphertext.
5. Leave B's viewer open. As A, change the share back to Preview. B's existing Download button may remain visible, but clicking it must fail without saving a file. Close and reopen: the button disappears.
6. Enable Download again, reopen B's viewer, and revoke the share as A. B must not be able to download or reopen the document. The already rendered preview/copies cannot be recalled.
7. A third, unrelated account must not be able to preview or download either file. Owners can download their own documents without sharing.
8. Repeat with a wallet-signed blockchain grant. Revocation or unavailable RPC must block downloads.

## Automated checks

From backend/key-generation:

```sh
.venv/bin/python -m pytest tests/test_download_permissions.py tests/test_grant_api.py tests/test_documents.py tests/test_wallet_document_sharing.py -q
```

From frontend:

```sh
npx tsc --noEmit
npm test
```

The backend tests use isolated data and mocked blockchain calls; they do not establish live RPC, Hippius, deployed browser, or Cloud Run persistence behavior.
