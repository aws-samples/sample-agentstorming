# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""OpenTelemetry instrumentation for the Agent Storming server.

The spec only says "the server emits OpenTelemetry." Exporter choice
(Langfuse / AgentCore-via-ADOT / Honeycomb / Jaeger / ...) is an
implementation concern configured via standard OTEL env vars:

- ``OTEL_EXPORTER_OTLP_ENDPOINT``
- ``OTEL_EXPORTER_OTLP_HEADERS``
- ``OTEL_SERVICE_NAME`` (defaults to ``agentstorming-server``)
- ``OTEL_RESOURCE_ATTRIBUTES``

If no endpoint is set, metrics + traces are emitted to the process's
console exporters at DEBUG level — useful for local development; in
production always point at a collector.

Metrics exposed:

- ``agentstorming.events.published`` (counter, by type)
- ``agentstorming.hands.raised`` (counter)
- ``agentstorming.mutes`` / ``.ejects`` / ``.pens`` (counter)
- ``agentstorming.interviews.started`` / ``.accepted`` / ``.rejected``
- ``agentstorming.sse.subscribers_active`` (gauge)
- ``agentstorming.rooms.active`` (gauge)
- ``agentstorming.participants.per_room`` (histogram on snapshot)

The OTEL_SDK is entirely optional — the server boots and runs fine
without the ``opentelemetry-*`` packages installed. When they ARE
installed, instrumentation auto-wires on startup.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Optional OTEL wiring — all imports guarded so the server still runs
# when opentelemetry packages are not installed.
# ------------------------------------------------------------------


class _NoopCounter:
    def add(self, *a, **k) -> None: ...


class _NoopHistogram:
    def record(self, *a, **k) -> None: ...


class _NoopGauge:
    def set(self, *a, **k) -> None: ...


class Telemetry:
    """Thin facade. Callers use this; it either forwards to OTEL or no-ops."""

    def __init__(self) -> None:
        self.enabled = False
        self.tracer = None
        self._meter = None
        self.events_published = _NoopCounter()
        self.hands_raised = _NoopCounter()
        self.mutes = _NoopCounter()
        self.ejects = _NoopCounter()
        self.pens = _NoopCounter()
        self.interviews_started = _NoopCounter()
        self.interviews_accepted = _NoopCounter()
        self.interviews_rejected = _NoopCounter()
        self.sse_subscribers = _NoopGauge()
        self.rooms_active = _NoopGauge()

    def init(self, app) -> None:
        """Wire OTEL into a FastAPI app. Silently no-ops when SDK absent."""
        try:
            from opentelemetry import metrics, trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        except ImportError:
            log.info("opentelemetry SDK not installed; telemetry disabled")
            return

        service = os.environ.get("OTEL_SERVICE_NAME", "agentstorming-server")
        resource = Resource.create({"service.name": service, "service.version": "0.1.0"})

        # Trace provider
        tracer_provider = TracerProvider(resource=resource)
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except Exception as e:
            log.info("OTLP span exporter not available (%s); using console fallback", e)
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(tracer_provider)
        self.tracer = trace.get_tracer("agentstorming.server")

        # Meter provider
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            reader = PeriodicExportingMetricReader(OTLPMetricExporter())
        except Exception as e:
            log.info("OTLP metric exporter not available (%s); using console fallback", e)
            from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
            reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(provider)
        self._meter = metrics.get_meter("agentstorming.server")

        # Create the metric instruments.
        self.events_published = self._meter.create_counter(
            "agentstorming.events.published", description="Events published to rooms"
        )
        self.hands_raised = self._meter.create_counter("agentstorming.hands.raised")
        self.mutes = self._meter.create_counter("agentstorming.mutes")
        self.ejects = self._meter.create_counter("agentstorming.ejects")
        self.pens = self._meter.create_counter("agentstorming.pens")
        self.interviews_started = self._meter.create_counter("agentstorming.interviews.started")
        self.interviews_accepted = self._meter.create_counter("agentstorming.interviews.accepted")
        self.interviews_rejected = self._meter.create_counter("agentstorming.interviews.rejected")

        class _UpDownGauge:
            def __init__(self, meter, name, description=""):
                self._ud = meter.create_up_down_counter(name, description=description)
                self._current = 0

            def set(self, value, attributes=None):
                delta = value - self._current
                self._current = value
                if delta != 0:
                    self._ud.add(delta, attributes=attributes or {})

        self.sse_subscribers = _UpDownGauge(self._meter, "agentstorming.sse.subscribers_active")
        self.rooms_active = _UpDownGauge(self._meter, "agentstorming.rooms.active")

        # Instrument FastAPI for request-level traces.
        try:
            FastAPIInstrumentor.instrument_app(app)
        except Exception as e:
            log.info("FastAPIInstrumentor not applied (%s)", e)
        self.enabled = True
        log.info("telemetry enabled; service.name=%s", service)


telemetry = Telemetry()
