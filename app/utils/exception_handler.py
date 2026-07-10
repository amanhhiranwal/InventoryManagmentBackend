from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import SQLAlchemyError


def register_exception_handlers(app: FastAPI):

    @app.exception_handler(IntegrityError)
    async def integrity_exception_handler(
        request: Request,
        exc: IntegrityError,
    ):

        error = str(exc.orig)

        if "duplicate key value violates unique constraint" in error:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Resource already exists."
                },
            )

        if "violates foreign key constraint" in error:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Invalid reference."
                },
            )

        return JSONResponse(
            status_code=400,
            content={
                "detail": "Database integrity error."
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(
        request: Request,
        exc: SQLAlchemyError,
    ):
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Database error."
            },
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(
        request: Request,
        exc: Exception,
    ):
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error."
            },
        )