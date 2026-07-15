# Optional Cloud Deployment & Session Syncing

This guide outlines how to deploy Boundier to a cloud provider like Render. Cloud hosting requires setting up GitHub Gist syncing to preserve and update your ChatGPT browser session cookies across container restarts.

---

## 1. Setup GitHub Gist Syncing

Because cloud instances are ephemeral, Boundier can encrypt and backup your authenticated Chromium storage state to a private GitHub Gist.

1. Generate a **GitHub Personal Access Token (PAT)** with the `gist` scope.
2. Choose a secure **Encryption Key** passphrase to encrypt the session cookies.
3. Add these values to your `.env` file or environment variables:
   ```env
   GITHUB_PAT="your_github_pat"
   ENCRYPTION_KEY="your_encryption_passphrase"
   ```
4. Run Option `[2]` in the Boundier Terminal (`terminal.py`) to authenticate your ChatGPT account. Once logged in, your authenticated state will be encrypted and pushed to your Gist automatically.

---

## 2. Deploying to Render

Configure a web service on Render pointing to your fork/repository.

### Environment Variables
Configure the following in your Render Dashboard:
* `DISCORD_TOKEN`: Your Discord bot application token.
* `GITHUB_PAT`: The GitHub PAT with `gist` scope.
* `ENCRYPTION_KEY`: The passphrase chosen during setup.
* `PORT`: `10000` (for web service health checks).

Boundier will boot up, pull the encrypted state from the Gist, decrypt it, and run headless on the cloud container.
