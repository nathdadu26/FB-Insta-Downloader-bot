FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

EXPOSE 8000

CMD ["python", "bot.py"]
```

---

**Koyeb me deploy karne ke steps:**

1. GitHub repo banao aur ye sab files push karo:
```
your-repo/
├── bot.py
├── health_check.py
├── requirements.txt
├── Dockerfile
├── .env          ← ye .gitignore me add karo!
```

2. `.gitignore` file banao:
```
.env
cookies.txt
ig_cookies.txt
__pycache__/
*.pyc
```

3. Koyeb me:
   - **New Service** → **GitHub** → repo select karo
   - **Builder**: Dockerfile
   - **Port**: `8000`
   - **Health check path**: `/health`
   - **Environment Variables** me sab `.env` values add karo

---

**Final folder structure:**
```
your-repo/
├── bot.py
├── health_check.py
├── requirements.txt
├── Dockerfile
└── .gitignore
