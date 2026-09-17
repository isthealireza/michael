COMMIT AUTHORISATION — Stage 3. You are WORKER-4. Small task.

I verified your Stage 3 work myself: the diff, the rendered config parsing
with auxiliary.free_only present, the three-tool agent profile intact,
149 passed / 5 deselected, and mypy clean on 38 files. The owner has read the
verification and is content with the change.

Your phase-one evidence — quoting the auxiliary_client.py docstring at the
pinned tag rather than assuming the key existed — is the standard the owner
wants held on this project. Noted.

**You are authorised to make the local commit now.**

## Scope
Commit ONLY hermes/config.template.yaml.

Do not stage .orca/ — those are my orchestration files and stay untracked.
Do not stage hermes/config.yaml — it is rendered and git-ignored, and it
carries secrets.
Check `git status` before you commit and confirm nothing else is staged.

## Constraints
- **NO PUSH.** Push is the owner's alone. Local commit only.
- Do not amend or rebase any existing commit.
- Do not touch any other file.
- Never print a secret.

## Message
Write it in the style of the existing history (`git log --oneline -5` shows
the convention: a conventional-commit prefix and a plain factual subject).
The body should record WHY: the auxiliary lane's OpenRouter fallback was a
paid SKU spending outside the benchmarked $0.01171/run, and that the key was
verified against Hermes v0.21.0 (image v2026.8.31) before being written
rather than assumed.

## Observable acceptance
- `git status --short` before the commit, pasted.
- The commit hash and `git show --stat` for it, pasted, showing exactly one
  file changed.
- Confirmation that nothing was pushed and that origin/main is unchanged.
