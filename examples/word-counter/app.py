"""A one-page web app: paste text, get the word count and the most common word."""
import argparse

from flask import Flask, render_template, request

from counter import count_words, most_common

app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    text = request.form.get("text", "")
    result = None
    if request.method == "POST":
        result = {"words": count_words(text), "common": most_common(text)}
    return render_template("index.html", text=text, result=result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port)
