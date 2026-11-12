# Acceptance guide

1. Confirm ordinary CLI help/output and expiring single-use semantics remain.
2. Request a permanent link explicitly for an isolated hosted WordPress target; keep the actual key private.
3. Follow the complete link, confirm an authenticated session, log out and reuse it. Check beyond the ordinary expiry and verify request targets do not contain its key.
4. Wrong keys, null/foreign origins, missing users and invalid option combinations must refuse.
5. Issue a replacement; the old link must fail. Remove the installed fixture helper and verify no login.
6. Check local login output is unchanged and other provider modes reject permanent requests.
7. Retain sanitized evidence only; stop owned fixtures and preserve unrelated state.

New controller revision required: this feature branch, not installed old controllers. Do not deploy globally without approval.
