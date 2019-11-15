import shlex
import typing

import pyperclip

import mitmproxy.types
from mitmproxy import command
from mitmproxy import ctx, http
from mitmproxy import exceptions
from mitmproxy import flow
from mitmproxy.net.http.http1 import assemble
from mitmproxy.utils import strutils


def cleanup_request(f: flow.Flow) -> http.HTTPRequest:
    if not hasattr(f, "request"):
        raise exceptions.CommandError("Can't export flow with no request.")
    assert isinstance(f, http.HTTPFlow)
    request = f.request.copy()
    request.decode(strict=False)
    # a bit of clean-up - these headers should be automatically set by curl/httpie
    request.headers.pop('content-length')
    if request.headers.get("host", "") == request.host:
        request.headers.pop("host")
    if request.headers.get(":authority", "") == request.host:
        request.headers.pop(":authority")
    return request


def request_content_for_console(request: http.HTTPRequest) -> str:
    try:
        text = request.get_text(strict=True)
    except ValueError:
        # shlex.quote doesn't support a bytes object
        # see https://github.com/python/cpython/pull/10871
        raise exceptions.CommandError("Request content must be valid unicode")
    escape_control_chars = {chr(i): f"\\x{i:02x}" for i in range(32)}
    return "".join(
        escape_control_chars.get(x, x)
        for x in text
    )


def curl_command(f: flow.Flow) -> str:
    request = cleanup_request(f)
    args = ["curl"]
    for k, v in request.headers.items(multi=True):
        if k.lower() == "accept-encoding":
            args.append("--compressed")
        else:
            args += ["-H", f"{k}: {v}"]

    if request.method != "GET":
        args += ["-X", request.method]
    args.append(request.url)
    if request.content:
        args += ["-d", request_content_for_console(request)]
    return ' '.join(shlex.quote(arg) for arg in args)


def httpie_command(f: flow.Flow) -> str:
    request = cleanup_request(f)
    args = ["http", request.method, request.url]
    for k, v in request.headers.items(multi=True):
        args.append(f"{k}: {v}")
    cmd = ' '.join(shlex.quote(arg) for arg in args)
    if request.content:
        cmd += " <<< " + shlex.quote(request_content_for_console(request))
    return cmd


def raw(f: flow.Flow) -> bytes:
    return assemble.assemble_request(cleanup_request(f))  # type: ignore


formats = dict(
    curl=curl_command,
    httpie=httpie_command,
    raw=raw,
)


class Export():
    @command.command("export.formats")
    def formats(self) -> typing.Sequence[str]:
        """
            Return a list of the supported export formats.
        """
        return list(sorted(formats.keys()))

    @command.command("export.file")
    def file(self, fmt: str, f: flow.Flow, path: mitmproxy.types.Path) -> None:
        """
            Export a flow to path.
        """
        if fmt not in formats:
            raise exceptions.CommandError("No such export format: %s" % fmt)
        func: typing.Any = formats[fmt]
        v = func(f)
        try:
            with open(path, "wb") as fp:
                if isinstance(v, bytes):
                    fp.write(v)
                else:
                    fp.write(v.encode("utf-8"))
        except IOError as e:
            ctx.log.error(str(e))

    @command.command("export.clip")
    def clip(self, fmt: str, f: flow.Flow) -> None:
        """
            Export a flow to the system clipboard.
        """
        if fmt not in formats:
            raise exceptions.CommandError("No such export format: %s" % fmt)
        func: typing.Any = formats[fmt]
        v = strutils.always_str(func(f))
        try:
            pyperclip.copy(v)
        except pyperclip.PyperclipException as e:
            ctx.log.error(str(e))
