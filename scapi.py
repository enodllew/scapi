#!/usr/bin/python
# /// script
# dependencies = [
#   "fastapi==0.115.11",
#   "httpx==0.28.1",
#   "structlog==25.1.0",
#   "uvicorn==0.34.0",
#   "uvloop==0.21.0",
# ]
# ///

import asyncio
import time
import typing as t
from collections import defaultdict
from dataclasses import dataclass

import httpx
import structlog
import uvicorn
import uvloop
from fastapi import FastAPI
from fastapi.responses import JSONResponse

__version__ = "0.2.3"


STATUS_CODES_COUNTER = defaultdict(int)

request_counter = 0
request_time_list = [0.0]
logger = structlog.get_logger()
app = FastAPI(docs_url="/")


@dataclass
class RequestParams:
    url: str
    method: t.Literal[
        "GET",
        "OPTIONS",
        "HEAD",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    ]
    headers: t.Annotated[dict | None, None] = None
    response_header: t.Annotated[bool, True | False] = False
    response_body: t.Annotated[bool, True | False] = False
    body: t.Annotated[dict | None, None] = None
    attempts: int = 10
    delay: float = 0.1


def done_callback(task: asyncio.Task) -> None:
    """
    Callback function to handle the completion of an asyncio Task,
    extract the result, and log the completion of the request.

    Args:
        task (asyncio.Task): The completed asyncio Task.

    Returns:
        None

    """
    response_data = None

    try:
        url, method, result, response_header, response_body, request_time = task.result()
    except asyncio.CancelledError:
        logger.info("Task was cancelled")
        return

    STATUS_CODES_COUNTER[result.status_code] += 1

    if response_body:
        try:
            response_data = result.json()
        except ValueError:
            response_data = result.text

    # Store the average request time
    request_time_list.append(float(request_time))

    logger.info(
        "Request completed",
        url=url,
        method=method,
        status_code=result.status_code,
        request_time=request_time,
        response=response_data,
        headers=result.headers if response_header else None,
    )


async def fetch(
    client: httpx.AsyncClient,
    params: RequestParams,
) -> tuple[str, str, httpx.Response, bool, bool, str]:
    """
    Fetches a URL using the provided HTTP client and request parameters.

    Args:
        client (httpx.AsyncClient):
                The HTTP client to use for making the requests.

        params (RequestParams):
                The parameters for the request.

    Returns:
        tuple: A tuple containing:
            - str: The URL of the request.
            - str: The HTTP method used for the request.
            - httpx.Response: The response object from the request.
            - bool: A flag indicating whether to include the response headers.
            - bool: A flag indicating whether to include the response body.
            - str: The time taken to complete the request.

    Raises:
        httpx.HTTPStatusError: If an HTTP error occurs during the request.

    """
    start_time = time.perf_counter()
    await asyncio.sleep(params.delay)

    headers = params.headers or {}
    headers["User-Agent"] = "SCAPI/1.0"
    if params.body:
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"

    do_request = getattr(client, params.method.lower())

    try:
        result = await (
            do_request(params.url, headers=headers, json=params.body)
            if params.body
            else do_request(params.url, headers=headers)
        )
    except httpx.RequestError as err:
        result = err.response  # type: ignore PGH003

    request_time = f"{time.perf_counter() - start_time:.4f}"

    return (
        params.url,
        params.method,
        result,
        params.response_header,
        params.response_body,
        request_time,
    )


async def startup_event(params: RequestParams) -> None:
    """
    Handles the startup event by making asynchronous HTTP requests.

    Args:
        params (RequestParams): The parameters for the request,
        including the number of attempts.

    Returns:
        None

    """
    async with httpx.AsyncClient(timeout=10) as client:
        for _ in range(params.attempts):
            t = asyncio.create_task(fetch(client, params=params))
            t.add_done_callback(done_callback)
            await t


# ------------------------------------------------------------------------------
#     - API Endpoints -
# ------------------------------------------------------------------------------
@app.post("/load")
async def load(
    url: str,
    method: t.Literal[
        "GET",
        "OPTIONS",
        "HEAD",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    ],
    headers: t.Annotated[dict | None, None] = None,
    response_header: t.Annotated[bool, True | False] = False,
    response_body: t.Annotated[bool, True | False] = False,
    body: t.Annotated[dict | None, None] = None,
    attempts: int = 10,
    delay: float = 0.1,
) -> JSONResponse:
    """Endpoint to make asynchronous HTTP requests."""
    background_tasks = set()

    params = RequestParams(
        url=url,
        headers=headers,
        method=method,
        body=body,
        response_header=response_header,
        response_body=response_body,
        attempts=attempts,
        delay=delay,
    )

    # Store the reference to avoid gc
    task = asyncio.create_task(startup_event(params))

    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return JSONResponse({"num": str(attempts)}, status_code=200)


@app.get("/results")
async def results() -> JSONResponse:
    """
    Calculates and returns the average request time and status code counts.

    """
    # Calculate the average
    avg_time = round(sum(request_time_list) / len(request_time_list), 2)

    return JSONResponse(
        {
            "results": STATUS_CODES_COUNTER,
            "average_request_time": avg_time,
        },
        status_code=200,
    )


if __name__ == "__main__":
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = asyncio.get_event_loop_policy()
    logger.info("SCAPI Version", version=__version__, loop=type(loop))
    uvicorn.run(app, host="0.0.0.0", port=8000)
