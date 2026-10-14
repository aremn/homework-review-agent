# GetCourse extension

Chrome extension for configuring homework-review prompts and connecting course accounts.

Requires Node.js 20+ and a Supabase deployment with the authentication and account-management Edge Functions used in `background.js`.

```powershell
npm ci
$env:SUPABASE_URL = "https://your-project.supabase.co"
npm run build
```

Load `dist/` as an unpacked extension in Chrome. Sign in through the popup.

Use the public project URL here. Backend service keys belong in the worker environment and must never enter the extension bundle.
