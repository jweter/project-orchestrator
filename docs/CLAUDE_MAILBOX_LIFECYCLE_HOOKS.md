# Claude Code lifecycle mailbox pickup (M2, source-only)

**Scope:** Owner opt-in. This module does **not** edit Claude's settings, launch
Claude, change existing release pins, create tasks, or install a new polling
service. All communication is via the existing local SQLite coordination file.

The source adapter `project_orchestrator.claude_mailbox_lifecycle` supports:

- `SessionStart` and `UserPromptSubmit`: bounded read-only polling of the
  `claude-code` recipient's pending advisory inbox (up to five IDs), surfacing
  counts and message IDs only as Claude context. Raw untrusted message text,
  user prompts and private paths are never injected.
- `Stop`: **disabled by default**. If `IRON_GOD_CLAUDE_STOP_NOTICES=1`, send
  one replay-safe `TURN_ENDED_NOT_WORK_VERIFIED` notice per session to the
  `iron-god` inbox. A Stop is *per-turn*, not verified task completion.
- No automatic acknowledgments. Claude must open relevant messages through
  the existing scoped MCP mailbox or CLI and explicitly acknowledge them
  **after handling**, not when merely read. A correlated engineering outcome
  should use the existing structured handoff protocol with actual evidence.

## Owner-controlled integration

Prerequisites: Python 3.12, the **already installed** project-orchestrator
package accessible to the Python used by Claude, and the existing local
SQLite mailbox path. Do not install into or patch the pinned resident release
as a side effect of reading this document.

Set `IRON_GOD_MAILBOX_DB` in the **authorized Claude session environment** to
the absolute path of the pre-existing `coordination.sqlite`; do not expose
that file through a public endpoint or copy it into a Git checkout. The module
does nothing if that path is missing or invalid.

After the owner approves a separately guarded Claude hook configuration,
merge these `hooks` entries into Claude's existing settings. **Do not
overwrite other hooks or preferences.** This is an illustrative fragment,
not a file installed by this PR:

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "python",
                  "args": ["-m", "project_orchestrator.claude_mailbox_lifecycle"],
                  "timeout": 3}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "python",
                  "args": ["-m", "project_orchestrator.claude_mailbox_lifecycle"],
                  "timeout": 3}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "python",
                  "args": ["-m", "project_orchestrator.claude_mailbox_lifecycle"],
                  "timeout": 3}]}
    ]
  }
}
```

The optional Stop entry is inert without the explicit environment toggle.
A hook error exits zero without blocking the session or injecting untrusted
instructions. Neither the event input nor an advisory message can activate
paid Claude work, change owner controls, or approve a GitHub merge.

**Proving gate:** with an *already authorized* Claude session, verify a
`SessionStart` receives only pending message IDs, that normal MCP read/handled
acknowledgment clears a real message, and that restart sees remaining unread
messages again. Verify a `Stop` with the toggle disabled sends nothing.
Only afterward consider enabling the optional one-per-session yield notice.
No idle Claude session is awakened by this source change; supported event
hooks fire only at existing session boundaries.

Reference: https://code.claude.com/docs/en/hooks
