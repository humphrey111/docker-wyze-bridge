import logging
import signal
import sys
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
)
from werkzeug.exceptions import NotFound

import wyze_bridge
from wyze_bridge import WyzeBridge

log = logging.getLogger(__name__)
wb: WyzeBridge = None


def clean_up():
    """Run cleanup before exit."""
    if not wb:
        sys.exit(0)
    wb.clean_up()


signal.signal(signal.SIGTERM, lambda *_: clean_up())


def create_app():
    wyze_bridge.setup_logging()

    log.info("create_app")
    app = Flask(__name__)
    global wb
    if not wb:
        wb = WyzeBridge()
        wb.start()

    @app.route("/")
    def index():
        columns = request.cookies.get("number_of_columns", "2")
        number_of_columns = int(columns) if columns.isdigit() else 0
        refresh = request.cookies.get("refresh_period", "30")
        refresh_period = int(refresh) if refresh.isdigit() else 0
        show_video = bool(request.cookies.get("show_video"))
        if "video" in request.args:
            show_video = True
        elif "snapshot" in request.args:
            show_video = False
        resp = make_response(
            render_template(
                "index.html",
                cameras=wb.get_cameras(urlparse(request.root_url).hostname),
                number_of_columns=number_of_columns,
                refresh_period=refresh_period,
                hass=wb.hass,
                version=wb.version,
                show_video=show_video,
            )
        )
        resp.set_cookie("number_of_columns", str(number_of_columns))
        resp.set_cookie("refresh_period", str(refresh_period))
        resp.set_cookie("show_video", "1" if show_video else "")
        return resp

    @app.route("/events/<path:event>/<path:cam>")
    def events(event: str, cam: str):
        if event == "start" and not wb.start_on_demand(cam):
            abort(404)
        return {}

    @app.route("/cameras")
    @app.route("/cameras/<path:cam_name>")
    @app.route("/cameras/<path:cam_name>/<path:cam_cmd>")
    def cameras(cam_name=None, cam_cmd=None):
        """JSON api endpoints."""
        if cam_name == "sse_status":  # Server Side Event
            return Response(wb.sse_status(), mimetype="text/event-stream")

        host = urlparse(request.root_url).hostname
        if cam_name and cam_cmd == "status":
            return {"status": wb.get_cam_status(cam_name)}
        if cam_name:
            return wb.get_cam_info(cam_name, host)
        return wb.get_cameras(host)

    @app.route("/snapshot/<path:img_file>")
    def rtsp_snapshot(img_file: str):
        """Use ffmpeg to take a snapshot from the rtsp stream."""
        uri = Path(img_file).stem
        if not wb.rtsp_snap(uri, wait=True):
            abort(404)
        return send_from_directory(wb.img_path, img_file)

    @app.route("/photo/<path:img_file>")
    def boa_photo(img_file: str):
        """Take a photo on the camera and grab it over the boa http server."""
        uri = Path(img_file).stem
        if photo := wb.boa_photo(uri):
            return send_from_directory(wb.img_path, uri + "_" + photo[0])
        return redirect(f"/img/{img_file}", code=302)

    @app.route("/img/<path:img_file>")
    def img(img_file: str):
        """Serve static image if image exists else take a new snapshot from the rtsp stream."""
        try:
            return send_from_directory(wb.img_path, img_file)
        except NotFound:
            return rtsp_snapshot(img_file)

    @app.route("/restart/<string:restart_cmd>")
    def restart_bridge(restart_cmd: str):
        """
        Restart parts of the wyze-bridge.

        /restart/cameras:       Stop and start all enabled cameras.
        /restart/rtsp_server:   Stop and start rtsp-simple-server.
        /restart/all:           Stop and start all enabled cameras and rtsp-simple-server.
        """
        if restart_cmd == "cameras":
            wb.stop_cameras()
            wb.run()
        elif restart_cmd == "rtsp_server":
            wb.stop_rtsp_server()
            wb.start_rtsp_server()
        elif restart_cmd == "all":
            wb.stop_cameras()
            wb.stop_rtsp_server()
            wb.run()
            restart_cmd = "cameras,rtsp_server"
        else:
            return {"result": "error"}

        return {"result": "ok", "restart": restart_cmd.split(",")}

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    app = create_app()
    app.run(debug=False, host="0.0.0.0", port=5000)
