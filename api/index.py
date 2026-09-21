import sys
import os

# Add root directory to sys.path so app and src can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# Vercel handler
handler = app
