import os.path
import sys
import threading
import traceback
from http import HTTPStatus
from time import perf_counter
from typing import Literal, Optional, Tuple

# Import dependencies
sys.path.append(os.path.dirname(__file__) + "/deps")

import sublime
import sublime_plugin

from .rest_client import Response, client, parser
from .rest_client.request import Request

THROBBER_WIDTH = 6
THROBBER_REFRESH_RATE = 100

PANEL_NAME = "REST Client Response"


class RestException(Exception):
    pass


class HttpRequestThread(threading.Thread):
    def __init__(self, request: Request) -> None:
        super().__init__()
        self.request = request
        self.success: Optional[bool] = None
        self.response: Optional[Response] = None
        self.error: Optional[Tuple[Exception, str]] = None

    def run(self) -> None:
        self._start = perf_counter()
        try:
            self.response = client.request(self.request)
            self.success = True
        except Exception as exc:
            self.error = (exc, traceback.format_exc())
            self.success = False
        finally:
            self._end = perf_counter()
            self.elapsed = self._end - self._start

    def get_response(self) -> Response:
        if self.success is None or self.response is None:
            raise RestException("Attempted to retrieve response before completion")
        return self.response

    def get_error(self) -> Tuple[Exception, str]:
        if self.success is None or self.error is None:
            raise RestException("Attempted to retrieve error before completion")
        return self.error


class RestRequestCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        view = self.window.active_view()
        if view is None:
            return

        self.request = parser.parse(view)
        self.panel_view = self.window.create_output_panel(PANEL_NAME)
        self.window.run_command("show_panel", { "panel": f"output.{PANEL_NAME}" })
        self.send_request()

    def send_request(self):
        thread = HttpRequestThread(self.request)
        thread.start()

        output = "\n\n".join([
            f"{self.request.method} {self.request.url}",
            f"Waiting for response [{' ' * THROBBER_WIDTH}]",
        ])
        self.panel_view.run_command("update_rest_response_panel", { 'text': output })

        throbber_end = len(output) - 1
        throbber_start = throbber_end - THROBBER_WIDTH

        def update_throbber(position: int) -> None:
            throbber = f"{' ' * position}={' ' * (THROBBER_WIDTH - 1 - position)}"
            self.panel_view.run_command(
                "update_rest_response_panel",
                { 'text': throbber, 'region_start': throbber_start, 'region_end': throbber_end },
            )

        def poll(position: int, step: Literal[-1, 1]) -> None:
            if not thread.is_alive():
                if thread.success:
                    assert thread.response is not None
                    response = thread.response
                    sublime.set_timeout(lambda: self.on_success(response))
                else:
                    assert thread.error is not None
                    (exception, trace) = thread.error
                    sublime.set_timeout(lambda: self.on_error(exception, trace))
                return

            update_throbber(position)

            position = position + step
            if position == 0:
                step = 1
            elif position == THROBBER_WIDTH - 1:
                step = -1

            sublime.set_timeout(lambda: poll(position, step), THROBBER_REFRESH_RATE)

        sublime.set_timeout(lambda: poll(0, 1))

    def on_success(self, response: Response):
        headers_text = "\n".join(
            f"{header}: {value}" for header, value in response.headers.items()
        )
        http_status = HTTPStatus(response.status)
        output = "\n\n".join([
            f"{self.request.method} {self.request.url} {response.status} {http_status.phrase}",
            headers_text,
            response.data,
        ])
        self.panel_view.run_command("update_rest_response_panel", { 'text': output })

    def on_error(self, exception: Exception, trace: str):
        output = "\n\n".join([
            f"REST Client: Error on request to {self.request.method} {self.request.url}",
            repr(exception),
            trace,
        ])
        self.panel_view.run_command("update_rest_response_panel", { 'text': output })


class UpdateRestResponsePanelCommand(sublime_plugin.TextCommand):
    def is_visible(self) -> bool:
        return False

    def run(self, edit: sublime.Edit, text: str, region_start: Optional[int] = None, region_end: Optional[int] = None) -> None:
        print(text, region_start, region_end)
        self.view.set_scratch(True)
        self.view.assign_syntax("scope:source.http-response")

        region = sublime.Region(region_start or 0, region_end or self.view.size())
        self.view.replace(edit, sublime.Region(*region), text)
        self.view.sel().clear()
