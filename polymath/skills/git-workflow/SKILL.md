---
name: git-workflow
description: Git operations - branches, commits, merges, tags, history inspection, resolving conflicts.
---
## Rules
- Always non-interactive: `git commit -m "msg"`, `git merge --no-edit`, `git --no-pager log --oneline --graph --all`.
- Configure identity if missing: `git config user.email >/dev/null || git config user.email agent@example.com; git config user.name >/dev/null || git config user.name Agent`.
- Check state before and after every step: `git status --short`, `git branch -a`, `git log --oneline -5`.
- Merge commit (no fast-forward): `git merge --no-ff feature -m "Merge feature"`. Annotated tag: `git tag -a v1.0 -m "v1.0"`; lightweight: `git tag v1.0`.
- Find the default branch: `git symbolic-ref --short HEAD` / `git branch --show-current`.
- Conflicts: `git diff --name-only --diff-filter=U`, edit files to resolve, `git add`, `git commit --no-edit`.
