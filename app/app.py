import os
import re
import string

from flask import Flask, request, redirect, jsonify
import redis
import psycopg2

app = Flask(__name__)
ALPHABET = string.digits + string.ascii_lowercase + string.ascii_uppercase

cache = redis.Redis(
    host=os.environ.get("REDIS_HOST", "redis"),
    port=int(os.environ.get("REDIS_PORT", 6379)),
    decode_responses=True,
)


def get_db():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "db"),
        database=os.environ.get("POSTGRES_DB", "urlshortener"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
    )


def base62_encode(num):
    if num == 0:
        return ALPHABET[0]
    result = []
    while num > 0:
        num, remainder = divmod(num, 62)
        result.append(ALPHABET[remainder])
    return "".join(reversed(result))


def base62_decode(code):
    num = 0
    for char in code:
        if char not in ALPHABET:
            return None
        num = num * 62 + ALPHABET.index(char)
    return num

@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200

@app.route("/shorten", methods=["POST"])
def shorten_url():
    long_url = None
    data = request.get_json(silent=True)
    if isinstance(data, dict) and "url" in data:
        long_url = data["url"]
    elif request.form and "url" in request.form:
        long_url = request.form["url"]
    else:
        raw_body = request.get_data(as_text=True).strip()
        if raw_body:
            match = re.search(r'[\'"]?url[\'"]?\s*:\s*[\'"]?([^\'",}\s]+)', raw_body)
            if match:
                long_url = match.group(1)
            elif raw_body.startswith(("http://", "https://")):
                long_url = raw_body

    if not long_url or not isinstance(long_url, str):
        return jsonify({"error": "Missing url field"}), 400

    long_url = long_url.strip()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO urls (long_url) VALUES (%s) RETURNING id", (long_url,))
    url_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    short_code = base62_encode(url_id)

    cache.set(short_code, long_url)

    return jsonify({"short_url": f"http://localhost:5000/{short_code}", "short_code": short_code}), 201

@app.route("/<short_code>")
def redirect_url(short_code):
    long_url = cache.get(short_code)

    if long_url:
        print("CACHE HIT", flush=True)
        return redirect(long_url, code=302)

    print("CACHE MISS - querying database", flush=True)
    decoded_id = base62_decode(short_code)
    if decoded_id is None:
        return jsonify({"error": "Short URL not found"}), 404

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT long_url FROM urls WHERE id = %s", (decoded_id,))
    result = cur.fetchone()
    cur.close()
    conn.close()

    if not result:
        return jsonify({"error": "Short URL not found"}), 404

    long_url = result[0]
    cache.set(short_code, long_url)

    return redirect(long_url, code=302)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
