from __future__ import annotations

import aio_pika
from aio_pika import Channel, ExchangeType, RobustConnection
from aio_pika.abc import AbstractQueue

from app.config import Settings

_connection: RobustConnection | None = None

RETRY_COUNT_HEADER = "x-retry-count"


async def connect(settings: Settings) -> RobustConnection:
    global _connection
    if _connection is None or _connection.is_closed:
        if not settings.rabbitmq_uri:
            raise ValueError("RABBITMQ_URI is required")
        _connection = await aio_pika.connect_robust(settings.rabbitmq_uri)
    return _connection


async def close() -> None:
    global _connection
    if _connection is not None and not _connection.is_closed:
        await _connection.close()
    _connection = None


async def declare_jobs_queue(
    settings: Settings,
    channel: Channel | None = None,
) -> tuple[Channel, AbstractQueue]:
    connection = await connect(settings)
    if channel is None or channel.is_closed:
        channel = await connection.channel()
    await channel.set_qos(prefetch_count=settings.rabbitmq_prefetch)

    # Final dead-letter exchange + queue (DLQ consumer comes later).
    dlx = await channel.declare_exchange(
        settings.rabbitmq_dlx,
        ExchangeType.DIRECT,
        durable=True,
    )
    dlq = await channel.declare_queue(
        settings.rabbitmq_dlq,
        durable=True,
    )
    await dlq.bind(dlx, routing_key=settings.rabbitmq_dlq_routing_key)

    queue = await channel.declare_queue(
        settings.rabbitmq_queue,
        durable=True,
        arguments={
            "x-dead-letter-exchange": settings.rabbitmq_dlx,
            "x-dead-letter-routing-key": settings.rabbitmq_dlq_routing_key,
            "x-max-priority": 10,
        },
    )
    return channel, queue
