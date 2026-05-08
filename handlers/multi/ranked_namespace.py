"""Lightweight ``/ranked`` Socket.IO namespace.

Clients connect here once they hit ``/api/ranked/queue/join`` so they can
receive ``rankedMatchFound`` broadcasts (and any future global ranked
events). The namespace itself is stateless — it's just a pub/sub channel.
"""

from __future__ import annotations

import socketio


class RankedNamespace(socketio.AsyncNamespace):
    async def on_connect(self, sid, environ, *args, **kwargs):
        # Anonymous subscribe is fine — we only emit non-sensitive
        # match-found pings here, scoped to the two opponents in payload.
        return True

    async def on_disconnect(self, sid):
        return None
