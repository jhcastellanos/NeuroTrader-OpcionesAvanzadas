import logging

from fastapi import APIRouter, HTTPException, Query

from app.data_provider import normalize_ticker

from .databento import get_option_chain_for_dte

logger = logging.getLogger("neurotrader.databento")
router = APIRouter(prefix="/api/options", tags=["options"])


@router.get("/{ticker}")
async def option_chain(
    ticker: str,
    dte: int = Query(7, ge=0, le=3650),
):
    try:
        symbol = normalize_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return await get_option_chain_for_dte(symbol, dte)
    except Exception:
        logger.exception("Unexpected error loading Databento chain ticker=%s dte=%s", symbol, dte)
        return {
            "ticker": symbol,
            "requestedDte": dte,
            "actualDte": None,
            "expiration": None,
            "updated": None,
            "live": False,
            "ok": False,
            "dataStatus": "unavailable",
            "expirations": [],
            "contracts": [],
        }
