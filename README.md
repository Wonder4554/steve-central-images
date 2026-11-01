# Steve Central's Images — Full-Stack Photography Business Website

A complete, hand-coded full-stack web application for a photography business:
a public marketing site, customer-facing forms, a reviews system, email
notifications, a password-protected admin panel, and an interactive analytics
dashboard — built with **no frameworks and zero third-party dependencies**.

> **Live demo:** _add your Render/hosting link here_
> **Dashboard demo login (read-only):** username `demo` · password `demo`

---

## 📸 Screenshots

| Home | Dashboard | Reviews |
|:----:|:---------:|:-------:|
| ![Home page](images/screenshots/home.jpg) | ![Analytics dashboard](images/screenshots/dashboard.jpg) | ![Reviews page](images/screenshots/reviews.jpg) |

---

## ✨ Features

**Public site**
- Responsive multi-page site (Home, Pricing, About, Contact, History, Library, Reviews)
- Navy-and-gold theme driven entirely by CSS custom properties
- Auto-sliding photo gallery populated dynamically from the server

**Customer features (full-stack)**
- **Contact form** — submissions saved to the database and emailed to the owner
- **Pricing & ordering** — packages loaded from an API, order form with a **calendar date picker**
- **Reviews** — visitors leave star-rated reviews; **moderated** (hidden until the owner approves)
- Email notifications for every new message, order, and review (SMTP)

**Admin & analytics**
- Password-protected **admin panel**: view, delete, mark orders handled, approve reviews, export CSV
- **Analytics dashboard**: KPI cards, revenue-by-package and monthly-revenue charts, order-status
  breakdown, top customers, upcoming bookings, recent-activity feed, and a date-range filter
- **Read-only demo account** so the dashboard can be shown without exposing admin controls

**Engineering details**
- Clean REST-style JSON API (`/api/products`, `/api/gallery`, `/api/contact`, `/api/orders`, `/api/reviews`, …)
- SQLite persistence with automatic schema migrations and demo-data seeding
- HTTP Basic Auth with separate admin and read-only demo roles
- Per-IP rate limiting on public forms (spam guard)
- Client-side rendered dashboard that also works offline via an embedded demo snapshot

---

## 🛠 Tech Stack

| Layer      | Technology |
|------------|-----------|
| Front end  | HTML5, CSS3 (Flexbox/Grid, custom properties, keyframe animations), vanilla JavaScript (Fetch API), SVG |
| Back end   | Python 3 standard library (`http.server`, `sqlite3`, `smtplib`) — **no frameworks** |
| Database   | SQLite |
| Auth       | HTTP Basic Auth (admin + read-only demo roles) |
| Email      | SMTP (Yahoo/Gmail) |

---

## 🚀 Run it locally

Requires **Python 3** (no packages to install).

```bash
python server.py 8000
```

Then open **http://localhost:8000**. On Windows you can double-click `start-website.bat`.

- **Site:** http://localhost:8000
- **Dashboard:** http://localhost:8000/dashboard.html
- **Admin:** http://localhost:8000/admin

**Logins**
- Full admin: set `ADMIN_USER` / `ADMIN_PASS` (defaults are for local use only — change before deploying)
- Read-only demo: `demo` / `demo`

**Email notifications (optional):** set `SMTP_USER` and `SMTP_PASS` (an app password) as
environment variables. Without them the app still saves everything; it just skips the email step.

---

## 📁 Project structure

```
index.html, pricing.html, aboutus.html, contactus.html,
history.html, library.html, reviews.html   # public pages
dashboard.html                              # client-rendered analytics dashboard
styles.css                                  # site-wide theme
gallery.js                                  # data-driven photo gallery
server.py                                   # backend: API, DB, auth, email, admin
images/                                     # photos and logo
DEPLOY.md                                   # step-by-step hosting guide
```

---

## 📌 Notes
Originally built as a university web-implementation project and extended into a
full-stack application. All data shown in the live demo is sample data; no real
customer information is included.
