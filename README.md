---
title: FSL Video Label App
emoji: 🤟
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
---

# Filipino Sign Language Video-to-Label App

A simple FastAPI + browser frontend project that:

- opens the browser camera
- records a short video clip
- uploads the clip to FastAPI
- extracts a few frames
- sends them to Gemini if configured
- returns JSON prediction
- falls back safely if Gemini is missing or fails

## Local run

### Windows
```bash
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
python -m uvicorn main:app --reload

## Project structure

```text
.
├─ main.py
├─ requirements.txt
├─ Dockerfile
├─ README.md
├─ .env.example
└─ static/
   ├─ index.html
   ├─ app.js
   └─ style.css


