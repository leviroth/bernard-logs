import base36
import config
import functools
import math
import redis
import requests
import requests.auth
import sqlite3
import time
import urllib.parse
from uuid import uuid4
from flask import (Flask, render_template, g, abort, url_for, session,
                   redirect, request)


app = Flask(__name__)

app.secret_key = config.SECRET_KEY
app.config.database = config.DATABASE
app.config.client_id = config.CLIENT_ID
app.config.client_secret = config.CLIENT_SECRET
app.config.redirect_uri = config.REDIRECT_URI


def get_redis():
    if not hasattr(g, 'redis'):
        g.redis = redis.StrictRedis(host='localhost', port=6379, db=0)
    return g.redis


def connect_db():
    rv = sqlite3.connect(app.config.database, uri=True)
    rv.row_factory = sqlite3.Row
    return rv


def get_db():
    if not hasattr(g, 'sqlite_db'):
        g.sqlite_db = connect_db()
    return g.sqlite_db


def user_agent():
    '''reddit API clients should each have their own, unique user-agent
    Ideally, with contact info included.

    e.g.,
    return "oauth2-sample-app by /u/%s" % your_reddit_username
    '''
    return "BJO logs by /u/TheGrammarBolshevik"


# The following five functions are adapted from reddit's tutorial on OAuth:
# https://github.com/reddit/reddit/wiki/OAuth2-Python-Example

def base_headers():
    return {"User-Agent": user_agent()}


def save_created_state(state):
    r = get_redis()
    r.zadd("states", time.time() + 600, state)


def is_valid_state(state):
    r = get_redis()
    pipe = r.pipeline()
    pipe.zremrangebyscore("states", "-inf", time.time())
    pipe.zscore("states", state)
    return pipe.execute()[1] is not None


def make_authorization_url():
    # Generate a random string for the state parameter
    # Save it for use later to prevent xsrf attacks
    state = str(uuid4())
    save_created_state(state)
    params = {"client_id": app.config.client_id,
              "response_type": "code",
              "state": state,
              "redirect_uri": app.config.redirect_uri,
              "duration": "temporary",
              "scope": "identity"}
    url = "https://ssl.reddit.com/api/v1/authorize?" + \
          urllib.parse.urlencode(params)
    return url


def get_token(code):
    client_auth = requests.auth.HTTPBasicAuth(app.config.client_id,
                                              app.config.client_secret)
    post_data = {"grant_type": "authorization_code",
                 "code": code,
                 "redirect_uri": app.config.redirect_uri}
    headers = base_headers()
    response = requests.post("https://ssl.reddit.com/api/v1/access_token",
                             auth=client_auth,
                             headers=headers,
                             data=post_data)
    token_json = response.json()
    return token_json["access_token"]


def get_id_by_username(username):
    db = get_db()
    try:
        return next(
            db.execute("SELECT id FROM users WHERE upper(username) = upper(?)",
                       (username,)))["id"]
    except StopIteration:
        abort(404)


def get_username(access_token):
    headers = base_headers()
    headers.update({"Authorization": "bearer " + access_token})
    response = requests.get("https://oauth.reddit.com/api/v1/me",
                            headers=headers)
    me_json = response.json()
    return me_json['name']


@app.route('/login')
def login():
    return '''
    Log in via <a href="{}">reddit</a>.
    '''.format(make_authorization_url())


@app.route('/authorize')
def authorize():
    error = request.args.get('error', '')
    if error:
        return "Error: " + error
    state = request.args.get('state', '')
    if not is_valid_state(state):
        abort(403)
    code = request.args.get('code')
    access_token = get_token(code)
    session['username'] = get_username(access_token)
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    # remove the username from the session if it's there
    session.pop('username', None)
    return redirect(url_for('index'))


def get_where_clauses(restrictions):
    return "WHERE " + " AND ".join("{} = ?"
                                   .format(field) for field in restrictions)


def get_subs(username):
    db = get_db()
    rows = db.execute("""SELECT s.display_name
    FROM subreddits s
    LEFT JOIN subreddit_moderator sm ON (s.id = sm.subreddit_id)
    LEFT JOIN users u ON (u.id = sm.moderator_id)
    WHERE upper(u.username) = upper(?)
    ORDER BY subscribers DESC
    """, (username,))
    return [row[0] for row in rows]


def render_rows(subreddit, page, url_gen, restrictions):
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()

    username = session['username']
    subs = get_subs(username)
    if len(subs) == 0:
        abort(403)
    if subreddit not in subs:
        abort(403)

    try:
        subreddit_id = next(
            db.execute("SELECT id FROM subreddits WHERE display_name = ?",
                       (subreddit,)))["id"]
    except StopIteration:
        abort(404)

    restrictions['a.subreddit'] = subreddit_id
    where_clauses = get_where_clauses(restrictions)

    num_rows = next(
        db.execute("SELECT COUNT(*) from actions a {where_clauses}"
                   .format(where_clauses=where_clauses),
                   tuple(restrictions.values())))[0]
    num_pages = math.ceil(num_rows / 25)
    prev_url = url_gen(page=page - 1) if page > 1 else None
    next_url = url_gen(page=page + 1) if page < num_pages else None
    offset = (page - 1) * 25

    sql_command = """SELECT a.time, mod.username mod, a.target_type,
    a.target_id, author.username author,
    a.action_summary, a.action_details
    FROM actions a
    LEFT JOIN users mod ON (a.moderator=mod.id)
    LEFT JOIN users author ON (a.author=author.id)
    {where_clauses}
    ORDER BY a.time DESC
    LIMIT 25 OFFSET ?;
    """.format(where_clauses=where_clauses)
    rows = db.execute(sql_command,
                      tuple(restrictions.values()) + (offset,))
    return render_template('index.html', rows=rows, b36converter=base36.dumps,
                           subs=subs, subreddit=subreddit, page=page,
                           prev_url=prev_url, next_url=next_url,
                           num_pages=num_pages, username=username)


@app.route("/")
@app.route("/biggest")
def index():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    subs = get_subs(username)
    if len(subs) == 0:
        abort(403)

    return redirect(url_for('by_sub', subreddit=subs[0]))


@app.route("/r/<subreddit>/")
@app.route("/r/<subreddit>/page/<int:page>/")
def by_sub(subreddit, page=1):
    url_gen = functools.partial(url_for, 'by_sub', subreddit=subreddit)
    return render_rows(subreddit, page, url_gen, {})


@app.route("/r/<subreddit>/mod/<username>/")
@app.route("/r/<subreddit>/mod/<username>/page/<int:page>/")
def by_mod(subreddit=None, username=None, page=1):
    mod_id = get_id_by_username(username)
    url_gen = functools.partial(url_for, 'by_mod', subreddit=subreddit,
                                username=username)

    return render_rows(subreddit, page, url_gen,
                       restrictions={'a.moderator': mod_id})


@app.route("/r/<subreddit>/author/<username>/")
@app.route("/r/<subreddit>/author/<username>/page/<int:page>/")
def by_author(subreddit=None, username=None, page=1):
    author_id = get_id_by_username(username)

    url_gen = functools.partial(url_for, 'by_author', subreddit=subreddit,
                                username=username)

    return render_rows(subreddit, page, url_gen,
                       restrictions={'a.author': author_id})


if __name__ == "__main__":
    app.run()
