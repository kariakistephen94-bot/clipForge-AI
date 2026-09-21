# Publishing to YouTube and TikTok

ClipForge AI can upload a rendered clip straight to **your own** YouTube channel and TikTok account.
You register your own developer apps (free), paste two keys per platform into `.env`, and connect the
accounts once from Settings.

**How publishing behaves**

* Nothing is ever posted automatically. You pick a clip, press **Publish…**, review exactly what will be
  posted, and press the final button. Uploads then run in the background.
* Clips that **FAIL** the campaign compliance check are refused.
* Defaults are the safe ones: YouTube **private**, TikTok **drafts**. Posting publicly needs one extra tick.
* Your tokens are stored only in `workspace/credentials/*.json` (permissions `0600`), never in the database,
  never sent to the browser, never written to logs. "Disconnect" deletes the file.

---

## 1. YouTube (about 10 minutes)

1. Open <https://console.cloud.google.com/> and sign in with the Google account that owns the channel.
2. Top bar → project dropdown → **New project** → name it `ClipForge AI` → **Create**.
3. Left menu → **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
4. **APIs & Services → OAuth consent screen**:
   - User type **External** → **Create**
   - App name `ClipForge AI`, your email for both support and developer contact → **Save and continue**
   - Scopes page: **Save and continue** (ClipForge requests scopes at sign-in)
   - **Test users → Add users →** your own Google address → **Save and continue**
5. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - Name: `ClipForge local`
   - **Authorised redirect URIs → Add URI**, paste exactly:
     ```
     http://127.0.0.1:8765/api/publish/youtube/callback
     ```
   - **Create**, then copy the **Client ID** and **Client secret**.
6. Put them in `.env` in the ClipForge folder:
   ```
   YOUTUBE_CLIENT_ID=1234...apps.googleusercontent.com
   YOUTUBE_CLIENT_SECRET=GOCSPX-...
   ```
7. Restart ClipForge (`Ctrl+C`, then `./start.sh`) → **Settings → Connected accounts → Connect** next to YouTube.
   Approve the permissions in the window that opens. Google will warn that the app is unverified — that is
   expected for an app only you use; choose **Advanced → Go to ClipForge AI (unsafe)**.

**Things to know**

| | |
|---|---|
| Daily limit | A new Google Cloud project gets a fixed daily API allowance. Uploads are the expensive call, so expect only a handful of uploads per day before YouTube returns a quota error (it resets at midnight US Pacific time). Request more in the Cloud console if you need it. |
| Test-mode expiry | While the consent screen stays in **Testing**, Google expires the connection after ~7 days. Just press **Connect** again, or publish the app (needs Google verification for upload scope). |
| Shorts | A vertical clip of 3 minutes or less is treated as a Short automatically. ClipForge uploads 1080×1920, so your clips qualify. |

---

## 2. TikTok (about 15 minutes, plus review time for public posting)

1. Open <https://developers.tiktok.com/> → **Manage apps** → **Connect an app** (register as a developer if asked).
2. Create the app, then add these products:
   - **Login Kit** (sign-in)
   - **Content Posting API** (uploading)
3. In Content Posting API settings choose the permission you want:
   - **`video.upload`** — clips land in your TikTok **inbox/drafts** and you finish posting in the app.
     Works immediately, no review.
   - **`video.publish`** — posts directly. Requires TikTok to **audit/approve** your app. Until approved,
     posts are forced to private (SELF_ONLY).
4. **Redirect URI**: add
   ```
   http://127.0.0.1:8765/api/publish/tiktok/callback
   ```
   TikTok may reject a plain `http://` address. If it does, register any HTTPS page you control
   (even a personal site) and use ClipForge's **paste-the-code** fallback: after approving access,
   TikTok redirects to that page with `?code=...` in the address bar — copy that value into the box
   ClipForge shows under **Connected accounts**.
5. Copy **Client key** and **Client secret** into `.env`:
   ```
   TIKTOK_CLIENT_KEY=aw...
   TIKTOK_CLIENT_SECRET=...
   ```
6. Restart ClipForge → **Settings → Connected accounts** → pick drafts or direct posting → **Connect**.

**Things to know**

* Unapproved apps: private posts or drafts only. That is TikTok's rule, not a ClipForge limitation.
* TikTok rate-limits posting; if you publish many clips quickly you will see a "too many recent posts" error.
* Drafts mode still needs you to open TikTok and press Post — TikTok requires that final human step.

---

## 3. Publishing a clip

1. Analyze and render a project as usual.
2. On a rendered clip press **Publish…**
3. Choose the version (A hook overlay / B alternative hook / C captions only), check the title, description,
   tags, caption and visibility, then press **Publish to …**.
4. Progress appears in the job panel on the project page. Results (with links) are listed under
   **Settings → Connected accounts → Recent publishes**.

## Troubleshooting

| Message | What to do |
|---|---|
| "YouTube is not configured" | The two `YOUTUBE_*` values are missing from `.env`, or ClipForge wasn't restarted after adding them |
| "quota for today is used up" | You hit the daily API allowance; wait for the reset or request more quota |
| "connection is no longer valid" / expired | Press **Connect** again (normal weekly for apps in Testing mode) |
| "not audited yet, can only post privately" | Use drafts mode, or submit your TikTok app for review |
| "too many recent posts" | TikTok rate limit — wait before publishing more |
| Redirect page shows an error instead of "connected" | Check the redirect URI in the developer console matches exactly, including port `8765` |
