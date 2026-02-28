"""Flask-based HTTP test server that mimics httpbin.org endpoints.

Used only for the rupy test suite — not part of the library itself.
"""

import base64
import json
import threading
import time
import urllib.parse
from flask import Flask, request, jsonify


app = Flask(__name__)
app.config["TESTING"] = True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _common_response():
    """Build the standard httpbin-like response dict."""
    ct = request.content_type or ""
    json_data = None
    raw_data = ""

    # Flask parses x-www-form-urlencoded bodies into request.form automatically,
    # leaving request.data empty — so read form directly.
    if "application/x-www-form-urlencoded" in ct:
        form_data = dict(request.form)
    elif request.data:
        form_data = {}
        if "application/json" in ct:
            try:
                json_data = request.get_json(force=True)
            except Exception:
                raw_data = request.data.decode("utf-8", errors="replace")
        else:
            raw_data = request.data.decode("utf-8", errors="replace")
    else:
        form_data = {}

    return {
        "args": dict(request.args),
        "headers": dict(request.headers),
        "form": form_data,
        "data": raw_data,
        "json": json_data,
        "url": request.url,
        "method": request.method,
    }


@app.route("/get", methods=["GET"])
def get_endpoint():
    return jsonify(_common_response())


@app.route("/post", methods=["POST"])
def post_endpoint():
    return jsonify(_common_response())


@app.route("/put", methods=["PUT"])
def put_endpoint():
    return jsonify(_common_response())


@app.route("/patch", methods=["PATCH"])
def patch_endpoint():
    return jsonify(_common_response())


@app.route("/delete", methods=["DELETE"])
def delete_endpoint():
    return jsonify(_common_response())


@app.route("/head", methods=["HEAD", "GET"])
def head_endpoint():
    return jsonify(_common_response())


@app.route("/headers")
def headers_endpoint():
    return jsonify({"headers": dict(request.headers)})


@app.route("/json")
def json_endpoint():
    return jsonify({
        "slideshow": {
            "author": "Yours Truly",
            "date": "date of publication",
            "slides": [{"title": "Wake up to WonderWidgets!", "type": "all"}],
            "title": "Sample Slide Show",
        }
    })


@app.route("/status/<int:code>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
def status_endpoint(code):
    return ("", code)


@app.route("/basic-auth/<user>/<passwd>", methods=["GET"])
def basic_auth(user, passwd):
    auth = request.authorization
    if auth and auth.username == user and auth.password == passwd:
        return jsonify({"authenticated": True, "user": user})
    from flask import Response
    return Response(
        "Unauthorized",
        status=401,
        headers={"WWW-Authenticate": 'Basic realm="test"'},
    )


@app.route("/redirect/<int:count>", methods=["GET"])
def redirect_endpoint(count):
    from flask import redirect
    if count <= 1:
        return redirect("/get")
    return redirect(f"/redirect/{count - 1}")


@app.route("/delay/<float:secs>", methods=["GET"])
@app.route("/delay/<int:secs>", methods=["GET"])
def delay_endpoint(secs):
    time.sleep(secs)
    return jsonify({"delay": secs})


@app.route("/anything", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def anything_endpoint():
    return jsonify(_common_response())


@app.route("/options", methods=["OPTIONS", "GET"])
def options_endpoint():
    from flask import Response as FlaskResponse
    resp = FlaskResponse("", status=200)
    resp.headers["Allow"] = "GET, POST, OPTIONS"
    return resp


@app.route("/cookies/set")
def set_cookies_endpoint():
    from flask import make_response
    resp = make_response(jsonify({"cookies_set": True}))
    resp.set_cookie("session_id", "abc123")
    resp.set_cookie("token", "xyz789")
    return resp


@app.route("/response-headers")
def response_headers_endpoint():
    """Return a response with custom headers specified in query params."""
    from flask import Response as FlaskResponse
    headers = dict(request.args)
    body = json.dumps(headers)
    resp = FlaskResponse(body, content_type="application/json")
    for k, v in headers.items():
        resp.headers[k] = v
    return resp


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def start_server(port: int = 18888):
    """Start the Flask server in a background daemon thread.

    Returns a simple object with a ``shutdown()`` method.
    """
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

    server_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False),
        daemon=True,
    )
    server_thread.start()
    # Wait for the server to be ready
    import socket, time
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)

    class _Server:
        def shutdown(self):
            pass  # daemon thread dies with the process

    return _Server()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18888
    print(f"Starting test server on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
