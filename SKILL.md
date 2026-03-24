---
name: x-account-ops
description: Operate an X account with official X API credentials stored in a local .env file. Use when Codex needs to publish X posts, publish image-plus-text posts, turn a long article into a thread, search a topic's recent or "hot" posts, like or repost a post, or automatically reply to hot posts using a supplied reply text or reply template. This skill supports OAuth 2.0 user tokens for general posting and search, plus OAuth 1.0a credentials for more reliable media upload when available.
---

# X Account Ops

Use the local CLI at `scripts/x_ops.py`. It reads OAuth 2.0 credentials from `.env`, refreshes expired access tokens with the refresh token, and writes the new tokens back to `.env`. If OAuth 1.0a credentials are also present, image upload and image-plus-text posting automatically use OAuth 1.0a as a fallback.

## Quick Start

Run from the skill directory or pass `--env-file` explicitly:

```bash
python scripts/x_ops.py me
python scripts/x_ops.py search --query "AI agents lang:en -is:retweet" --sort hot --limit 5
python scripts/x_ops.py post --text "Shipping a new build today." --image ./cover.png
python scripts/x_ops.py article --title "Launch notes" --text-file ./launch.md --image ./cover.png
python scripts/x_ops.py reply --tweet-id 1900000000000000000 --text "Strong point. The rollout risk is mostly around distribution, not capability."
python scripts/x_ops.py hot-reply --query "open source agents" --limit 2 --reply-template "Good thread, @{username}. The part about '{excerpt}' is the key constraint here."
```

## Credential Rules

- Default env file: workspace `.env`
- Accepted key names: `Client ID`, `Client Secret`, `Access Token`, `Refresh Token`
- Also accepted: `X_CLIENT_ID`, `X_CLIENT_SECRET`, `X_ACCESS_TOKEN`, `X_REFRESH_TOKEN`
- Optional Auth1 keys for media fallback: `Consumer Key`, `Consumer Key Secret`, `auth1 Access Token`, `auth1 Access Secret`
- If image upload is rejected on OAuth2, the script automatically uses Auth1 when those four keys exist. Read `references/env-and-scopes.md` if a request is rejected.

## Recommended Workflow

1. Run `python scripts/x_ops.py me` to confirm the token works.
2. Search before engaging:

```bash
python scripts/x_ops.py search --query "topic words here -is:retweet" --sort hot --limit 10
```

3. Draft the reply text in-context with the current task.
4. Send a direct reply:

```bash
python scripts/x_ops.py reply --tweet-id <id> --text "<reply>"
```

5. For batch engagement, use `hot-reply` with `--dry-run` first, inspect targets, then run it again without `--dry-run`.

## Command Guide

### Account and token

- `doctor`: check whether OAuth2 and Auth1 credentials are both healthy, and which path media upload will use
- `me`: show the authenticated account
- `refresh`: refresh the access token and persist it back into `.env`
- `lookup`: fetch one post by id and fail clearly when the post does not exist

### Publishing

- `post`: create a text post or image-plus-text post; image upload prefers Auth1 when available
- `article`: convert a long text file into a numbered thread; attach one image to the first post
- `reply`: reply to a specific post
- `delete`: delete a post owned by the authenticated account

### Discovery and engagement

- `search`: search recent posts and optionally rank them as "hot"
- `search` and `hot-reply` skip replies and reposts by default; use `--no-skip-replies` or `--no-skip-reposts` when you want broader recall
- `like`: like a post
- `repost`: repost a post
- `hot-reply`: search, rank, and reply to the hottest matches using either `--reply-text`, `--reply-text-file`, or `--reply-template`

## Behavioral Notes

- "Hot" is heuristic. X recent search returns reverse-chronological results, so the script re-ranks them using public engagement metrics plus a mild freshness decay.
- `article` is the skill's "图文文章" path. It turns long text into a thread because public X API support for standalone article publishing is not exposed here.
- `hot-reply` does not invent text on its own. Supply the final reply or a template so the behavior stays explicit and reviewable.
- For risky automation, prefer `--dry-run` first.

## References

- Scope and env details: `references/env-and-scopes.md`
