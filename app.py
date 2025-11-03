from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    make_response,
    redirect,
    url_for,
)
import psycopg2
import os
import secrets
import feedparser
from datetime import datetime
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")


def get_db_connection():
    db_url = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(db_url, sslmode="require")
    return conn


def generate_token():
    return secrets.token_urlsafe(32)


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    # フィードテーブル
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS feeds_a1b2c3 (
            id SERIAL PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            token TEXT NOT NULL,
            UNIQUE (url, token)
        )
    """
    )

    # 記事テーブル
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS articles_d4e5f6 (
            id SERIAL PRIMARY KEY,
            feed_id INTEGER REFERENCES feeds_a1b2c3(id),
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            content TEXT,
            published_at TIMESTAMP,
            is_read BOOLEAN DEFAULT FALSE,
            starred BOOLEAN DEFAULT FALSE,
            token TEXT NOT NULL,
            UNIQUE (url, token)
        )
    """
    )

    conn.commit()
    cur.close()
    conn.close()


def fetch_full_content(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        # 一般的な記事本文のセレクター
        selectors = [
            "article",
            ".article-body",
            ".post-content",
            ".entry-content",
            ".main-content",
            ".content",
            ".post-body",
            ".blog-post-body",
        ]

        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                return element.get_text(separator="\n", strip=True)

        # セレクターが見つからない場合は全体から抽出
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        print(f"Error fetching full content: {e}")
        return None


@app.route("/")
def index():
    token = request.args.get("token") or request.cookies.get("token")
    if not token:
        token = generate_token()
        return redirect(url_for("index", token=token))

    feed_url = request.args.get("feed_url")
    if feed_url:
        feed = feedparser.parse(feed_url)
        feed_title = feed.feed.get("title", "Untitled Feed")

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT id FROM feeds_a1b2c3 WHERE url = %s AND token = %s
        """,
            (feed_url, token),
        )
        if not cur.fetchone():
            cur.execute(
                """
                INSERT INTO feeds_a1b2c3 (url, title, token)
                VALUES (%s, %s, %s)
            """,
                (feed_url, feed_title, token),
            )
            conn.commit()

        cur.close()
        conn.close()

    resp = make_response(render_template("index.html", token=token))
    resp.set_cookie("token", token)
    return resp


@app.route("/api/add_feed", methods=["POST"])
def add_feed():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403

    feed_url = request.json.get("url")
    if not feed_url:
        return jsonify({"error": "URL is required"}), 400

    # フィードをパースしてタイトルを取得
    feed = feedparser.parse(feed_url)
    feed_title = feed.feed.get("title", "Untitled Feed")

    conn = get_db_connection()
    cur = conn.cursor()

    # フィードが既に登録されているか確認
    cur.execute(
        """
        SELECT id FROM feeds_a1b2c3 WHERE url = %s AND token = %s
    """,
        (feed_url, token),
    )
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({"error": "Feed already exists"}), 400

    # フィードを登録
    cur.execute(
        """
        INSERT INTO feeds_a1b2c3 (url, title, token)
        VALUES (%s, %s, %s)
    """,
        (feed_url, feed_title, token),
    )

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success", "title": feed_title})


@app.route("/api/load_feeds")
def load_feeds():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403

    conn = get_db_connection()
    cur = conn.cursor()

    # フィードごとの既読数をカウント
    cur.execute(
        """
        SELECT f.id, f.url, f.title,
               (SELECT COUNT(*) FROM articles_d4e5f6 WHERE feed_id = f.id AND is_read = TRUE AND token = f.token) as read_count
        FROM feeds_a1b2c3 f
        WHERE f.token = %s
        ORDER BY read_count DESC
    """,
        (token,),
    )

    feeds = [
        {"id": row[0], "url": row[1], "title": row[2], "read_count": row[3]}
        for row in cur.fetchall()
    ]

    cur.close()
    conn.close()

    return jsonify({"feeds": feeds})


@app.route("/api/fetch_articles", methods=["POST"])
def fetch_articles():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403

    feed_id = request.json.get("feed_id")
    if not feed_id:
        return jsonify({"error": "Feed ID is required"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    # フィードURLを取得
    cur.execute(
        "SELECT url FROM feeds_a1b2c3 WHERE id = %s AND token = %s", (feed_id, token)
    )
    result = cur.fetchone()
    if not result:
        cur.close()
        conn.close()
        return jsonify({"error": "Feed not found"}), 404

    feed_url = result[0]

    # フィードをパースして記事を取得
    feed = feedparser.parse(feed_url)

    for entry in feed.entries:
        # 公開日時をパース
        published_at = None
        if hasattr(entry, "published_parsed"):
            published_at = datetime(*entry.published_parsed[:6])

        # リンク先から全文を取得
        full_content = fetch_full_content(entry.link)
        content = (
            full_content
            if full_content
            else (entry.description if hasattr(entry, "description") else entry.title)
        )

        # 記事を保存
        cur.execute(
            """"""
            INSERT INTO articles_d4e5f6 (feed_id, title, url, content, published_at, token)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (url, token) DO NOTHING
        """,
            (feed_id, entry.title, entry.link, content, published_at, token),
        )

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success"})


@app.route("/api/load_articles")
def load_articles():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403

    feed_id = request.args.get("feed_id")
    if not feed_id:
        return jsonify({"error": "Feed ID is required"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    # 記事一覧を取得
    cur.execute(
        """
        SELECT id, title, url, content, published_at, is_read, starred
        FROM articles_d4e5f6
        WHERE feed_id = %s AND token = %s
        ORDER BY published_at DESC
    """,
        (feed_id, token),
    )

    articles = [
        {
            "id": row[0],
            "title": row[1],
            "url": row[2],
            "content": row[3],
            "published_at": row[4].isoformat() if row[4] else None,
            "is_read": row[5],
            "starred": row[6],
        }
        for row in cur.fetchall()
    ]

    cur.close()
    conn.close()

    return jsonify({"articles": articles})


@app.route("/api/mark_as_read", methods=["POST"])
def mark_as_read():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403

    article_id = request.json.get("article_id")
    if not article_id:
        return jsonify({"error": "Article ID is required"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE articles_d4e5f6
        SET is_read = TRUE
        WHERE id = %s AND token = %s
    """,
        (article_id, token),
    )

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success"})


@app.route("/api/toggle_starred", methods=["POST"])
def toggle_starred():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403

    article_id = request.json.get("id")
    new_starred = request.json.get("starred")

    conn = get_db_connection()
    cur = conn.cursor()

    # お気に入り状態を更新
    cur.execute(
        """
        UPDATE articles_d4e5f6
        SET starred = %s
        WHERE id = %s AND token = %s
    """,
        (new_starred, article_id, token),
    )

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "success"})


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True)
