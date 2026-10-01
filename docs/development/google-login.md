# Set up Google login for Athenaeum

This guide connects Google sign-in to the shared Athenaeum account service.
One Google configuration covers Bernoulli, Quacktuaries, and future hosted apps.
Your application data stays in Athenaeum's database on the Oracle host.

The main website stays public. Its **Login** link opens
`https://valentemath.com/account/`, where users sign in and see their history.

## Before you start

Have these available:

- A Google account that will own the project.
- Access to the DNS settings for `valentemath.com` in Cloudflare.
- A support email address you want users to see.
- SSH access to the Athenaeum host for installing the credentials later.

You can complete steps 1–4 before deploying the new application images.
Complete the public-page checks and the live sign-in test after deployment.

## 1. Create a Google Cloud project

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Use the project selector at the top of the page.
3. Select **New project**.
4. Name it **Athenaeum Apps**, or another recognizable name.
5. Create the project and select it.
6. Open [Google Auth Platform](https://console.cloud.google.com/auth/overview).
7. If the page shows **Get started**, select it.

Google sign-in uses an OAuth client for a web application. You do not need a
service-account key, a Gmail integration, or Google Classroom API access.

## 2. Configure Branding and Audience

In **Google Auth Platform → Branding**, enter:

| Field | Value |
| --- | --- |
| App name | `Valente Math` |
| User support email | Your chosen support email |
| App home page | `https://valentemath.com/` |
| Privacy policy | `https://valentemath.com/privacy/` |
| Authorized domain | `valentemath.com` |
| Developer contact email | An address you monitor |

The home page and privacy page must be publicly accessible before you submit
branding verification. Use the main website as the home page; no separate
account landing page is needed. Keep a brief description of the classroom apps
and a footer link to the privacy page there.

A logo is optional; the shared Athenaeum mark is in
`design/mark.svg` if you want to prepare an upload in Google's accepted format.
An authorized **domain** is just `valentemath.com`, without `https://` or a path.

In **Audience**, choose **External** if users may use personal Google accounts
or school accounts from more than one organization. **Internal** limits access
to your Google Workspace organization and is available only in that context.

Google's setup screen says an External application starts in **Testing** and
is available to listed test users. Keep that status for the initial pilot.
After finishing the initial setup:

1. Open **Google Auth Platform → Audience**.
2. Confirm the user type is **External** and publishing status is **Testing**.
3. Under **Test users**, select **Add users**.
4. Add your own Google email and any pilot accounts, then save.

Google makes an exception to the test-user restriction for apps requesting only
`openid`, `email`, and `profile`, as this implementation does. Adding your pilot
accounts is still a useful setup step; Testing does not restrict this app to
those accounts. Once the live pilot works, follow step 8 to publish the app.

Reference: [Configure consent](https://developers.google.com/workspace/guides/configure-oauth-consent)
and [Manage app audience](https://support.google.com/cloud/answer/15549945?hl=en).

## 3. Choose the identity scopes

Open **Data Access**, then **Add or remove scopes**.
Select only the basic identity scopes:

| Scope | Purpose |
| --- | --- |
| `openid` | Establish the Google account identity |
| `https://www.googleapis.com/auth/userinfo.email` | Supply the email address |
| `https://www.googleapis.com/auth/userinfo.profile` | Supply basic profile information, such as name |

The application's request spells these as `openid email profile`.
Save your changes. Do not add Gmail, Drive, Contacts, or Classroom scopes.
These identity scopes do not require the sensitive/restricted-scope review;
branding verification is a separate process.

Reference: [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect).

## 4. Create the production OAuth client

1. Open **Clients**.
2. Select **Create client**.
3. Choose **Web application** as the application type.
4. Name it **Athenaeum production**.
5. Leave **Authorized JavaScript origins** empty. The implementation uses a
   server-side authorization-code flow.
6. Under **Authorized redirect URIs**, add exactly:

   ```text
   https://valentemath.com/auth/google/callback
   ```

7. Select **Create**.
8. Save the **Client ID** and **Client secret** in your password manager or
   another private credential store immediately. Download the client JSON if
   offered and keep it outside your repositories. Google may show the secret
   only at creation.

Use the apex domain, not `www.valentemath.com`. Do not add a trailing slash to
the callback URI. The scheme, hostname, path, and slash must match exactly.
There is no separate Google client or callback for each hosted app.

Reference: [Manage OAuth clients](https://support.google.com/cloud/answer/15549257).

## 5. Verify ownership of the domain

1. Open [Google Search Console](https://search.google.com/search-console).
2. Select **Add property**.
3. Choose the **Domain** property type and enter `valentemath.com`.
4. Copy the TXT record Google gives you.
5. In Cloudflare, open **valentemath.com → DNS → Records**.
6. Add a **TXT** record at the root domain (`@`) with Google's value. Keep
   existing TXT records; add this as a separate record.
7. Return to Search Console and select **Verify**. If verification has not
   propagated yet, wait and retry.
8. Keep the TXT record after verification.

The Google account verifying the domain should also be an owner or editor of
the Cloud project. Search Console ownership and Google Cloud project access
are separate permissions.

Reference: [Google brand verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification).

## 6. Install the credentials on the Athenaeum host

The new source, host tooling and image versions must be available before this
step. Follow the deployment procedure in [Guide 3](../guides/3-daily-usage.md) and the
account-specific details in [the account reference](../reference/accounts.md).
The host installer creates `/etc/athenaeum/accounts-session-secret` once and
preserves it on subsequent runs. This file is private and included in encrypted
backups. It also stores Google's credentials after the following command.

On the host, run:

```bash
sudo python3 /opt/athenaeum/stack/accounts/configure_google.py
```

At the prompts:

1. Paste the **Client ID** from step 4 and press Enter.
2. Paste the **Client secret** and press Enter. Its input is hidden.

The command preserves the existing session secret, file ownership, and mode
0600. It coordinates with the host's operation lock, so retry after a backup
or deployment finishes if the lock is busy.

Do not put the client secret in a command-line argument, Git, a screenshot, or
the public Compose file. Do not replace the account session secret manually.

For production, ensure `/etc/athenaeum/compose.env` includes:

```text
ACCOUNT_ORIGIN=https://valentemath.com
```

After credential installation, recreate the stack with the normal backed-up
deployment command:

```bash
athenaeumctl docker deploy
athenaeumctl verify
```

This is also necessary after credential rotation: the service loads its private
configuration at startup. Schedule deployments outside active classroom use.

## 7. Test the complete login

1. Open `https://valentemath.com/` and select **Login**.
2. Select **Sign in with Google** and choose your account.
3. Confirm the account overview shows the correct name and email.
4. Open Bernoulli and Quacktuaries. Both should show the same account.
5. Join a test classroom while signed in. Reveal a Bernoulli round or end a
   Quacktuaries game. Allow roughly 15 seconds for history to synchronize.
6. Open **Account** and check the recorded result.
7. Sign in to the same Google account in a second browser. Confirm the history
   and classroom identity return without copying the first browser's app cookie.
8. Test guest access in a separate browser. After an activity, sign in and use
   **Save activity** in that app to save only the activity you own.
9. Use the account overview's **Sign out** button.
   Confirm the account is signed out in both apps after navigation or refresh.

If students use school-managed Google accounts, repeat this test with a real
student account before classroom rollout. The school's administrator may need
to approve the OAuth client, even when a personal Google account works.

## 8. Publish the Google application

Once the pilot works:

1. Check that the public home page and privacy policy match the actual service.
   Review the privacy page's contact information and retention descriptions.
2. In **Audience**, change the publishing status from **Testing** to
   **In production**, using the publish action Google presents.
3. Complete **Branding → Verify Branding** if you want the app's display name
   and logo shown on the Google consent screen.
4. Resolve any issues Google lists, then select **Publish branding** when it is
   ready. Google distinguishes publishing the application from publishing its
   verified branding.
5. Test again with a Google account that was not part of your pilot.

You are requesting only basic identity information. If the console unexpectedly
asks for a sensitive/restricted-scope review, check Data Access for unintended
scopes before proceeding.

## Optional: a separate local development project

Keep development separate from the production OAuth project and client.
The local HTTPS stack defaults to `https://localhost:8443`.

1. Create a second Google Cloud project, such as **Athenaeum development**.
2. Configure the same basic identity scopes with an External testing audience.
3. Create a Web application client with this redirect URI:

   ```text
   https://localhost:8443/auth/google/callback
   ```

4. Initialize the local stack with `ops/local-stack init`.
5. Install the development credentials on your computer:

   ```bash
   python3 accounts/configure_google.py \
     --secret-file .state/local/accounts-session-secret \
     --lock-file .state/local/google-config.lock
   ```

6. Recreate the local stack: `ops/local-stack up --build --wait`.
7. Trust the local Caddy certificate as described in
   [local development](local-stack.md), then test login.

If you change the local HTTPS port, update the development client's exact
redirect URI too. Production OAuth clients should contain only production
redirects. Reference: [Production OAuth policies](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance).

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `redirect_uri_mismatch` | The URI in Clients must be exactly `https://valentemath.com/auth/google/callback`; also check `ACCOUNT_ORIGIN` |
| Google sign-in is unavailable | Install both credentials and recreate the accounts container |
| Personal account works; student account fails | Ask the Workspace administrator to review the client ID and student third-party-app access settings |
| App name or logo is missing on Google's screen | Complete branding verification and publish the verified branding |
| Login fails after returning from Google | Start again from the account page; allow cookies, finish within ten minutes, and check the host clock. Use the diagnostic steps below if it persists |
| Guest results do not appear | Sign in, then explicitly save the activity from its original browser; names alone cannot recover ownership |
| Results are waiting to synchronize | Check the app's Save activity page and accounts container health; sleeping apps retry when they wake |
| Google client secret was lost | Rotate/create credentials in Google Console, rerun the private installer, and recreate the accounts container |

For a repeated sign-in failure, retry once and run this on the host:

```bash
athenaeumctl docker logs accounts --tail 50
```

Look for `Google sign-in failed`. This line records the failing phase, exception
class, a recognized OAuth error, and the claim name if token validation failed.
It excludes exception messages, credentials, authorization codes and tokens.
You can share that line to diagnose the failure.
`invalid_client` indicates the installed credentials need checking;
`MismatchingStateError` indicates the browser's sign-in session was lost or
expired. A connection or timeout error indicates a failed request to Google.

Google Console labels can change. The linked official documentation describes
the current project, audience, branding, scope and client settings.
