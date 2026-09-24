"""Serverless entry points. Point your scheduler (EventBridge, Cloud Scheduler, Azure Timer) here."""

from __future__ import annotations

import logging

from .runner import run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def lambda_handler(event, context):
    """AWS Lambda (EventBridge schedule)."""
    return run_once().as_dict()


def cloud_function(request):
    """Google Cloud Functions / Cloud Run (HTTP trigger from Cloud Scheduler)."""
    return run_once().as_dict()


def azure_timer(mytimer) -> None:
    """Azure Functions timer trigger."""
    logging.getLogger(__name__).info("run: %s", run_once().as_dict())
