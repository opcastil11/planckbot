# `.mcpignore` — secret filtering at the proxy layer

The upstream `@modelcontextprotocol/server-filesystem` has no notion of
an ignore list — it will happily serve whatever file Claude asks for,
including `.env.local`, `id_rsa`, `credentials.json`, and friends.
PlanckBot's proxy sits in between and enforces a blocklist **before**
the call reaches upstream.

## What happens when a path matches

- **Read-a-specific-file tools** (`read_file`, `read_text_file`,
  `get_file_info`, `read_media_file`): the proxy refuses WITHOUT
  calling upstream. Claude receives a short message naming the
  pattern that caught the path. The file contents never leave the
  upstream process, so they never get recorded as a triple.

- **Listing tools** (`list_directory`, `list_directory_with_sizes`,
  `directory_tree`, `search_files`): the upstream responds
  normally, then the proxy strips matching entries from the listing
  before forwarding. Claude never sees the entry, so it can't ask
  to read it.

Everything is logged to stderr so an operator tailing the proxy
stream can see what got blocked / redacted.

## Hard defaults (always enforced)

Dotenv files, private keys, SSH/GPG/cloud credentials, netrc, common
service-account dumps, kube/docker config files. Full list in
`src/planckbot/proxy/ignore.py :: HARD_DEFAULTS`. These are enforced
even when no `.mcpignore` exists — you'd have to edit the source to
disable them.

## Custom rules — `.mcpignore`

Drop a file named `.mcpignore` in the root of the path your MCP is
serving. One pattern per line; `#` starts a comment; blank lines
ignored. Patterns are standard `fnmatch` globs:

- `name` matches files or directories named exactly `name`
- `*.secret` matches any filename ending in `.secret`
- `secrets/` with a trailing slash matches the directory AND
  everything under it
- `config/local.json` matches a path component pattern

### Example for an orquesta-style project

```
# orquesta's .env and backups are already hard-blocked, but we
# also want to hide project-internal secret bundles.

# hand-rolled secret bundles
secrets/
secret-*.yml
secret-*.yaml

# terraform state often has plaintext passwords
*.tfstate
*.tfstate.backup

# Private scripts that shell out to production tokens
scripts/update-production-*.sh
scripts/rotate-*.sh

# Local developer tooling
.mycli-history
```

## How to verify it's active

1. Start your Claude Code session as usual.
2. Look at the proxy's stderr (enable `planckbot cron daemon` logs,
   or run `planckbot-mcp` in a foreground shell for debugging). On
   startup you should see:
       [planckbot-mcp] loaded .mcpignore from /path/to/.mcpignore
       (N patterns total)
3. In Claude, ask it to `read_text_file` on a blocked path. The
   response will start with "Refused by PlanckBot .mcpignore policy".
4. In Claude, ask it to `list_directory` on a path with blocked
   entries. They won't appear, and stderr will note the redaction.

## When to add custom patterns

The hard defaults cover the 80% case: dotenv, keys, SSH, cloud
credentials, netrc, service accounts, kube/docker config. Add custom
patterns when:

- Your project has secret files with non-standard names
  (`mytoken.txt`, `internal-keys.yml`).
- You want to exclude whole subdirectories from observation
  (`private/`, `scratch/`).
- A file is technically shareable but you don't want adapters
  trained on it (large binary dumps, personal notes).

The goal is to be conservative — triples are persistent, so a secret
recorded once lives in the DB forever. Better to over-block and
relax later than to under-block and clean up.
