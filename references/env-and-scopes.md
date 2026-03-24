# Env And Scopes

This skill expects official X OAuth 2.0 Authorization Code with PKCE credentials.

## Accepted env keys

The loader accepts either the existing human-readable keys or canonical machine-friendly keys:

- `Client ID` or `X_CLIENT_ID`
- `Client Secret` or `X_CLIENT_SECRET`
- `Access Token` or `X_ACCESS_TOKEN`
- `Refresh Token` or `X_REFRESH_TOKEN`
- `User ID` or `X_USER_ID` (optional, auto-discovered if missing)
- `Consumer Key` or `X_CONSUMER_KEY`
- `Consumer Key Secret` or `X_CONSUMER_SECRET`
- `auth1 Access Token` or `X_AUTH1_ACCESS_TOKEN`
- `auth1 Access Secret` or `X_AUTH1_ACCESS_SECRET`

## Minimum useful scopes

- `tweet.read`
- `users.read`
- `tweet.write`
- `offline.access`

## Extra scopes by feature

- Image upload: `media.write`
- Like: `like.write`
- Repost: `tweet.write`

## Notes

- The script refreshes tokens against `https://api.x.com/2/oauth2/token`.
- For confidential clients, it uses HTTP Basic auth with `Client ID:Client Secret`.
- If `.env` already uses the spaced key names, the script preserves them and only updates the token values.
- Recent search only covers the last 7 days. "Hot" ranking is computed locally from recent-search results.
- Image upload uses OAuth 1.0a automatically when the Auth1 keys are present, because some X apps expose media upload more reliably there than on OAuth 2.0.
