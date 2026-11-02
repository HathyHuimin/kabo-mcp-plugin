# Linked accounts and the saved profile

Read this when the creator says "my account" without a handle, asks about their saved profile, or
asks to connect a public account.

- For "my account" without a handle, check whether `list_connected_accounts` is available and use
  the user's linked account; ask which one only when the request is ambiguous.
- `get_kabo_info` reads identity and granted permissions without credits.
- `get_creator_profile` provides stored context, not a fresh review; with `"effective": true` it adds
  the profile with the user's own edits (call once more without `effective` if Kabo refuses only
  that). When the user asks to change that saved profile, follow `references/profile-edits.md`.
- `get_social_connection` distinguishes official social authorization from a public-profile
  binding.
- Keep complete responses from these tools as context files, separate from the research evidence
  the driver expects; never fabricate connector envelopes for them.
- Only when the user asks to connect a public account, use `connect_public_account`. A replacement
  requires user confirmation plus the current id and bindingVersion. It can use data allowance; it
  neither starts an official social OAuth flow nor runs an account analysis. Write access is granted
  on first use: if it fails with `insufficient_scope`, ask the user to approve the host's prompt and
  retry once.
- If these tools or permissions are unavailable, explain the gap and continue the public review
  with a supplied handle.
