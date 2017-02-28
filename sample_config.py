# Generate a secret with os.urandom. Keep it secret, keep it safe.
SECRET_KEY = b'garbage data'

# This ensures we can't possibly ruin the database. Plus, some versions of
# Python seem not to like multiple processes accessing the same SQLite DB.
DATABASE = 'file:/path/to/database?mode=ro'

# These need to be separate from the "script" key that's used for the bot
# itself. You need to register a Web app.
CLIENT_ID = "your_client_id"
CLIENT_SECRET = "your_client_secret"
REDIRECT_URI = "http://your.url.here/authorize"
