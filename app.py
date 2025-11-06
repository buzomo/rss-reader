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
from urllib.parse import urlparse

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


def extract_feed_url_from_html(html_url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(html_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        # RSSフィードのリンクを探す
        feed_link = soup.find("link", {"type": "application/rss+xml"})
        if feed_link and feed_link.get("href"):
            return feed_link.get("href")
        # atomフィードも探す
        feed_link = soup.find("link", {"type": "application/atom+xml"})
        if feed_link and feed_link.get("href"):
            return feed_link.get("href")
        return None
    except Exception as e:
        print(f"Error extracting feed URL: {e}")
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
    try:
        # フィードを登録（ユニーク制約により重複は自動的にエラーになる）
        cur.execute(
            """
            INSERT INTO feeds_a1b2c3 (url, title, token)
            VALUES (%s, %s, %s)
            """,
            (feed_url, feed_title, token),
        )
        conn.commit()
    except Exception as e:
        # 重複エラーの場合
        if "unique_feed_url" in str(e):
            return jsonify({"error": "Feed already exists"}), 400
        else:
            return jsonify({"error": "Database error"}), 500
    finally:
        cur.close()
        conn.close()
    return jsonify({"status": "success", "title": feed_title})


@app.route("/api/update_feed", methods=["POST"])
def update_feed():
    token = request.cookies.get("token") or request.json.get("token")
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
        # 記事を保存（新着記事は unlisted = FALSE で追加）
        content = entry.description if hasattr(entry, "description") else entry.title
        # 記事を保存
        cur.execute(
            """
            INSERT INTO articles_d4e5f6 (feed_id, title, url, content, published_at, token, unlisted)
            VALUES (%s, %s, %s, %s, %s, %s, FALSE)
            ON CONFLICT (url, token) DO NOTHING
            """,
            (feed_id, entry.title, entry.link, content, published_at, token),
        )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "success"})


@app.route("/api/load_feeds")
def load_feeds():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403
    conn = get_db_connection()
    cur = conn.cursor()
    # 新しい未読があるフィードだけを表示
    cur.execute(
        """
        SELECT f.id, f.url, f.title,
               (SELECT COUNT(*) FROM articles_d4e5f6 WHERE feed_id = f.id AND is_read = FALSE AND token = f.token AND unlisted = FALSE) as unread_count
        FROM feeds_a1b2c3 f
        WHERE f.token = %s
        AND EXISTS (
            SELECT 1 FROM articles_d4e5f6
            WHERE feed_id = f.id AND is_read = FALSE AND token = f.token AND unlisted = FALSE
        )
        ORDER BY unread_count DESC
        """,
        (token,),
    )
    feeds = [
        {"id": row[0], "url": row[1], "title": row[2], "unread_count": row[3]}
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
        # 記事を保存（新着記事は unlisted = FALSE で追加）
        content = entry.description if hasattr(entry, "description") else entry.title
        # 記事を保存
        cur.execute(
            """
            INSERT INTO articles_d4e5f6 (feed_id, title, url, content, published_at, token, unlisted)
            VALUES (%s, %s, %s, %s, %s, %s, FALSE)
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
    try:
        token = request.cookies.get("token")
        if not token:
            return jsonify({"error": "Token not found"}), 403
        feed_id = request.args.get("feed_id")
        if not feed_id:
            return jsonify({"error": "Feed ID is required"}), 400
        conn = get_db_connection()
        cur = conn.cursor()
        # スターした記事と unlisted = FALSE の記事のみを表示
        cur.execute(
            """
            SELECT a.id, a.title, a.url, a.content, a.published_at, a.is_read, a.starred, f.url as feed_url
            FROM articles_d4e5f6 a
            JOIN feeds_a1b2c3 f ON a.feed_id = f.id
            WHERE a.feed_id = %s AND a.token = %s AND (a.starred = TRUE OR a.unlisted = FALSE)
            ORDER BY
                CASE
                    WHEN a.is_read = FALSE THEN 0  -- 未読が最優先
                    WHEN a.is_read = TRUE AND a.starred = FALSE THEN 1  -- 既読が次
                    WHEN a.starred = TRUE THEN 2  -- スターが最後
                END,
                a.published_at DESC  -- それぞれのグループ内で新しい順
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
                "feed_url": row[7],
            }
            for row in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify({"articles": articles})
    except psycopg2.Error as e:
        print(f"Database error: {e}")
        return jsonify({"error": "Database error"}), 500
    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"error": "Internal server error"}), 500

        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/fetch_full_content", methods=["POST"])
def api_fetch_full_content():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403
    article_id = request.json.get("article_id")
    if not article_id:
        return jsonify({"error": "Article ID is required"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    # 記事のURLを取得
    cur.execute(
        "SELECT url FROM articles_d4e5f6 WHERE id = %s AND token = %s",
        (article_id, token),
    )
    result = cur.fetchone()
    if not result:
        cur.close()
        conn.close()
        return jsonify({"error": "Article not found"}), 404
    article_url = result[0]
    full_content = fetch_full_content(article_url)
    if full_content:
        # 全文をデータベースに更新
        cur.execute(
            """
            UPDATE articles_d4e5f6
            SET content = %s
            WHERE id = %s AND token = %s
        """,
            (full_content, article_id, token),
        )
        conn.commit()
    cur.close()
    conn.close()
    return jsonify({"content": full_content})


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


@app.route("/api/mark_starred_as_read", methods=["POST"])
def mark_starred_as_read():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403
    conn = get_db_connection()
    cur = conn.cursor()
    # スターした記事を全て既読にする
    cur.execute(
        """
        UPDATE articles_d4e5f6
        SET is_read = TRUE
        WHERE starred = TRUE AND token = %s
        """,
        (token,),
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
    # お気に入り状態を更新し、スターした場合は既読にする
    cur.execute(
        """
        UPDATE articles_d4e5f6
        SET starred = %s, is_read = CASE WHEN %s = TRUE THEN TRUE ELSE is_read END
        WHERE id = %s AND token = %s
        """,
        (new_starred, new_starred, article_id, token),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "success"})


@app.route("/api/subscribe_feed", methods=["POST"])
def subscribe_feed():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403
    url = request.json.get("url")
    if not url:
        return jsonify({"error": "URL is required"}), 400

    # URLからホスト名を取得
    parsed_url = urlparse(url)
    if not parsed_url.hostname:
        return jsonify({"error": "Invalid URL"}), 400

    # HTMLからフィードURLを抽出
    feed_url = extract_feed_url_from_html(url)
    if not feed_url:
        return jsonify({"error": "No feed found on the page"}), 404

    # フィードをパースしてタイトルを取得
    feed = feedparser.parse(feed_url)
    if not feed.feed.get("title"):
        return jsonify({"error": "Invalid feed"}), 400

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
        (feed_url, feed.feed.title, token),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "success"})


@app.route("/api/load_starred_articles")
def load_starred_articles():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.id, a.title, a.url, a.content, a.published_at, f.url as feed_url
        FROM articles_d4e5f6 a
        JOIN feeds_a1b2c3 f ON a.feed_id = f.id
        WHERE a.starred = TRUE AND a.token = %s
        ORDER BY a.published_at DESC
        """,
        (token,),
    )
    articles = [
        {
            "id": row[0],
            "title": row[1],
            "url": row[2],
            "content": row[3],
            "published_at": row[4].isoformat() if row[4] else None,
            "feed_url": row[5],
        }
        for row in cur.fetchall()
    ]
    cur.close()
    conn.close()
    return jsonify({"articles": articles})


@app.route("/api/purge_feed", methods=["POST"])
def purge_feed():
    token = request.cookies.get("token")
    if not token:
        return jsonify({"error": "Token not found"}), 403
    feed_id = request.json.get("feed_id")
    if not feed_id:
        return jsonify({"error": "Feed ID is required"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    # スター以外の記事を unlisted = TRUE に更新
    cur.execute(
        """
        UPDATE articles_d4e5f6
        SET unlisted = TRUE
        WHERE feed_id = %s AND token = %s AND starred = FALSE
        """,
        (feed_id, token),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "success"})


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)
