"""HTTP/range compatibility on one owned descriptor, never path reopening."""
from __future__ import annotations

from contextlib import ExitStack
import logging

import anyio
from starlette._utils import create_collapsing_task_group
from starlette.datastructures import MutableHeaders
from starlette.responses import FileResponse, PlainTextResponse

from scieflow.core.turn_artifacts import ArtifactError, RegularArtifact

logger = logging.getLogger(__name__)


class ArtifactResponse(FileResponse):
    """Reuse installed pure range/header machinery, replace ALL path I/O.

    The caller transfers its active root/source ExitStack on construction.
    FileResponse's pathname is an inert header seed, not a checked path; no
    inherited path reader is used and the pathsend extension is ignored.
    """
    def __init__(self, handle: RegularArtifact, owner: ExitStack, media_type: str,
                 filename: str | None = None):
        if type(handle) is not RegularArtifact or not isinstance(owner, ExitStack):
            raise ValueError('owned regular artifact required')
        self.handle, self.owner = handle, owner
        handle.verify_unchanged()
        super().__init__('owned-artifact', media_type=media_type, filename=filename,
                         stat_result=handle.snapshot_stat,
                         headers={'X-Content-Type-Options': 'nosniff'})

    async def _verify(self):
        await anyio.to_thread.run_sync(self.handle.verify_unchanged)

    async def __call__(self, scope, receive, send):
        started = False
        async def checked_send(message):
            nonlocal started
            await self._verify()
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        async def transfer():
            try:
                await self._verify()
                await super(ArtifactResponse, self).__call__(scope, receive, checked_send)
                await self._verify()
            except ArtifactError:
                if not started:
                    await PlainTextResponse('artifact unavailable', status_code=400)(scope, receive, send)
                else:
                    logger.warning('artifact transfer invalidated; incomplete response')
                    raise RuntimeError('artifact transfer invalidated') from None

        async def watch_disconnect(cancel_scope):
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    cancel_scope.cancel()
                    return

        try:
            async with create_collapsing_task_group() as group:
                group.start_soon(watch_disconnect, group.cancel_scope)
                try:
                    await transfer()
                finally:
                    group.cancel_scope.cancel()
        finally:
            # Synchronous fd close is bounded; it must also run under cancellation.
            self.owner.close()

    async def _body(self, send, start, end):
        while start < end:
            count = min(65536, end - start)
            chunk = await anyio.to_thread.run_sync(self.handle.read_at, start, count)
            if len(chunk) != count:
                raise ArtifactError('artifact-short-read')
            start += len(chunk)
            await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
        await self._verify()

    async def _finish(self, send):
        await self._verify()
        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})

    async def _handle_simple(self, send, send_header_only, send_pathsend):
        await send({'type': 'http.response.start', 'status': self.status_code, 'headers': self.raw_headers})
        if not send_header_only:
            await self._body(send, 0, self.handle.snapshot_size)
        await self._finish(send)

    async def _handle_single_range(self, send, start, end, file_size, send_header_only):
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers['content-range'] = f'bytes {start}-{end - 1}/{file_size}'
        headers['content-length'] = str(end - start)
        await send({'type': 'http.response.start', 'status': 206, 'headers': headers.raw})
        if not send_header_only:
            await self._body(send, start, end)
        await self._finish(send)

    async def _handle_multiple_ranges(self, send, ranges, file_size, send_header_only):
        from secrets import token_hex

        boundary = token_hex(13)
        length, header = self.generate_multipart(ranges, boundary, file_size, self.headers['content-type'])
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers['content-type'] = f'multipart/byteranges; boundary={boundary}'
        headers['content-length'] = str(length)
        await send({'type': 'http.response.start', 'status': 206, 'headers': headers.raw})
        if not send_header_only:
            for start, end in ranges:
                await send({'type': 'http.response.body', 'body': header(start, end), 'more_body': True})
                await self._body(send, start, end)
                await send({'type': 'http.response.body', 'body': b'\r\n', 'more_body': True})
            await send({'type': 'http.response.body', 'body': f'--{boundary}--'.encode('ascii'), 'more_body': True})
        await self._finish(send)
