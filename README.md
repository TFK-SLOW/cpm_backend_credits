# CPM Web Tool — Setup Guide

## Architecture

This project uses:

- **InfinityFree** — hosts the website/frontend
- **GitHub** — stores the project files
- **Render** — hosts the Python backend/API

```text
Browser
   |
   v
InfinityFree Website
   |
   | HTTPS
   v
Render Python Backend
   |
   +-- Login
   +-- Load account
   +-- Modify account
   +-- Save account
```

## 1. GitHub Setup

Create a GitHub repository for the backend.

The repository should contain:

```text
app.py
requirements.txt
README.md
```

The repository can be **private**. Render can deploy from private repositories that your connected GitHub account can access.

### Important

Do NOT upload API keys, passwords, bot tokens, or `.env` files to GitHub.

Keep backend secrets in Render Environment Variables.

## 2. Render Setup

Go to https://render.com/

1. Sign in with GitHub.
2. Click **New**.
3. Select **Web Service**.
4. Connect GitHub.
5. Select your backend repository.
6. Select the branch containing `app.py`.

Recommended settings:

```text
Language: Python 3
Build Command: pip install -r requirements.txt
Start Command: python app.py
Plan: Free
```

Create the service and wait for the deployment to finish.

Render will give you an address similar to:

```text
https://your-service-name.onrender.com
```

For this project, the backend address is:

```text
https://cpm-web-backend.onrender.com
```

Render supports Python web services from both public and private GitHub repositories. See the official documentation:
https://render.com/docs/web-services

## 3. Render Environment Variables

In Render:

1. Open your Web Service.
2. Open **Environment**.
3. Find **Environment Variables**.
4. Add:

```text
FIREBASE_KEY=YOUR_FIREBASE_API_KEY
```

Replace the value with your actual Firebase API key.

If your backend version requires them, also add:

```text
UNLOCK_SOURCE_EMAIL=your@email.com
UNLOCK_SOURCE_PASSWORD=your_password
```

Only add variables that your `app.py` actually uses.

Save and redeploy.

Render recommends environment variables for secrets so credentials do not need to be committed to source control:
https://render.com/docs/configure-environment-variables

## 4. Test Render

After deployment, open:

```text
https://cpm-web-backend.onrender.com/api/health
```

You should receive a successful health response.

If it fails:

1. Open your Render service.
2. Open **Logs**.
3. Check the Python error.
4. Make sure all required packages are in `requirements.txt`.
5. Make sure required environment variables are set.

## 5. InfinityFree Setup

Open your InfinityFree hosting account.

Use the File Manager or FileZilla and open:

```text
htdocs
```

Upload the frontend files:

```text
index.html
style.css
script.js
```

If the project contains other frontend files, upload those too.

Your `htdocs` folder should contain `index.html`.

## 6. Connect the Website to Render

Open:

```text
script.js
```

Find the API/base URL.

It should point to:

```text
https://cpm-web-backend.onrender.com
```

For example:

```javascript
const API_URL = "https://cpm-web-backend.onrender.com";
```

Do NOT put the Firebase secret/API key in `script.js`.

Anything in JavaScript can be viewed by website visitors.

## 7. Test the Website

Open your InfinityFree website.

Test:

1. Open the tool.
2. Enter the CPM login information.
3. Log in.
4. Load the account.
5. Select an operation.
6. Submit it.
7. Wait for the backend response.
8. Check the account in-game.

If the website cannot connect, first test:

```text
https://cpm-web-backend.onrender.com/api/health
```

## 8. Updating the Backend

To update the Python backend:

1. Edit `app.py`.
2. Test your changes.
3. Commit and push to GitHub.
4. Render will deploy the new commit if automatic deploys are enabled.

Render's GitHub integration supports automatic deployments from the linked branch:
https://render.com/docs/github

## 9. Updating the Website

For frontend changes:

```text
index.html
style.css
script.js
```

Upload the changed files to InfinityFree's `htdocs`.

If the old version appears, hard-refresh the browser or clear its cache.

## 10. requirements.txt

Render needs all Python dependencies listed in:

```text
requirements.txt
```

For example, depending on the backend:

```text
Flask
requests
brotli
```

Use the `requirements.txt` included with the working backend package if it already has one.

Do not remove packages unless you know they are unused.

## 11. Troubleshooting

### ModuleNotFoundError

Example:

```text
ModuleNotFoundError: No module named 'requests'
```

Add the missing package to `requirements.txt`, commit it to GitHub, and redeploy.

### Render crashes on startup

Check the Render logs.

Common causes:

- Missing Python package
- Missing environment variable
- Python syntax error
- Wrong start command
- Missing file
- Import error

The start command for this project should normally be:

```text
python app.py
```

### Website cannot connect to Render

Check:

```text
https://cpm-web-backend.onrender.com/api/health
```

If that works, check the API URL in `script.js`.

Make sure it uses HTTPS:

```text
https://cpm-web-backend.onrender.com
```

### Render deploy succeeds but API does not respond

Render web services need the server to listen on `0.0.0.0` and the assigned `PORT`. The working `app.py` should already handle this.

## 12. Free Render Notes

The Render Free plan is useful for testing and hobby projects, but free services have limitations.

A free web service can spin down after inactivity. The first request after it has been idle may therefore take longer while the service starts.

That does not mean the InfinityFree website is broken.

## 13. Security Checklist

Never commit:

```text
.env
Firebase API keys
Passwords
Telegram bot tokens
Private credentials
```

Never put private backend credentials inside:

```text
script.js
index.html
```

If a secret is accidentally uploaded to GitHub, rotate/revoke it and replace it with a new secret.

## 14. Final Setup

### GitHub

```text
Private Repository
|
+-- app.py
+-- requirements.txt
+-- README.md
```

### Render

```text
Web Service
|
+-- GitHub backend repository
+-- Build: pip install -r requirements.txt
+-- Start: python app.py
+-- Plan: Free
+-- FIREBASE_KEY: Environment Variable
```

### InfinityFree

```text
htdocs
|
+-- index.html
+-- style.css
+-- script.js
```

### Connection

```text
InfinityFree Website
        |
        | HTTPS
        v
https://cpm-web-backend.onrender.com
        |
        v
Python Backend
```

## Useful Links

Render:
https://render.com/

Render Web Services:
https://render.com/docs/web-services

Render Environment Variables:
https://render.com/docs/configure-environment-variables

Render GitHub:
https://render.com/docs/github

Render Python Versions:
https://render.com/docs/python-version

## Done

Once these three pieces are working:

```text
GitHub -> Render -> InfinityFree
```

your InfinityFree website can use the Python backend while keeping private backend credentials on Render.
