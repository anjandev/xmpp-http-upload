import contextlib
import errno
import json
import hashlib
import hmac
import pathlib
import typing

import flask

app = flask.Flask("xmpp-http-upload")
app.config.from_envvar("XMPP_HTTP_UPLOAD_CONFIG")
application = app


def sanitized_join(path: str, root: pathlib.Path) -> pathlib.Path:
    result = (root / path).absolute()
    if not str(result).startswith(str(root) + "/"):
        raise ValueError("resulting path is outside root")
    return result


def get_paths(base_path: pathlib.Path):
    data_file = pathlib.Path(str(base_path) + ".data")
    metadata_file = pathlib.Path(str(base_path) + ".meta")

    return data_file, metadata_file


def load_metadata(metadata_file):
    with metadata_file.open("r") as f:
        return json.load(f)


def get_info(path: str, root: pathlib.Path) -> typing.Tuple[
        pathlib.Path,
        dict]:
    dest_path = sanitized_join(
        path,
        pathlib.Path(app.config["DATA_ROOT"]),
    )

    data_file, metadata_file = get_paths(dest_path)

    return data_file, load_metadata(metadata_file)


@contextlib.contextmanager
def write_file(at: pathlib.Path):
    with at.open("xb") as f:
        try:
            yield f
        except:  # NOQA
            at.unlink()
            raise


@app.route("/")
def index():
    return flask.Response(
        "Welcome to XMPP HTTP Upload. State your business.",
        mimetype="text/plain",
    )


def stream_file(src, dest, nbytes):
    while nbytes > 0:
        data = src.read(min(nbytes, 4096))
        if not data:
            break
        dest.write(data)
        nbytes -= len(data)

    if nbytes > 0:
        raise EOFError


@app.route("/<path:path>", methods=["PUT"])
def put_file(path):
    try:
        dest_path = sanitized_join(
            path,
            pathlib.Path(app.config["DATA_ROOT"]),
        )
    except ValueError:
        return flask.Response(
            "Not Found",
            404,
            mimetype="text/plain",
        )

    verification_key = flask.request.args.get("v", "")
    length = int(flask.request.headers.get("Content-Length", 0))
    hmac_input = (path + " " + str(length)).encode("ascii")
    key = app.config["SECRET_KEY"]
    mac = hmac.new(key, hmac_input, hashlib.sha256)
    digest = mac.hexdigest()

    if not hmac.compare_digest(digest, verification_key):
        return flask.Response(
            "Invalid verification key",
            403,
            mimetype="text/plain",
        )

    content_type = flask.request.headers.get(
        "Content-Type",
        "application/octet-stream",
    )

    dest_path.parent.mkdir(parents=True, exist_ok=True, mode=0o770)
    data_file, metadata_file = get_paths(dest_path)

    try:
        with write_file(data_file) as fout:
            stream_file(flask.request.stream, fout, length)

            with metadata_file.open("x") as f:
                json.dump(
                    {
                        "headers": {"Content-Type": content_type}
                    },
                    f,
                )
    except EOFError:
        return flask.Response(
            "Bad Request",
            400,
            mimetype="text/plain",
        )
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            return flask.Response(
                "Conflict",
                409,
                mimetype="text/plain",
            )
        raise

    return flask.Response(
        "Created",
        201,
        mimetype="text/plain",
    )


@app.route("/<path:path>", methods=["HEAD"])
def head_file(path):
    try:
        data_file, metadata = get_info(
            path,
            pathlib.Path(app.config["DATA_ROOT"])
        )

        stat = data_file.stat()
    except (OSError, ValueError):
        return flask.Response(
            "Not Found",
            404,
            mimetype="text/plain",
        )

    response = flask.Response()
    response.headers["Content-Length"] = str(stat.st_size)
    for key, value in metadata["headers"].items():
        response.headers[key] = value
    return response


@app.route("/<path:path>", methods=["GET"])
def get_file(path):
    try:
        data_file, metadata = get_info(
            path,
            pathlib.Path(app.config["DATA_ROOT"])
        )
    except (OSError, ValueError):
        return flask.Response(
            "Not Found",
            404,
            mimetype="text/plain",
        )

    response = flask.make_response(flask.send_file(
        str(data_file),
    ))
    for key, value in metadata["headers"].items():
        response.headers[key] = value
    return response
