## Summary

The closeout commits do the claimed credential freeze, start-time output redaction, provider-delete retention, SyncStore and workflow-draft restart, preference command freeze, preview encoding, wheel resource comparison, and MCP in-flight start drain. Those paths match the committed code and tests. The remaining correctness hole is the shared management retry: an uncertain failure now reloads live revisions and tells the user to repeat the operation, while the retained command id is keyed to that reloaded body. A committed Skill enable can therefore be undone by the retry the new message asks for.

## Issues

### Issue 1 -- Severity: bug
- File: gui/src/views/management/hooks.ts:79
- Description: On status 0 and 5xx, `useManagementMutate` keeps `retry.current` but also bumps `refresh` and calls `onChanged`. The retry key is `JSON.stringify([kind, target, body])` (line 67), and it is consulted only when the next call passes that same body. Skill bindings are not passed a frozen body. `WorkspaceToolsPage` feeds `refresh` into `SkillManager`, `useManagement` clears and reloads on that change, and `SkillCard` rebuilds the click from the new row: `change(item.enabled ? 'disable' : 'enable')` with `expected_digest: digest` (`gui/src/views/SkillManager.tsx:28` and `:67`). After a lost reply to a successful enable, the binding digest has changed and the same button is now disable. The next click therefore misses the stored command id, and `skills.lifecycle` records a new command (`src/morrow/application/skills/persistence.py:217`) instead of replaying the enable receipt. The same reload also drops the id for `skill-draft` edit. `DraftCard` sends `expected_row_version` from the reloaded draft (`SkillManager.tsx:89`), and `SkillDraftService.edit` ignores `command_id` and always allocates a new draft row (`src/morrow/application/skills/drafts.py:241`). A retry against the successor draft writes a second revision. The new hook test retries the identical body object, so it does not catch either path. Preference replay does not have this hole: `gui/src/state/preferences.ts:231` freezes the original operations and revision.
- Suggestion: Freeze the first management body and command id the way preference mutate does, and keep read-back from changing that retry. If a reload shows the write already applied, say that explicitly and do not leave the opposite Skill action labeled as a retry of the lost reply.
- Status: open

### Issue 2 -- Severity: suggestion
- File: gui/src/views/preferences/BatchPanel.tsx:66
- Description: `submit` returns false both when preference mutate gets an explicit 4xx and when the reply is lost. This line now tells both cases that the batch result is unconfirmed. A 4xx is a confirmed rejection: mutate already drops that command id (`gui/src/state/preferences.ts:252`). The batch panel does not render the row message, so this string is the only status the batch user sees.
- Suggestion: Have `submit` report rejection separately from an uncertain reply, and keep the unconfirmed wording for status 0 and 5xx only.
- Status: open
