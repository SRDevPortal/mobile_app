import io
import socket
from unittest import TestCase
from unittest.mock import Mock, patch
from PIL import Image
import frappe
from mobile_app.api import image_download as dl, profile_image


def png():
    out = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(out, "PNG")
    return out.getvalue()


class FakeResponse:
    def __init__(self, data=b"", status=200, headers=None):
        self.data = io.BytesIO(data)
        self.status = status
        self.headers = headers or {}
        self.closed = False
    def getheader(self, name, default=None):
        return self.headers.get(name, default)
    def read1(self, size):
        return self.data.read(size)
    def close(self):
        self.closed = True


def connection(response):
    conn = Mock()
    conn.getresponse.return_value = response
    return conn


class TestImageDownload(TestCase):
    def test_valid_image_and_false_mime(self):
        response = FakeResponse(png(), headers={"Content-Type": "text/plain"})
        conn = connection(response)
        with patch.object(dl, "_connection", return_value=(conn, "/image")):
            body, fmt = dl.download_image("https://public.example/image")
        self.assertEqual(fmt, "PNG")
        self.assertEqual(body, png())
        self.assertTrue(response.closed)
        conn.close.assert_called_once()
        with patch.object(dl, "_connection", return_value=(connection(FakeResponse(b"not an image"*20, headers={"Content-Type": "image/png"})), "/")):
            with self.assertRaises(dl.ImageDownloadError):
                dl.download_image("https://public.example/fake.png")

    def test_size_limit_declared_and_streamed(self):
        for data, headers in [(b"", {"Content-Length": str(dl.MAX_BYTES+1)}), (b"x"*(dl.MAX_BYTES+1), {})]:
            response=FakeResponse(data, headers=headers)
            conn=connection(response)
            with patch.object(dl, "_connection", return_value=(conn, "/")):
                with self.assertRaisesRegex(dl.ImageDownloadError, "too large"):
                    dl.download_image("https://public.example/image")
            self.assertTrue(response.closed)

    def test_redirects_revalidate_and_close(self):
        first=connection(FakeResponse(status=302, headers={"Location":"http://127.0.0.1/private"}))
        with patch.object(dl, "_connection", side_effect=[(first,"/"),dl.ImageDownloadError("private")]) as build:
            with self.assertRaises(dl.ImageDownloadError):
                dl.download_image("https://public.example/image")
            self.assertEqual(build.call_args_list[1].args[0], "http://127.0.0.1/private")
        first.close.assert_called_once()

    def test_private_dns_and_mixed_answers_blocked(self):
        for addresses in [["127.0.0.1"],["10.1.1.1"],["169.254.169.254"],["::1"],["93.184.216.34","192.168.1.1"]]:
            answers=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a,443)) for a in addresses]
            with patch.object(socket,"getaddrinfo",return_value=answers):
                with self.assertRaises(dl.ImageDownloadError):
                    dl._connection("https://public.example/image", dl.time.monotonic()+20)

    def test_validated_ip_is_pinned_and_tls_keeps_hostname(self):
        with patch.object(dl, "_resolve", return_value=["93.184.216.34"]):
            conn,_=dl._connection("https://images.example/file", dl.time.monotonic()+20)
        with patch.object(socket,"create_connection", return_value=Mock()) as create:
            conn._create_connection(("images.example",443))
        self.assertEqual(create.call_args.args[0],("93.184.216.34",443))
        self.assertEqual(conn.host, "images.example")
        self.assertTrue(conn._context.check_hostname)

    def test_deadline_redirect_cap_and_bad_schemes(self):
        with patch.object(dl,"TOTAL_SECONDS",0):
            with self.assertRaises(dl.ImageDownloadError):
                dl.download_image("https://example.com/image")
        response=FakeResponse(status=302,headers={"Location":"/again"})
        with patch.object(dl,"_connection",side_effect=lambda *a:(connection(response),"/")) as build:
            with self.assertRaises(dl.ImageDownloadError):
                dl.download_image("https://example.com/image")
            self.assertEqual(build.call_count,dl.MAX_REDIRECTS+1)
        for url in ["file:///etc/passwd","ftp://example.com/image","http://user:pass@example.com/image","http://example.com:8080/image"]:
            with self.assertRaises(dl.ImageDownloadError):
                dl._connection(url,dl.time.monotonic()+20)

    def test_permissions_precede_download_and_failure_never_replaces_photo(self):
        doc=Mock()
        doc.image="/files/original.png"
        with patch.object(frappe,"get_doc",return_value=doc), patch.object(dl,"download_image") as download, patch.object(profile_image,"save_file") as save:
            doc.check_permission.side_effect=frappe.PermissionError
            with self.assertRaises(frappe.PermissionError):
                profile_image.set_profile_image_from_url("user","https://example.com/image")
            download.assert_not_called();save.assert_not_called()
            doc.check_permission.side_effect=None
            download.side_effect=dl.ImageDownloadError("broken image")
            with self.assertRaises(frappe.ValidationError):
                profile_image.set_profile_image_from_url("user","https://example.com/image")
            save.assert_not_called()
            self.assertEqual(doc.image,"/files/original.png")

    def test_real_transfer_and_total_deadline(self):
        """Exercise HTTPResponse streaming and watchdog, not only mocked responses."""
        import http.client
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        import time
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.path == "/slow":
                    time.sleep(0.7)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.end_headers()
                    self.wfile.write(png())
                except (OSError, BrokenPipeError):
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        def local_connection(url, deadline):
            return http.client.HTTPConnection("127.0.0.1", server.server_port), url
        try:
            with patch.object(dl, "_connection", side_effect=local_connection):
                self.assertEqual(dl.download_image("/ok")[1], "PNG")
                start = time.monotonic()
                with patch.object(dl, "TOTAL_SECONDS", 0.15):
                    with self.assertRaises(dl.ImageDownloadError):
                        dl.download_image("/slow")
                self.assertLess(time.monotonic()-start, 0.6)
        finally:
            server.shutdown()
            server.server_close()

    def test_image_dimensions_and_truncated_data(self):
        with self.assertRaises(dl.ImageDownloadError):
            dl.validate_image(png()[:30])
        image = Mock()
        image.format = "PNG"
        image.width = 10000
        image.height = 10000
        image.__enter__ = Mock(return_value=image)
        image.__exit__ = Mock(return_value=False)
        with patch.object(dl.Image, "open", return_value=image):
            with self.assertRaises(dl.ImageDownloadError):
                dl.validate_image(png())

    def test_url_upload_preserves_response_and_does_not_replace_on_save_failure(self):
        doc=Mock()
        doc.name="test-user"
        doc.doctype="Mobile App User"
        doc.image="/files/original.png"
        with patch.object(frappe, "get_doc", return_value=doc), patch.object(dl, "download_image", return_value=(png(),"PNG")), patch.object(profile_image, "save_file", side_effect=OSError("disk unavailable")), patch.object(profile_image, "_sync_mobile_app_user_image_field") as sync:
            with self.assertRaises(OSError):
                profile_image.set_profile_image_from_url(doc.name,"https://example.com/image")
            sync.assert_not_called()
            self.assertEqual(doc.image,"/files/original.png")
        def synced(*args):
            doc.image="/files/new.png"
        with patch.object(frappe,"get_doc",return_value=doc), patch.object(dl,"download_image",return_value=(png(),"PNG")), patch.object(profile_image,"save_file",return_value=Mock()), patch.object(profile_image,"_sync_mobile_app_user_image_field",side_effect=synced):
            result=profile_image.set_profile_image_from_url(doc.name,"https://example.com/image")
            self.assertEqual(result,{"success":True,"image":"/files/new.png"})
