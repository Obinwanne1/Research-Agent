import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod-please")
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "research_agent.db")
    RESEARCH_BASE_DIR = os.environ.get("RESEARCH_BASE_DIR", "research")
    MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "10"))
    CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "120"))
    PORT = int(os.environ.get("PORT", "5000"))
    DEBUG = os.environ.get("DEBUG", "true").lower() == "true"
