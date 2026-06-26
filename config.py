import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod-please")
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "research_agent.db")
    RESEARCH_BASE_DIR = os.environ.get("RESEARCH_BASE_DIR", "research")
    MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "10"))
    CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "120"))
    CLAUDE_FAST_TIMEOUT = int(os.environ.get("CLAUDE_FAST_TIMEOUT", "60"))
    PORT = int(os.environ.get("PORT", "5001"))
    DEBUG = os.environ.get("DEBUG", "true").lower() == "true"
    SESSION_LIFETIME_MINUTES = int(os.environ.get("SESSION_LIFETIME_MINUTES", "30"))
    API_RATE_LIMIT = int(os.environ.get("API_RATE_LIMIT", "20"))  # max research/job_search calls per user per hour
    SEARCH_CACHE_TTL_HOURS = int(os.environ.get("SEARCH_CACHE_TTL_HOURS", "6"))
    MAX_DOC_SIZE_MB = int(os.environ.get("MAX_DOC_SIZE_MB", "10"))
    MAX_DOC_CHARS = int(os.environ.get("MAX_DOC_CHARS", "15000"))
