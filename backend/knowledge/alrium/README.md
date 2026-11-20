# Alrium knowledge base (demo content)

Demo content written for the CoO hackathon. The people, vendors, prices and policies are invented. This is not real Alrium policy.

Each document is a markdown file with front matter:

```
---
title: Leave and holidays
category: HR
audience: everyone   # or "admins": only users with knowledge:read_restricted can retrieve it
updated: 2026-10-01
---
```

`## ` headings split a document into the passages that search returns, so keep each section about one topic.

Load or reload everything (this README is skipped):

```bash
cd backend
uv run python -m app.seed
```
