from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.data_provider import normalize_ticker
from app.models import User

from .explain import (
    compare_strategies,
    evaluate_covered_call,
    evaluate_csp,
    explain_covered_call,
    explain_csp,
)
from .schemas import CashSecuredPutRequest, CompareStrategiesRequest, CoveredCallRequest
from .service import load_dashboard

router = APIRouter(prefix="/api/income", tags=["premium-income"])


def _normalize_body_ticker(ticker):
    try:
        return normalize_ticker(ticker)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/covered-call")
async def income_covered_call(body: CoveredCallRequest, _user: User = Depends(get_current_user)):
    try:
        body.ticker = _normalize_body_ticker(body.ticker)
        return await evaluate_covered_call(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=500, detail="No se pudo evaluar el Covered Call.")


@router.post("/csp")
async def income_csp(body: CashSecuredPutRequest, _user: User = Depends(get_current_user)):
    try:
        body.ticker = _normalize_body_ticker(body.ticker)
        return await evaluate_csp(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=500, detail="No se pudo evaluar el Cash-Secured Put.")


@router.post("/compare")
async def income_compare(body: CompareStrategiesRequest, _user: User = Depends(get_current_user)):
    try:
        body.ticker = _normalize_body_ticker(body.ticker)
        return await compare_strategies(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=500, detail="No se pudo comparar las estrategias.")


@router.post("/explain/covered-call")
async def income_explain_cc(body: CoveredCallRequest, _user: User = Depends(get_current_user)):
    try:
        body.ticker = _normalize_body_ticker(body.ticker)
        return await explain_covered_call(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=500, detail="No se pudo generar la explicación.")


@router.post("/explain/csp")
async def income_explain_csp(body: CashSecuredPutRequest, _user: User = Depends(get_current_user)):
    try:
        body.ticker = _normalize_body_ticker(body.ticker)
        return await explain_csp(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=500, detail="No se pudo generar la explicación.")


@router.get("/{ticker}")
async def income_dashboard(ticker: str, _user: User = Depends(get_current_user)):
    try:
        symbol = normalize_ticker(ticker)
        return await load_dashboard(symbol)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="No se pudo cargar Premium Income para este ticker.")
