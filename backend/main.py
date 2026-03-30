from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
import sqlite3
import smtplib
import os
import httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

app = FastAPI(title="Brain Checker AI Feedback System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ──────────────────────────────────────────────────────────────────
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "2SsUzikG3oXrW4tkaIba4cgTKVpAaJW5")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "harshalb2907@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "gxuepgbczaovrgxj")

BRANCH_EMAILS = {
    "pune":   "julabandjamun@gmail.com",
    "nashik": "nashik@brainchecker.com",
    "thane":  "thane@brainchecker.com",
}

DB_PATH = "brain_checker.db"

# ── Database ─────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            branch    TEXT    NOT NULL,
            rating    INTEGER NOT NULL,
            message   TEXT    NOT NULL,
            type      TEXT    NOT NULL,   -- 'complaint' or 'review'
            timestamp TEXT    NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_last_reviews(branch: str, limit: int = 10) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT message FROM feedback WHERE type='review' AND branch=? ORDER BY id DESC LIMIT ?",
        (branch, limit),
    )
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def save_feedback(branch: str, rating: int, message: str, fb_type: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO feedback (branch, rating, message, type, timestamp) VALUES (?, ?, ?, ?, ?)",
        (branch, rating, message, fb_type, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

# ── Schemas ───────────────────────────────────────────────────────────────────
class GenerateReviewRequest(BaseModel):
    branch: str
    rating: int

class SubmitFeedbackRequest(BaseModel):
    branch: str
    rating: int
    message: str
    type: str   # 'complaint' or 'review'

# ── AI ────────────────────────────────────────────────────────────────────────
async def generate_ai_review(branch: str, rating: int) -> str:
    past_reviews = get_last_reviews(branch)

    past_text = (
        "\n".join(f"- {r}" for r in past_reviews)
        if past_reviews
        else "No previous reviews yet."
    )

    prompt = f"""You are writing a genuine customer review for Brain Checker, a brain training and cognitive assessment center.

Branch: {branch.title()}
Customer Rating: {rating}/5 stars

Previous reviews (DO NOT repeat or closely paraphrase any of these):
{past_text}

Write a NEW, UNIQUE, AUTHENTIC-sounding customer review that:
- Is 2–3 sentences long
- Has a professional yet warm tone
- Highlights something specific (staff, ambiance, results, technology, etc.)
- Does NOT start with "I" 
- Does NOT sound robotic or templated
- Is clearly different from the previous reviews above

Return ONLY the review text, nothing else."""

    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "mistral-small-latest",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 150,
        "temperature": 0.9,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(MISTRAL_API_URL, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

# ── Email ─────────────────────────────────────────────────────────────────────
def send_complaint_email(branch: str, rating: int, complaint: str):
    recipient = BRANCH_EMAILS.get(branch.lower())
    if not recipient:
        raise ValueError(f"Unknown branch: {branch}")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"⚠️ New Complaint – Brain Checker {branch.title()} (Rating: {rating}/5)"
    msg["From"]    = SMTP_USER
    msg["To"]      = recipient

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;padding:24px;">
      <h2 style="color:#c0392b;">New Customer Complaint</h2>
      <table style="border-collapse:collapse;width:100%;max-width:600px;">
        <tr><td style="padding:8px;font-weight:bold;width:140px;">Branch</td>
            <td style="padding:8px;">{branch.title()}</td></tr>
        <tr style="background:#f8f8f8;">
            <td style="padding:8px;font-weight:bold;">Rating</td>
            <td style="padding:8px;">{'⭐' * rating} ({rating}/5)</td></tr>
        <tr><td style="padding:8px;font-weight:bold;">Date</td>
            <td style="padding:8px;">{datetime.utcnow().strftime('%d %b %Y, %H:%M UTC')}</td></tr>
        <tr style="background:#f8f8f8;">
            <td style="padding:8px;font-weight:bold;vertical-align:top;">Complaint</td>
            <td style="padding:8px;">{complaint}</td></tr>
      </table>
      <p style="margin-top:24px;color:#888;font-size:12px;">
        Sent automatically by Brain Checker AI Feedback System
      </p>
    </body></html>
    """

    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, recipient, msg.as_string())

# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.post("/generate-review")
async def generate_review(req: GenerateReviewRequest):
    if req.branch.lower() not in BRANCH_EMAILS:
        raise HTTPException(status_code=400, detail="Invalid branch name.")
    if req.rating < 4:
        raise HTTPException(status_code=400, detail="AI reviews only for ratings ≥ 4.")
    try:
        review = await generate_ai_review(req.branch.lower(), req.rating)
        return {"review": review}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Mistral API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/submit-feedback")
async def submit_feedback(req: SubmitFeedbackRequest):
    if req.branch.lower() not in BRANCH_EMAILS:
        raise HTTPException(status_code=400, detail="Invalid branch name.")
    if req.type not in ("complaint", "review"):
        raise HTTPException(status_code=400, detail="type must be 'complaint' or 'review'.")

    # Save to DB
    save_feedback(req.branch.lower(), req.rating, req.message, req.type)

    # Send email for complaints
    if req.type == "complaint":
        try:
            send_complaint_email(req.branch.lower(), req.rating, req.message)
        except Exception as e:
            # Don't fail the whole request if email fails; log it
            print(f"[EMAIL ERROR] {e}")

    redirect_url = (
        "https://www.google.com/maps/search/Brain+Checker/"
        if req.type == "review"
        else None
    )

    return {
        "status": "success",
        "message": "Feedback saved successfully.",
        "redirect_url": redirect_url,
    }


@app.get("/feedback")
def list_feedback(branch: Optional[str] = None, limit: int = 50):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if branch:
        c.execute(
            "SELECT * FROM feedback WHERE branch=? ORDER BY id DESC LIMIT ?",
            (branch.lower(), limit),
        )
    else:
        c.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [
        {"id": r[0], "branch": r[1], "rating": r[2],
         "message": r[3], "type": r[4], "timestamp": r[5]}
        for r in rows
    ]


@app.get("/health")
def health():
    return {"status": "ok", "service": "Brain Checker AI Feedback System"}
