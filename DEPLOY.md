# Deploying Steve Central's Images to the Internet

This site has a **Python backend** (`server.py`), so it needs a host that can *run Python*
— not a static-only host like GitHub Pages. This guide uses **Render.com** (free tier,
gives you HTTPS automatically). PythonAnywhere is an alternative but is geared toward WSGI
apps, so Render is the smoother path for this project.

---

## Before you deploy — checklist

- [ ] **Change the admin password.** Edit `ADMIN_PASS` in the launchers (and set it on the
      host, see below). Never ship the default.
- [ ] **Do NOT upload `my-email-password.txt`.** It holds your email password in plain text.
      Delete it before uploading, or exclude it (see `.gitignore` note below).
- [ ] **Do NOT upload `steve.db`** if it has real customer data you don't want online, and
      remember: on Render's free tier the database file is **not permanent** (it resets on
      redeploy). For a class demo that's fine; for real orders you'd need a managed database.
- [ ] Decide your email settings — you'll enter them as environment variables on the host,
      not in a file.

---

## Option A — Render.com (recommended)

### 1. Put the project on GitHub
1. Create a free account at https://github.com
2. Create a new **repository** (e.g. `steve-central-images`)
3. Add a file named **`.gitignore`** to the project with these lines so secrets/data aren't
   uploaded:
   ```
   my-email-password.txt
   steve.db
   ```
4. Upload all the project files to the repo (GitHub's "Add file → Upload files" works).

### 2. Create the web service on Render
1. Sign up at https://render.com (you can log in with GitHub)
2. Click **New → Web Service**, and connect your GitHub repo
3. Fill in:
   - **Runtime:** Python 3
   - **Build Command:** *(leave blank — no dependencies to install)*
   - **Start Command:** `python server.py`
   - **Instance Type:** Free
4. Under **Environment → Environment Variables**, add:
   | Key          | Value                                   |
   |--------------|-----------------------------------------|
   | `ADMIN_USER` | `steve`                                 |
   | `ADMIN_PASS` | *your strong admin password*            |
   | `SMTP_USER`  | `robertsteeved@yahoo.com`               |
   | `SMTP_PASS`  | *your Yahoo app password*               |
   | `NOTIFY_TO`  | `robertsteeved@yahoo.com`               |
   | `SMTP_HOST`  | `smtp.mail.yahoo.com`                   |
   | `SMTP_PORT`  | `587`                                   |
5. Click **Create Web Service**. Render builds it and gives you a public URL like
   `https://steve-central-images.onrender.com` — that's your live site, with HTTPS.

> Render sets a `PORT` for you automatically; `server.py` already reads it, so no change needed.

### 3. Test the live site
- Open the Render URL, click around, submit the contact form, place an order.
- Check the **robertsteeved@yahoo.com** inbox for the notification.
- Visit `https://your-url/admin` and log in with your admin user/password.

---

## Option B — A cloud VM (more control, more work)
Providers: DigitalOcean, Linode, AWS Lightsail. You get a Linux server, install Python,
copy the files, run `python server.py 80`, and set up HTTPS (e.g. with a reverse proxy like
Caddy or Nginx). Only go this route if you're comfortable with a Linux command line.

---

## Notes & limitations to be honest about
- **Free tiers sleep.** Render's free service spins down when idle, so the first visit after a
  while takes ~30 seconds to wake up. Fine for a demo.
- **The database isn't permanent on free tiers.** Contact messages and orders can be lost on
  redeploy. For a real business, use a managed database (Render Postgres, etc.).
- **The built-in server is lightweight.** It's great for a class project and low traffic; a
  high-traffic production site would move to a production server (gunicorn/uvicorn + a
  framework), but that's beyond what this project needs.
- **A custom domain** (like `stevecentralimages.com`) can be added in Render's settings after
  you buy the domain from a registrar (Namecheap, Google Domains, etc.).
