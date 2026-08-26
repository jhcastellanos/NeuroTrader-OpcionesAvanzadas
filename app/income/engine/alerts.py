"""
Alertas internas (seccion 12 del brief — configuracion inicial):
- Considerar cierre al capturar 50% de la prima.
- Revisar la posicion cuando falten 7-14 DTE.
- Alertar si el precio rompe el soporte o resistencia.
- Alertar si el contrato entra ITM.
- Alertar antes de earnings.

LIMITACION DOCUMENTADA: la alerta de "la delta aumento significativamente"
no esta implementada porque requeriria almacenar la delta al momento de
abrir la posicion (no se captura hoy en `PositionCreate`). Se puede
agregar en una fase de calidad posterior sin cambiar el resto del
diseno.
"""
from app.income.schemas import Alert, AlertSeverity, PositionResponse

_REVIEW_WINDOW_LOW_DTE = 7
_REVIEW_WINDOW_HIGH_DTE = 14
_PROFIT_TARGET_PCT = 50.0


def generate_alerts_for_position(
    position: PositionResponse,
    *,
    has_earnings_before_expiration: bool,
    price_broke_key_level: bool = False,
) -> list[Alert]:
    alerts: list[Alert] = []
    metrics = position.metrics
    if metrics is None:
        return alerts

    if metrics.pct_premium_captured is not None and metrics.pct_premium_captured >= _PROFIT_TARGET_PCT:
        alerts.append(
            Alert(
                position_id=position.id,
                ticker=position.ticker,
                alert_type="beneficio_objetivo",
                message=(
                    f"Ya capturaste {metrics.pct_premium_captured}% de la prima. "
                    "Considera cerrar la posicion segun tu plan de manejo."
                ),
                severity=AlertSeverity.INFO,
            )
        )

    if _REVIEW_WINDOW_LOW_DTE <= metrics.days_remaining <= _REVIEW_WINDOW_HIGH_DTE:
        alerts.append(
            Alert(
                position_id=position.id,
                ticker=position.ticker,
                alert_type="revision_dte",
                message=f"Quedan {metrics.days_remaining} dias para el vencimiento: revisa la posicion.",
                severity=AlertSeverity.CAUTION,
            )
        )

    if position.status == "itm":
        alerts.append(
            Alert(
                position_id=position.id,
                ticker=position.ticker,
                alert_type="itm",
                message="El contrato esta ITM. " + metrics.assignment_risk_note,
                severity=AlertSeverity.RISK,
            )
        )

    if has_earnings_before_expiration:
        alerts.append(
            Alert(
                position_id=position.id,
                ticker=position.ticker,
                alert_type="earnings_proximos",
                message="Hay earnings antes del vencimiento de este contrato: espera mayor volatilidad.",
                severity=AlertSeverity.CAUTION,
            )
        )

    if price_broke_key_level:
        alerts.append(
            Alert(
                position_id=position.id,
                ticker=position.ticker,
                alert_type="nivel_tecnico_roto",
                message="El precio rompio un soporte o resistencia tecnica relevante para este contrato.",
                severity=AlertSeverity.CAUTION,
            )
        )

    return alerts
